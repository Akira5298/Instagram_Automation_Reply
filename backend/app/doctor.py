"""Pre-flight check for running ECHO on your Mac.

    python -m app.doctor

Checks your .env, PostgreSQL + migrations, the Claude API key (free models
call, no tokens spent), the Cloudflare Tunnel (reaches /health and passes a
webhook verification round trip through the public URL) and the Instagram
settings. Prints the exact URLs to paste into the Meta App Dashboard.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

import httpx

from app.core.config import Settings, get_settings
from app.core.public_url import meta_dashboard_urls, public_base_url, url_file

OK, WARN, FAIL = "✅", "⚠️ ", "❌"
results: list[str] = []


def report(symbol: str, title: str, detail: str = "") -> None:
    line = f"{symbol} {title}" + (f" — {detail}" if detail else "")
    results.append(symbol)
    print(line)


def check(title: str, fn: Callable[[], tuple[str, str]]) -> None:
    try:
        symbol, detail = fn()
    except Exception as exc:  # report, never crash
        symbol, detail = FAIL, f"{type(exc).__name__}: {exc}"
    report(symbol, title, detail)


def python_version() -> tuple[str, str]:
    v = sys.version_info
    return (OK if v >= (3, 11) else FAIL), f"{v.major}.{v.minor}.{v.micro}"


def api_token(s: Settings) -> tuple[str, str]:
    token = s.echo_api_token.get_secret_value()
    if token in ("change-me", "dev-token") or len(token) < 24:
        return FAIL, "set a long random ECHO_API_TOKEN in backend/.env (scripts/setup_mac.sh does this)"
    return OK, "set"


def env_file() -> tuple[str, str]:
    import stat
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.exists():
        return WARN, "backend/.env not found (settings come from the environment)"
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return FAIL, f"backend/.env is readable by other users ({oct(mode & 0o777)}). Run: chmod 600 backend/.env"
    return OK, "only you can read backend/.env"


def push(s: Settings) -> tuple[str, str]:
    from app.services.push import PushNotConfigured, apns_configured, load_signer

    if not apns_configured(s):
        return WARN, "not set up (optional) — see docs/TESTFLIGHT.md §3 for APNS_* settings"
    try:
        signer = load_signer(s)
        signer.token()
    except (PushNotConfigured, ValueError) as exc:
        return FAIL, f"APNs key problem: {exc}"
    return OK, f"APNs key {s.apns_key_id} loads and signs; topic {s.apns_bundle_id}"


def database(s: Settings) -> tuple[str, str]:
    import psycopg

    from app.migrate import pending

    if s.database_url is None:
        return FAIL, "DATABASE_URL missing — e.g. postgresql://localhost:5432/echo"
    url = s.database_url.get_secret_value()
    with psycopg.connect(url, connect_timeout=5) as conn:
        version = conn.execute("show server_version").fetchone()[0]
    todo = pending(url)
    if todo:
        return WARN, f"PostgreSQL {version}; pending migrations: {', '.join(todo)} → run: python -m app.migrate"
    return OK, f"PostgreSQL {version}, migrations up to date"


def claude(s: Settings) -> tuple[str, str]:
    if s.echo_ai_provider == "mock":
        return WARN, "ECHO_AI_PROVIDER=mock (no real Claude replies)"
    if s.anthropic_api_key is None:
        return FAIL, "ANTHROPIC_API_KEY missing"
    response = httpx.get(
        "https://api.anthropic.com/v1/models",
        params={"limit": "1000"},
        headers={"x-api-key": s.anthropic_api_key.get_secret_value(), "anthropic-version": "2023-06-01"},
        timeout=15,
    )
    if response.status_code == 401:
        return FAIL, "the API key was rejected"
    response.raise_for_status()
    ids = {m.get("id") for m in response.json().get("data", [])}
    missing = [m for m in (s.anthropic_chat_model, s.anthropic_summary_model) if ids and m not in ids]
    if missing:
        return WARN, f"key works, but these models aren't listed for your account: {', '.join(missing)}"
    return OK, f"key works; using {s.anthropic_chat_model}"


def tunnel(s: Settings) -> tuple[str, str]:
    base = public_base_url(s)
    if not base:
        return FAIL, (f"no public https URL. Run scripts/start.sh (quick tunnel writes {url_file(s)}) "
                      "or set ECHO_PUBLIC_BASE_URL for a named tunnel")
    r = httpx.get(f"{base}/health", timeout=15)
    if r.status_code != 200:
        return FAIL, f"{base}/health answered {r.status_code} — is the server running behind the tunnel?"
    return OK, f"{base} reaches this server (version {r.json().get('version')})"


def webhook_handshake(s: Settings) -> tuple[str, str]:
    base = public_base_url(s)
    if not base:
        return WARN, "skipped (no public URL)"
    if s.meta_webhook_verify_token is None:
        return FAIL, "META_WEBHOOK_VERIFY_TOKEN missing"
    r = httpx.get(f"{base}/webhooks/meta", timeout=15, params={
        "hub.mode": "subscribe", "hub.verify_token": s.meta_webhook_verify_token.get_secret_value(),
        "hub.challenge": "echo-doctor"})
    if r.status_code == 200 and r.text == "echo-doctor":
        return OK, "Meta's verification request will succeed"
    return FAIL, f"verification failed ({r.status_code}); the verify token must match the dashboard"


def instagram(s: Settings) -> tuple[str, str]:
    from app.core.crypto import TokenCipher

    missing = s.instagram_missing_settings()
    if missing:
        return WARN, (f"not configured yet (META_LOGIN_MODE={s.meta_login_mode}): {', '.join(missing)} "
                      "— see docs/META_SETUP.md")
    TokenCipher(s.echo_token_encryption_key.get_secret_value())  # validates the key
    detail = f"credentials set, login={s.meta_login_mode}"
    if s.meta_login_mode == "unified":
        # An app access token (id|secret) can read the app node: proves ID + secret belong together.
        r = httpx.get(f"https://graph.facebook.com/{s.meta_graph_version}/{s.meta_app_id}",
                      params={"fields": "id,name",
                              "access_token": f"{s.meta_app_id}|{s.meta_app_secret.get_secret_value()}"},
                      timeout=15)
        if r.status_code != 200:
            message = (r.json().get("error") or {}).get("message", r.text[:120]) if r.content else r.status_code
            return FAIL, (f"Meta rejected META_APP_ID/META_APP_SECRET ({message}). Use the App ID and App secret "
                          "from App settings → Basic — not the Instagram app ID/secret.")
        detail = f"Meta app \"{r.json().get('name')}\" ({s.meta_app_id}), login=unified, config {s.meta_login_config_id}"
    if s.echo_messaging_provider != "instagram":
        return WARN, detail + "; ECHO_MESSAGING_PROVIDER=mock (no real DMs are sent)"
    return OK, detail + ", provider=instagram"


def main() -> None:
    s = get_settings()
    print("ECHO doctor\n")
    check("Python", python_version)
    check("API token", lambda: api_token(s))
    check("Secrets file", env_file)
    check("Database", lambda: database(s))
    check("Claude API", lambda: claude(s))
    check("Public tunnel", lambda: tunnel(s))
    check("Webhook verification", lambda: webhook_handshake(s))
    check("Instagram settings", lambda: instagram(s))
    check("Push notifications", lambda: push(s))

    base = public_base_url(s)
    if base:
        print("\nPaste these into the Meta App Dashboard (docs/META_SETUP.md §3–4):")
        for label, url in meta_dashboard_urls(base, s.meta_login_mode).items():
            print(f"  {label:<28} {url}")
        if "trycloudflare.com" in base:
            print("\n  ⚠️  Quick-tunnel address: it changes every time the tunnel restarts, so these\n"
                  "     must be updated in the dashboard (and the iPhone app) after each restart.")
    print()
    sys.exit(1 if FAIL in results else 0)


if __name__ == "__main__":
    main()
