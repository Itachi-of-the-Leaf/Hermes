"""Change notifications for the dashboard's Server-Sent Events stream.

Single process: an in-memory fan-out. With REDIS_URL set, every instance publishes to a
Redis channel and relays it to its own SSE clients, so the board stays live behind a load
balancer with several API tasks and a separate worker.
"""
import asyncio
import json
import logging

from .config import settings

log = logging.getLogger("bus")
CHANNEL = "coordinator:events"
_subscribers: set[asyncio.Queue] = set()
_redis = None
_loop: asyncio.AbstractEventLoop | None = None


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _fan_out(payload: str) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


def publish(kind: str, **data) -> None:
    payload = json.dumps({"kind": kind, **data}, default=str)
    if _redis is None:
        _fan_out(payload)
        return
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_redis.publish(CHANNEL, payload))
    except RuntimeError:            # called from a thread without a loop
        if _loop:
            asyncio.run_coroutine_threadsafe(_redis.publish(CHANNEL, payload), _loop)


async def start() -> None:
    """Connect to Redis and relay its messages to local SSE clients."""
    global _redis, _loop
    if not settings.redis_url:
        return
    import redis.asyncio as redis

    _redis = redis.from_url(settings.redis_url, decode_responses=True)
    _loop = asyncio.get_running_loop()

    async def relay():
        while True:
            try:
                pubsub = _redis.pubsub()
                await pubsub.subscribe(CHANNEL)
                async for msg in pubsub.listen():
                    if msg.get("type") == "message":
                        _fan_out(msg["data"])
            except Exception as e:      # reconnect after Redis restarts / failovers
                log.warning("redis relay error: %s; retrying", e)
                await asyncio.sleep(2)

    asyncio.create_task(relay())
    log.info("event bus on Redis")


async def ping() -> bool:
    if _redis is None:
        return True
    try:
        return bool(await _redis.ping())
    except Exception:
        return False
