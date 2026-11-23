import hmac
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings

_bearer = HTTPBearer(auto_error=False)


def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> UUID:
    """Single-owner auth: a long random bearer token, kept in the iOS Keychain.

    Returns the owner's user id. Swap this dependency for a real auth provider (e.g. Sign in with Apple JWTs)
    verification if ECHO ever serves more than one person.
    """
    from app.core.hardening import client_ip, is_local, throttle

    expected = settings.echo_api_token.get_secret_value()
    if credentials is None or not hmac.compare_digest(credentials.credentials.encode(), expected.encode()):
        if not is_local(request):          # only internet-facing guesses are throttled
            throttle.failure(client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    throttle.success(client_ip(request))
    return settings.echo_owner_user_id
