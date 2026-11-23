"""MOCK AI provider — deterministic, no network, no API key. Used for local
development (ECHO_AI_PROVIDER=mock) and tests. Same interface as Claude."""

from __future__ import annotations

import re

from app.ai.provider import AssistantTurn, ComposedMessage, LearnedStyle, MessageIdea, ReplyDraft, SummaryResult
from app.domain.safety import keyword_flags

_SLANG = {"lol", "lmao", "ngl", "tbh", "idk", "omg", "bruh", "fr", "btw", "rn"}
_ITALIAN = {"ciao", "grazie", "sono", "come", "stai", "anche", "perché", "molto", "oggi", "piace"}
_INTEREST = re.compile(r"\bi\s+(?:really\s+)?(?:love|like|enjoy|am into|'m into)\s+([^.,!?\n]{2,40})", re.IGNORECASE)
# "send kenji a message at 8pm tomorrow asking ..." (mock scheduling)
_SCHEDULE = re.compile(
    r"\b(?:send|message|text)\s+(?P<name>[A-Za-z]+)\b.*?\bat\s+(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?"
    r"\s*(?P<day>today|tonight|tomorrow)?(?:.*?\b(?:asking|to say|saying|about)\s+(?P<what>[^.!?]+))?",
    re.IGNORECASE,
)
_NOT_ANYMORE = re.compile(r"\bi(?:'m| am)\s+not\s+into\s+([^.,!?\n]{2,40})\s+anymore", re.IGNORECASE)


def detect_language(text: str) -> str:
    if re.search(r"[぀-ヿ]", text):
        return "ja"
    if re.search(r"[一-鿿]", text):
        return "zh-Hans"
    words = set(re.findall(r"[a-zà-ù]+", text.lower()))
    if words & _ITALIAN:
        return "it"
    return "en"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40]


_REPLIES = {
    "en": "Got it! How would you usually say that to a close friend?",
    "ja": "なるほど！仲のいい友達には普段どんな感じで言う？",
    "zh-Hans": "明白了！你平时跟好朋友会怎么说这句话？",
    "it": "Capito! Come lo diresti di solito a un amico stretto?",
}


class MockAIProvider:
    async def chat_turn(self, context_block: str, messages: list[dict]) -> AssistantTurn:
        user_text = str(messages[-1]["content"]) if messages else ""
        lang = detect_language(user_text)
        updates: list[dict] = []
        words = re.findall(r"[\w']+", user_text.lower())

        if lang != "en":
            updates.append({"op": "set", "category": "languages", "key": f"languages.{lang.lower()}",
                            "value": f"Writes in {lang}", "kind": "observation", "confidence": 0.5})
        for term in sorted(_SLANG & set(words)):
            updates.append({"op": "set", "category": "slang", "key": f"slang.{term}",
                            "value": f"Uses '{term}'", "kind": "observation", "confidence": 0.45})
        if 0 < len(words) <= 6:
            updates.append({"op": "set", "category": "sentence_style", "key": "sentence_style.short_messages",
                            "value": "Tends to write short messages", "kind": "observation", "confidence": 0.35})
        for match in _NOT_ANYMORE.finditer(user_text):
            updates.append({"op": "remove", "category": "interests", "key": f"interests.{_slug(match.group(1))}",
                            "value": "", "kind": "stated_fact", "confidence": 1.0})
        if not updates or not any(u["op"] == "remove" for u in updates):
            for match in _INTEREST.finditer(user_text):
                interest = match.group(1).strip()
                updates.append({"op": "set", "category": "interests", "key": f"interests.{_slug(interest)}",
                                "value": f"Loves {interest}", "kind": "stated_fact", "confidence": 1.0})

        schedules = []
        match = _SCHEDULE.search(user_text)
        if match:
            schedules.append(_mock_schedule(match, context_block, user_text))
            return AssistantTurn(text="[mock] On it!", language=lang, memory_updates=[], schedule_requests=schedules)

        return AssistantTurn(text=f"[mock] {_REPLIES[lang]}", language=lang, memory_updates=updates)

    async def summarize(self, previous_summary: str | None, transcript: str) -> SummaryResult:
        lines = [line for line in transcript.splitlines() if line.startswith("User:")]
        bullets = "\n".join(f"- {line[5:].strip()[:80]}" for line in lines[-8:])
        merged = f"{previous_summary}\n{bullets}" if previous_summary else bullets
        return SummaryResult(text=merged.strip()[-2000:])

    async def draft_reply(self, context_block: str, images: list | None = None) -> ReplyDraft:
        return await _mock_draft_reply(context_block, images)

    async def suggest_messages(self, context_block: str, occasion: str | None, count: int) -> list[MessageIdea]:
        return await _mock_suggestions(context_block, occasion, count)

    async def learn_style(self, transcript_block: str) -> LearnedStyle:
        return await _mock_learn_style(transcript_block)

    async def compose_message(self, context_block: str, instruction: str) -> ComposedMessage:
        style = "close_friend"
        m = re.search(r"preset=(\w+)", context_block)
        if m:
            style = m.group(1)
        base = instruction.strip().rstrip(".")
        if style == "professional":
            text = f"Hello! I wanted to {base[0].lower() + base[1:]}." if base else "Hello!"
        elif style == "classmate":
            text = f"Hey! Quick one: {base.lower()}?"
        else:
            text = f"yo {base.lower()} 😄"
        flags = sorted(keyword_flags(instruction))
        return ComposedMessage(text=f"[mock] {text}", language="en", flags=flags,
                               note="mock flag" if flags else "")


_NO_REPLY = re.compile(r"^\s*(ok(ay)?|k|thanks?|thx|ty|haha+|lol|👍|❤️|🙏|😂|gn|good ?night)\s*[.!]*\s*$", re.IGNORECASE)


