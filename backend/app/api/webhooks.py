"""Meta webhook endpoint (Instagram API with Instagram Login).

GET  — subscription handshake (hub.mode / hub.verify_token / hub.challenge).
POST — verify X-Hub-Signature-256 over the raw bytes, store each messaging
       event in webhook_events (deduplicated), answer 200 immediately. The
       worker processes them; nothing slow happens inside this request.
"""

from __future__ import annotations

import hmac
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.core.config import Settings, get_settings
from app.integrations.meta.signature import verify_x_hub_signature_256
from app.services.webhook_service import ingest

router = APIRouter(prefix="/webhooks/meta", tags=["webhooks"])
log = logging.getLogger("echo.webhooks")


@router.get("")
def verify_subscription(
    hub_mode: str = Query(alias="hub.mode"),
    hub_verify_token: str = Query(alias="hub.verify_token"),
    hub_challenge: str = Query(alias="hub.challenge"),
    settings: Settings = Depends(get_settings),
) -> Response:
    expected = settings.meta_webhook_verify_token
    if (
        hub_mode != "subscribe"
        or expected is None
        or not hmac.compare_digest(hub_verify_token, expected.get_secret_value())
    ):
        raise HTTPException(status_code=403, detail="verification failed")
    return Response(content=hub_challenge, media_type="text/plain")


@router.post("")
async def receive_event(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    if settings.meta_app_secret is None:
        raise HTTPException(status_code=503, detail="webhook secret not configured")
    raw = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not verify_x_hub_signature_256(raw, signature, settings.meta_app_secret.get_secret_value()):
        log.warning("rejected webhook with bad signature")
        raise HTTPException(status_code=403, detail="bad signature")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"received": True, "stored": 0}  # signed but unparseable: acknowledge, don't retry forever

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")  # Meta will retry
    async with pool.connection() as conn:
        stored = await ingest(conn, payload)
    log.info("webhook accepted", extra={"fields": {"stored": stored}})
    return {"received": True, "stored": stored}
