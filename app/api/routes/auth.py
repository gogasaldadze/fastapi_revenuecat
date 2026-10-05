from fastapi import APIRouter

from app.api.schemas import TokenResponse
from app.core.config import settings
from app.core.errors import AppError, ErrorResponse
from app.core.security import create_access_token
from app.db.models import User
from app.db.session import DbSession

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/dev_token",
    summary="Create a test user and token (debug only)",
    responses={404: {"model": ErrorResponse, "description": "DEBUG is off"}},
)
async def dev_token(db: DbSession) -> TokenResponse:
    """Create a user and return a bearer token for it. Returns 404 unless DEBUG is on."""
    if not settings.debug:
        raise AppError(404, "not_found", "Not Found")
    user = User()
    db.add(user)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id), user_id=str(user.id))
