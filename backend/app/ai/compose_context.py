"""Context for drafting a DM to a friend in the user's voice."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.ai.context import _escape, format_trait

STYLE_HINTS = {
    "close_friend": "casual, funny, informal, short, natural emoji",
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
    """Per-friend rules from friend_style_profiles, as plain lines."""
    out: list[str] = []
    if style.get("tone"):
        out.append(f"tone: {_escape(str(style['tone']))}")
    if style.get("formality") is not None:
        out.append(f"formality: {_FORMALITY[max(0, min(4, int(style['formality'])))]}")
    if style.get("emoji_level") is not None:
        out.append(f"emoji: {_LEVELS[max(0, min(4, int(style['emoji_level'])))]}")
    if style.get("max_sentences"):
        out.append(f"max sentences: {int(style['max_sentences'])}")
    if style.get("slang_allowed") is False:
        out.append("no slang")
    avoid = (style.get("boundaries") or {}).get("avoid_topics") or []
    if avoid:
        out.append("never bring up: " + ", ".join(_escape(str(t)) for t in avoid))
    if style.get("custom_instructions"):
        out.append(f"instructions from the user: {_escape(str(style['custom_instructions']))}")
    return "\n".join(out) or "(no extra rules)"
