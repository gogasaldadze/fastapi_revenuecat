"""Row lock helpers."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings


async def set_lock_timeout(db: AsyncSession) -> None:
    """Make row lock waits in the current transaction fail fast on Postgres.

    A request that cannot get a lock within LOCK_TIMEOUT_MS raises
    OperationalError, which the routes turn into 503 instead of hanging.
    Other databases are left alone.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text(f"SET LOCAL lock_timeout = {int(settings.lock_timeout_ms)}"))
