"""Orchestrator: validated event -> graph snapshot -> CP-SAT -> risks -> action plan -> group message.

Runs after a task changes (debounced), and on a slow tick so promises that expire get chased. Properties:

  * event driven      only scheduling-relevant changes call `request()`; chit-chat never does
  * debounced         a burst of events becomes one run (INTEL_DEBOUNCE_S)
  * single flight     one run at a time; changes that arrive meanwhile trigger exactly one more
  * versioned         each solve is tagged with the hash of its inputs; if the data changed while the solver
                      was running, the result is discarded as stale and recomputed
  * non-fatal         if scheduling fails the structural risks (confirmed blockers, overdue promises) still
                      produce follow-ups, and the last good forecast is kept
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import Session, select

from .. import bus, channels, jobs
from ..config import settings
from ..db import engine as db_engine
from ..models import AgentAction, Forecast, Member
from . import compose, planner, risk, schedule
from .graph import ProjectGraph, priority_scores

log = logging.getLogger("intel")
REUSE_MIN = 10                    # a forecast this young with unchanged inputs is reused instead of re-solved
KEEP_FORECASTS = 30

_loop: asyncio.AbstractEventLoop | None = None
_lock = asyncio.Lock()
_triggers: list[tuple[str, str | None]] = []
_task: asyncio.Task | None = None


# ---------------------------------------------------------------- triggering

def attach(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def request(kind: str, task_id: str | None = None) -> None:
    """Called from the (sync) business logic after a scheduling-relevant change. Thread-safe and cheap."""
    if not settings.intel_autorun or _loop is None:
        return
    try:
        if settings.redis_url and not settings.runs_worker:      # API replica: hand over to the one worker
            _loop.call_soon_threadsafe(lambda: asyncio.ensure_future(jobs.enqueue("intel.replan", kind=kind, task_id=task_id)))
        else:
            _loop.call_soon_threadsafe(_on_request, kind, task_id)
    except RuntimeError:                                          # loop already closed (shutdown)
        pass


def _on_request(kind: str, task_id: str | None) -> None:
    global _task
    _triggers.append((kind, task_id))
    if _task is None or _task.done():
        _task = asyncio.ensure_future(_drain())


@jobs.handler("intel.replan")
async def _job(kind: str, task_id: str | None = None) -> None:
    _on_request(kind, task_id)


async def _drain() -> None:
    global _task
    try:
        while _triggers:
            await asyncio.sleep(settings.intel_debounce_s)       # let related events arrive together
            batch = list(_triggers)
            _triggers.clear()
            try:
                await run_once(trigger=",".join(sorted({k for k, _ in batch})))
            except Exception:
                log.exception("replan failed")
    finally:
        _task = None


async def tick() -> None:
    await run_once(trigger="TICK")


# ---------------------------------------------------------------- persistence

def _load_latest(s: Session) -> tuple[Forecast | None, dict | None]:
    row = s.exec(select(Forecast).order_by(Forecast.id.desc())).first()
    return row, (json.loads(row.payload) if row else None)


def _store(s: Session, cur: schedule.ScheduleResult, payload: dict) -> None:
    s.add(Forecast(version=cur.version, status=cur.status, payload=json.dumps(payload, default=str)))
    s.commit()
    old = s.exec(select(Forecast).order_by(Forecast.id.desc()).offset(KEEP_FORECASTS)).all()
    for row in old:
        s.delete(row)
    s.commit()


def latest(s: Session) -> dict | None:
    return _load_latest(s)[1]


def close_stale_actions(s: Session, g: ProjectGraph, now: datetime) -> None:
    """An action about a situation that no longer exists is done; an unanswered one eventually expires."""
    for a in s.exec(select(AgentAction).where(AgentAction.status.in_(["sent", "committed"]))).all():
        t = g.tasks.get(a.task_id)
        gone = t is None or not t.is_open or (a.risk_type in ("CONFIRMED_BLOCKER", "COMMITMENT_OVERDUE") and not t.is_blocked)
        if gone:
            a.status = "resolved"
        elif a.status == "sent" and a.created_at < now - timedelta(hours=24):
            a.status = "expired"
        else:
            continue
        s.add(a)
    s.commit()


async def _alert(p: planner.ActionProposal, action: AgentAction, g: ProjectGraph) -> None:
    """Mirror the things a human should notice (escalations, undeliverable messages) to the Slack alert feed."""
    if not settings.slack_alert_channel or (p.type != "ESCALATE_BLOCKER" and action.status not in ("failed", "unreachable")):
        return
    slack = channels.REGISTRY.get("slack")
    if slack is None or not slack.ready() or not hasattr(slack, "post"):
        return
    task = g.tasks.get(p.task_id)
    what = ("Escalated to the lead" if p.type == "ESCALATE_BLOCKER"
            else "Could not reach a teammate (not connected to Slack or Telegram yet)" if action.status == "unreachable"
            else "Could not deliver a message")
    line = f"{what}: {task.title if task else p.task_id} ({action.type}, {p.severity.lower()} severity)"
    try:
        await slack.post(settings.slack_alert_channel, line, [])
    except Exception as e:                                                   # an alert feed must never break the loop
        log.warning("Slack alert skipped: %s", e)


def _commit_sig(commits: list[dict]) -> list:
    return sorted([c["task_id"], c["commitment_at"].isoformat()] for c in commits)


# ---------------------------------------------------------------- analysis for the dashboard

def analyse(g: ProjectGraph, cur: schedule.ScheduleResult | None) -> dict:
    if g.cycle:
        return {"issues": g.issues}
    slack = {i: (cur.tasks[i].slack_min if cur and i in cur.tasks else None) for i in g.tasks}
    w = (settings.intel_w_deadline, settings.intel_w_impact, settings.intel_w_blocker, settings.intel_w_resource)
    durations = {i: tf.duration_min for i, tf in cur.tasks.items()} if cur and cur.usable else {}
    cpm = g.cpm(durations) if durations else {}
    return {
        "issues": g.issues,
        "blocked": [t.id for t in g.blocked_tasks()],
        "affected": {t.id: sorted(g.downstream(t.id)) for t in g.blocked_tasks()},
        "bottlenecks": [{"task_id": i, "waiting_behind": n} for i, n in g.bottlenecks() if n > 0],
        "critical_path": [i for i, v in cpm.items() if v["critical"]],
        "owner_conflicts": g.owner_conflicts(cpm) if cpm else [],
        "priority": priority_scores(g, slack, w),
    }


# ---------------------------------------------------------------- the run

async def run_once(trigger: str = "MANUAL", now: datetime | None = None, send: bool = True) -> dict:
    """One full pass. `send=False` plans everything and reports what it would say without contacting anyone."""
    now = now or datetime.now(timezone.utc)
    async with _lock:
        for _attempt in range(3):
            with Session(db_engine) as s:
                g = ProjectGraph.from_db(s)
                close_stale_actions(s, g, now)
                commits = planner.open_commitments(s)
                version = g.version(_commit_sig(commits))
                row, payload = _load_latest(s)
            cur: schedule.ScheduleResult | None = None
            prev: schedule.ScheduleResult | None = None
            fresh = False
            if payload and row.version == version and (now - row.at.astimezone(timezone.utc)) < timedelta(minutes=REUSE_MIN):
                cur = schedule.ScheduleResult.from_dict(payload["forecast"])         # nothing changed recently
                prev = schedule.ScheduleResult.from_dict(payload["prev"]) if payload.get("prev") else None
            else:
                try:
                    cur = await asyncio.to_thread(schedule.plan, g, now, {c["task_id"]: c["commitment_at"] for c in commits}, version)
                except Exception:                                                     # plan() should never raise
                    log.exception("scheduling failed; continuing with structural checks only")
                if payload:
                    last = schedule.ScheduleResult.from_dict(payload["forecast"])
                    prev = last if last.version != version else (
                        schedule.ScheduleResult.from_dict(payload["prev"]) if payload.get("prev") else None)
                fresh = True
            with Session(db_engine) as s:                                             # did the data move while solving?
                g2 = ProjectGraph.from_db(s)
                v2 = g2.version(_commit_sig(planner.open_commitments(s)))
            if v2 == version:
                break
            log.info("discarding stale schedule %s (inputs are now %s)", version, v2)
        else:
            return {"trigger": trigger, "stale": True, "version": version, "risks": [], "actions": [], "suppressed": []}

        try:
            risks = risk.detect(g, cur, prev, commits, now, trigger)
        except Exception:
            log.exception("risk detection failed; falling back to structural-only")
            risks = risk.detect(g, None, None, commits, now, trigger)

        with Session(db_engine) as s:
            proposals, suppressed = planner.plan_actions(s, g, sorted(risks, key=risk.sort_key), now)
            analysis = analyse(g, cur)
            if fresh and cur is not None:
                _store(s, cur, {"forecast": cur.to_dict(), "prev": prev.to_dict() if prev else None,
                                "risks": [r.to_dict() for r in risks], "analysis": analysis, "trigger": trigger})
            sent = []
            people = {m.id: m for m in s.exec(select(Member)).all()}
            for p in proposals:
                text = compose.render(p, g, cur, now)
                item = {"type": p.type, "task_id": p.task_id, "recipient_id": p.recipient_id, "risk_type": p.risk_type,
                        "recipient": s.get(Member, p.recipient_id).name, "text": text, "status": "dry-run"}
                if send:
                    text = await compose.polish(text)
                    action = AgentAction(type=p.type, risk_type=p.risk_type, task_id=p.task_id, recipient_id=p.recipient_id,
                                         dedupe_key=p.dedupe_key, context_hash=p.context_hash, reason=planner.reason_json(p),
                                         text=text, status="pending", graph_version=version, created_at=now)   # "sent" only once it really was
                    s.add(action)
                    s.commit()
                    s.refresh(action)
                    ids = list(dict.fromkeys([p.recipient_id] + [t.owner_id for t in (g.tasks.get(p.task_id),) if t]))
                    ext, channel = await channels.deliver_group(s, text, task_id=p.task_id, kind="agent_action",
                                                                recipient_ids=ids)       # the person asked comes first
                    action.channel, action.ext_id = channel, ext
                    action.status = channel if channel in ("failed", "unreachable") else "sent"
                    s.add(action)
                    s.commit()
                    item.update(id=action.id, status=action.status, channel=channel)
                    await _alert(p, action, g)
                    item["text"] = text
                item["text"] = channels.to_plain(channels.expand_mentions(item["text"], people, "plain"))    # readable in the API/dashboard
                sent.append(item)
    if settings.neo4j_uri and cur is not None:
        try:
            from . import neo4j_mirror
            await asyncio.to_thread(neo4j_mirror.sync, g, cur, "main", None,
                                    {i: v["score"] for i, v in analysis.get("priority", {}).items()})
        except Exception as e:
            log.warning("Neo4j mirror skipped: %s", e)
    bus.publish("forecast")
    return {"trigger": trigger, "stale": False, "version": version, "reused": not fresh,
            "status": cur.status if cur else "NONE", "solver": cur.solver if cur else None,
            "confidence": cur.confidence if cur else None, "project_end": cur.project_end if cur else None,
            "total_late_min": cur.total_late_min if cur else None, "risks": [r.to_dict() for r in risks],
            "actions": sent, "suppressed": suppressed, "analysis": analysis}

