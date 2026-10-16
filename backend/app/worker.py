"""Worker process: Telegram long polling, scheduled check-ins, Recall polling and the job queue.

Run exactly one (ECS desired count 1). Start with:  python -m app.worker
"""
import asyncio
import signal

from . import runtime
from .config import settings
from .db import engine
from .telemetry import setup_logging, setup_tracing


async def main() -> None:
    settings.role = "worker"
    setup_logging()
    setup_tracing(engine=engine)
    await runtime.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):      # ECS sends SIGTERM on deploys
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
