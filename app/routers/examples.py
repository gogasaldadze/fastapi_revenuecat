"""Example routes showing how to use the dependencies. Replace with your own."""

from fastapi import APIRouter, Depends

from app.auth import CurrentUser
from app.deps import IsPremium, PremiumUser, charge, require_premium

router = APIRouter(tags=["examples"])


@router.get("/me")
async def me(user: CurrentUser, is_premium: IsPremium) -> dict:
    return {"id": str(user.id), "is_premium": is_premium}


@router.get("/examples/premium")
async def premium_only(user: PremiumUser) -> dict:
    return {"message": "premium content", "user_id": str(user.id)}


@router.post("/examples/basic", dependencies=[Depends(charge("basic"))])
async def basic_feature() -> dict:
    return {"ok": True}


# Route dependencies run in order, so free users are rejected before any charge.
@router.post(
    "/examples/heavy",
    dependencies=[Depends(require_premium), Depends(charge("heavy"))],
)
async def heavy_feature() -> dict:
    return {"ok": True}
