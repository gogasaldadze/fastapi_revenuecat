"""Minimal bearer token auth.

This exists so the template runs on its own. Swap get_current_user for your
own auth and keep its contract: return the User row or raise 401.
"""

import uuid
from datetime import timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.db import DbSession
from app.models import User
from app.timeutil import utcnow

bearer_scheme = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"}
)


def _secret() -> str:
    if not settings.jwt_secret:
        raise HTTPException(status_code=503, detail="Auth not configured")
    return settings.jwt_secret


def create_access_token(user_id: uuid.UUID) -> str:
    now = utcnow()
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(claims, _secret(), algorithm=settings.jwt_algorithm)


def decode_user_id(token: str) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            _secret(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        return uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise UNAUTHORIZED from exc


async def get_current_user(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None:
        raise UNAUTHORIZED
    user = await db.get(User, decode_user_id(credentials.credentials))
    if user is None:
        raise UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
