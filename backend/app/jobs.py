"""Background jobs (meeting analysis, live triage, scheduled check-ins).

Without Redis, jobs run as asyncio tasks in the same process. With REDIS_URL, the API
enqueues to a Redis list and the worker consumes it, so slow model calls never hold an
HTTP request and survive an API task being replaced during a deploy.
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable

from .config import settings

log = logging.getLogger("jobs")
QUEUE = "coordinator:jobs"
_handlers: dict[str, Callable[..., Awaitable[None]]] = {}
_redis = None


def handler(name: str):
    def deco(fn):
        _handlers[name] = fn
        return fn
    return deco


async def _run(name: str, kwargs: dict) -> None:
    fn = _handlers.get(name)
    if not fn:
        log.error("no handler for job %s", name)
        return
    try:
        await fn(**kwargs)
    except Exception:
        log.exception("job %s failed", name)


async def enqueue(name: str, **kwargs) -> None:
    global _redis
    if settings.redis_url:
        if _redis is None:
            import redis.asyncio as redis
            _redis = redis.from_url(settings.redis_url, decode_responses=True)
        await _redis.rpush(QUEUE, json.dumps({"name": name, "kwargs": kwargs}))
    else:
        asyncio.create_task(_run(name, kwargs))


async def consume_forever() -> None:
    """Worker loop: blocking pop from Redis."""
    if not settings.redis_url:
        return
    import redis.asyncio as redis
    # socket timeout must exceed the BLPOP wait, or every idle poll looks like an error
    r = redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=30, socket_connect_timeout=5,
                       health_check_interval=30)
    log.info("job consumer started")
    while True:
        try:
            item = await r.blpop([QUEUE], timeout=5)
            if item:
                job = json.loads(item[1])
                await _run(job["name"], job.get("kwargs", {}))
        except Exception as e:
            log.warning("job consumer error: %s", e)
            await asyncio.sleep(2)
