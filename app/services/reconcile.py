"""Compare stored premium state with RevenueCat for every bound user.

Webhooks can be missed: a misconfigured secret, a long outage, an event type
the server ignores. This job finds the drift and can fix it with the same
rules the webhook uses. RevenueCat is called with no locks held; each fix
runs in its own short transaction and re checks the binding first.
"""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.locks import set_lock_timeout
from app.db.models import User
from app.services.entitlements import Entitlement, EntitlementStatus, fetch_entitlement
from app.services.premium import stored_premium
from app.services.subscription_state import grant, revoke

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Drift:
    """One user whose stored state disagrees with RevenueCat."""

    user_id: uuid.UUID
    app_user_id: str
    stored_premium: bool
    revenuecat: EntitlementStatus


@dataclass
class ReconcileReport:
    """Totals for one run."""

    checked: int = 0
    unknown: int = 0
    fixed: int = 0
    drift: list[Drift] = field(default_factory=list)


async def _bound_users(
    sessions: async_sessionmaker[AsyncSession], batch_size: int
) -> AsyncIterator[list[User]]:
    last_id: uuid.UUID | None = None
    while True:
        query = select(User).where(User.rc_app_user_id.is_not(None)).order_by(User.id)
        if last_id is not None:
            query = query.where(User.id > last_id)
        async with sessions() as db:
            batch = list((await db.execute(query.limit(batch_size))).scalars())
        if not batch:
            return
        last_id = batch[-1].id
        yield batch


async def _fix(sessions: async_sessionmaker[AsyncSession], drift: Drift, ent: Entitlement) -> bool:
    async with sessions() as db:
        await set_lock_timeout(db)
        row = (
            await db.execute(select(User).where(User.id == drift.user_id).with_for_update())
        ).scalar_one_or_none()
        if row is None or row.rc_app_user_id != drift.app_user_id:
            return False
        if ent.active:
            grant(row, ent.expires_at)
        else:
            revoke(row)
        await db.commit()
        return True


async def reconcile(
    sessions: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    *,
    fix: bool = False,
    batch_size: int = 200,
    concurrency: int = 5,
) -> ReconcileReport:
    """Check every bound user against RevenueCat.

    Args:
        sessions: Session factory. Each batch and each fix uses its own session.
        http: HTTP client for RevenueCat.
        fix: Write RevenueCat's answer to drifted rows.
        batch_size: Users loaded per query.
        concurrency: Parallel RevenueCat requests.
    """
    report = ReconcileReport()
    limiter = asyncio.Semaphore(concurrency)

    async def check(user: User) -> tuple[User, Entitlement]:
        async with limiter:
            return user, await fetch_entitlement(http, str(user.rc_app_user_id))

    async for batch in _bound_users(sessions, batch_size):
        for user, ent in await asyncio.gather(*(check(u) for u in batch)):
            report.checked += 1
            if ent.status is EntitlementStatus.UNKNOWN:
                report.unknown += 1
                continue
            stored = stored_premium(user)
            if stored == ent.active:
                continue
            drift = Drift(user.id, str(user.rc_app_user_id), stored, ent.status)
            report.drift.append(drift)
            logger.warning(
                "Premium drift",
                extra={
                    "user_id": str(drift.user_id),
                    "app_user_id": drift.app_user_id,
                    "stored_premium": stored,
                    "revenuecat": ent.status.value,
                },
            )
            if fix and await _fix(sessions, drift, ent):
                report.fixed += 1
    return report
