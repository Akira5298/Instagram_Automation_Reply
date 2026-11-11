"""Builds the per-turn context sent to Claude: a compact, relevant slice of
memory instead of the full history."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def _escape(text: str) -> str:
    # Stored text is user-controlled; keep it from closing our XML-ish tags.
    return text.replace("<", "‹").replace(">", "›")


def format_trait(trait: dict) -> str:
    source = trait["source"]
    if source == "inferred":
        label = f"tentative {int(round(trait['confidence'] * 100))}%, seen {trait['evidence_count']}x"
    elif source == "user_edited":
        label = "confirmed by user"
    else:
        label = "user said"
    return f"- [{trait['category']}] {_escape(trait['value'])} ({label}) key={trait['key']}"


def build_context_block(
    *,
    traits: list[dict],
    rejected_keys: list[str],
    summary: str | None,
    examples: list[dict],
    now: datetime,
    timezone: str,
    friends: list[dict] | None = None,
) -> str:
    local = now.astimezone(ZoneInfo(timezone))
    parts = [
        f"<now>{local:%A %Y-%m-%d %H:%M} ({timezone})</now>",
        "<profile>\n" + ("\n".join(format_trait(t) for t in traits) or "(nothing learned yet)") + "\n</profile>",
    ]
    if rejected_keys:
        parts.append("<rejected_keys>" + ", ".join(rejected_keys) + "</rejected_keys>")
    if summary:
        parts.append(f"<conversation_summary>\n{_escape(summary)}\n</conversation_summary>")
    if examples:
        body = "\n".join(f"- {_escape(e['text'])}" for e in examples)
        parts.append(
            "<writing_examples note=\"Messages the user approved as typical of how they write\">\n"
            f"{body}\n</writing_examples>"
        )
    if friends:
        body = "\n".join(
            f"- {_escape(f['display_name'])}" + (f" (@{_escape(f['username'])})" if f.get("username") else "")
            + f" — {f.get('style') or 'custom'}"
            for f in friends
        )
        parts.append(f"<friends note=\"People the user can schedule messages to\">\n{body}\n</friends>")
    else:
        parts.append("<friends>(none connected yet)</friends>")
    return "\n\n".join(parts)


def build_messages(history: list[dict], current_text: str) -> list[dict]:
    """Turn stored rows into a valid Messages API list: must start with a user
    turn, alternate roles, and end with the current user message. Consecutive
    same-role rows (e.g. a user message whose reply failed) are merged."""
    turns: list[dict] = []
    for row in [*history, {"role": "user", "content": current_text}]:
        role = row["role"]
        content = str(row["content"]).strip()
        if not content:
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + content
        else:
            turns.append({"role": role, "content": content})
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns


def transcript(rows: list[dict]) -> str:
    return "\n".join(
        f"{'User' if r['role'] == 'user' else 'ECHO'}: {_escape(str(r['content']))}" for r in rows
    )
