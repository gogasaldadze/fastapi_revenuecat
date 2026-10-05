"""Example routes showing how to use the dependencies. Replace with your own."""

from fastapi import APIRouter, Depends

from app.api.deps import IsPremium, PremiumUser, charge, require_premium
from app.api.schemas import ExampleResponse, MeResponse
from app.core.errors import ErrorResponse
from app.core.security import CurrentUser

router = APIRouter(tags=["examples"])

AUTH_ERRORS: dict[int | str, dict[str, object]] = {401: {"model": ErrorResponse}}
PREMIUM_ERRORS: dict[int | str, dict[str, object]] = {
    **AUTH_ERRORS,
    403: {"model": ErrorResponse, "description": "Not premium"},
}
QUOTA_ERRORS: dict[int | str, dict[str, object]] = {
    429: {"model": ErrorResponse, "description": "Daily units used up"},
    503: {"model": ErrorResponse, "description": "Quota backend down"},
}


@router.get("/me", summary="Current user and premium status", responses=AUTH_ERRORS)
async def me(user: CurrentUser, is_premium: IsPremium) -> MeResponse:
    """Return the current user with server side premium status."""
    return MeResponse(id=str(user.id), is_premium=is_premium)


@router.get("/examples/premium", summary="Premium only route", responses=PREMIUM_ERRORS)
async def premium_only(user: PremiumUser) -> ExampleResponse:
    """Reachable only by premium users."""
    return ExampleResponse()


@router.post(
    "/examples/basic",
    summary="Cheap metered feature",
    dependencies=[Depends(charge("basic"))],
    responses={**AUTH_ERRORS, **QUOTA_ERRORS},
)
async def basic_feature() -> ExampleResponse:
    """Costs the "basic" weight. Lets requests through if Redis is down."""
    return ExampleResponse()


# Route dependencies run in order, so free users are rejected before any charge.
@router.post(
    "/examples/heavy",
    summary="Expensive premium feature",
    dependencies=[Depends(require_premium), Depends(charge("heavy"))],
    responses={**PREMIUM_ERRORS, **QUOTA_ERRORS},
)
async def heavy_feature() -> ExampleResponse:
    """Premium only, costs the "heavy" weight, rejected if Redis is down."""
    return ExampleResponse()
