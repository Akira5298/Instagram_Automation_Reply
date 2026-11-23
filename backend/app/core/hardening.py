"""HTTP hardening for a server that's reachable from the internet through a tunnel.

* Requests from the tunnel (anything not from this Mac) get:
    - no API docs (/docs, /redoc, /openapi.json are local-only);
    - no /v1 access at all while ECHO_API_TOKEN is still weak (default / short).
* Repeated wrong tokens from one address are slowed down: 10 failures within
  10 minutes → that address gets 429 for 15 minutes.
* Bodies over 1 MB are refused (nothing ECHO accepts is that big).
* Security headers on every response; /v1 responses are never cached.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import Settings

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
MAX_BODY = 1_000_000
FAIL_LIMIT, FAIL_WINDOW, BLOCK_FOR = 10, 600.0, 900.0
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers --forwarded-allow-ips 127.0.0.1 already replaced the
    # tunnel's address with the real client from X-Forwarded-For.
    return request.client.host if request.client else "unknown"


def is_local(request: Request) -> bool:
    return client_ip(request) in LOCAL_HOSTS and "x-forwarded-for" not in request.headers


def weak_token(settings: Settings) -> bool:
    token = settings.echo_api_token.get_secret_value()
    return token in ("change-me", "dev-token") or len(token) < 24


class AuthThrottle:
    """In-memory, per-address failed-login counter (single process)."""

    def __init__(self) -> None:
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.blocked_until: dict[str, float] = {}

    def blocked(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        until = self.blocked_until.get(ip)
        if until and until > now:
            return True
        self.blocked_until.pop(ip, None)
        return False

    def failure(self, ip: str, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        q = self.failures[ip]
        q.append(now)
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        if len(q) >= FAIL_LIMIT:
            self.blocked_until[ip] = now + BLOCK_FOR
            q.clear()

    def success(self, ip: str) -> None:
        self.failures.pop(ip, None)


throttle = AuthThrottle()


def install(app: FastAPI, settings: Settings) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next):
        path = request.url.path
        local = is_local(request)
        if path.startswith(DOCS_PATHS) and not local:
            return JSONResponse({"detail": "not found"}, status_code=404)
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        if path.startswith("/v1/"):
            if not local and weak_token(settings):
                return JSONResponse({"detail": (
                    "ECHO_API_TOKEN is too weak to use over the internet. Run scripts/setup_mac.sh "
                    "(or set a random 32+ character token) and restart.")}, status_code=403)
            if throttle.blocked(client_ip(request)):
                return JSONResponse({"detail": "too many failed attempts; try again later"}, status_code=429,
                                    headers={"Retry-After": str(int(BLOCK_FOR))})
        response = await call_next(request)
        for key, value in _HEADERS.items():
            response.headers.setdefault(key, value)
        if path.startswith("/v1/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
