"""Action Planner: decides *whether* to contact someone, *whom*, and *with what kind of message*.

It sits between the risk engine (what is wrong and why) and the message writer (how to say it). Every
decision here is deterministic policy, so the LLM can never invent a recipient, a deadline or a reason.

Anti-spam rules, all enforced here:
  * cooldown      the same action, about the same situation, to the same person: at most once per cooldown
  * min gap       any two messages to one person about one task are at least a few minutes apart
  * promise       someone who promised a time is not chased before that time (plus a grace period)
  * attempts      after N unanswered attempts the agent escalates to the team lead once, then stops
  * run limit     at most a couple of messages per planning run, most severe first
  * new situation a changed blocker or a clearly worse forecast is a different context and may go out sooner
Task priority (how much a task matters) is a separate concept from notification priority (whether now is the
right moment to speak); a very important task still obeys all of the above.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, select

from ..config import settings
from ..models import AgentAction, Member, Task
from .graph import ProjectGraph
from .risk import RiskEvent, _h, sort_key

ACTIONS = ("NO_ACTION", "REQUEST_STATUS", "REQUEST_CLARIFICATION", "REQUEST_ETA", "REMIND_OWNER",
           "NOTIFY_DEPENDENT_OWNER", "REQUEST_HANDOFF", "ESCALATE_BLOCKER")
_STRENGTH = {"ESCALATE_BLOCKER": 5, "REQUEST_ETA": 4, "REMIND_OWNER": 3, "REQUEST_STATUS": 2, "REQUEST_CLARIFICATION": 1}


@dataclass
class ActionProposal:
    type: str
    recipient_id: int
    task_id: str
    risk_type: str
    severity: str
    risks: list[RiskEvent] = field(default_factory=list)
    context_hash: str = ""

    @property
    def dedupe_key(self) -> str:
        return f"{self.type}:{self.task_id}:{self.recipient_id}:{self.context_hash}"

    def reason(self) -> dict:
        return {"risk_type": self.risk_type, "severity": self.severity, "risks": [r.to_dict() for r in self.risks]}


def lead_of(s: Session) -> Member | None:
    return s.exec(select(Member).where(Member.role == "lead")).first()


def propose(g: ProjectGraph, r: RiskEvent, s: Session) -> ActionProposal | None:
    """One risk -> at most one proposal (before de-duplication across risks)."""
    if r.risk_type in ("OWNER_OVERLOAD", "PLAN_UNAVAILABLE") or not r.task_id:
        return None
    ld = lead_of(s)
    if r.risk_type == "GRAPH_INVALID":
        return ActionProposal("REQUEST_CLARIFICATION", ld.id, r.task_id, r.risk_type, r.severity, [r], r.context_hash) if ld else None
    t = g.tasks.get(r.task_id)
    if t is None:
        return None
    if r.risk_type == "COMMITMENT_OVERDUE":
        return ActionProposal("REMIND_OWNER", r.responsible_member_id, r.task_id, r.risk_type, r.severity, [r], r.context_hash)
    if r.risk_type == "CONFIRMED_BLOCKER":
        resp = r.responsible_member_id
        if resp is None or resp == t.owner_id:
            # nobody else is known to be able to unblock it: ask the owner who can
            return ActionProposal("REQUEST_CLARIFICATION", t.owner_id, r.task_id, r.risk_type, r.severity, [r], r.context_hash)
        return ActionProposal("REQUEST_ETA", resp, r.task_id, r.risk_type, r.severity, [r], r.context_hash)
    if r.risk_type == "DEADLINE_RISK":
        ct = g.tasks.get(r.contributing_task or r.task_id, t)
        if r.responsible_member_id is None:
            return ActionProposal("ESCALATE_BLOCKER", ld.id, ct.id, r.risk_type, r.severity, [r], r.context_hash) if ld else None
        kind = "REQUEST_ETA" if ct.is_blocked else "REMIND_OWNER" if ct.status == "Pending" else "REQUEST_STATUS"
        return ActionProposal(kind, r.responsible_member_id, ct.id, r.risk_type, r.severity, [r], r.context_hash)
    return None


def merge(props: list[ActionProposal]) -> list[ActionProposal]:
    """Two risks pointing at the same person about the same task are one message with both reasons."""
    by: dict[tuple, ActionProposal] = {}
    for p in props:
        k = (p.task_id, p.recipient_id)
        if k not in by:
            by[k] = p
            continue
        cur = by[k]
        cur.risks += p.risks
        if _STRENGTH[p.type] > _STRENGTH[cur.type]:
            cur.type = p.type
        if {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[p.severity] < {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[cur.severity]:
            cur.severity = p.severity
        cur.context_hash = _h(*sorted({cur.context_hash, p.context_hash}))
    return list(by.values())


def _recent(s: Session, since: datetime, **eq) -> list[AgentAction]:
    q = select(AgentAction).where(AgentAction.created_at >= since, AgentAction.status != "failed")
    for k, v in eq.items():
        q = q.where(getattr(AgentAction, k) == v)
    return list(s.exec(q).all())


def check_policy(s: Session, g: ProjectGraph, p: ActionProposal, now: datetime) -> tuple[ActionProposal | None, str]:
    """Returns (proposal to send, "") or (None, reason it was suppressed). May rewrite to an escalation."""
    if not s.get(Member, p.recipient_id):
        return None, "recipient does not exist"
    task = s.get(Task, p.task_id)
    if p.risk_type != "GRAPH_INVALID" and (task is None or task.status == "Complete"):
        return None, "task is complete or unknown"
    cd = timedelta(minutes=settings.intel_cooldown_min)
    gap = timedelta(minutes=settings.intel_min_gap_min)
    if _recent(s, now - cd, dedupe_key=p.dedupe_key):
        return None, f"cooldown ({settings.intel_cooldown_min:g} min) for the same situation"
    if _recent(s, now - gap, task_id=p.task_id, recipient_id=p.recipient_id):
        return None, f"min gap ({settings.intel_min_gap_min:g} min) since the last message to them about this task"
    promised = [a for a in s.exec(select(AgentAction).where(
        AgentAction.task_id == p.task_id, AgentAction.recipient_id == p.recipient_id,
        AgentAction.status == "committed")).all() if a.commitment_at and a.commitment_at + timedelta(
        minutes=settings.intel_commit_grace_min) > now]
    if promised and p.type != "ESCALATE_BLOCKER":
        return None, "they promised a time that has not passed yet"
    if p.type != "ESCALATE_BLOCKER":
        tries = _recent(s, now - timedelta(hours=24), task_id=p.task_id, recipient_id=p.recipient_id)
        tries = [a for a in tries if a.type != "ESCALATE_BLOCKER"]
        if len(tries) >= settings.intel_max_attempts:
            ld = lead_of(s)
            if ld and ld.id != p.recipient_id:
                esc = ActionProposal("ESCALATE_BLOCKER", ld.id, p.task_id, p.risk_type, "HIGH", p.risks,
                                     _h("esc", p.task_id, p.recipient_id))
                if _recent(s, now - timedelta(hours=24), dedupe_key=esc.dedupe_key):
                    return None, "already escalated to the lead"
                return esc, ""
            return None, f"{len(tries)} attempts without an answer and nobody to escalate to"
    return p, ""


def plan_actions(s: Session, g: ProjectGraph, risks: list[RiskEvent], now: datetime
                 ) -> tuple[list[ActionProposal], list[dict]]:
    """-> (messages to send, [{"task_id","type","recipient","reason"}] for everything held back)."""
    suppressed: list[dict] = []
    props = []
    for r in sorted(risks, key=sort_key):
        p = propose(g, r, s)
        if p is None:
            suppressed.append({"task_id": r.task_id, "type": "NO_ACTION", "recipient": None,
                               "reason": f"{r.risk_type} is informational"})
        else:
            props.append(p)
    accepted: list[ActionProposal] = []
    for p in merge(props):
        ok, why = check_policy(s, g, p, now)
        if ok is None:
            m = s.get(Member, p.recipient_id)
            suppressed.append({"task_id": p.task_id, "type": p.type, "recipient": m.name if m else None, "reason": why})
        else:
            accepted.append(ok)
    accepted.sort(key=lambda a: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[a.severity], -_STRENGTH[a.type]))
    cap = settings.intel_max_actions_per_run
    for p in accepted[cap:]:
        m = s.get(Member, p.recipient_id)
        suppressed.append({"task_id": p.task_id, "type": p.type, "recipient": m.name if m else None,
                           "reason": f"run limit ({cap} messages per planning run); will be reconsidered"})
    return accepted[:cap], suppressed


def open_commitments(s: Session) -> list[dict]:
    rows = s.exec(select(AgentAction).where(AgentAction.status == "committed")).all()
    return [{"action_id": a.id, "task_id": a.task_id, "recipient_id": a.recipient_id, "commitment_at": a.commitment_at}
            for a in rows if a.commitment_at]


def reason_json(p: ActionProposal) -> str:
    return json.dumps(p.reason(), default=str)
