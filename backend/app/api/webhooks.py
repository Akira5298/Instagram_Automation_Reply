"""Meta webhook endpoint (Instagram API with Instagram Login).

Phase 1 ships the security boundary only: the subscription handshake and
signature verification. Phase 4 adds persistence to `webhook_events`
(deduplicated on event_key) and hands events to the worker. The endpoint
always answers quickly — heavy work never happens inside the request.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.core.config import Settings, get_settings
from app.integrations.meta.signature import verify_x_hub_signature_256

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
    # Phase 4: INSERT INTO webhook_events (event_key, payload) ON CONFLICT DO NOTHING
    log.info("webhook accepted", extra={"fields": {"bytes": len(raw)}})
    return {"received": True}
