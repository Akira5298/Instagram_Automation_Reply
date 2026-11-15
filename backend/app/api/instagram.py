"""Instagram connection (Unified Login by default; legacy Instagram Login with
META_LOGIN_MODE=instagram) and Meta's required deauthorize / data-deletion callbacks."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from psycopg import AsyncConnection
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.public_url import public_base_url
from app.core.security import require_user
from app.db import profile_repo
from app.db.pool import get_conn
from app.integrations.meta.instagram_client import GraphError
from app.integrations.meta.signed_request import (
    SignedRequestError,
    parse_signed_request,
)
from app.services.instagram_service import (
    InstagramNotConfigured,
    InstagramService,
    OAuthError,
)

router = APIRouter(prefix="/v1/instagram", tags=["instagram"])

APP_CALLBACK = "echo://instagram/connected"


def get_instagram(request: Request, settings: Settings = Depends(get_settings)) -> InstagramService:
    service = getattr(request.app.state, "instagram", None)
    if service is None:
        missing = settings.instagram_missing_settings() or ["META_* settings"]
        raise HTTPException(
            status_code=503,
            detail=f"Instagram isn't configured on the server. Set {', '.join(missing)} in backend/.env "
                   "(see docs/META_SETUP.md) and restart.",
        )
    return service


class Disconnect(BaseModel):
    delete_messages: bool = False


@router.get("/status")
async def status(request: Request, user_id: UUID = Depends(require_user),
                 conn: AsyncConnection = Depends(get_conn), settings: Settings = Depends(get_settings)):
    service = getattr(request.app.state, "instagram", None)
    if service is None:
        return {"connected": False, "configured": False, "provider": settings.echo_messaging_provider,
                "mock_account": False, "login_mode": settings.meta_login_mode,
                "missing_settings": settings.instagram_missing_settings()}
    return await service.status(conn, user_id)


@router.post("/connect")
async def connect(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn),
                  service: InstagramService = Depends(get_instagram)):
    """Returns Meta's login URL (Unified Login: Facebook's dialog, where you can
    sign in with Instagram or Facebook). The app opens it in a secure browser
    sheet; Meta redirects to /oauth/callback, which redirects to
    echo://instagram/connected?status=… to close the sheet."""
    try:
        url = await service.start_connect(conn, user_id)
    except InstagramNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"authorize_url": url, "callback_scheme": "echo"}


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    """Public endpoint Instagram redirects to. Authenticated by the single-use
    `state` value created in /connect, not by the bearer token."""
    def back(status_: str, **extra: str) -> RedirectResponse:
        return RedirectResponse(f"{APP_CALLBACK}?{urlencode({'status': status_, **extra})}", status_code=302)

    if error:
        return back("cancelled", message=error_description or error)
    if not code or not state:
        return back("error", message="Missing code from Meta")
    service = getattr(request.app.state, "instagram", None)
    if service is None:
        return back("error", message="Instagram isn't configured on the server")
    try:
        async with request.app.state.pool.connection() as conn:
            result = await service.complete_connect(conn, code=code, state=state)
    except OAuthError as exc:
        return back("error", message=str(exc))
    return back("ok", username=result.get("username") or "",
                webhooks="1" if result.get("webhooks_subscribed") else "0")


@router.post("/sync")
async def sync(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn),
               service: InstagramService = Depends(get_instagram), settings: Settings = Depends(get_settings)):
    try:
        return await service.sync_conversations(conn, user_id, settings.message_retention_days)
    except InstagramNotConfigured as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except GraphError as exc:
        raise HTTPException(status_code=502, detail=f"Instagram: {exc.message}") from exc


@router.post("/disconnect")
async def disconnect(body: Disconnect, user_id: UUID = Depends(require_user),
                     conn: AsyncConnection = Depends(get_conn), service: InstagramService = Depends(get_instagram)):
    await service.disconnect(conn, user_id, delete_messages=body.delete_messages)
    return await service.status(conn, user_id)


# --- Meta callbacks (configured in the App Dashboard) ----------------------------

