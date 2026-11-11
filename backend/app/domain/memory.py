"""Rules for merging what the model noticed into the communication profile.

Pure functions — no I/O — so the rules are easy to test and reason about.

  * Stated facts are stored as facts (confidence 1.0).
  * Observations are tentative: they start at most at 0.6 and each repeat
    closes 25% of the gap to certainty, capped at 0.95. They never become facts.
  * A trait you edited in the dashboard is never overwritten by inference.
  * A trait you deleted (status 'rejected') is never re-learned automatically.
  * "remove" (e.g. "I'm not into anime anymore") archives the trait; it can be
    learned again later if it becomes true again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

CATEGORIES: tuple[str, ...] = (
    "languages",
    "vocabulary",
    "tone",
    "slang",
    "emoji",
    "humour",
    "sentence_style",
    "social",
    "interests",
    "friend_preferences",
)

MAX_UPDATES_PER_TURN = 6
MAX_VALUE_LEN = 160
MAX_KEY_LEN = 80
NEW_OBSERVATION_CAP = 0.6
OBSERVATION_CEILING = 0.95

_KEY_CLEAN = re.compile(r"[^a-z0-9_.]+")

# Deterministic PII guard: never store contact details or card/ID-like numbers,
# whatever the model proposes.
_PII_PATTERNS = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                 # e-mail
    re.compile(r"(?:\+?\d[\s-]?){8,}"),                       # phone / card / ID numbers
    re.compile(r"\b(?:password|passcode|pin|otp)\b", re.I),
)


@dataclass(frozen=True)
class ProposedUpdate:
    op: Literal["set", "remove"]
    category: str
    key: str
    value: str
    kind: Literal["stated_fact", "observation"]
    confidence: float


@dataclass(frozen=True)
class TraitState:
    category: str
    key: str
    value: str
    source: Literal["user_stated", "inferred", "user_edited"]
    confidence: float
    evidence_count: int
    status: Literal["active", "confirmed", "rejected", "archived"]


def contains_pii(text: str) -> bool:
    return any(p.search(text) for p in _PII_PATTERNS)


def sanitize(raw: list[dict]) -> list[ProposedUpdate]:
    """Validate and normalise model output. Anything malformed is dropped."""
    clean: list[ProposedUpdate] = []
    seen: set[str] = set()
    for item in raw[: MAX_UPDATES_PER_TURN * 2]:
        try:
            op = str(item.get("op", "set")).strip().lower()
            category = str(item["category"]).strip().lower()
            kind = str(item.get("kind", "observation")).strip().lower()
            value = " ".join(str(item.get("value", "")).split())[:MAX_VALUE_LEN]
            key = _KEY_CLEAN.sub("_", str(item["key"]).strip().lower()).strip("_.")[:MAX_KEY_LEN]
            confidence = float(item.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError):
            continue
        if op not in ("set", "remove") or category not in CATEGORIES:
            continue
        if kind not in ("stated_fact", "observation"):
            continue
        if not key or key in seen:
            continue
        if op == "set" and (not value or contains_pii(value)):
            continue
        if not key.startswith(f"{category}."):
            key = f"{category}.{key}"[:MAX_KEY_LEN]
        seen.add(key)
        clean.append(
            ProposedUpdate(
                op=op,  # type: ignore[arg-type]
                category=category,
                key=key,
                value=value,
                kind=kind,  # type: ignore[arg-type]
                confidence=min(1.0, max(0.0, confidence)),
            )
        )
        if len(clean) >= MAX_UPDATES_PER_TURN:
            break
    return clean


def merge(existing: TraitState | None, update: ProposedUpdate) -> TraitState | None:
    """Return the new state for this trait, or None to leave it untouched."""
    if existing is not None and existing.status == "rejected":
        return None

    if update.op == "remove":
        if existing is None or existing.status == "archived":
            return None
        return replace(existing, status="archived")

    stated = update.kind == "stated_fact"

    if existing is None or existing.status == "archived":
        return TraitState(
            category=update.category,
            key=update.key,
            value=update.value,
            source="user_stated" if stated else "inferred",
            confidence=1.0 if stated else min(update.confidence, NEW_OBSERVATION_CAP),
            evidence_count=1 if existing is None else existing.evidence_count + 1,
            status="active",
        )

    bumped = existing.evidence_count + 1

    if existing.source == "user_edited":
        # Your correction wins; just note that the evidence agreed.
        return replace(existing, evidence_count=bumped)

    if stated:
        return replace(existing, value=update.value, source="user_stated", confidence=1.0, evidence_count=bumped)

    if existing.source == "inferred":
        c = existing.confidence
        return replace(
            existing,
            value=update.value,
            confidence=round(min(OBSERVATION_CEILING, c + (1 - c) * 0.25), 3),
            evidence_count=bumped,
        )

    # existing is user_stated; an observation doesn't change a stated fact.
    return replace(existing, evidence_count=bumped)
