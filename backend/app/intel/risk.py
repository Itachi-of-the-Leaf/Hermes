"""Risk detection and root-cause evidence.

Two independent sources of risk, so that the optimizer is never a single point of failure:

  structural   a confirmed blocker, an overdue promise, a dependency cycle. Derived from the graph alone;
               fires even when the solver is down or the schedule is built on guesses.
  scheduling   the forecast says a deadline will be missed (or got worse). Needs a usable schedule.

Attribution is evidence, not proof. A forecast change is linked to an input change only when a dependency
path connects them; otherwise the event says so ("possible" / "none") instead of claiming a cause.
"""
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime

from .graph import ProjectGraph
from .schedule import ScheduleResult

RISK_TYPES = ("CONFIRMED_BLOCKER", "COMMITMENT_OVERDUE", "DEADLINE_RISK", "OWNER_OVERLOAD", "GRAPH_INVALID",
              "PLAN_UNAVAILABLE")


@dataclass
class RiskEvent:
    risk_type: str
    severity: str                            # LOW | MEDIUM | HIGH
    task_id: str | None                      # the task the message will be about
    summary: str
    responsible_member_id: int | None = None
    contributing_task: str | None = None
    blocker: str | None = None
    affected: list[dict] = field(default_factory=list)   # [{task_id,title,owner,late_min,path}]
    previous_forecast: str | None = None
    new_forecast: str | None = None
    delay_minutes: int = 0
    deadline: str | None = None
    evidence: list[str] = field(default_factory=list)
    attribution: str = "direct"              # direct | possible | none
    confidence: str = "HIGH"                 # confidence of the forecast the numbers came from
    assumptions: list[str] = field(default_factory=list)
    context_hash: str = ""
    trigger: str = ""
    commitment_action_id: int | None = None
    promised_at: str | None = None           # when someone said it would be ready (COMMITMENT_OVERDUE)

    def to_dict(self) -> dict:
        return asdict(self)


def _h(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:8]


def changed_inputs(prev: ScheduleResult | None, cur: ScheduleResult) -> dict[str, list[str]]:
    """Which tasks' scheduling inputs differ between two forecasts, and how."""
    if prev is None or not prev.inputs:
        return {}
    out: dict[str, list[str]] = {}
    for tid in set(prev.inputs) | set(cur.inputs):
        a, b = prev.inputs.get(tid), cur.inputs.get(tid)
        if a is None:
            out[tid] = ["added"]
        elif b is None:
            out[tid] = ["completed"]
        else:
            diffs = [k for k in ("status", "duration", "earliest", "deps", "owner_id", "unknown_unblock")
                     if a.get(k) != b.get(k)]
            if diffs:
                out[tid] = [f"{k}: {a.get(k)} -> {b.get(k)}" for k in diffs]
    return out


def _responsible_for_blocker(g: ProjectGraph, task_id: str) -> int | None:
    t = g.tasks[task_id]
    if t.blocked_by_id:
        return t.blocked_by_id
    owners = {g.tasks[f].owner_id for f in g.frontier(task_id) if f != task_id and g.tasks[f].owner_id != t.owner_id}
    return next(iter(owners)) if len(owners) == 1 else None


def _affected(g: ProjectGraph, cur: ScheduleResult | None, root: str) -> list[dict]:
    rows = []
    for tid, path in g.downstream(root).items():
        tf = cur.tasks.get(tid) if cur and cur.usable else None
        rows.append({"task_id": tid, "title": g.tasks[tid].title, "owner": g.tasks[tid].owner,
                     "late_min": tf.late_min if tf else 0, "path": path})
    return rows


