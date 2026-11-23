"""Push notifications through Apple Push Notification service (APNs).

Uses token-based auth: an APNs auth key (.p8) from your Apple Developer
account signs a short JWT (ES256) that authorises every push. No certificates
to renew. Requests go over HTTP/2, which APNs requires.

    POST https://api.push.apple.com/3/device/<token>              (TestFlight / App Store builds)
    POST https://api.sandbox.push.apple.com/3/device/<token>      (builds run from Xcode)
    authorization: bearer <JWT>   apns-topic: <bundle id>   apns-push-type: alert

Responses handled:
    200                      delivered to APNs
    410 / BadDeviceToken     the token is gone: the device is disabled
    403 *ProviderToken       JWT rejected/expired: a fresh one is made next time
    429 / 5xx                transient: the outbox retries (up to 5 attempts)

Configure in backend/.env (see docs/TESTFLIGHT.md §3):
    APNS_KEY_PATH=/Users/you/keys/AuthKey_ABC123DEFG.p8
    APNS_KEY_ID=ABC123DEFG
    APNS_TEAM_ID=XYZ987TEAM
    APNS_BUNDLE_ID=com.yourname.echo
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings

log = logging.getLogger("echo.push")

HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
JWT_LIFETIME = 45 * 60   # Apple accepts tokens up to 60 min old and rejects refreshing more than every 20 min
_GONE = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


class PushNotConfigured(Exception):
    pass


class TransientPushError(Exception):
    """Every device failed temporarily; the notification outbox will retry."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class ApnsTokenSigner:
    """Makes and caches the ES256 provider token."""

    def __init__(self, key_pem: bytes, key_id: str, team_id: str) -> None:
        key = serialization.load_pem_private_key(key_pem, password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise PushNotConfigured("APNs key must be the .p8 EC private key from Apple")
        self._key = key
        self.key_id = key_id
        self.team_id = team_id
        self._token: str | None = None
        self._issued_at = 0.0

    def token(self, now: float | None = None) -> str:
        now = time.time() if now is None else now
        if self._token is None or now - self._issued_at > JWT_LIFETIME:
            header = _b64(json.dumps({"alg": "ES256", "kid": self.key_id}, separators=(",", ":")).encode())
            claims = _b64(json.dumps({"iss": self.team_id, "iat": int(now)}, separators=(",", ":")).encode())
            signing_input = f"{header}.{claims}".encode()
            der = self._key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
            r, s = decode_dss_signature(der)
            signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")   # JWS wants raw r||s, not DER
            self._token = f"{header}.{claims}.{_b64(signature)}"
            self._issued_at = now
        return self._token

    def invalidate(self) -> None:
        self._token = None


def apns_configured(settings: Settings) -> bool:
    return bool(settings.apns_key_id and settings.apns_team_id and settings.apns_bundle_id
                and (settings.apns_key_path or settings.apns_private_key))


def load_signer(settings: Settings) -> ApnsTokenSigner:
    if not apns_configured(settings):
        raise PushNotConfigured("Set APNS_KEY_PATH, APNS_KEY_ID, APNS_TEAM_ID and APNS_BUNDLE_ID")
    if settings.apns_private_key:
        pem = settings.apns_private_key.get_secret_value().replace("\\n", "\n").encode()
    else:
        path = Path(str(settings.apns_key_path)).expanduser()
        if not path.is_file():
            raise PushNotConfigured(f"APNs key file not found: {path}")
        pem = path.read_bytes()
    return ApnsTokenSigner(pem, str(settings.apns_key_id), str(settings.apns_team_id))


@dataclass
class PushResult:
    device: str        # last 8 chars of the token, for logs
    ok: bool
    status: int
    reason: str | None = None


class ApnsNotificationSender:
    """NotificationSender that pushes to every active device of the user."""

    def __init__(self, *, pool: AsyncConnectionPool, settings: Settings, signer: ApnsTokenSigner,
                 http: httpx.AsyncClient | None = None) -> None:
        self.pool = pool
        self.bundle_id = str(settings.apns_bundle_id)
        self.signer = signer
        self.http = http or httpx.AsyncClient(http2=True, timeout=httpx.Timeout(15.0, connect=10.0))

    async def send(self, user_id: UUID, category: str, title: str, body: str, payload: dict) -> None:
        results = await self.push(user_id, category, title, body, payload)
        if results and not any(r.ok for r in results) and any(r.status == 429 or r.status >= 500 for r in results):
            raise TransientPushError(", ".join(f"{r.status} {r.reason}" for r in results))
        # No devices, or permanent failures: nothing to retry.

    async def push(self, user_id: UUID, category: str, title: str, body: str, payload: dict) -> list[PushResult]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "select id, token, environment from devices where user_id = %s and disabled_at is null",
                (user_id,),
            )
            devices = await cur.fetchall()
        results = []
        for device in devices:
            results.append(await self._send_one(device, category, title, body, payload))
        return results

    async def _send_one(self, device: dict, category: str, title: str, body: str, payload: dict) -> PushResult:
        message: dict[str, Any] = {
            "aps": {
                "alert": {"title": title, "body": body},
                "sound": "default",
                "thread-id": str(payload.get("friend_id") or category),
            },
            "category": category,
            **{k: v for k, v in payload.items() if isinstance(v, str | int | bool)},
        }
        headers = {
            "authorization": f"bearer {self.signer.token()}",
            "apns-topic": self.bundle_id,
            "apns-push-type": "alert",
            "apns-priority": "10",
        }
        if payload.get("auto_reply_id"):
            headers["apns-collapse-id"] = str(payload["auto_reply_id"])[:64]
        url = f"{HOSTS[device['environment']]}/3/device/{device['token']}"
        short = device["token"][-8:]
        try:
            response = await self.http.post(url, headers=headers, content=json.dumps(message).encode())
        except httpx.HTTPError as exc:
            log.warning("apns request failed", extra={"fields": {"device": short, "error": str(exc)}})
            return PushResult(short, False, 503, type(exc).__name__)
        reason = None
        if response.status_code != 200:
            try:
                reason = response.json().get("reason")
            except ValueError:
                reason = response.text[:80]
        if response.status_code == 200:
            return PushResult(short, True, 200)
        if response.status_code == 410 or reason in _GONE:
            await self._disable(device["id"], reason or "Unregistered")
        elif response.status_code == 403 and reason and "ProviderToken" in reason:
            self.signer.invalidate()
        log.warning("apns rejected push", extra={"fields": {"device": short, "status": response.status_code,
                                                            "reason": reason}})
        return PushResult(short, False, response.status_code, reason)

    async def _disable(self, device_id: UUID, reason: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("update devices set disabled_at = now(), last_error = %s where id = %s",
                               (reason, device_id))


def build_sender(settings: Settings, pool: AsyncConnectionPool):
    """APNs when configured, otherwise log-only (the activity feed still works)."""
    from app.services.notifications import LogNotificationSender

    if not apns_configured(settings):
        return LogNotificationSender()
    try:
        return ApnsNotificationSender(pool=pool, settings=settings, signer=load_signer(settings))
    except (PushNotConfigured, ValueError) as exc:
        log.error("push disabled: %s", exc)
        return LogNotificationSender()
