"""Sync in legacy mode, where any active id may be bound. See test_sync_strict.py
for the default, where the id must belong to the signed in user."""

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.services.entitlement_cache import cache_key
from tests.conftest import WEBHOOK_SECRET, auth, make_user, reload
from tests.rc_fake import active, expired

URL = "/subscription/sync"


@pytest.fixture(autouse=True)
def legacy_mode(monkeypatch):
    monkeypatch.setattr(settings, "revenuecat_require_user_id_match", False)


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


# Races with other syncs and with the webhook


async def test_concurrent_bind_yields_to_the_winner(client, session_maker, rc, monkeypatch):
    """Simulate another request binding the id between our release and our write."""
    from sqlalchemy import false, update

    import app.services.premium as premium

    rc.set("rc_1", 200, active())
    winner = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    loser = await make_user(session_maker)
    monkeypatch.setattr(premium, "update", lambda model: update(model).where(false()))

    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(loser))
    assert resp.status_code == 200
    assert resp.json() == {"is_premium": False}
    assert (await reload(session_maker, winner)).rc_app_user_id == "rc_1"
    assert (await reload(session_maker, loser)).rc_app_user_id is None


async def test_sync_yields_to_webhook_state(client, session_maker, rc):
    """Once a webhook has written the row, sync must not overwrite it."""
    rc.set("rc_1", 200, active())
    expired_at = datetime.now(UTC) - timedelta(days=1)
    user = await make_user(
        session_maker,
        rc_app_user_id="rc_1",
        is_premium=False,
        premium_expires_at=expired_at,
        rc_last_event_at=datetime.now(UTC),
    )
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    row = await reload(session_maker, user)
    assert row.is_premium is False
    assert row.premium_expires_at.replace(tzinfo=UTC) == expired_at


async def test_sync_seeds_bound_row_before_any_webhook(client, session_maker, rc):
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    row = await reload(session_maker, user)
    assert row.is_premium is True
    assert row.premium_expires_at is not None


async def test_webhook_after_sync_still_applies(client, session_maker, rc):
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker)
    await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    body = {
        "event": {
            "id": "evt_after_sync",
            "type": "EXPIRATION",
            "app_user_id": "rc_1",
            "event_timestamp_ms": 1790000000000,
        }
    }
    headers = {"Authorization": f"Bearer {WEBHOOK_SECRET}"}
    await client.post("/webhooks/revenuecat", json=body, headers=headers)
    assert (await reload(session_maker, user)).is_premium is False


async def test_lock_timeout_during_sync_is_503(client, session_maker, rc, monkeypatch):
    from sqlalchemy.exc import OperationalError

    import app.services.premium as premium

    async def lock_timeout(db):
        raise OperationalError("SET LOCAL lock_timeout", {}, Exception("lock timeout"))

    rc.set("rc_1", 200, active())
    user = await make_user(session_maker)
    monkeypatch.setattr(premium, "set_lock_timeout", lock_timeout)
    resp = await client.post(URL, json={"app_user_id": "rc_1"}, headers=auth(user))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"
