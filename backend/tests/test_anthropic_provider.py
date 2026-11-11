"""AnthropicProvider against a fake SDK client: checks the exact request shape
we send and how responses/errors are handled. No network."""

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.ai.anthropic_provider import AnthropicProvider
from app.ai.prompts import CHAT_SYSTEM_PROMPT
from app.ai.provider import AIBadOutput, AIError, AIRateLimited, AIRefused
from tests.conftest import make_settings


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def response(text, stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(input_tokens=900, output_tokens=60, cache_read_input_tokens=700,
                              cache_creation_input_tokens=0),
    )


def provider_with(fake, **settings):
    return AnthropicProvider(make_settings(echo_ai_provider="anthropic", **settings),
                             client=SimpleNamespace(messages=fake))


GOOD = json.dumps({
    "reply": "Nice! What got you into it?",
    "language": "en",
    "memory_updates": [{"op": "set", "category": "interests", "key": "interests.bouldering",
                        "value": "Loves bouldering", "kind": "stated_fact", "confidence": 1}],
})


async def test_request_shape_and_parsing():
    fake = FakeMessages(response(GOOD))
    turn = await provider_with(fake).chat_turn("<profile>…</profile>", [{"role": "user", "content": "I love bouldering"}])

    call = fake.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["thinking"] == {"type": "between_tools"}
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in call and "top_p" not in call
    system = call["system"]
    assert system[0]["text"] == CHAT_SYSTEM_PROMPT and system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]

    assert turn.text == "Nice! What got you into it?"
    assert turn.memory_updates[0]["key"] == "interests.bouldering"
    assert turn.usage.cache_read_tokens == 700


async def test_static_prompt_is_long_enough_to_cache():
    # Sonnet 5.5's minimum cacheable prompt is 512 tokens (~4 chars/token).
    assert len(CHAT_SYSTEM_PROMPT) / 4 > 600


async def test_thinking_can_be_omitted():
    fake = FakeMessages(response(GOOD))
    await provider_with(fake, anthropic_chat_thinking="omit").chat_turn("", [{"role": "user", "content": "x"}])
    assert "thinking" not in fake.calls[0]


async def test_refusal_and_truncation():
    with pytest.raises(AIRefused):
        await provider_with(FakeMessages(response("", "refusal"))).chat_turn("", [{"role": "user", "content": "x"}])
    with pytest.raises(AIBadOutput):
        await provider_with(FakeMessages(response('{"reply": "cut', "max_tokens"))).chat_turn(
            "", [{"role": "user", "content": "x"}])


async def test_garbled_json():
    with pytest.raises(AIBadOutput):
        await provider_with(FakeMessages(response("not json"))).chat_turn("", [{"role": "user", "content": "x"}])
    with pytest.raises(AIBadOutput):
        await provider_with(FakeMessages(response('{"reply": "", "language": "en", "memory_updates": []}'))).chat_turn(
            "", [{"role": "user", "content": "x"}])


def _status_error(cls, code):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message="err", response=httpx.Response(code, request=req), body=None)


async def test_error_mapping():
    rate = _status_error(anthropic.RateLimitError, 429)
    with pytest.raises(AIRateLimited):
        await provider_with(FakeMessages(error=rate)).chat_turn("", [{"role": "user", "content": "x"}])
    overloaded = _status_error(anthropic.APIStatusError, 529)
    with pytest.raises(AIRateLimited):
        await provider_with(FakeMessages(error=overloaded)).chat_turn("", [{"role": "user", "content": "x"}])
    bad = _status_error(anthropic.BadRequestError, 400)
    with pytest.raises(AIError):
        await provider_with(FakeMessages(error=bad)).chat_turn("", [{"role": "user", "content": "x"}])


async def test_summary_uses_haiku():
    fake = FakeMessages(response("- Has an interview Friday"))
    result = await provider_with(fake).summarize(None, "User: interview friday")
    assert fake.calls[0]["model"] == "claude-haiku-5-5"
    assert result.text == "- Has an interview Friday"


async def test_compose_request_and_flags():
    body = json.dumps({"message": "yo free this weekend? 🍜", "language": "en", "flags": [], "note": ""})
    fake = FakeMessages(response(body))
    draft = await provider_with(fake).compose_message("<friend>…</friend>", "ask if he's free")
    call = fake.calls[0]
    assert call["output_config"]["format"]["schema"]["required"] == ["message", "language", "flags", "note"]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "<instruction>" in call["messages"][0]["content"]
    assert draft.text == "yo free this weekend? 🍜" and draft.flags == []


async def test_chat_schema_includes_schedule_requests():
    fake = FakeMessages(response(json.dumps({
        "reply": "On it!", "language": "en", "memory_updates": [],
        "schedule_requests": [{"friend_name": "Kenji", "local_datetime": "2026-11-12T20:00", "recurrence": "once",
                               "instruction": "ask if free", "send_automatically": True}],
    })))
    turn = await provider_with(fake).chat_turn("", [{"role": "user", "content": "x"}])
    assert turn.schedule_requests[0]["friend_name"] == "Kenji"
    assert "schedule_requests" in fake.calls[0]["output_config"]["format"]["schema"]["required"]