def detect(g: ProjectGraph, cur: ScheduleResult | None, prev: ScheduleResult | None,
           open_commitments: list[dict], now: datetime, trigger: str = "") -> list[RiskEvent]:
    """`open_commitments`: [{"action_id","task_id","recipient_id","commitment_at": aware datetime}]"""
    risks: list[RiskEvent] = []
    conf = cur.confidence if cur and cur.usable else "LOW"
    assumptions = list(cur.assumptions) if cur else []

    if g.cycle:
        risks.append(RiskEvent(
            "GRAPH_INVALID", "HIGH", g.cycle[0], "The task dependencies contain a cycle, so nothing can be scheduled.",
            evidence=["cycle: " + " -> ".join(g.cycle)], attribution="direct", trigger=trigger,
            context_hash=_h("cycle", *g.cycle)))
        return risks                                         # everything below needs a DAG

    # ---- structural: confirmed blockers and promises
    commits = {c["task_id"]: c for c in open_commitments}
    for t in g.blocked_tasks():
        resp = _responsible_for_blocker(g, t.id)
        affected = _affected(g, cur, t.id)
        worst = max([a["late_min"] for a in affected] + [cur.tasks[t.id].late_min if cur and cur.usable and t.id in cur.tasks else 0])
        sev = "HIGH" if worst >= 120 else "MEDIUM" if (affected or worst > 0) else "LOW"
        ev = [f"{t.owner} reported '{t.title}' blocked: {t.blocker_text or 'reason not given'}"]
        if resp is not None:
            ev.append(f"{g.members.get(resp, '?')} is the person who can unblock it")
        for a in affected[:3]:
            ev.append("dependency path: " + " -> ".join(g.tasks[p].title for p in a["path"]))
        c = commits.get(t.id)
        if c and c["commitment_at"] > now:
            continue                                         # a promise is pending; the scheduler already uses it
        if c:                                                # the promised time has passed
            overdue = int((now - c["commitment_at"]).total_seconds() // 60)
            risks.append(RiskEvent(
                "COMMITMENT_OVERDUE", "HIGH" if affected else "MEDIUM", t.id,
                f"{g.members.get(c['recipient_id'], '?')} promised to unblock '{t.title}' by "
                f"{c['commitment_at'].astimezone():%H:%M}; it is {overdue} min past that.",
                responsible_member_id=c["recipient_id"], contributing_task=t.id, blocker=t.blocker_text,
                affected=affected, evidence=ev + [f"promise made for {c['commitment_at'].astimezone():%H:%M}, not confirmed"],
                confidence=conf, assumptions=assumptions, trigger=trigger, commitment_action_id=c["action_id"],
                promised_at=c["commitment_at"].isoformat(),
                context_hash=_h("overdue", t.id, c["action_id"])))
            continue
        risks.append(RiskEvent(
            "CONFIRMED_BLOCKER", sev, t.id, f"'{t.title}' is blocked: {t.blocker_text or 'reason not given'}.",
            responsible_member_id=resp, contributing_task=t.id, blocker=t.blocker_text, affected=affected,
            evidence=ev, confidence=conf, assumptions=assumptions, trigger=trigger,
            context_hash=_h("blocker", t.id, t.blocker_text, t.blocked_by_id)))

    # ---- scheduling: forecast deadline violations
    if cur is not None and cur.status == "NO_WORK":
        return risks                                         # nothing left to schedule is not a problem
    if cur is None or not cur.usable:
        risks.append(RiskEvent(
            "PLAN_UNAVAILABLE", "LOW", None,
            f"No usable schedule ({cur.status if cur else 'solver error'}); blocker follow-ups still run.",
            evidence=[cur.note] if cur and cur.note else [], attribution="none", trigger=trigger,
            context_hash=_h("plan", cur.status if cur else "none")))
        return risks

    late = {tid: tf for tid, tf in cur.tasks.items() if tf.late_min > 0}
    if late:
        changes = changed_inputs(prev, cur)
        candidates: dict[str, list[str]] = {}               # changed task -> path to a late task
        for c_id in changes:
            if c_id not in g.tasks:
                continue
            for l_id in late:
                p = g.path_between(c_id, l_id)
                if p and g.tasks[c_id].is_open:
                    if c_id not in candidates or len(p) < len(candidates[c_id]):
                        candidates[c_id] = p
        if len(candidates) == 1:
            contrib, path = next(iter(candidates.items()))
            attribution = "direct"
        elif candidates:
            contrib = max(candidates, key=lambda c: len(g.downstream(c)))
            path, attribution = candidates[contrib], "possible"
        else:
            worst_id = max(late, key=lambda i: late[i].late_min)
            contrib = g.frontier(worst_id)[0]
            path, attribution = (g.path_between(contrib, worst_id) or [contrib]), "none"
        ct = g.tasks[contrib]
        resp = _responsible_for_blocker(g, contrib) if ct.is_blocked else ct.owner_id
        if ct.is_blocked and resp is None:
            resp = ct.owner_id
        prev_end = prev.project_end if prev and prev.usable else None
        delay = (datetime.fromisoformat(cur.project_end) - datetime.fromisoformat(prev_end)).total_seconds() / 60 \
            if prev_end and cur.project_end else max(tf.late_min for tf in late.values())
        worst = max(tf.late_min for tf in late.values())
        ev = [f"forecast: {len(late)} task(s) finish after their deadline, worst by {worst} min"]
        if changes:
            ev += [f"input changed for '{g.tasks[k].title}' ({'; '.join(v)})" for k, v in changes.items() if k in g.tasks][:3]
        ev.append("dependency path: " + " -> ".join(g.tasks[p].title for p in path))
        if attribution == "none":
            ev.append("no recent input change explains this; the deadline is tight for the remaining work")
        elif attribution == "possible":
            ev.append(f"{len(candidates)} changed tasks could each explain it; the largest bottleneck is shown")
        ev += [f"assumption: {a}" for a in assumptions[:3]]
        worst_late = max(late, key=lambda i: late[i].late_min)
        risks.append(RiskEvent(
            "DEADLINE_RISK", "HIGH" if worst >= 120 else "MEDIUM", contrib,
            f"Forecast: '{g.tasks[worst_late].title}' finishes {worst} min after its deadline.",
            responsible_member_id=resp, contributing_task=contrib,
            blocker=ct.blocker_text if ct.is_blocked else None,
            affected=[{"task_id": i, "title": g.tasks[i].title, "owner": g.tasks[i].owner, "late_min": tf.late_min,
                       "path": g.path_between(contrib, i) or [contrib, i]} for i, tf in late.items()],
            previous_forecast=prev_end, new_forecast=cur.project_end, delay_minutes=int(delay),
            deadline=late[worst_late].deadline, evidence=ev, attribution=attribution, confidence=conf,
            assumptions=assumptions, trigger=trigger,
            context_hash=_h("deadline", contrib, ct.status, ct.blocker_text, worst // 60)))

    # ---- informational: one person cannot do overlapping tasks
    cpm = g.cpm({tid: tf.duration_min for tid, tf in cur.tasks.items()})
    for conflict in g.owner_conflicts(cpm):
        if any(t in late for t in conflict["tasks"]):
            risks.append(RiskEvent(
                "OWNER_OVERLOAD", "LOW", conflict["tasks"][0],
                f"{conflict['owner']} has overlapping tasks: " + ", ".join(g.tasks[t].title for t in conflict["tasks"]),
                responsible_member_id=conflict["owner_id"], attribution="direct", trigger=trigger, confidence=conf,
                evidence=["one person cannot work on both at once; the schedule serialises them"],
                context_hash=_h("overload", *conflict["tasks"])))
    return risks


def sort_key(r: RiskEvent) -> tuple:
    return ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[r.severity], ("GRAPH_INVALID", "COMMITMENT_OVERDUE", "CONFIRMED_BLOCKER",
            "DEADLINE_RISK", "OWNER_OVERLOAD", "PLAN_UNAVAILABLE").index(r.risk_type), r.task_id or "")
