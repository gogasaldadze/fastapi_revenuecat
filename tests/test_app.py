"""Health checks, error envelope, logging, settings and app lifespan."""

import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.logging import JsonFormatter, configure_logging
from app.main import app, create_app, lifespan


async def test_liveness(client):
    resp = await client.get("/health")
    assert resp.json() == {"status": "ok"}


async def test_ready_when_everything_is_up(client):
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


async def test_not_ready_when_redis_is_down(client, broken_redis):
    from app.core.clients import get_redis

    app.dependency_overrides[get_redis] = lambda: broken_redis
    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["checks"] == {"database": "ok", "redis": "error"}


async def test_not_ready_when_database_is_down(client):
    from app.db.session import get_db

    class BrokenSession:
        async def execute(self, *args, **kwargs):
            raise ConnectionError("database is down")

    app.dependency_overrides[get_db] = lambda: BrokenSession()
    resp = await client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "unavailable", "checks": {"database": "error", "redis": "ok"}}


async def test_unknown_route_uses_error_envelope(client):
    resp = await client.get("/nope")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_wrong_method_uses_error_envelope(client):
    resp = await client.get("/webhooks/revenuecat")
    assert resp.json()["error"]["code"] == "method_not_allowed"


async def test_unhandled_error_is_500_envelope(session_maker, redis, http):
    from app.core.security import get_current_user

    async def explode():
        raise RuntimeError("bug")

    app.dependency_overrides[get_current_user] = explode
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/me")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}


def test_every_route_has_summary_and_tag():
    schema = create_app().openapi()
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            assert operation.get("summary"), f"{method} {path} has no summary"
            assert operation.get("tags"), f"{method} {path} has no tag"


def test_json_formatter_includes_extra_fields():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.user_id = "u1"
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "hello world"
    assert line["user_id"] == "u1"
    assert line["level"] == "INFO"


def test_json_formatter_includes_exception():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exc_info"]


def test_configure_logging_text_and_json():
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        configure_logging("debug", "text")
        assert not isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG
        configure_logging("info", "json")
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers[:], level = saved
        root.setLevel(level)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("postgresql+asyncpg://u:p@h/d", "postgresql+asyncpg://u:p@h/d"),
        ("sqlite+aiosqlite:///x.db", "sqlite+aiosqlite:///x.db"),
    ],
)
def test_database_url_gets_async_driver(raw, expected):
    assert Settings(database_url=raw).database_url == expected


def test_zero_feature_weight_is_rejected():
    with pytest.raises(ValidationError, match="at least 1"):
        Settings(feature_weights={"free_lunch": 0})


def test_sandbox_allowed_flag(monkeypatch):
    assert settings.sandbox_allowed is False
    monkeypatch.setattr(settings, "debug", True)
    assert settings.sandbox_allowed is True


async def test_lifespan_creates_and_closes_clients():
    application = create_app()
    async with lifespan(application):
        assert isinstance(application.state.http_client, httpx.AsyncClient)
        assert application.state.redis is not None
    assert application.state.http_client.is_closed


async def test_lifespan_refuses_to_start_without_jwt_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        async with lifespan(create_app()):
            pass


async def test_real_dependencies_resolve_from_app_state():
    from types import SimpleNamespace

    from app.core.clients import get_http_client, get_redis
    from app.db.session import get_db

    state = SimpleNamespace(redis="redis_client", http_client="http_client")
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    assert get_redis(request) == "redis_client"
    assert get_http_client(request) == "http_client"

    sessions = get_db()
    session = await anext(sessions)
    assert session is not None
    await sessions.aclose()


async def test_lock_timeout_is_set_on_postgres(monkeypatch):
    from types import SimpleNamespace

    from app.db.locks import set_lock_timeout

    monkeypatch.setattr(settings, "lock_timeout_ms", 1500)
    executed: list[str] = []

    class FakeSession:
        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        async def execute(self, statement):
            executed.append(str(statement))

    await set_lock_timeout(FakeSession())
    assert executed == ["SET LOCAL lock_timeout = 1500"]
