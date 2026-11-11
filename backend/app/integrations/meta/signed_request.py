"""Meta `signed_request` parsing (deauthorize and data-deletion callbacks).

Format: base64url(signature) "." base64url(json payload), where signature =
HMAC-SHA256(payload_part, app_secret).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json


class SignedRequestError(ValueError):
    pass


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def parse_signed_request(signed_request: str, app_secret: str) -> dict:
    try:
        sig_part, payload_part = signed_request.split(".", 1)
        signature = _b64decode(sig_part)
        payload = json.loads(_b64decode(payload_part))
    except (ValueError, json.JSONDecodeError) as exc:
        raise SignedRequestError("malformed signed_request") from exc
    if str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        raise SignedRequestError("unexpected algorithm")
    expected = hmac.new(app_secret.encode(), payload_part.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise SignedRequestError("bad signature")
    return payload


def make_signed_request(payload: dict, app_secret: str) -> str:
    """For tests."""
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(app_secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode().rstrip("=") + "." + body
