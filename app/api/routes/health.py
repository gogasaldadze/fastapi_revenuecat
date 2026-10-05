import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.schemas import HealthResponse, ReadinessResponse
from app.core.clients import RedisClient
from app.db.session import DbSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="Liveness check")
async def health() -> HealthResponse:
    """Returns 200 while the process is up. Checks nothing else."""
    return HealthResponse()


@router.get(
    "/ready",
    summary="Readiness check for the database and Redis",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse, "description": "A dependency is down"}},
)
async def ready(db: DbSession, redis: RedisClient) -> JSONResponse:
    """Returns 200 when both the database and Redis answer, 503 otherwise."""
    checks: dict[str, str] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("Readiness: database check failed", extra={"error": str(exc)})
        checks["database"] = "error"
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("Readiness: redis check failed", extra={"error": str(exc)})
        checks["redis"] = "error"

    healthy = all(value == "ok" for value in checks.values())
    body = {"status": "ok" if healthy else "unavailable", "checks": checks}
    return JSONResponse(status_code=200 if healthy else 503, content=body)
