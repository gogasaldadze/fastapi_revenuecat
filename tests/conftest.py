"""Shared fixtures.

The environment is set before any app import so Settings never reads a real
.env. Each test gets its own SQLite file and fakeredis instance, and every
RevenueCat request goes through respx.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="fastapi_revenuecat_tests_")
os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{_TMP}/import.db",
        "REDIS_URL": "redis://127.0.0.1:1/0",
        "JWT_SECRET": "test_jwt_secret_that_is_long_enough_for_hs256",
        "REVENUECAT_SECRET_KEY": "sk_test",
        "REVENUECAT_WEBHOOK_SECRET": "whsec_test",
        "REVENUECAT_ENTITLEMENT_ID": "premium",
        "DEBUG": "false",
    }
)

from datetime import datetime  # noqa: I001

import fakeredis
import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.clients import get_http_client, get_redis
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.db.models import User
from tests.rc_fake import RC_SUBSCRIBERS_URL, FakeRevenueCat

WEBHOOK_SECRET = "whsec_test"


@pytest.fixture(autouse=True)
def prod_settings(monkeypatch):
    """Production like settings unless a test overrides them."""
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "allow_sandbox_subscriptions", False)
    monkeypatch.setattr(settings, "revenuecat_secret_key", "sk_test")
    monkeypatch.setattr(settings, "revenuecat_webhook_secret", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "store_webhook_events", True)
    monkeypatch.setattr(settings, "free_daily_units", 20)
    monkeypatch.setattr(settings, "premium_daily_units", 1000)
    monkeypatch.setattr(settings, "feature_weights", {"basic": 1, "heavy": 10})
    monkeypatch.setattr(settings, "fail_open_features", {"basic"})
    return settings


@pytest.fixture
def rc():
    fake = FakeRevenueCat()
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith=RC_SUBSCRIBERS_URL).mock(side_effect=fake)
        yield fake


@pytest.fixture
async def session_maker(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def redis():
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
async def client(session_maker, redis, http):
    async def _db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_http_client] = lambda: http
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def make_user(
    session_maker,
    *,
    rc_app_user_id: str | None = None,
    is_premium: bool = False,
    premium_expires_at: datetime | None = None,
) -> User:
    async with session_maker() as session:
        user = User(
            rc_app_user_id=rc_app_user_id,
            is_premium=is_premium,
            premium_expires_at=premium_expires_at,
        )
        session.add(user)
        await session.commit()
        return user


async def reload(session_maker, user: User) -> User:
    async with session_maker() as session:
        return await session.get(User, user.id)


def auth(user: User) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


class BrokenRedis:
    """Stands in for a Redis server that is down."""

    def __getattr__(self, name):
        async def fail(*args, **kwargs):
            raise ConnectionError("redis is down")

        return fail

    def pipeline(self, *args, **kwargs):
        raise ConnectionError("redis is down")


@pytest.fixture
def broken_redis():
    return BrokenRedis()
