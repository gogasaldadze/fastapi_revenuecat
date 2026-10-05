"""Rules for changing premium state on a user row.

Shared by the webhook, the client sync and the reconcile job so all three
apply the same rules:

* Expiry only moves forward. A replayed or late event cannot shorten or
  stretch access in the wrong direction. None on a premium row means a
  lifetime purchase and is kept.
* Events older than the newest one already applied to a row are skipped.
* Rows are locked in id order to avoid deadlocks between concurrent events.
  Never call RevenueCat while holding these locks.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutil import as_utc
from app.db.models import User


def forward_expiry(user: User, new: datetime | None) -> datetime | None:
    """The expiry to store after a grant. Never earlier than the current one.

    Args:
        user: The row before the change.
        new: Expiry from the event or RevenueCat. None means lifetime.
    """
    if new is None:
        return None
    current = user.premium_expires_at
    if current is None:
        # A premium row without expiry is a lifetime purchase. Keep it.
        return None if user.is_premium else new
    return max(as_utc(current), new)


def is_stale(user: User, event_at: datetime | None) -> bool:
    """True when a newer event has already been applied to this row."""
    if event_at is None or user.rc_last_event_at is None:
        return False
    return as_utc(user.rc_last_event_at) > event_at


def _mark_applied(user: User, event_at: datetime | None) -> None:
    if event_at is not None:
        user.rc_last_event_at = event_at


def grant(user: User, expires_at: datetime | None, event_at: datetime | None = None) -> None:
    """Turn premium on with a forward only expiry."""
    user.premium_expires_at = forward_expiry(user, expires_at)
    user.is_premium = True
    _mark_applied(user, event_at)


def revoke(user: User, event_at: datetime | None = None) -> None:
    """Turn premium off. The stored expiry is kept for reference."""
    user.is_premium = False
    _mark_applied(user, event_at)


async def lock_users(db: AsyncSession, app_user_ids: Iterable[str]) -> list[User]:
    """SELECT FOR UPDATE every user bound to one of `app_user_ids`, in id order."""
    ids = list(dict.fromkeys(app_user_ids))
    if not ids:
        return []
    result = await db.execute(
        select(User)
        .where(User.rc_app_user_id.in_(ids))
        .order_by(User.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())
