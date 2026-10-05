"""users and subscription events

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rc_app_user_id", sa.String(length=255), nullable=True),
        sa.Column("is_premium", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("premium_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_users_rc_app_user_id",
        "users",
        ["rc_app_user_id"],
        unique=True,
        postgresql_where=sa.text("rc_app_user_id IS NOT NULL"),
        sqlite_where=sa.text("rc_app_user_id IS NOT NULL"),
    )

    op.create_table(
        "subscription_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=64), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("app_user_id", sa.String(length=255), nullable=True),
        sa.Column("product_id", sa.String(length=255), nullable=True),
        sa.Column("store", sa.String(length=32), nullable=True),
        sa.Column("environment", sa.String(length=32), nullable=True),
        sa.Column("expiration_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscription_events_event_id", "subscription_events", ["event_id"])
    op.create_index("ix_subscription_events_app_user_id", "subscription_events", ["app_user_id"])


def downgrade() -> None:
    op.drop_index("ix_subscription_events_app_user_id", table_name="subscription_events")
    op.drop_index("ix_subscription_events_event_id", table_name="subscription_events")
    op.drop_table("subscription_events")
    op.drop_index("uq_users_rc_app_user_id", table_name="users")
    op.drop_table("users")
