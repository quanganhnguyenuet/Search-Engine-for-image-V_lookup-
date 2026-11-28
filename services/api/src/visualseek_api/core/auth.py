"""Verifies the Supabase access token sent as `Authorization: Bearer`.

The signature is checked locally against the project's public keys (JWKS), so a request does
not call Supabase. A token therefore stays valid until it expires, even after sign-out.
"""

from dataclasses import dataclass
from functools import lru_cache
from threading import Event, Thread
from typing import Annotated

import jwt
import structlog
from fastapi import Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from visualseek_api.core.config import Settings, get_settings
from visualseek_api.core.errors import ApplicationError

# Asymmetric only: accepting HS256 would let anyone who has the public key forge a token.
ALGORITHMS = ["ES256", "RS256"]
AUDIENCE = "authenticated"
# Tolerated clock difference between this machine and Supabase.
LEEWAY_SECONDS = 30
ADMIN_ROLE = "admin"
# Supabase caches its JWKS endpoint for 10 minutes. An unknown key id still forces a refetch.
JWKS_LIFESPAN_SECONDS = 600
JWKS_TIMEOUT_SECONDS = 5
# Refetched before the cache expires, so no request waits for the download (150-500 ms measured).
JWKS_REFRESH_SECONDS = JWKS_LIFESPAN_SECONDS - 120
JWKS_RETRY_SECONDS = 30

_bearer = HTTPBearer(auto_error=False, description="Supabase access token")


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None
    # From the "user_role" claim that the Supabase Custom Access Token Hook adds
    # (supabase/migrations/0001_profiles_roles.sql). Anything other than "admin" is a plain user.
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == ADMIN_ROLE


class TokenVerifier:
    def __init__(self, supabase_url: str) -> None:
        self.issuer = f"{supabase_url.rstrip('/')}/auth/v1"
        self._keys = jwt.PyJWKClient(
            f"{self.issuer}/.well-known/jwks.json",
            lifespan=JWKS_LIFESPAN_SECONDS,
            timeout=JWKS_TIMEOUT_SECONDS,
        )

    def refresh_keys(self) -> bool:
        """Download the keys now. A failure leaves the cached ones in place."""
        try:
            self._keys.get_jwk_set(refresh=True)
        except Exception as error:  # offline or a bad answer: requests still try on their own
            structlog.get_logger(__name__).warning("jwks_refresh_failed", error=str(error)[:200])
            return False
        return True

    def verify(self, token: str) -> AuthUser:
        """Raises jwt.PyJWTError when the token cannot be trusted."""
        key = self._keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=self.issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
        # Anonymous sign-ins share the "authenticated" audience but are not accounts.
        if claims.get("is_anonymous"):
            raise jwt.InvalidTokenError("anonymous session")
        role = claims.get("user_role")
        return AuthUser(
            id=claims["sub"],
            email=claims.get("email"),
            role=role if isinstance(role, str) else "user",
        )


@lru_cache
def _verifier(supabase_url: str) -> TokenVerifier:
    # One per project URL, so that the downloaded keys are reused between requests.
    return TokenVerifier(supabase_url)


def keep_keys_warm(supabase_url: str) -> Event:
    """Load the keys now and refresh them in the background until the returned event is set."""
    verifier, stop = _verifier(supabase_url), Event()

    def run() -> None:
        while not stop.is_set():
            ok = verifier.refresh_keys()
            stop.wait(JWKS_REFRESH_SECONDS if ok else JWKS_RETRY_SECONDS)

    Thread(target=run, name="jwks-keep-warm", daemon=True).start()
    return stop


def _unauthorized(code: str, message: str) -> ApplicationError:
    return ApplicationError(message, code=code, status_code=status.HTTP_401_UNAUTHORIZED)


def _unavailable(code: str, message: str) -> ApplicationError:
    return ApplicationError(message, code=code, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


def _verified_user(
    credentials: HTTPAuthorizationCredentials | None, settings: Settings
) -> AuthUser:
    if not settings.supabase_url:
        # Fail closed: a missing setting must never open the routes.
        raise _unavailable("auth_unconfigured", "Login is required but SUPABASE_URL is not set.")
    if credentials is None:
        raise _unauthorized("not_authenticated", "Sign in to use this endpoint.")
    try:
        return _verifier(settings.supabase_url).verify(credentials.credentials)
    except jwt.PyJWKClientConnectionError as error:
        structlog.get_logger(__name__).warning("jwks_unreachable", error=str(error))
        raise _unavailable("auth_unavailable", "Cannot reach the login server.") from error
    except jwt.PyJWTError as error:
        # The reason is logged, not returned; the token itself is never logged.
        structlog.get_logger(__name__).info("token_rejected", reason=type(error).__name__)
        raise _unauthorized("invalid_token", "The session is invalid or has expired.") from error


# A plain `def`: FastAPI runs it in a worker thread, so a key download cannot block the loop.
def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthUser | None:
    """The signed-in user; None only when REQUIRE_LOGIN is off."""
    if not settings.require_login:
        return None
    return _verified_user(credentials, settings)


def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuthUser:
    """A signed-in admin. REQUIRE_LOGIN=false does not open these routes: they change data."""
    user = _verified_user(credentials, settings)
    if not user.is_admin:
        raise ApplicationError(
            "This endpoint is for admins.", code="forbidden", status_code=status.HTTP_403_FORBIDDEN
        )
    return user
