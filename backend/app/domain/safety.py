"""Deterministic safety gate for automated messages.

The model also flags risky drafts, but this keyword pass runs regardless, so a
message about money or a commitment never goes out unattended just because the
model missed it. False positives only cost a notification ("needs your
attention"); false negatives could send something you'd regret.
"""

from __future__ import annotations

import re

FLAGS = ("money", "commitment", "sensitive", "unknown_info", "emotional")

META_TEXT_LIMIT_BYTES = 1000  # Instagram text messages: max 1000 bytes UTF-8

_PATTERNS: dict[str, re.Pattern[str]] = {
    "money": re.compile(
        r"(\$|€|£|¥|\bRM\s?\d|\b\d+\s?(?:usd|myr|eur|yen|ringgit)\b|\b(?:pay|paid|payment|lend|loan|owe|"
        r"borrow|money|cash|invoice|refund|deposit|transfer|venmo|paypal|duitnow|bank)\w*"
        r"|お金|払|貸|借|钱|付款|借钱|soldi|pagare|prestito)",
        re.IGNORECASE,
    ),
    "commitment": re.compile(
        r"\b(?:i promise|promise you|i(?:'ll| will) definitely|i agree|it's a deal|confirm(?:ed)?\b|book(?:ed|ing)\b|"
        r"reserv\w+|sign(?:ed)? up|commit\w*|guarantee\w*|count me in|i(?:'ll| will) be there)"
        r"|約束|承诺|保证|prometto|confermo",
        re.IGNORECASE,
    ),
    "sensitive": re.compile(
        r"\b(?:password|passcode|otp|pin code|ic number|passport|address is|diagnos\w+|pregnan\w+|"
        r"hospital|medication|lawyer|police)\b",
        re.IGNORECASE,
    ),
    "emotional": re.compile(
        r"\b(?:passed away|funeral|died|break ?up|broke up|divorce|depress\w*|suicid\w*|self[- ]harm|"
        r"so sorry for your loss|condolence\w*)\b",
        re.IGNORECASE,
    ),
}


def keyword_flags(*texts: str) -> set[str]:
    joined = "\n".join(t for t in texts if t)
    return {flag for flag, pattern in _PATTERNS.items() if pattern.search(joined)}


def normalise_flags(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {str(f).strip().lower() for f in raw if str(f).strip().lower() in FLAGS}


def fits_instagram(text: str) -> bool:
    return 0 < len(text.encode("utf-8")) <= META_TEXT_LIMIT_BYTES


REASON_TEXT = {
    "money": "mentions money or payments",
    "commitment": "would make a promise or commitment for you",
    "sensitive": "touches on sensitive information",
    "unknown_info": "needs information ECHO doesn't have",
    "emotional": "is emotionally serious",
    "too_long": "is longer than Instagram allows",
    "automation_off": "is set to draft-only",
}
