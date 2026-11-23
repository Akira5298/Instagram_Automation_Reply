"""What a friend sent, in words the model (and you) can read, plus photo fetching.

Webhook attachment types (Meta docs): image (also gifs/stickers), video, audio,
file, ig_post (shared post: url + title = caption), ig_reel / reel (url +
title + video id), share (legacy post share, removed in 2026), story_mention,
story / ig_story, fallback.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("echo.media")

MAX_IMAGE_BYTES = 5_000_000
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_LABELS = {
    "image": "photo", "video": "video", "audio": "voice message", "file": "file",
    "story_mention": "mentioned you in their story", "story": "story", "ig_story": "story",
}


def _clip(text: str | None, limit: int = 220) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalise_attachments(raw: list[dict] | None) -> list[dict]:
    """Keep type, https url and title; drop the legacy `share` when `ig_post` is present."""
    items = []
    for a in raw or []:
        payload = a.get("payload") or {}
        url = payload.get("url") if str(payload.get("url") or "").startswith("https://") else None
        items.append({"type": str(a.get("type") or "attachment"), "url": url,
                      "title": _clip(payload.get("title"), 500) or None})
    if any(i["type"] == "ig_post" for i in items):
        items = [i for i in items if i["type"] != "share"]
    return items


def describe(text: str | None, attachments: list[dict], reply_to_story: bool = False) -> str:
    """'[Reel: “caption”]', '[photo]', 'nice! [Post: “caption”]' …"""
    parts: list[str] = []
    for a in attachments:
        kind = a["type"]
        if kind in ("ig_reel", "reel"):
            parts.append(f"[Reel: “{_clip(a['title'])}”]" if a.get("title") else "[Reel]")
        elif kind in ("ig_post", "post", "share"):
            parts.append(f"[Post: “{_clip(a['title'])}”]" if a.get("title") else "[Post]")
        else:
            parts.append(f"[{_LABELS.get(kind, 'attachment')}]")
    body = " ".join(p for p in [text or "", *parts] if p).strip()
    if reply_to_story:
        body = f"(replying to your story) {body}".strip()
    return body or "[message]"


def image_urls(attachments: list[dict]) -> list[str]:
    """Attachments worth showing the model as pictures (photos, and post previews)."""
    return [a["url"] for a in attachments if a.get("url") and a["type"] in ("image", "ig_post", "post", "share")]


@dataclass(frozen=True)
class FetchedImage:
    media_type: str
    data_b64: str


# Webhook media links point at Meta's CDNs; never fetch anything else (no SSRF via a crafted URL).
MEDIA_HOST_SUFFIXES = (".fbcdn.net", ".cdninstagram.com", ".fbsbx.com", ".facebook.com", ".instagram.com")


def _allowed(url: str) -> bool:
    try:
        parsed = httpx.URL(url)
    except Exception:  # noqa: BLE001
        return False
    host = (parsed.host or "").lower()
    return parsed.scheme == "https" and any(host.endswith(sfx) or host == sfx[1:] for sfx in MEDIA_HOST_SUFFIXES)


class ImageFetcher:
    """Downloads a few small images so Claude can look at them. Failures are
    fine: ECHO then works from the caption and the conversation."""

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(10.0), follow_redirects=False)

    async def _get(self, url: str) -> httpx.Response | None:
        """GET that only ever talks to Meta's media hosts, following at most 3 redirects."""
        for _ in range(4):
            if not _allowed(url):
                return None
            response = await self.http.get(url)
            if response.status_code in (301, 302, 303, 307, 308) and "location" in response.headers:
                url = str(response.url.join(response.headers["location"]))
                continue
            return response
        return None

    async def fetch(self, urls: list[str], limit: int = 2) -> list[FetchedImage]:
        images: list[FetchedImage] = []
        for url in urls[:limit]:
            try:
                response = await self._get(url)
                if response is None:
                    continue
                kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if response.status_code != 200 or kind not in IMAGE_TYPES or len(response.content) > MAX_IMAGE_BYTES:
                    continue
                images.append(FetchedImage(kind, base64.b64encode(response.content).decode()))
            except httpx.HTTPError as exc:
                log.info("image fetch failed", extra={"fields": {"error": type(exc).__name__}})
        return images
