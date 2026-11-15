"""The backend's public https address (your Cloudflare Tunnel).

Resolution order:
  1. ECHO_PUBLIC_BASE_URL — set this when you use a *named* tunnel with a
     fixed hostname (e.g. https://echo.example.com).
  2. The file written by scripts/start.sh when it starts a *quick* tunnel
     (backend/.public_url by default). The random trycloudflare.com address
     changes on every restart, so it's read fresh on every request — no
     server restart needed.
  3. None: Instagram login and Meta callbacks can't work without https.
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_URL_FILE = BACKEND_DIR / ".public_url"


def url_file(settings: Settings) -> Path:
    if settings.echo_public_url_file:
        path = Path(settings.echo_public_url_file).expanduser()
        return path if path.is_absolute() else BACKEND_DIR / path
    return DEFAULT_URL_FILE


def public_base_url(settings: Settings) -> str | None:
    if settings.echo_public_base_url:
        return settings.echo_public_base_url.strip().rstrip("/") or None
    try:
        text = url_file(settings).read_text().strip().rstrip("/")
    except OSError:
        return None
    return text if text.startswith("https://") else None


def oauth_redirect_uri(settings: Settings) -> str | None:
    if settings.meta_oauth_redirect_uri:
        return settings.meta_oauth_redirect_uri
    base = public_base_url(settings)
    return f"{base}/v1/instagram/oauth/callback" if base else None


def meta_dashboard_urls(base: str, login_mode: str = "unified") -> dict[str, str]:
    """What to paste into the Meta App Dashboard for this address."""
    redirect_label = ("Valid OAuth Redirect URI" if login_mode == "unified" else "OAuth redirect URI")
    return {
        redirect_label: f"{base}/v1/instagram/oauth/callback",
        "Webhook callback URL": f"{base}/webhooks/meta",
        "Deauthorize callback URL": f"{base}/v1/instagram/deauthorize",
        "Data deletion request URL": f"{base}/v1/instagram/data-deletion",
        "Privacy policy URL": f"{base}/privacy",
    }
