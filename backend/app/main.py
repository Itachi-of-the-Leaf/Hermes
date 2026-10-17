"""API process: REST + SSE for the dashboard, and webhook endpoints for Slack, Teams and Recall.

    uvicorn app.main:app            (ROLE=all runs the worker loops in-process for local dev)
"""
import asyncio
import contextlib
import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session, select
from starlette.responses import StreamingResponse

from . import bus, channels, runtime, service
from .channels.slack import CHANNEL as SLACK
from .channels.telegram import CHANNEL as TELEGRAM
from .config import settings
from .db import engine, get_session, reset_db
from .intel.routes import router as intel_router
from .meetings.routes import router as meetings_router
from .models import Event, Member, Review, Task
from .telemetry import setup_logging, setup_tracing

setup_logging()
log = logging.getLogger("main")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await runtime.start(app)
    yield
    await runtime.stop()


app = FastAPI(title="Saarathi", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_methods=["*"], allow_headers=["*"])
setup_tracing(app, engine)
app.include_router(meetings_router)
app.include_router(intel_router)
SLACK.build()                               # so /api/slack/events can be served
from .channels.teams import CHANNEL as TEAMS  # noqa: E402

TEAMS.build(app)                            # mounts /api/teams/messages on this app at startup

DEV = settings.env != "prod"


# ---------------------------------------------------------------- health

@app.get("/api/health")
def health():
    """Liveness + what is configured (cheap; used by the ALB)."""
    return {"ok": True, "env": settings.env, "role": settings.role, "mode": settings.mode,
            "telegram": TELEGRAM.ready(), "bot_username": TELEGRAM.username,
            "slack": SLACK.ready(), "teams": TEAMS.ready(), "recall": settings.recall_enabled,
            "jev": settings.jev_enabled, "jev_model": settings.jev_model if settings.jev_enabled else None,
            "llm": settings.llm_enabled, "llm_model": settings.llm_model or None,
            "slack_summary_channel": bool(settings.slack_summary_channel),
            "slack_project_channel": bool(settings.slack_project_channel), "neo4j": bool(settings.neo4j_uri),
            "slack_last_real_event": channels.STATS["slack_real"], "telegram_last_update": channels.STATS["telegram"]}