def _unanswered(context_block: str) -> list[str]:
    return [line.split("Friend [unanswered]:", 1)[1].strip()
            for line in context_block.splitlines() if "Friend [unanswered]:" in line]


async def _mock_draft_reply(context_block: str, images: list | None = None) -> ReplyDraft:
    pending = _unanswered(context_block)
    last = pending[-1] if pending else ""
    alts = ["[mock] hahaha", "[mock] wait fr? 😮"]
    if last and _NO_REPLY.match(last):
        return ReplyDraft(action="no_reply", text="", language="en", note="nothing to answer")
    if images:
        return ReplyDraft(action="reply", text="[mock] omg look at that 😍", language="en", alternatives=alts)
    if last.startswith("[Reel"):
        return ReplyDraft(action="reply", text="[mock] LMAOOO 😭 this is so you", language="en",
                          alternatives=["[mock] 😂😂😂", "[mock] nahh i'm sending this to everyone"])
    if last.startswith("[Post"):
        return ReplyDraft(action="react", text="", language="en", note="a heart says it all")
    if last.startswith("[voice message]"):
        return ReplyDraft(action="needs_user", text="[mock] will listen in a bit!", language="en",
                          alternatives=["[mock] can't listen rn, text me?"], note="can't hear audio")
    flags = sorted(keyword_flags(*pending))
    if "free" in last.lower() or "when" in last.lower():
        busy = "busy" in context_block.lower() and "<user_context>" in context_block
        text = "[mock] can't, busy till later 😭" if busy else "[mock] maybe! let me check"
        return ReplyDraft(action="needs_user", text=text, language="en", flags=flags or ["unknown_info"],
                          alternatives=["[mock] yes i'm free!", "[mock] can't sorry 😭", "[mock] what time?"],
                          note="asks about your plans")
    style = re.search(r"preset=(\w+)", context_block)
    prefix = "Hello! " if style and style.group(1) == "professional" else "haha "
    length = re.search(r"<reply_length>\s*(\w+)", context_block)
    body = last.lower()[:60] or "hey"
    if length and length.group(1) == "short":
        body = body.split()[0] if body.split() else "hey"
    return ReplyDraft(action="reply", text=f"[mock] {prefix}{body} 😄", language="en",
                      flags=flags, note="mock flag" if flags else "", alternatives=alts)


async def _mock_suggestions(context_block: str, occasion: str | None, count: int) -> list[MessageIdea]:
    m = re.search(r"preset=(\w+)", context_block)
    preset = m.group(1) if m else "custom"
    about = f" ({occasion})" if occasion else ""
    bank = {
        "partner": [("good morning babe ❤️", "sweet"), ("miss you already 🥺", "sweet"),
                    ("thinking of u, hope today's kind to you", "caring"), ("u + me + food later? 😋", "playful")],
        "close_friend": [("bro did u survive yesterday 💀", "funny"), ("i saw a cat that looks exactly like u", "funny"),
                         ("we're overdue for a hangout fr", "check-in"), ("random but ur the goat", "hype")],
        "family": [("hi! how are you doing today?", "check-in"), ("thinking of you ❤️ eat well ok", "caring"),
                   ("call me when you're free?", "check-in"), ("miss you guys!", "warm")],
    }.get(preset, [("hey! how's it going?", "check-in"), ("hope your week is going well!", "friendly"),
                   ("long time no talk!", "casual"), ("thinking of you, hope all's good", "warm")])
    return [MessageIdea(f"[mock] {text}{about}", vibe) for text, vibe in bank][:count]


async def _mock_learn_style(transcript: str) -> LearnedStyle:
    mine = [line[3:].strip() for line in transcript.splitlines() if line.startswith("Me:")]
    emoji = [ch for line in mine for ch in line if ord(ch) > 0x2600]
    avg = sum(len(m) for m in mine) / max(1, len(mine))
    style = {
        "tone": "playful" if any(w in " ".join(mine).lower() for w in ("haha", "lol", "lmao")) else "warm",
        "formality": 1, "emoji_level": min(4, len(emoji) // max(1, len(mine)) + (1 if emoji else 0)),
        "typical_length": "very short" if avg < 15 else "short" if avg < 60 else "medium",
        "favourite_emoji": sorted(set(emoji))[:5],
        "phrases": sorted({w for m in mine for w in m.lower().split() if w in _SLANG})[:8],
        "humor": "teasing" if "haha" in " ".join(mine).lower() else "",
        "language": "en", "notes": f"learned from {len(mine)} messages",
    }
    updates = [{"op": "set", "category": "sentence_style", "key": "sentence_style.length",
                "value": f"usually {style['typical_length']} messages", "kind": "observation"}]
    return LearnedStyle(friend_style=style, profile_updates=updates)


def _mock_schedule(match: re.Match, context_block: str, user_text: str) -> dict:
    from datetime import datetime, timedelta

    now_match = re.search(r"<now>\w+ (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})", context_block)
    today = datetime.strptime(now_match.group(1), "%Y-%m-%d") if now_match else datetime.now()
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if (match.group("ampm") or "").lower() == "pm" and hour < 12:
        hour += 12
    day = (match.group("day") or "").lower()
    date = today + timedelta(days=1) if day == "tomorrow" else today
    recurrence = "daily" if re.search(r"\bevery ?day\b|\bdaily\b", user_text, re.IGNORECASE) else "once"
    return {
        "friend_name": match.group("name"),
        "local_datetime": f"{date:%Y-%m-%d}T{hour:02d}:{minute:02d}",
        "recurrence": recurrence,
        "instruction": (match.group("what") or "say hi").strip(),
        "send_automatically": not re.search(r"\b(?:review|check with me|ask me first)\b", user_text, re.IGNORECASE),
    }
