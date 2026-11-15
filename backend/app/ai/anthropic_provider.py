"""Claude provider (Anthropic Messages API).

Request shape (verified against the Claude API docs, Nov 2026):
  * Structured output via `output_config.format` (type "json_schema"), which
    makes Claude's reply valid JSON matching our schema.
  * `output_config.effort` controls thinking depth; `thinking:
    {"type": "between_tools"}` turns off up-front thinking on Sonnet 5.5 for
    lower latency.
  * No temperature/top_p: Sonnet 5.5 rejects them.
  * The static system prompt carries `cache_control` so repeated turns read
    it from the prompt cache (min. cacheable length on Sonnet 5.5: 512 tokens).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from app.ai.prompts import (
    CHAT_SYSTEM_PROMPT,
    COMPOSE_SYSTEM_PROMPT,
    REPLY_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    chat_output_schema,
    compose_output_schema,
    reply_output_schema,
)
from app.ai.provider import (
    AIBadOutput,
    AIError,
    AIRateLimited,
    AIRefused,
    AssistantTurn,
    ComposedMessage,
    ReplyDraft,
    SummaryResult,
    Usage,
)
from app.core.config import Settings

log = logging.getLogger("echo.ai")


def _usage(response: Any) -> Usage:
    u = getattr(response, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )


def _first_text(response: Any) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise AIBadOutput("no text block in response")


class AnthropicProvider:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        if client is None:
            if settings.anthropic_api_key is None:
                raise RuntimeError("ANTHROPIC_API_KEY is not set")
            client = anthropic.AsyncAnthropic(
                api_key=settings.anthropic_api_key.get_secret_value(),
                max_retries=settings.anthropic_max_retries,
                timeout=settings.anthropic_timeout_seconds,
            )
        self.client = client
        self.chat_model = settings.anthropic_chat_model
        self.summary_model = settings.anthropic_summary_model
        self.effort = settings.anthropic_chat_effort
        self.thinking = settings.anthropic_chat_thinking
        self._schema = chat_output_schema()
        self._compose_schema = compose_output_schema()
        self._reply_schema = reply_output_schema()

    async def chat_turn(self, context_block: str, messages: list[dict]) -> AssistantTurn:
        kwargs: dict[str, Any] = {
            "model": self.chat_model,
            "max_tokens": 2048,
            "system": [
                {"type": "text", "text": CHAT_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": context_block},
            ],
            "messages": messages,
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
        }
        if self.thinking != "omit":
            kwargs["thinking"] = {"type": self.thinking}

        response = await self._call(**kwargs)

        if response.stop_reason == "refusal":
            raise AIRefused("model refused")
        if response.stop_reason == "max_tokens":
            raise AIBadOutput("reply truncated at max_tokens")

        try:
            data = json.loads(_first_text(response))
            reply = str(data["reply"]).strip()
            language = str(data.get("language") or "en").strip()[:16]
            updates = data.get("memory_updates") or []
            schedules = data.get("schedule_requests") or []
            if not reply or not isinstance(updates, list) or not isinstance(schedules, list):
                raise ValueError("empty reply or bad lists")
        except (ValueError, KeyError, TypeError) as exc:
            raise AIBadOutput(f"unparseable structured output: {exc}") from exc

        usage = _usage(response)
        log.info(
            "claude chat turn",
            extra={"fields": {"model": response.model, "in": usage.input_tokens, "out": usage.output_tokens,
                              "cache_read": usage.cache_read_tokens, "cache_write": usage.cache_write_tokens}},
        )
        return AssistantTurn(text=reply, language=language, memory_updates=updates,
                             schedule_requests=schedules, model=response.model, usage=usage)

    async def summarize(self, previous_summary: str | None, transcript: str) -> SummaryResult:
        content = (
            f"<previous_summary>\n{previous_summary or '(none yet)'}\n</previous_summary>\n\n"
            f"<new_turns>\n{transcript}\n</new_turns>"
        )
        response = await self._call(
            model=self.summary_model,
            max_tokens=1024,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low"},
        )
        if response.stop_reason == "refusal":
            raise AIRefused("summary refused")
        return SummaryResult(text=_first_text(response).strip(), model=response.model, usage=_usage(response))

    async def compose_message(self, context_block: str, instruction: str) -> ComposedMessage:
        kwargs: dict[str, Any] = {
            "model": self.chat_model,
            "max_tokens": 1024,
            "system": [
                {"type": "text", "text": COMPOSE_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": context_block},
            ],
            "messages": [{"role": "user", "content": f"<instruction>\n{instruction}\n</instruction>"}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._compose_schema},
            },
        }
        if self.thinking != "omit":
            kwargs["thinking"] = {"type": self.thinking}
        response = await self._call(**kwargs)
        if response.stop_reason == "refusal":
            raise AIRefused("compose refused")
        if response.stop_reason == "max_tokens":
            raise AIBadOutput("draft truncated")
        try:
            data = json.loads(_first_text(response))
            text = str(data["message"]).strip()
            if not text:
                raise ValueError("empty message")
            flags = data.get("flags") or []
            if not isinstance(flags, list):
                raise ValueError("bad flags")
        except (ValueError, KeyError, TypeError) as exc:
            raise AIBadOutput(f"unparseable draft: {exc}") from exc
        return ComposedMessage(
            text=text,
            language=str(data.get("language") or "en")[:16],
            flags=[str(f) for f in flags],
            note=str(data.get("note") or "")[:300],
            model=response.model,
            usage=_usage(response),
        )

    async def draft_reply(self, context_block: str) -> ReplyDraft:
        kwargs: dict[str, Any] = {
            "model": self.chat_model,
            "max_tokens": 1024,
            "system": [
                {"type": "text", "text": REPLY_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": context_block},
            ],
            "messages": [{"role": "user", "content": "Draft the reply to the [unanswered] messages now."}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._reply_schema},
            },
        }
        if self.thinking != "omit":
            kwargs["thinking"] = {"type": self.thinking}
        response = await self._call(**kwargs)
        if response.stop_reason == "refusal":
            raise AIRefused("reply refused")
        if response.stop_reason == "max_tokens":
            raise AIBadOutput("reply truncated")
        try:
            data = json.loads(_first_text(response))
            action = str(data["action"])
            if action not in ("reply", "no_reply", "needs_user"):
                raise ValueError(f"bad action {action!r}")
            text = str(data.get("message") or "").strip()
            if action == "reply" and not text:
                raise ValueError("empty reply")
            flags = data.get("flags") or []
            if not isinstance(flags, list):
                raise ValueError("bad flags")
        except (ValueError, KeyError, TypeError) as exc:
            raise AIBadOutput(f"unparseable reply: {exc}") from exc
        return ReplyDraft(
            action=action,
            text=text,
            language=str(data.get("language") or "en")[:16],
            flags=[str(f) for f in flags],
            note=str(data.get("note") or "")[:300],
            model=response.model,
            usage=_usage(response),
        )

    async def _call(self, **kwargs: Any) -> Any:
        try:
            return await self.client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise AIRateLimited(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code in (429, 529):
                raise AIRateLimited(str(exc)) from exc
            log.error("claude api error", extra={"fields": {"status": exc.status_code}})
            raise AIError(f"Anthropic API error {exc.status_code}") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise AIError("could not reach Anthropic") from exc
