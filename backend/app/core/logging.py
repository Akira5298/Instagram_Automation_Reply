import json
import logging
import re
import sys
from datetime import UTC, datetime

# Query parameters and headers that carry secrets. Meta's API puts some of
# them in URLs (debug_token, the OAuth code exchange), and HTTP client / access
# logs print URLs, so they're masked everywhere before anything is written.
_SECRET_PARAMS = re.compile(
    r"(?i)\b(access_token|input_token|client_secret|fb_exchange_token|appsecret_proof|code|state|"
    r"hub\.verify_token|signed_request)=([^&\s\"']+)"
)
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-|]{12,}")


def redact(text: str) -> str:
    text = _SECRET_PARAMS.sub(lambda m: f"{m.group(1)}=***", text)
    return _BEARER.sub(lambda m: f"{m.group(1)} ***", text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — never break logging
            return True
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = {k: redact(v) if isinstance(v, str) else v for k, v in fields.items()}
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line — easy to search with grep/jq (logs/echo.log)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs every request URL at INFO; Meta URLs can contain secrets.
    for noisy in ("httpx", "httpcore", "hpack", "h2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn's access log has its own handlers: mask query strings there too.
    for name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, RedactFilter) for f in logger.filters):
            logger.addFilter(RedactFilter())
