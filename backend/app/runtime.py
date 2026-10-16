"""Start/stop shared by the API process (main.py) and the worker process (worker.py)."""
import asyncio
import logging
import tempfile
import time
from pathlib import Path

from sqlmodel import Session

from . import appointments, bus, channels, jobs, service
from .channels.slack import CHANNEL as SLACK
from .channels.teams import CHANNEL as TEAMS
from .channels.telegram import CHANNEL as TELEGRAM
from .config import settings
from .db import engine, init_db
from .intel import engine as intel_engine
from .meetings import analyze  # noqa: F401  (registers meeting jobs)

log = logging.getLogger("runtime")
HEARTBEAT = Path(tempfile.gettempdir()) / "worker-heartbeat"   # /tmp in containers (health checks read it)
_tasks: list[asyncio.Task] = []


async def _every(seconds: float, fn, name: str):
    while True:
        await asyncio.sleep(seconds)
        try:
            await fn()
        except Exception:
            log.exception("%s failed", name)


async def _followups():
    with Session(engine) as s:
        outbox = service.followups(s)
        await channels.deliver(s, outbox)
        if outbox:
            log.info("scheduled check-ins sent: %d", len(outbox))


async def _heartbeat():
    HEARTBEAT.write_text(str(time.time()))


async def start(fastapi_app=None) -> None:
    init_db()
    intel_engine.attach(asyncio.get_running_loop())
    await bus.start()
    if settings.runs_api:
        SLACK.build()
        TEAMS.build(fastapi_app)
    else:                                   # worker: send-only Slack/Teams clients
        SLACK.build()
        TEAMS.build(None)
    await TELEGRAM.start(poll=settings.runs_worker)          # exactly one poller: the worker
    await SLACK.start(socket_mode=settings.role == "all" and bool(settings.slack_app_token))
    await TEAMS.start()

    if settings.runs_worker:
        if settings.followup_interval_min > 0:
            _tasks.append(asyncio.create_task(_every(settings.followup_interval_min * 60, _followups, "followups")))
        _tasks.append(asyncio.create_task(_every(60, lambda: jobs.enqueue("meeting.poll_recall"), "recall-poll")))
        _tasks.append(asyncio.create_task(_every(20, _heartbeat, "heartbeat")))
        if settings.intel_autorun:
            _tasks.append(asyncio.create_task(_every(settings.intel_tick_s, intel_engine.tick, "intel-tick")))
        _tasks.append(asyncio.create_task(_every(30, appointments.tick, "appointments")))      # meeting reminders
        await _heartbeat()
        if settings.redis_url:
            _tasks.append(asyncio.create_task(jobs.consume_forever()))
    log.info("runtime started: role=%s channels=%s", settings.role, channels.status())


async def stop() -> None:
    for t in _tasks:
        t.cancel()
    await TELEGRAM.stop()
    await SLACK.stop()
