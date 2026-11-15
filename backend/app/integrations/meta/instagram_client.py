"""Meta Graph API client for Instagram messaging.

Two login setups are supported. New Meta apps (since autumn 2026) only get
**Instagram API with Unified Login**; apps created earlier may still use
**Instagram API with Instagram Login**. Pick one with META_LOGIN_MODE.

Unified Login (default, META_LOGIN_MODE=unified)
  authorize      https://www.facebook.com/{v}/dialog/oauth?client_id=<META_APP_ID>&config_id=<CONFIG_ID>
                 &redirect_uri=…&response_type=code&state=…   (no `scope`: the configuration sets permissions)
  code → token   GET https://graph.facebook.com/{v}/oauth/access_token?client_id&client_secret&redirect_uri&code
                 With a "System-user access token" configuration this is a Business Integration
                 System User token that doesn't expire by default. A user-token configuration
                 returns a short-lived token, which we swap via grant_type=fb_exchange_token.
  inspect        GET https://graph.facebook.com/{v}/debug_token?input_token=…&access_token=<app_id>|<secret>
  accounts       GET https://graph.facebook.com/{v}/me/assigned_instagram_accounts
                 (fallbacks: debug_token granular_scopes target_ids; /me/accounts Page links)
  account        GET https://graph.facebook.com/{v}/{IG_ID}?fields=id,username,name
  subscribe      POST https://graph.facebook.com/{v}/{IG_ID}/subscribed_apps?subscribed_fields=messages
  send           POST https://graph.facebook.com/{v}/{IG_ID}/messages  {"recipient":{"id"},"message":{"text"}}
  user profile   GET  https://graph.facebook.com/{v}/{IGSID}?fields=name,username
  conversations  GET  https://graph.facebook.com/{v}/{IG_ID}/conversations?platform=instagram
  Every call carries appsecret_proof (HMAC-SHA256 of the token with the app secret).
  Permissions (set in the configuration): instagram_basic, instagram_manage_messages.

Instagram Login (legacy, META_LOGIN_MODE=instagram)
  authorize        https://www.instagram.com/oauth/authorize
  code → short     POST https://api.instagram.com/oauth/access_token   (≈1 hour; response wrapped in "data")
  short → long     GET  https://graph.instagram.com/access_token?grant_type=ig_exchange_token  (≈60 days)
  refresh          GET  https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token
  me / subscribe / conversations use /me on graph.instagram.com; send and profile as above.
  Permissions: instagram_business_basic, instagram_business_manage_messages.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode

import httpx

log = logging.getLogger("echo.instagram")

LoginMode = Literal["unified", "instagram"]

# Legacy Instagram Login
IG_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
IG_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
IG_GRAPH_HOST = "https://graph.instagram.com"
IG_SCOPES = ("instagram_business_basic", "instagram_business_manage_messages")

# Unified Login
FB_DIALOG_HOST = "https://www.facebook.com"
FB_GRAPH_HOST = "https://graph.facebook.com"
UNIFIED_MESSAGING_SCOPE = "instagram_manage_messages"

# Kept for older imports.
AUTHORIZE_URL, TOKEN_URL, GRAPH_HOST, SCOPES = IG_AUTHORIZE_URL, IG_TOKEN_URL, IG_GRAPH_HOST, IG_SCOPES

# Graph error codes that mean "try again later".
_TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 80002, 80006}


@dataclass(frozen=True)
class GraphError(Exception):
    status: int
    code: int | None
    subcode: int | None
    message: str
    trace_id: str | None = None

    def __str__(self) -> str:
        sub = f"/{self.subcode}" if self.subcode else ""
        return f"Instagram API error {self.code}{sub}: {self.message}"

    @property
    def is_transient(self) -> bool:
        return self.status >= 500 or self.status == 429 or (self.code in _TRANSIENT_CODES)

    @property
    def is_auth_error(self) -> bool:
        return self.code == 190 or self.status == 401

    @property
    def is_outside_window(self) -> bool:
        text = self.message.lower()
        # Matched on the message text; 2018278 is the subcode developers commonly
        # report for this case — confirm against your own logs.
        return self.subcode == 2018278 or "outside of allowed window" in text or "outside the allowed window" in text


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    expires_in: int | None = None
    user_id: str | None = None
    permissions: list[str] | None = None


class InstagramClient:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        login_mode: LoginMode = "unified",
        config_id: str | None = None,
        extra_login_params: str | None = None,
        redirect_uri: str | None = None,
        graph_version: str = "v26.0",
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.login_mode: LoginMode = login_mode
        self.config_id = config_id
        self.extra_login_params = extra_login_params
        self.redirect_uri = redirect_uri
        self.version = graph_version
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))

    @property
    def unified(self) -> bool:
        return self.login_mode == "unified"

    @property
    def graph_host(self) -> str:
        return FB_GRAPH_HOST if self.unified else IG_GRAPH_HOST

    # ------------------------------------------------------------- OAuth

    def authorize_url(self, state: str, redirect_uri: str | None = None) -> str:
        redirect_uri = redirect_uri or self.redirect_uri
        if not redirect_uri:
            raise RuntimeError("OAuth redirect URI is not configured")
        if self.unified:
            if not self.config_id:
                raise RuntimeError("META_LOGIN_CONFIG_ID is not set")
            params: dict[str, str] = {
                "client_id": self.app_id,
                "config_id": self.config_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "override_default_response_type": "true",
                "state": state,
            }
            # Anything else the dashboard's generated login URL carries (e.g. "display=popup").
            for key, value in parse_qsl(self.extra_login_params or ""):
                params.setdefault(key, value)
            return f"{FB_DIALOG_HOST}/{self.version}/dialog/oauth?{urlencode(params)}"
        params = {
            "client_id": self.app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ",".join(IG_SCOPES),
            "state": state,
            "enable_fb_login": "false",
        }
        return f"{IG_AUTHORIZE_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str | None = None) -> TokenResult:
        code = code.split("#", 1)[0]  # Meta may append "#_" to the code
        redirect_uri = redirect_uri or self.redirect_uri
        if self.unified:
            data = await self._request("GET", f"{FB_GRAPH_HOST}/{self.version}/oauth/access_token", params={
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            })
            expires = data.get("expires_in")
            return TokenResult(access_token=data["access_token"], expires_in=int(expires) if expires else None)
        data = await self._request("POST", IG_TOKEN_URL, data={
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": code,
        })
        item = data["data"][0] if isinstance(data.get("data"), list) and data["data"] else data
        perms = item.get("permissions")
        if isinstance(perms, str):
            perms = [p.strip() for p in perms.split(",") if p.strip()]
        return TokenResult(access_token=item["access_token"], user_id=str(item.get("user_id") or ""),
                           permissions=perms)

    async def exchange_long_lived(self, short_token: str) -> TokenResult:
        if self.unified:
            data = await self._request("GET", f"{FB_GRAPH_HOST}/{self.version}/oauth/access_token", params={
                "grant_type": "fb_exchange_token",
                "client_id": self.app_id,
                "client_secret": self.app_secret,
                "fb_exchange_token": short_token,
            })
        else:
            data = await self._request("GET", f"{IG_GRAPH_HOST}/access_token", params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.app_secret,
                "access_token": short_token,
            })
        return TokenResult(access_token=data["access_token"], expires_in=data.get("expires_in"))

    async def refresh(self, long_token: str) -> TokenResult:
        """Legacy Instagram Login only. Unified Login tokens can't be refreshed."""
        data = await self._request("GET", f"{IG_GRAPH_HOST}/refresh_access_token", params={
            "grant_type": "ig_refresh_token",
            "access_token": long_token,
        })
        return TokenResult(access_token=data["access_token"], expires_in=data.get("expires_in"))

    async def debug_token(self, token: str) -> dict[str, Any]:
        """Unified Login: what the token is, its scopes and the IDs each scope covers."""
        data = await self._request("GET", f"{FB_GRAPH_HOST}/{self.version}/debug_token", params={
            "input_token": token,
            "access_token": f"{self.app_id}|{self.app_secret}",
        })
        return data.get("data") or {}

    # ----------------------------------------------------------- Graph API

    async def me(self, token: str) -> dict[str, Any]:
        fields = "id,name" if self.unified else "user_id,username,account_type,name"
        return await self._graph("GET", "me", token, params={"fields": fields})

    async def assigned_instagram_accounts(self, token: str, max_pages: int = 5) -> list[str]:
        return await self._collect_ids(token, "me/assigned_instagram_accounts", {}, max_pages,
                                       lambda item: item.get("id"))

    async def page_instagram_accounts(self, token: str, max_pages: int = 5) -> list[str]:
        """Fallback for user-token configurations: Instagram accounts linked to Pages."""
        return await self._collect_ids(
            token, "me/accounts", {"fields": "instagram_business_account{id,username}"}, max_pages,
            lambda item: (item.get("instagram_business_account") or {}).get("id"))

    async def instagram_account(self, token: str, ig_id: str) -> dict[str, Any]:
        return await self._graph("GET", ig_id, token, params={"fields": "id,username,name"})

    async def subscribe_webhooks(self, token: str, ig_id: str | None = None, fields: str = "messages") -> dict:
        path = f"{ig_id}/subscribed_apps" if self.unified and ig_id else "me/subscribed_apps"
        return await self._graph("POST", path, token, params={"subscribed_fields": fields})

    async def send_text(self, token: str, ig_id: str, recipient_igsid: str, text: str) -> str:
        data = await self._graph("POST", f"{ig_id}/messages", token, json={
            "recipient": {"id": recipient_igsid},
            "message": {"text": text},
        })
        return str(data["message_id"])

    async def user_profile(self, token: str, igsid: str) -> dict[str, Any]:
        return await self._graph("GET", igsid, token, params={"fields": "name,username"})

    async def conversations(self, token: str, ig_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        path = f"{ig_id}/conversations" if self.unified and ig_id else "me/conversations"
        data = await self._graph("GET", path, token, params={
            "platform": "instagram",
            "fields": "id,updated_time,participants,messages.limit(20){id,created_time,from,to,message}",
            "limit": str(limit),
        })
        return data.get("data", [])

    # ------------------------------------------------------------ plumbing

    def appsecret_proof(self, token: str) -> str:
        return hmac.new(self.app_secret.encode(), token.encode(), hashlib.sha256).hexdigest()

    async def _collect_ids(self, token: str, path: str, params: dict, max_pages: int, pick) -> list[str]:
        ids: list[str] = []
        data = await self._graph("GET", path, token, params=params)
        for _ in range(max_pages):
            for item in data.get("data", []) or []:
                value = pick(item) if isinstance(item, dict) else None
                if value and str(value) not in ids:
                    ids.append(str(value))
            next_url = (data.get("paging") or {}).get("next")
            if not next_url or not str(next_url).startswith(self.graph_host):
                break
            data = await self._request("GET", next_url, headers={"Authorization": f"Bearer {token}"})
        return ids

    async def _graph(self, method: str, path: str, token: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.graph_host}/{self.version}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token}"}
        if self.unified:
            kwargs["params"] = {**(kwargs.get("params") or {}), "appsecret_proof": self.appsecret_proof(token)}
        return await self._request(method, url, headers=headers, **kwargs)

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        response = await self.http.request(method, url, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or (isinstance(body, dict) and "error" in body):
            err = body.get("error", {}) if isinstance(body, dict) else {}
            if isinstance(err, str):  # api.instagram.com sometimes returns {"error_type", "error_message"}
                err = {"message": body.get("error_message") or err}
            if isinstance(body, dict) and "error_message" in body and not err:
                err = {"message": body["error_message"], "code": body.get("code")}
            raise GraphError(
                status=response.status_code,
                code=err.get("code"),
                subcode=err.get("error_subcode"),
                message=str(err.get("message") or response.text[:200]),
                trace_id=err.get("fbtrace_id"),
            )
        return body if isinstance(body, dict) else {}

    async def aclose(self) -> None:
        await self.http.aclose()
