from fastapi import APIRouter, HTTPException

from app.auth import create_access_token
from app.config import settings
from app.db import DbSession
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/dev_token")
async def dev_token(db: DbSession) -> dict:
    """Create a user and return a token for it. Only available when DEBUG is on."""
    if not settings.debug:
        raise HTTPException(status_code=404, detail="Not Found")
    user = User()
    db.add(user)
    await db.commit()
    return {
        "access_token": create_access_token(user.id),
        "token_type": "bearer",
        "user_id": str(user.id),
    }
