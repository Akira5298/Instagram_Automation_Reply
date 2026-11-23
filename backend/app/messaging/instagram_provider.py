"""Real delivery through Meta's Instagram API (text messages and ❤️ reactions)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict

import httpx
from psycopg_pool import AsyncConnectionPool

from app.integrations.meta.instagram_client import GraphError
from app.messaging.provider import (
    PermanentSendError,
    SendResult,
    TransientSendError,
    UnknownOutcome,
)
from app.services.instagram_service import InstagramService


class InstagramMessagingProvider:
    """Maps Graph API outcomes onto the worker's error contract:

      connection never established / 5xx / rate limit  → TransientSendError
      invalid token, outside window, blocked user, …   → PermanentSendError
      timeout after the request was sent                → UnknownOutcome (never retried)

    Sends are paced per account (`instagram_min_send_interval`).
    """

    name = "instagram"

    def __init__(self, *, pool: AsyncConnectionPool, service: InstagramService, min_interval: float = 1.0) -> None:
        self.pool = pool
        self.service = service
        self.min_interval = min_interval
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_sent: dict[str, float] = {}

    async def send_text(
        self, *, account_ig_id: str, recipient_igsid: str, text: str, idempotency_key: str
    ) -> SendResult:
        message_id = await self._paced(account_ig_id, lambda token: self.service.client.send_text(
            token, account_ig_id, recipient_igsid, text))
        return SendResult(message_id)

    async def send_reaction(
        self, *, account_ig_id: str, recipient_igsid: str, message_id: str, idempotency_key: str
    ) -> None:
        await self._paced(account_ig_id, lambda token: self.service.client.send_reaction(
            token, account_ig_id, recipient_igsid, message_id))

    async def _paced(self, account_ig_id: str, call):
        async with self.pool.connection() as conn:
            token = await self.service.token_for(conn, account_ig_id)
        if token is None:
            raise PermanentSendError("Instagram isn't connected or the login expired")

        async with self._locks[account_ig_id]:
            wait = self.min_interval - (time.monotonic() - self._last_sent.get(account_ig_id, 0.0))
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                return await call(token)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise TransientSendError(f"couldn't reach Instagram: {exc}") from exc
            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as exc:
                raise UnknownOutcome(f"Instagram didn't answer after the request was sent: {exc}") from exc
            except GraphError as exc:
                if exc.is_outside_window:
                    raise PermanentSendError("outside Instagram's messaging window") from exc
                if exc.is_transient:
                    raise TransientSendError(str(exc)) from exc
                if exc.is_auth_error:
                    async with self.pool.connection() as conn:
                        await conn.execute(
                            "update instagram_accounts set status = 'expired', last_error = %s where ig_user_id = %s",
                            (str(exc)[:300], account_ig_id),
                        )
                raise PermanentSendError(str(exc)) from exc
            finally:
                self._last_sent[account_ig_id] = time.monotonic()
