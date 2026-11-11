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
    if thread:
        rendered = []
        for m in thread:
            who = "Friend" if m["direction"] == "inbound" else "User"
            rendered.append(f"{who}: {_escape(str(m['content']))}")
        lines.append("<recent_thread>\n" + "\n".join(rendered) + "\n</recent_thread>")
    else:
        lines.append("<recent_thread>(no messages yet)</recent_thread>")
    return "\n\n".join(lines)
