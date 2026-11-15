from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.ai.mock_provider import MockAIProvider
from app.ai.provider import AIError, AIProvider, AIRateLimited
from app.api import (
    chat,
    dev,
    friends,
    health,
    instagram,
    jobs,
    profile,
    reviews,
    updates,
    webhooks,
)
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.pool import create_pool, ensure_owner
from app.messaging.provider import MessagingProvider, MockMessagingProvider

log = logging.getLogger("echo")


def build_provider(settings: Settings) -> AIProvider:
    if settings.echo_ai_provider == "anthropic":
        from app.ai.anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)
    return MockAIProvider()


def build_instagram(settings: Settings):
    """InstagramService when Meta credentials are configured, else None."""
    from app.services.instagram_service import (
        InstagramNotConfigured,
        InstagramService,
        make_cipher,
        make_client,
    )

    try:
        return InstagramService(settings=settings, client=make_client(settings), cipher=make_cipher(settings))
    except InstagramNotConfigured:
        return None


def build_messaging(settings: Settings, pool=None, instagram=None) -> MessagingProvider:
    if settings.echo_messaging_provider == "instagram":
        if pool is None or instagram is None:
            raise RuntimeError("ECHO_MESSAGING_PROVIDER=instagram needs DATABASE_URL and the META_* settings")
        from app.messaging.instagram_provider import InstagramMessagingProvider

        return InstagramMessagingProvider(pool=pool, service=instagram,
                                          min_interval=settings.instagram_min_send_interval)
    return MockMessagingProvider()


def create_app(
    settings: Settings | None = None,
    provider: AIProvider | None = None,
    messaging: MessagingProvider | None = None,
    run_worker: bool | None = None,
    instagram_service=None,
) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()
    start_worker = settings.echo_run_worker if run_worker is None else run_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ai_provider = provider or build_provider(settings)
        app.state.instagram = instagram_service if instagram_service is not None else build_instagram(settings)
        app.state.messaging = messaging
        app.state.pool = None
        app.state.worker = None
        stop = asyncio.Event()
        worker_task: asyncio.Task | None = None
        if settings.database_url is not None:
            pool = create_pool(settings.database_url.get_secret_value(), settings.db_pool_min, settings.db_pool_max)
            await pool.open(wait=True, timeout=15)
            await ensure_owner(pool, settings.echo_owner_user_id, settings.default_timezone)
            app.state.pool = pool
            from app.services.webhook_service import WebhookProcessor
            from app.worker.runner import Worker

            if app.state.messaging is None:
                app.state.messaging = build_messaging(settings, pool, app.state.instagram)
            app.state.worker = Worker(
                pool=pool, ai=app.state.ai_provider, messaging=app.state.messaging, settings=settings,
                webhooks=WebhookProcessor(pool=pool, instagram=app.state.instagram),
                instagram=app.state.instagram,
            )
            if start_worker:
                worker_task = asyncio.create_task(app.state.worker.run_forever(stop))
        else:
            app.state.messaging = app.state.messaging or MockMessagingProvider()
            log.warning("DATABASE_URL not set — chat and profile endpoints will return 503")
        try:
            yield
        finally:
            stop.set()
            if worker_task is not None:
                await worker_task
            if app.state.pool is not None:
                await app.state.pool.close()
            if app.state.instagram is not None:
                await app.state.instagram.client.aclose()

    app = FastAPI(title="ECHO backend", version="0.5.1", lifespan=lifespan)
    app.dependency_overrides[get_settings] = lambda: settings

    @app.middleware("http")
    async def require_database(request: Request, call_next):
        if request.url.path.startswith("/v1/") and getattr(request.app.state, "pool", None) is None:
            return JSONResponse({"detail": "database not configured"}, status_code=503)
        return await call_next(request)

    @app.exception_handler(AIError)
    async def ai_error_handler(_: Request, exc: AIError) -> JSONResponse:
        log.warning("ai error", extra={"fields": {"type": type(exc).__name__, "detail": str(exc)}})
        headers = {"Retry-After": "30"} if isinstance(exc, AIRateLimited) else None
        return JSONResponse({"detail": exc.user_message}, status_code=exc.status_code, headers=headers)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(profile.router)
    app.include_router(jobs.router)
    app.include_router(friends.router)
    app.include_router(reviews.router)
    app.include_router(updates.router)
    app.include_router(dev.router)
    app.include_router(instagram.router)
    app.include_router(instagram.public_router)
    app.include_router(webhooks.router)
    return app


def _default_app() -> FastAPI:
    return create_app()


app = _default_app()
