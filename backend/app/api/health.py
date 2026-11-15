from fastapi import APIRouter, Depends, Request

from app.core.config import Settings, get_settings
from app.core.public_url import public_base_url

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    pool = getattr(request.app.state, "pool", None)
    database = "not_configured"
    if pool is not None:
        try:
            async with pool.connection(timeout=3) as conn:
                await conn.execute("select 1")
            database = "ok"
        except Exception:
            database = "error"
    return {
        "status": "ok" if database == "ok" else "degraded",
        "env": settings.echo_env,
        "ai_provider": settings.echo_ai_provider,
        "chat_model": settings.anthropic_chat_model if settings.echo_ai_provider == "anthropic" else "mock",
        "database": database,
        "messaging_provider": settings.echo_messaging_provider,
        "instagram_configured": getattr(request.app.state, "instagram", None) is not None,
        "public_url": public_base_url(settings),
        "version": request.app.version,
    }
