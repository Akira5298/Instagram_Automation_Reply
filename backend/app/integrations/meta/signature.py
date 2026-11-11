import hashlib
import hmac


def verify_x_hub_signature_256(raw_body: bytes, header_value: str | None, app_secret: str) -> bool:
    """Verify Meta's `X-Hub-Signature-256: sha256=<hex>` header.

    Must be computed over the *raw* request bytes exactly as received — never
    over re-serialised JSON. For Instagram API with Instagram Login, the
    secret is the Instagram app secret from the App Dashboard.
    """
    if not header_value or not app_secret:
        return False
    prefix = "sha256="
    if not header_value.startswith(prefix):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value[len(prefix):].lower())
