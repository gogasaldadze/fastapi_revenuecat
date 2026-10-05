"""Small helpers for timezone aware datetimes."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Attach UTC to naive datetimes, which SQLite returns."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)
