"""Parsing and classifying RevenueCat webhook events. No I/O here.

Buckets:

    SYNC             premium on, expiry moved forward
    EXPIRE           premium off
    TRANSFER         premium moves from transferred_from ids to transferred_to ids
    INFO_ONLY        recorded only, the user keeps access until the period ends
    UNKNOWN          recorded only (includes the dashboard TEST event)
    SANDBOX_IGNORED  dropped, unless sandbox purchases are allowed
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.db.models import APP_USER_ID_MAX_LENGTH

ANONYMOUS_PREFIX = "$RCAnonymousID:"
EVENT_ID_MAX_LENGTH = 64


class Bucket(StrEnum):
    """What the server does with an event."""

    SYNC = "sync"
    EXPIRE = "expire"
    TRANSFER = "transfer"
    INFO_ONLY = "info_only"
    UNKNOWN = "unknown"
    SANDBOX_IGNORED = "sandbox_ignored"


SYNC_EVENTS = frozenset({"INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"})
EXPIRE_EVENTS = frozenset({"EXPIRATION"})
# All three leave access in place until the current period ends. RevenueCat
# sends EXPIRATION when it actually does.
INFO_ONLY_EVENTS = frozenset({"CANCELLATION", "BILLING_ISSUE", "SUBSCRIPTION_PAUSED"})


def classify(event_type: str) -> Bucket:
    """Map a RevenueCat event type to a bucket. Case does not matter."""
    name = (event_type or "").upper()
    if name in SYNC_EVENTS:
        return Bucket.SYNC
    if name in EXPIRE_EVENTS:
        return Bucket.EXPIRE
    if name == "TRANSFER":
        return Bucket.TRANSFER
    if name in INFO_ONLY_EVENTS:
        return Bucket.INFO_ONLY
    return Bucket.UNKNOWN


def parse_ms(raw: object) -> datetime | None:
    """Parse a millisecond epoch timestamp. Returns None for anything unusable."""
    if not raw or isinstance(raw, bool) or not isinstance(raw, int | float | str):
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _ids(values: list[object]) -> tuple[str, ...]:
    valid = (v for v in values if isinstance(v, str) and 0 < len(v) <= APP_USER_ID_MAX_LENGTH)
    return tuple(dict.fromkeys(valid))


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return str(value) if value is not None else None


@dataclass(frozen=True)
class WebhookEvent:
    """The parts of a webhook event the server uses."""

    id: str
    type: str
    environment: str
    app_user_ids: tuple[str, ...]
    transferred_from: tuple[str, ...] = ()
    transferred_to: tuple[str, ...] = ()
    event_at: datetime | None = None
    expires_at: datetime | None = None
    product_id: str | None = None
    store: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def all_ids(self) -> tuple[str, ...]:
        """Every RevenueCat id the event mentions, deduplicated in order."""
        return tuple(dict.fromkeys(self.app_user_ids + self.transferred_from + self.transferred_to))

    @property
    def primary_id(self) -> str | None:
        """The id to show in logs and the events table."""
        return self.all_ids[0] if self.all_ids else None


def parse_event(raw: object) -> WebhookEvent | None:
    """Turn the `event` object of a webhook body into a WebhookEvent.

    Returns:
        None when the payload can never be processed: not an object, or no
        usable `id` or `type`. Retrying such a payload would not help.
    """
    if not isinstance(raw, dict):
        return None
    event_id, event_type = raw.get("id"), raw.get("type")
    if not isinstance(event_id, str) or not 0 < len(event_id) <= EVENT_ID_MAX_LENGTH:
        return None
    if not isinstance(event_type, str) or not event_type:
        return None
    return WebhookEvent(
        id=event_id,
        type=event_type.upper(),
        environment=str(raw.get("environment") or "PRODUCTION").upper(),
        app_user_ids=_ids(
            [raw.get("app_user_id"), raw.get("original_app_user_id"), *_list(raw.get("aliases"))]
        ),
        transferred_from=_ids(_list(raw.get("transferred_from"))),
        transferred_to=_ids(_list(raw.get("transferred_to"))),
        event_at=parse_ms(raw.get("event_timestamp_ms")),
        expires_at=parse_ms(raw.get("expiration_at_ms")),
        product_id=_text(raw.get("product_id")),
        store=_text(raw.get("store")),
        raw=raw,
    )