@router.post("/deauthorize", include_in_schema=False)
async def deauthorize(request: Request, signed_request: str = Form(...),
                      settings: Settings = Depends(get_settings)):
    """Called by Meta when you remove ECHO from your account. The signed
    request's user_id is the Instagram user (legacy login) or the Meta user
    (Unified Login), so both columns are matched."""
    payload = _verify(signed_request, settings)
    meta_id = str(payload.get("user_id") or "")
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            """
            update instagram_accounts set status = 'revoked', access_token_ciphertext = null
            where %s::text <> '' and (ig_user_id = %s or meta_user_id = %s) returning user_id, id
            """,
            (meta_id, meta_id, meta_id),
        )
        row = await cur.fetchone()
        if row:
            await profile_repo.audit(conn, row["user_id"], "system", "instagram.deauthorized",
                                     "instagram_account", str(row["id"]))
    return {"ok": True}


@router.post("/data-deletion", include_in_schema=False)
async def data_deletion(request: Request, signed_request: str = Form(...),
                        settings: Settings = Depends(get_settings)):
    """Meta's data-deletion callback: delete everything ECHO stored about this
    Instagram account and return a status URL + confirmation code."""
    payload = _verify(signed_request, settings)
    meta_id = str(payload.get("user_id") or "")
    code = secrets.token_hex(8)
    async with request.app.state.pool.connection() as conn:
        await conn.execute("insert into data_deletion_requests (confirmation_code, ig_user_id) values (%s, %s)",
                           (code, meta_id))
        cur = await conn.execute(
            "select id, user_id, ig_user_id from instagram_accounts "
            "where %s::text <> '' and (ig_user_id = %s or meta_user_id = %s)",
            (meta_id, meta_id, meta_id))
        account = await cur.fetchone()
        ig_user_id = account["ig_user_id"] if account else meta_id
        if account:
            # Cascades to friends, their messages, consent records and jobs.
            await conn.execute("delete from instagram_accounts where id = %s", (account["id"],))
            await profile_repo.audit(conn, account["user_id"], "system", "instagram.data_deleted",
                                     "instagram_account", ig_user_id)
        await conn.execute("delete from webhook_events where entry_ig_id = %s", (ig_user_id,))
        await conn.execute(
            "update data_deletion_requests set status = 'completed', completed_at = now() where confirmation_code = %s",
            (code,),
        )
    base = (public_base_url(settings) or str(request.base_url)).rstrip("/")
    return {"url": f"{base}/v1/instagram/data-deletion/{code}", "confirmation_code": code}


@router.get("/data-deletion/{code}", include_in_schema=False)
async def data_deletion_status(code: str, request: Request):
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "select status, requested_at, completed_at from data_deletion_requests where confirmation_code = %s",
            (code,),
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="unknown confirmation code")
    return {"confirmation_code": code, **row}


def _verify(signed_request: str, settings: Settings) -> dict:
    if settings.meta_app_secret is None:
        raise HTTPException(status_code=503, detail="not configured")
    try:
        return parse_signed_request(signed_request, settings.meta_app_secret.get_secret_value())
    except SignedRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- Public privacy policy (Meta requires a URL before the app can go Live) --------

public_router = APIRouter(tags=["public"])


@public_router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy(settings: Settings = Depends(get_settings)) -> str:
    base = (public_base_url(settings) or "").rstrip("/")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ECHO — Privacy Policy</title>
<style>body{{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;color:#111}}</style>
</head><body>
<h1>ECHO — Privacy Policy</h1>
<p><em>Edit this page (backend/app/api/instagram.py) with your name and contact e-mail before submitting for App Review.</em></p>
<p>ECHO is a personal messaging assistant operated by a single individual for their own Instagram professional account.</p>
<h2>What ECHO processes</h2>
<ul>
<li>Direct messages sent to and from the connected Instagram account, received through Meta's official Instagram API.</li>
<li>Basic profile information (name, username) of people who message the account, via Meta's User Profile API.</li>
<li>The account owner's own chats with the assistant and the writing-style profile derived from them.</li>
</ul>
<h2>How it is used</h2>
<p>Only to show conversations to the account owner, draft or send replies the owner has authorised, and schedule messages the owner configured. Message text is sent to Anthropic's Claude API to draft replies. Data is never sold or used for advertising.</p>
<h2>Retention</h2>
<p>Message text is deleted after {settings.message_retention_days} days. Access tokens are encrypted at rest.</p>
<h2>Your choices</h2>
<p>People who receive automated messages are told an AI assistant may reply and can ask the owner to stop at any time. To delete data, remove ECHO from your Instagram account's connected apps; Meta notifies ECHO and all data related to that account is deleted. Deletion status: {base}/v1/instagram/data-deletion/&lt;code&gt;.</p>
</body></html>"""
