"""Run the scheduler worker as its own process:

    python -m app.worker

Use this (with ECHO_RUN_WORKER=false on the API service) when you want the API
and worker deployed separately. Several workers can run at once safely.
"""

from __future__ import annotations

import asyncio
import signal

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.pool import create_pool, ensure_owner
from app.main import build_instagram, build_messaging, build_provider
from app.services.webhook_service import WebhookProcessor
from app.worker.runner import Worker


async def main() -> None:
    configure_logging()
    settings = get_settings()
    if settings.database_url is None:
        raise SystemExit("DATABASE_URL is required for the worker")
    pool = create_pool(settings.database_url.get_secret_value(), 1, 3)
    await pool.open(wait=True, timeout=15)
    await ensure_owner(pool, settings.echo_owner_user_id, settings.default_timezone)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    instagram = build_instagram(settings)
    worker = Worker(
        pool=pool, ai=build_provider(settings), messaging=build_messaging(settings, pool, instagram),
        settings=settings, webhooks=WebhookProcessor(pool=pool, instagram=instagram), instagram=instagram,
    )
    try:
        await worker.run_forever(stop)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