@app.get("/api/ready")
async def ready():
    """Readiness: dependencies reachable."""
    try:
        with Session(engine) as s:
            s.exec(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(503, f"database: {e}")
    if not await bus.ping():
        raise HTTPException(503, "redis unreachable")
    return {"ok": True}


# ---------------------------------------------------------------- dashboard API

@app.get("/api/board")
def get_board(s: Session = Depends(get_session)):
    return service.board(s)


class TaskIn(BaseModel):
    title: str
    owner_key: str
    deadline: str | None = None            # 2026-10-25
    est_minutes: int | None = None
    depends_on: list[str] = []
    requested_by_key: str | None = None    # who asked for it, if not the owner themself


@app.post("/api/tasks")
def create_task(body: TaskIn, s: Session = Depends(get_session)):
    owner = s.exec(select(Member).where(Member.key == body.owner_key)).first()
    if not owner:
        raise HTTPException(400, f"no member {body.owner_key}")
    asker = s.exec(select(Member).where(Member.key == body.requested_by_key)).first() if body.requested_by_key else None
    if body.requested_by_key and not asker:
        raise HTTPException(400, f"no member {body.requested_by_key}")
    try:
        t = service.create_task(s, title=body.title, owner_id=owner.id, deadline=body.deadline,
                                est_minutes=body.est_minutes, depends_on=body.depends_on,
                                requested_by_id=asker.id if asker else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": t.id, "title": t.title}


class DependencyIn(BaseModel):
    depends_on: str


@app.post("/api/tasks/{task_id}/dependencies")
def add_dependency(task_id: str, body: DependencyIn, s: Session = Depends(get_session)):
    if not s.get(Task, task_id):
        raise HTTPException(404, f"no task {task_id}")
    try:
        service.add_dependency(s, task_id, body.depends_on)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.delete("/api/tasks/{task_id}/dependencies/{dep_id}")
def remove_dependency(task_id: str, dep_id: str, s: Session = Depends(get_session)):
    if not service.remove_dependency(s, task_id, dep_id):
        raise HTTPException(404, "no such dependency")
    return {"ok": True}


@app.post("/api/tasks/infer-dependencies")
async def infer_dependencies(s: Session = Depends(get_session)):
    """Ask the LLM to link the obvious prerequisites between existing tasks (only strong, loop-free relationships)."""
    from . import converse
    try:
        return {"linked": await converse.infer_dependencies(s)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, s: Session = Depends(get_session)):
    if not service.delete_task(s, task_id):
        raise HTTPException(404, f"no task {task_id}")
    return {"ok": True}


@app.get("/api/appointments")
def list_appointments(s: Session = Depends(get_session)):
    """Upcoming team meetings (announced in chat) and the few most recent ones."""
    from .models import Appointment
    names = {m.id: m.name for m in s.exec(select(Member)).all()}
    rows = s.exec(select(Appointment).where(Appointment.status != "cancelled").order_by(Appointment.starts_at.desc()).limit(30)).all()
    return [{"id": a.id, "title": a.title, "starts_at": a.starts_at, "status": a.status, "remind_min": a.remind_min,
             "created_by": names.get(a.created_by_id), "source_text": a.source_text,
             "attendees": [names.get(int(i), "?") for i in a.attendee_ids.split(",") if i] or ["everyone"]} for a in reversed(rows)]


@app.delete("/api/appointments/{appointment_id}")
def cancel_appointment(appointment_id: int, s: Session = Depends(get_session)):
    if not service.cancel_appointment(s, appointment_id):
        raise HTTPException(404, "no such upcoming meeting")
    return {"ok": True}


@app.get("/api/tasks/{task_id}/timeline")
def get_timeline(task_id: str, s: Session = Depends(get_session)):
    if not s.get(Task, task_id):
        raise HTTPException(404)
    return service.timeline(s, task_id)


@app.get("/api/activity")
def get_activity(s: Session = Depends(get_session)):
    return service.activity(s)


@app.get("/api/reviews")
def get_reviews(s: Session = Depends(get_session)):
    return service.reviews(s)


class ResolveIn(BaseModel):
    verdict: str          # "approve" | "In Progress" | "Blocked" | "Complete"


@app.post("/api/reviews/{review_id}/resolve")
async def resolve(review_id: int, body: ResolveIn, s: Session = Depends(get_session)):
    if not s.get(Review, review_id):
        raise HTTPException(404, f"no review {review_id}")
    if body.verdict != "approve" and body.verdict not in service.STATUS_EMOJI:
        raise HTTPException(400, f"verdict must be 'approve' or one of {list(service.STATUS_EMOJI)}")
    outbox = service.resolve_review(s, review_id, body.verdict, "dashboard")
    await channels.deliver(s, outbox)
    return {"ok": True}


class CheckinIn(BaseModel):
    task_id: str | None = None


@app.post("/api/checkin")
async def checkin(body: CheckinIn, s: Session = Depends(get_session)):
    outbox = service.followups(s, force=True, task_id=body.task_id)
    await channels.deliver(s, outbox)
    return {"sent": len(outbox)}


@app.post("/api/events/{event_id}/undo")
async def undo(event_id: int, s: Session = Depends(get_session)):
    if not s.get(Event, event_id):
        raise HTTPException(404, f"no event {event_id}")
    outbox = service.undo(s, event_id, "dashboard")
    await channels.deliver(s, outbox)
    return {"ok": True}


@app.get("/api/team")
def team(s: Session = Depends(get_session)):
    u = TELEGRAM.username
    return [{"key": m.key, "name": m.name, "email": m.email, "role": m.role, "preferred": m.preferred_channel,
             "channels": m.linked_channels(),
             "telegram_link": f"https://t.me/{u}?start={m.key}" if u else None}
            for m in s.exec(select(Member)).all()]


class PreferIn(BaseModel):
    channel: str


@app.post("/api/team/{key}/preferred")
def set_preferred(key: str, body: PreferIn, s: Session = Depends(get_session)):
    m = s.exec(select(Member).where(Member.key == key)).first()
    if not m or body.channel not in m.linked_channels():
        raise HTTPException(400, "member not connected on that channel")
    m.preferred_channel = body.channel
    s.add(m)
    s.commit()
    bus.publish("board")
    return {"ok": True}


@app.post("/api/reset")
def reset():
    if not DEV:
        raise HTTPException(403, "reset is disabled in production")
    reset_db()
    bus.publish("board")
    service.replan("RESET")                 # forecast and the Neo4j mirror must not keep showing the old project
    return {"ok": True}


# ---------------------------------------------------------------- platform webhooks

@app.post("/api/slack/events")
async def slack_events(request: Request):
    """Slack Events API, interactivity and slash commands (HTTP mode). Bolt verifies signatures."""
    if not SLACK.handler:
        raise HTTPException(404, "Slack HTTP mode not configured (set SLACK_SIGNING_SECRET)")
    if request.headers.get("user-agent", "").startswith("Slackbot"):        # Slack's own servers, not a test request
        channels.stamp("slack_real")
    return await SLACK.handler.handle(request)

# /api/teams/messages is registered by the Teams SDK (JWT-validated) during startup.
# /api/meetings/webhooks/recall lives in meetings/routes.py.


# ---------------------------------------------------------------- dev only (disabled when APP_ENV=prod)

class DevMessageIn(BaseModel):
    member_key: str
    text: str
    group: bool = False


class DevButtonIn(BaseModel):
    member_key: str
    data: str


def _member(s: Session, key: str) -> Member:
    m = s.exec(select(Member).where(Member.key == key)).first()
    if not m:
        raise HTTPException(404, f"no member {key}")
    return m


@app.post("/api/dev/message")
async def dev_message(body: DevMessageIn, s: Session = Depends(get_session)):
    if not DEV:
        raise HTTPException(403)
    m = _member(s, body.member_key)
    outbox = await (service.handle_group(s, m, body.text, channel="dev") if body.group
                    else service.handle_dm(s, m, body.text, channel="dev"))
    await channels.deliver(s, outbox)
    return {"outbox": [{**o, "to": s.get(Member, o["member_id"]).name} for o in outbox]}


@app.post("/api/dev/button")
async def dev_button(body: DevButtonIn, s: Session = Depends(get_session)):
    if not DEV:
        raise HTTPException(403)
    m = _member(s, body.member_key)
    outbox = await channels.handle_callback(s, m, body.data)
    await channels.deliver(s, outbox)
    return {"outbox": [{**o, "to": s.get(Member, o["member_id"]).name} for o in outbox]}


# ---------------------------------------------------------------- live stream

@app.get("/api/stream")
async def stream():
    q = bus.subscribe()

    async def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"          # keeps the ALB from closing idle streams
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
