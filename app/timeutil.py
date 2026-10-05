from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    # SQLite hands back naive datetimes; treat them as UTC.
    return value if value.tzinfo else value.replace(tzinfo=UTC)
