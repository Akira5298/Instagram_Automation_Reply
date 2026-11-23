"""Context for drafting a DM to a friend in the user's voice."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.ai.context import _escape, format_trait

STYLE_HINTS = {
    "close_friend": "casual, funny, informal, short, natural emoji; teasing and inside jokes are fine",
    "partner": "affectionate and warm, playful, pet names if the user uses them, hearts/emoji natural",
    "family": "warm and caring, respectful, simple; check-ins and small updates",
    "classmate": "friendly, clear and concise, moderately casual",
    "professional": "polite, more formal, no unnecessary slang",
    "custom": "follow the user's profile",
}


def build_compose_context(
    *,
    traits: list[dict],
    examples: list[dict],
    friend: dict,
    thread: list[dict],
    now: datetime,
    timezone: str,
    friend_style: dict | None = None,
    memories: list[str] | None = None,
    mark_unanswered: bool = False,
    user_context: dict | None = None,
    reply_length: str | None = None,
) -> str:
    local = now.astimezone(ZoneInfo(timezone))
    style = friend.get("style") or "custom"
    lines = [
        f"<now>{local:%A %Y-%m-%d %H:%M} ({timezone})</now>",
        "<profile>\n" + ("\n".join(format_trait(t) for t in traits) or "(nothing learned yet)") + "\n</profile>",
    ]
    if examples:
        lines.append("<writing_examples>\n" + "\n".join(f"- {_escape(e['text'])}" for e in examples)
                     + "\n</writing_examples>")
    lines.append(
        "<friend>\n"
        f"name: {_escape(friend['display_name'])}\n"
        f"relationship preset={style} ({STYLE_HINTS.get(style, '')})\n"
        f"preferred language: {friend.get('language') or 'not set'}\n"
        "</friend>"
    )
    if friend_style:
        lines.append("<friend_style>\n" + render_friend_style(friend_style) + "\n</friend_style>")
    if user_context:
        lines.append("<user_context>\n" + render_user_context(user_context, timezone, now) + "\n</user_context>")
    if reply_length:
        lines.append(f"<reply_length>{reply_length}</reply_length>")
    if memories:
        lines.append("<friend_memories>\n" + "\n".join(f"- {_escape(m)}" for m in memories)
                     + "\n</friend_memories>")
    if thread:
        rendered = []
        last_outbound = max((i for i, m in enumerate(thread) if m["direction"] != "inbound"), default=-1)
        for i, m in enumerate(thread):
            if m["direction"] == "inbound":
                who = "Friend [unanswered]" if mark_unanswered and i > last_outbound else "Friend"
            else:
                who = "User"
            rendered.append(f"{who}: {_escape(str(m['content']))}")
        lines.append("<recent_thread>\n" + "\n".join(rendered) + "\n</recent_thread>")
    else:
        lines.append("<recent_thread>(no messages yet)</recent_thread>")
    return "\n\n".join(lines)


_LEVELS = ("none", "rare", "some", "frequent", "lots")
_FORMALITY = ("very casual", "casual", "neutral", "polite", "formal")


def render_friend_style(style: dict) -> str:
    """Per-friend style: what ECHO learned from your chats, then your own rules (which win)."""
    out: list[str] = []
    learned = style.get("learned") or {}
    if learned:
        def add(label: str, value) -> None:
            if value not in (None, "", []):
                out.append(f"learned {label}: {_escape(str(value))}")
        add("tone", learned.get("tone"))
        if learned.get("formality") is not None:
            out.append(f"learned formality: {_FORMALITY[max(0, min(4, int(learned['formality'])))]}")
        if learned.get("emoji_level") is not None:
            out.append(f"learned emoji: {_LEVELS[max(0, min(4, int(learned['emoji_level'])))]}")
        add("typical length", learned.get("typical_length"))
        add("favourite emoji", " ".join(learned.get("favourite_emoji") or []))
        add("phrases the user uses with them", ", ".join(learned.get("phrases") or []))
        add("humour", learned.get("humor"))
        add("language", learned.get("language"))
        add("notes", learned.get("notes"))
    rules: list[str] = []
    if style.get("tone"):
        rules.append(f"tone: {_escape(str(style['tone']))}")
    if style.get("formality") is not None and style.get("formality") != 2:
        rules.append(f"formality: {_FORMALITY[max(0, min(4, int(style['formality'])))]}")
    if style.get("emoji_level") is not None and style.get("emoji_level") != 2:
        rules.append(f"emoji: {_LEVELS[max(0, min(4, int(style['emoji_level'])))]}")
    if style.get("max_sentences"):
        rules.append(f"max sentences: {int(style['max_sentences'])}")
    if style.get("slang_allowed") is False:
        rules.append("no slang")
    avoid = (style.get("boundaries") or {}).get("avoid_topics") or []
    if avoid:
        rules.append("never bring up: " + ", ".join(_escape(str(t)) for t in avoid))
    if style.get("custom_instructions"):
        rules.append(f"instructions from the user: {_escape(str(style['custom_instructions']))}")
    out += [f"rule (from the user, wins): {line}" for line in rules]
    return "\n".join(out) or "(no extra rules)"


def render_user_context(ctx: dict, timezone: str, now: datetime) -> str:
    """Rough place + the next ~36 h of calendar, in the user's local time."""
    tz = ZoneInfo(timezone)
    lines: list[str] = []
    if ctx.get("place"):
        lines.append(f"location (city level, {ctx.get('location_age_min', 0)} min ago): {_escape(ctx['place'])}")
    events = ctx.get("events")
    if events is not None:
        today = now.astimezone(tz).date()
        if not events:
            lines.append("calendar: nothing scheduled in the next day and a half")
        else:
            lines.append("calendar (local time):")
            for e in events[:12]:
                start = datetime.fromisoformat(e["start"]).astimezone(tz)
                end = datetime.fromisoformat(e["end"]).astimezone(tz)
                day = "today" if start.date() == today else "tomorrow" if (start.date() - today).days == 1 \
                    else start.strftime("%a %d %b")
                when = f"{day} all day" if e.get("all_day") else f"{day} {start:%H:%M}–{end:%H:%M}"
                what = f": {_escape(e['title'])}" if e.get("title") else ""
                lines.append(f"- {when} {'busy' if e.get('busy', True) else 'free/tentative'}{what}")
    return "\n".join(lines)
