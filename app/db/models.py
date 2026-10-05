"""Database models."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

APP_USER_ID_MAX_LENGTH = 255


class User(Base):
    """An account in your app, optionally bound to one RevenueCat app user id."""

    __tablename__ = "users"
    __table_args__ = (
        # One RevenueCat id per user, many users without one.
        Index(
            "uq_users_rc_app_user_id",
            "rc_app_user_id",
            unique=True,
            postgresql_where=text("rc_app_user_id IS NOT NULL"),
            sqlite_where=text("rc_app_user_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    rc_app_user_id: Mapped[str | None] = mapped_column(String(APP_USER_ID_MAX_LENGTH))
    is_premium: Mapped[bool] = mapped_column(default=False, server_default=false())
    premium_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # event_timestamp_ms of the newest webhook applied to this row. Older
    # events are ignored, so retries and late deliveries cannot undo newer state.
    rc_last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SubscriptionEvent(Base):
    """One row per processed webhook event. The unique event_id makes processing idempotent."""

    __tablename__ = "subscription_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    bucket: Mapped[str] = mapped_column(String(32))
    app_user_id: Mapped[str | None] = mapped_column(String(APP_USER_ID_MAX_LENGTH), index=True)
    product_id: Mapped[str | None] = mapped_column(String(255))
    store: Mapped[str | None] = mapped_column(String(32))
    environment: Mapped[str | None] = mapped_column(String(32))
    event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expiration_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
