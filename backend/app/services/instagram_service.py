"""Connecting, maintaining and syncing the Instagram professional account.

Tokens are only ever held server-side, encrypted with AES-GCM. The app never
sees an Instagram token.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.crypto import TokenCipher
from app.core.public_url import oauth_redirect_uri, public_base_url
from app.db import messaging_repo, profile_repo
from app.integrations.meta.instagram_client import (
    UNIFIED_MESSAGING_SCOPE,
    GraphError,
    InstagramClient,
)
from app.services.notifications import notify

log = logging.getLogger("echo.instagram")

STATE_TTL = timedelta(minutes=15)
REFRESH_BEFORE = timedelta(days=10)
MIN_TOKEN_AGE_FOR_REFRESH = timedelta(hours=24)
PROFILE_REFRESH = timedelta(days=7)


class InstagramNotConfigured(RuntimeError):
    pass


class OAuthError(RuntimeError):
    pass


def make_client(settings: Settings) -> InstagramClient:
    missing = [m for m in settings.instagram_missing_settings() if m != "ECHO_TOKEN_ENCRYPTION_KEY"]
    if missing:
        raise InstagramNotConfigured(f"{', '.join(missing)} must be set (META_LOGIN_MODE={settings.meta_login_mode})")
    return InstagramClient(
        app_id=settings.meta_client_id or "",
        app_secret=settings.meta_app_secret.get_secret_value(),
        login_mode=settings.meta_login_mode,
        config_id=settings.meta_login_config_id,
        extra_login_params=settings.meta_login_extra_params,
        graph_version=settings.meta_graph_version,
    )


def make_cipher(settings: Settings) -> TokenCipher:
    if settings.echo_token_encryption_key is None:
        raise InstagramNotConfigured("ECHO_TOKEN_ENCRYPTION_KEY must be set")
    return TokenCipher(settings.echo_token_encryption_key.get_secret_value())


class InstagramService:
    def __init__(self, *, settings: Settings, client: InstagramClient, cipher: TokenCipher,
                 clock=lambda: datetime.now(UTC)) -> None:
        self.settings = settings
        self.client = client
        self.cipher = cipher
        self.clock = clock

    # ------------------------------------------------------------ connect

    async def start_connect(self, conn: AsyncConnection, user_id: UUID) -> str:
        redirect_uri = oauth_redirect_uri(self.settings)
        if not redirect_uri:
            raise InstagramNotConfigured(
                "No public https address yet. Start the Cloudflare Tunnel (scripts/start.sh) "
                "or set ECHO_PUBLIC_BASE_URL.")
        state = secrets.token_urlsafe(24)
        await conn.execute(
            "insert into oauth_states (state, user_id, expires_at, redirect_uri) values (%s, %s, %s, %s)",
            (state, user_id, self.clock() + STATE_TTL, redirect_uri),
        )
        return self.client.authorize_url(state, redirect_uri)

    async def complete_connect(self, conn: AsyncConnection, *, code: str, state: str) -> dict[str, Any]:
        cur = await conn.execute(
            """
            update oauth_states set used_at = now()
            where state = %s and used_at is null and expires_at > %s
            returning user_id, redirect_uri
            """,
            (state, self.clock()),
        )
        row = await cur.fetchone()
        if row is None:
            raise OAuthError("This login link expired. Start again from the app.")
        user_id = row["user_id"]

        try:
            if self.client.unified:
                acct = await self._authorize_unified(code, row["redirect_uri"])
            else:
                acct = await self._authorize_instagram(code, row["redirect_uri"])
        except GraphError as exc:
            log.warning("instagram oauth failed", extra={"fields": {"error": str(exc)}})
            raise OAuthError(f"Meta rejected the login ({exc.message}).") from exc

        now = self.clock()
        cur = await conn.execute(
            """
            insert into instagram_accounts (user_id, ig_user_id, username, account_type, provider, status,
                access_token_ciphertext, token_expires_at, token_refreshed_at, granted_scopes, profile_name,
                last_error, connected_at, login_mode, meta_user_id)
            values (%s, %s, %s, %s, 'instagram', 'active', %s, %s, %s, %s, %s, null, %s, %s, %s)
            on conflict (ig_user_id) do update set
                user_id = excluded.user_id, username = excluded.username, account_type = excluded.account_type,
                provider = 'instagram', status = 'active',
                access_token_ciphertext = excluded.access_token_ciphertext,
                token_expires_at = excluded.token_expires_at, token_refreshed_at = excluded.token_refreshed_at,
                granted_scopes = excluded.granted_scopes, profile_name = excluded.profile_name,
                last_error = null, connected_at = excluded.connected_at,
                login_mode = excluded.login_mode, meta_user_id = excluded.meta_user_id
            returning id
            """,
            (user_id, acct["ig_user_id"], acct["username"], acct["account_type"],
             self.cipher.encrypt(acct["token"]), acct["expires_at"], now, sorted(acct["scopes"]),
             acct["name"], now, self.client.login_mode, acct["meta_user_id"]),
        )
        account_id = (await cur.fetchone())["id"]

        # Only one active account per user: retire others (incl. the Phase 3 mock).
        await conn.execute(
            "update instagram_accounts set status = 'revoked' where user_id = %s and id <> %s and status = 'active'",
            (user_id, account_id),
        )

        subscribed = True
        try:
            await self.client.subscribe_webhooks(acct["token"], acct["ig_user_id"], "messages")
            await conn.execute("update instagram_accounts set webhook_subscribed_at = %s where id = %s",
                               (now, account_id))
        except GraphError as exc:
            subscribed = False
            await conn.execute("update instagram_accounts set last_error = %s where id = %s",
                               (f"webhook subscription failed: {exc.message}"[:300], account_id))

        await profile_repo.audit(conn, user_id, "user", "instagram.connected", "instagram_account",
                                 str(account_id), {"username": acct["username"], "webhooks": subscribed,
                                                   "login_mode": self.client.login_mode})
        return {"username": acct["username"], "webhooks_subscribed": subscribed}

    async def _authorize_instagram(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Legacy Business Login for Instagram: short → long-lived Instagram User token."""
        short = await self.client.exchange_code(code, redirect_uri)
        granted = set(short.permissions or [])
        if "instagram_business_manage_messages" not in granted and short.permissions is not None:
            raise OAuthError("Messaging permission wasn't granted. Allow access to messages and try again.")
        long = await self.client.exchange_long_lived(short.access_token)
        me = await self.client.me(long.access_token)
        account_type = (me.get("account_type") or "").upper() or None
        if account_type not in (None, "BUSINESS", "MEDIA_CREATOR"):
            raise OAuthError("This is a personal account. Switch it to a Creator or Business account in Instagram.")
        return {
            "ig_user_id": str(me.get("user_id") or me.get("id") or short.user_id),
            "username": me.get("username"), "name": me.get("name"), "account_type": account_type,
            "token": long.access_token, "scopes": short.permissions or [],
            "expires_at": self.clock() + timedelta(seconds=long.expires_in or 60 * 24 * 3600),
            "meta_user_id": None,
        }

    async def _authorize_unified(self, code: str, redirect_uri: str) -> dict[str, Any]:
        """Unified Login (Facebook Login for Business + configuration).

        With a system-user configuration the code becomes a Business Integration
        System User token that doesn't expire. The Instagram account the person
        shared is found with /me/assigned_instagram_accounts, falling back to the
        token's granular scopes and then to Page-linked accounts."""
        token = await self.client.exchange_code(code, redirect_uri)
        if token.expires_in and token.expires_in < 7 * 24 * 3600:
            # User-token configuration: swap the short-lived token for a ~60-day one.
            try:
                token = await self.client.exchange_long_lived(token.access_token)
            except GraphError as exc:
                log.info("long-lived exchange failed; keeping short token", extra={"fields": {"error": str(exc)}})
        access = token.access_token

        info: dict[str, Any] = {}
        try:
            info = await self.client.debug_token(access)
        except GraphError as exc:
            log.info("debug_token failed", extra={"fields": {"error": str(exc)}})
        scopes = [str(s) for s in info.get("scopes") or []]
        if scopes and UNIFIED_MESSAGING_SCOPE not in scopes:
            raise OAuthError("Messaging permission wasn't granted. Add instagram_manage_messages to your "
                             "Unified login configuration and connect again.")

        ig_ids: list[str] = []
        try:
            ig_ids = await self.client.assigned_instagram_accounts(access)
        except GraphError as exc:
            log.info("assigned_instagram_accounts failed", extra={"fields": {"error": str(exc)}})
        if not ig_ids:
            for grant in info.get("granular_scopes") or []:
                if grant.get("scope") in (UNIFIED_MESSAGING_SCOPE, "instagram_basic"):
                    ig_ids += [str(t) for t in grant.get("target_ids") or [] if str(t) not in ig_ids]
        if not ig_ids:
            try:
                ig_ids = await self.client.page_instagram_accounts(access)
            except GraphError as exc:
                log.info("page lookup failed", extra={"fields": {"error": str(exc)}})
        if not ig_ids:
            raise OAuthError("No Instagram account was shared with ECHO. Connect again and select your "
                             "Instagram professional account on Meta's screen.")

        ig_id = ig_ids[0]
        if len(ig_ids) > 1:
            log.info("several Instagram accounts shared; using the first",
                     extra={"fields": {"ig_ids": ig_ids}})
        profile: dict[str, Any] = {}
        try:
            profile = await self.client.instagram_account(access, ig_id)
        except GraphError as exc:
            log.info("account lookup failed", extra={"fields": {"error": str(exc)}})
        meta_user_id = None
        try:
            meta_user_id = str((await self.client.me(access)).get("id") or "") or None
        except GraphError:
            pass
        if meta_user_id is None and info.get("user_id"):
            meta_user_id = str(info["user_id"])

        expires_at = None  # system-user tokens: never, by default
        if info.get("expires_at"):  # 0 means "never"
            expires_at = datetime.fromtimestamp(int(info["expires_at"]), UTC)
        elif token.expires_in:
            expires_at = self.clock() + timedelta(seconds=int(token.expires_in))
        return {
            "ig_user_id": str(profile.get("id") or ig_id),
            "username": profile.get("username"), "name": profile.get("name"), "account_type": None,
            "token": access, "scopes": scopes, "expires_at": expires_at, "meta_user_id": meta_user_id,
        }

    # -------------------------------------------------------------- status

    async def status(self, conn: AsyncConnection, user_id: UUID) -> dict[str, Any]:
        account = await messaging_repo.get_active_account(conn, user_id)
        public_url = public_base_url(self.settings)
        configured = not self.settings.instagram_missing_settings() and bool(public_url)
        if account is None or account["provider"] != "instagram":
            return {
                "connected": False,
                "provider": self.settings.echo_messaging_provider,
                "configured": configured,
                "mock_account": account is not None and account["provider"] == "mock",
                "public_url": public_url,
                "login_mode": self.client.login_mode,
            }
        needs_reconnect = account.get("login_mode", "instagram") != self.client.login_mode
        return {
            "connected": account["status"] == "active" and not needs_reconnect,
            "login_mode": self.client.login_mode,
            "account_login_mode": account.get("login_mode", "instagram"),
            "needs_reconnect": needs_reconnect or account["status"] in ("expired", "revoked"),
            "provider": self.settings.echo_messaging_provider,
            "configured": configured,
            "mock_account": False,
            "username": account["username"],
            "account_type": account["account_type"],
            "status": account["status"],
            "token_expires_at": account["token_expires_at"],
            "webhooks_subscribed": account["webhook_subscribed_at"] is not None,
            "last_webhook_at": account["last_webhook_at"],
            "last_synced_at": account["last_synced_at"],
            "last_error": account["last_error"],
            "public_url": public_url,
        }

    async def disconnect(self, conn: AsyncConnection, user_id: UUID, *, delete_messages: bool) -> None:
        account = await messaging_repo.get_active_account(conn, user_id)
        if account is None or account["provider"] != "instagram":
            return
        await conn.execute(
            """
            update instagram_accounts set status = 'revoked', access_token_ciphertext = null,
                webhook_subscribed_at = null
            where id = %s
            """,
            (account["id"],),
        )
        if delete_messages:
            await conn.execute("delete from friends where instagram_account_id = %s", (account["id"],))
        await profile_repo.audit(conn, user_id, "user", "instagram.disconnected", "instagram_account",
                                 str(account["id"]), {"deleted_messages": delete_messages})

    async def ensure_webhook_subscriptions(self, pool: AsyncConnectionPool) -> int:
        """Meta unsubscribes an account after ~1 hour of failed webhook
        deliveries — e.g. while your MacBook was asleep. Re-subscribing is
        idempotent, so the worker does it at startup and every hour."""
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                select id, ig_user_id, access_token_ciphertext from instagram_accounts
                where provider = 'instagram' and status = 'active' and access_token_ciphertext is not null
                  and login_mode = %s
                """,
                (self.client.login_mode,),
            )
            accounts = await cur.fetchall()
        done = 0
        for account in accounts:
            try:
                token = self.cipher.decrypt(account["access_token_ciphertext"])
                await self.client.subscribe_webhooks(token, account["ig_user_id"], "messages")
            except GraphError as exc:
                async with pool.connection() as conn:
                    await conn.execute(
                        "update instagram_accounts set webhook_subscribed_at = null, last_error = %s where id = %s",
                        (f"webhook subscription failed: {exc.message}"[:300], account["id"]),
                    )
                continue
            async with pool.connection() as conn:
                await conn.execute(
                    "update instagram_accounts set webhook_subscribed_at = %s where id = %s",
                    (self.clock(), account["id"]),
                )
            done += 1
        return done

    # --------------------------------------------------------------- tokens

    async def token_for(self, conn: AsyncConnection, ig_user_id: str) -> str | None:
        cur = await conn.execute(
            """
            select access_token_ciphertext, token_expires_at from instagram_accounts
            where ig_user_id = %s and status = 'active' and provider = 'instagram' and login_mode = %s
            """,
            (ig_user_id, self.client.login_mode),
        )
        row = await cur.fetchone()
        if row is None or row["access_token_ciphertext"] is None:
            return None
        if row["token_expires_at"] and row["token_expires_at"] <= self.clock():
            return None
        return self.cipher.decrypt(row["access_token_ciphertext"])

    async def refresh_expiring_tokens(self, pool: AsyncConnectionPool) -> int:
        """Legacy Instagram tokens last ~60 days; refresh when <10 days remain.
        Unified Login tokens can't be refreshed: they're only marked expired."""
        now = self.clock()
        async with pool.connection() as conn:
            cur = await conn.execute(
                """
                select id, user_id, username, access_token_ciphertext, token_expires_at, token_refreshed_at,
                       login_mode
                from instagram_accounts
                where provider = 'instagram' and status = 'active' and access_token_ciphertext is not null
                  and token_expires_at is not null and token_expires_at < %s
                """,
                (now + REFRESH_BEFORE,),
            )
            accounts = await cur.fetchall()
        refreshed = 0
        for account in accounts:
            if account["token_expires_at"] <= now:
                async with pool.connection() as conn:
                    await conn.execute("update instagram_accounts set status = 'expired' where id = %s",
                                       (account["id"],))
                    await notify(conn, account["user_id"], "connection_error", "Instagram disconnected",
                                 "Your Instagram login expired. Reconnect it in Settings.")
                continue
            if account["login_mode"] != "instagram":
                continue  # Unified Login tokens can't be refreshed; reconnect when they expire
            if account["token_refreshed_at"] and now - account["token_refreshed_at"] < MIN_TOKEN_AGE_FOR_REFRESH:
                continue
            try:
                result = await self.client.refresh(self.cipher.decrypt(account["access_token_ciphertext"]))
            except GraphError as exc:
                async with pool.connection() as conn:
                    await conn.execute("update instagram_accounts set last_error = %s where id = %s",
                                       (f"token refresh failed: {exc.message}"[:300], account["id"]))
                    if exc.is_auth_error:
                        await conn.execute("update instagram_accounts set status = 'expired' where id = %s",
                                           (account["id"],))
                        await notify(conn, account["user_id"], "connection_error", "Instagram disconnected",
                                     "Instagram rejected ECHO's login. Reconnect it in Settings.")
                continue
            async with pool.connection() as conn:
                await conn.execute(
                    """
                    update instagram_accounts set access_token_ciphertext = %s, token_expires_at = %s,
                        token_refreshed_at = %s, last_error = null
                    where id = %s
                    """,
                    (self.cipher.encrypt(result.access_token),
                     now + timedelta(seconds=result.expires_in or 60 * 24 * 3600), now, account["id"]),
                )
            refreshed += 1
        return refreshed

    # ----------------------------------------------------------- profiles

    async def ensure_friend(
        self, conn: AsyncConnection, *, account: dict, igsid: str, token: str | None
    ) -> dict[str, Any]:
        """Find or create the friend for an IGSID. New people are never
        automated: AI replies are off and consent is unknown until you decide."""
        cur = await conn.execute(
            "select id, display_name, profile_fetched_at from friends where instagram_account_id = %s and igsid = %s",
            (account["id"], igsid),
        )
        row = await cur.fetchone()
        stale = row is None or row["profile_fetched_at"] is None or \
            self.clock() - row["profile_fetched_at"] > PROFILE_REFRESH
        name, username = None, None
        if stale and token:
            try:
                profile = await self.client.user_profile(token, igsid)
                name, username = profile.get("name"), profile.get("username")
            except GraphError as exc:
                log.info("profile lookup failed", extra={"fields": {"error": str(exc)}})
        if row is None:
            display = name or (f"@{username}" if username else f"Instagram user …{igsid[-4:]}")
            friend = await messaging_repo.upsert_friend(
                conn, user_id=account["user_id"], account_id=account["id"], igsid=igsid, display_name=display,
                username=username, style="custom", language=None, ai_reply_enabled=False, consent="unknown",
            )
            await conn.execute("update friends set profile_fetched_at = %s where id = %s",
                               (self.clock() if (name or username) else None, friend["id"]))
            return friend
        if name or username:
            await conn.execute(
                """
                update friends set display_name = coalesce(%s, display_name), username = coalesce(%s, username),
                    profile_fetched_at = %s
                where id = %s
                """,
                (name, username, self.clock(), row["id"]),
            )
        friend = await messaging_repo.get_friend(conn, account["user_id"], row["id"])
        assert friend is not None
        return friend

    # ---------------------------------------------------------------- sync

    async def sync_conversations(self, conn: AsyncConnection, user_id: UUID, retention_days: int = 30) -> dict:
        """Backfill recent threads via the Conversations API (e.g. right after
        connecting). Messages older than the retention period are skipped."""
        account = await messaging_repo.get_active_account(conn, user_id)
        if account is None or account["provider"] != "instagram":
            raise InstagramNotConfigured("Instagram isn't connected")
        token = await self.token_for(conn, account["ig_user_id"])
        if token is None:
            raise InstagramNotConfigured("Instagram login expired — reconnect it")
        cutoff = self.clock() - timedelta(days=retention_days)
        threads = await self.client.conversations(token, account["ig_user_id"])
        friends_seen, messages_seen = 0, 0
        for thread in threads:
            participants = (thread.get("participants") or {}).get("data", [])
            others = [p for p in participants if str(p.get("id")) != account["ig_user_id"]]
            if len(others) != 1:
                continue
            friend = await self.ensure_friend(conn, account=account, igsid=str(others[0]["id"]), token=token)
            friends_seen += 1
            for m in (thread.get("messages") or {}).get("data", []):
                at = datetime.fromisoformat(str(m["created_time"]).replace("+0000", "+00:00"))
                if at < cutoff or not m.get("message"):
                    continue
                from_id = str((m.get("from") or {}).get("id"))
                if from_id == account["ig_user_id"]:
                    await messaging_repo.record_outbound(conn, user_id=user_id, friend_id=friend["id"],
                                                         text=m["message"], mid=m["id"], sent_by="user_manual", at=at)
                else:
                    await messaging_repo.record_inbound(conn, user_id=user_id, friend_id=friend["id"],
                                                        text=m["message"], mid=m["id"], at=at)
                messages_seen += 1
        await conn.execute("update instagram_accounts set last_synced_at = %s where id = %s",
                           (self.clock(), account["id"]))
        return {"friends": friends_seen, "messages": messages_seen}
