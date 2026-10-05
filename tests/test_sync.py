import logging

from app.services.entitlement_cache import cache_key
from tests.conftest import auth, make_user, reload
from tests.rc_fake import active, expired

URL = "/subscription/sync"


async def test_requires_auth(client):
    resp = await client.post(URL, json={"app_user_id": "rc_1"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_binds_when_active(client, session_maker, rc):
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    row = await reload(session_maker, user)
    assert row.rc_app_user_id == "rc_1"
    assert row.is_premium is True
    assert row.premium_expires_at is not None


async def test_does_not_bind_when_expired(client, session_maker, rc):
    rc.set("rc_1", 200, expired())
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": False}
    assert (await reload(session_maker, user)).rc_app_user_id is None


async def test_does_not_bind_unknown_customer(client, session_maker, rc):
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "never_paid"}, headers=auth(user))
    assert resp.json() == {"is_premium": False}
    assert (await reload(session_maker, user)).rc_app_user_id is None


async def test_does_not_bind_during_outage(client, session_maker, rc):
    rc.set("rc_1", 503, {})
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": False}
    assert (await reload(session_maker, user)).rc_app_user_id is None


async def test_outage_keeps_existing_premium(client, session_maker, rc):
    rc.set("rc_1", 503, {})
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}


async def test_sync_ignores_stale_cache(client, session_maker, rc, redis):
    await redis.set(cache_key("rc_1"), '{"status": "inactive", "expires_at": null}')
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}


async def test_binding_moves_from_other_user(client, session_maker, rc, caplog):
    rc.set("rc_1", 200, active())
    old = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    new = await make_user(session_maker)
    with caplog.at_level(logging.WARNING):
        resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(new))
    assert resp.json() == {"is_premium": True}
    assert (await reload(session_maker, new)).rc_app_user_id == "rc_1"
    old_row = await reload(session_maker, old)
    assert old_row.rc_app_user_id is None
    assert old_row.is_premium is False
    assert "RevenueCat id moved from another user" in caplog.text


async def test_already_bound_id_stays_bound(client, session_maker, rc):
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    assert (await reload(session_maker, user)).rc_app_user_id == "rc_1"


async def test_rebinds_to_new_active_id(client, session_maker, rc):
    rc.set("rc_2", 200, active())
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await client.post(URL, json={"app_user_id": "rc_2"}, headers=auth(user))
    assert (await reload(session_maker, user)).rc_app_user_id == "rc_2"


async def test_placeholder_id_is_not_checked(client, session_maker, rc):
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "null"}, headers=auth(user))
    assert resp.json() == {"is_premium": False}
    assert rc.calls == []


async def test_too_long_id_is_422(client, session_maker):
    user = await make_user(session_maker)
    resp = await client.post(URL, json={"app_user_id": "x" * 300}, headers=auth(user))
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["code"] == "validation_error"
    assert body["details"][0]["loc"] == ["body", "app_user_id"]


async def test_concurrent_bind_conflict_is_handled(session_maker, rc, http, redis, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.services.premium import bind_app_user_id

    rc.set("rc_1", 200, active())
    user = await make_user(session_maker)
    async with session_maker() as session:
        row = await session.merge(user)

        async def conflict():
            raise IntegrityError("insert", {}, Exception("unique"))

        monkeypatch.setattr(session, "commit", conflict)
        assert await bind_app_user_id(session, http, redis, row, "rc_1") is False
