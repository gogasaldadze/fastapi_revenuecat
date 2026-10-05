"""Minimal bearer token auth.

This exists so the template runs on its own. Swap `get_current_user` for your
own auth and keep its contract: return the User row or raise 401.
"""

import uuid
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.errors import AppError
from app.core.timeutil import utcnow
from app.db.models import User
from app.db.session import DbSession

bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized() -> AppError:
    return AppError(
        401, "unauthorized", "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
    )


def _secret() -> str:
    if not settings.jwt_secret:
        raise AppError(503, "auth_not_configured", "Auth is not configured")
    return settings.jwt_secret


def create_access_token(user_id: uuid.UUID) -> str:
    """Issue a signed token whose subject is the user id."""
    now = utcnow()
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(claims, _secret(), algorithm=settings.jwt_algorithm)


def decode_user_id(token: str) -> uuid.UUID:
    """Validate a token and return its user id.

    Raises:
        AppError: 401 for a bad, expired or malformed token.
    """
    try:
        claims = jwt.decode(
            token,
            _secret(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        return uuid.UUID(str(claims["sub"]))
    except (jwt.InvalidTokenError, ValueError) as exc:
        raise _unauthorized() from exc


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Dependency returning the authenticated user, or raising 401."""
    if credentials is None:
        raise _unauthorized()
    user = await db.get(User, decode_user_id(credentials.credentials))
    if user is None:
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
