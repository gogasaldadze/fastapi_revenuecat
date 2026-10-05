"""The Alembic migration must build the same schema as the models."""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db.base import Base

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_upgrade_matches_models_and_downgrade_cleans_up(tmp_path):
    db_file = tmp_path / "migrations.db"
    config = alembic_config(f"sqlite+aiosqlite:///{db_file}")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_file}")
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
            assert diff == []
            indexes = {i["name"]: i for i in inspect(conn).get_indexes("users")}
            assert indexes["uq_users_rc_app_user_id"]["unique"]

        command.downgrade(config, "base")
        with engine.connect() as conn:
            assert set(inspect(conn).get_table_names()) == {"alembic_version"}
    finally:
        engine.dispose()
