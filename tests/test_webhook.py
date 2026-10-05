import itertools
import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.db.models import SubscriptionEvent
from app.services import webhook_events
from app.services.entitlement_cache import cache_key
from tests.conftest import WEBHOOK_SECRET, make_user, reload

URL = "/webhooks/revenuecat"
EXPIRES_MS = 1893456000000  # 2030-01-01
EVENT_MS = 1790000000000  # 2026-09-21
_ids = itertools.count(1)


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def payload(event_type: str, app_user_id: str = "rc_1", **extra) -> dict:
    return {
        "event": {
            "id": f"evt_{next(_ids)}",
            "type": event_type,
            "app_user_id": app_user_id,
            "original_app_user_id": app_user_id,
            "aliases": [app_user_id],
            "product_id": "example_monthly",
            "environment": "PRODUCTION",
            "store": "APP_STORE",
            "event_timestamp_ms": EVENT_MS,
            "expiration_at_ms": EXPIRES_MS,
            **extra,
        }
    }


def bearer(token: str = WEBHOOK_SECRET) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def send(client, body: dict):
    return await client.post(URL, json=body, headers=bearer())


async def event_rows(session_maker) -> list[SubscriptionEvent]:
    async with session_maker() as s:
        return list((await s.execute(select(SubscriptionEvent))).scalars())


# Auth


async def test_wrong_secret_is_401(client):
    resp = await client.post(URL, json=payload("RENEWAL"), headers=bearer("nope"))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_webhook_authorization"


async def test_missing_header_is_401(client):
    assert (await client.post(URL, json=payload("RENEWAL"))).status_code == 401


async def test_bearer_secret_accepted(client):
    assert (await send(client, payload("RENEWAL"))).status_code == 200


async def test_bare_secret_accepted(client):
    headers = {"Authorization": WEBHOOK_SECRET}
    assert (await client.post(URL, json=payload("RENEWAL"), headers=headers)).status_code == 200


async def test_lowercase_bearer_accepted(client):
    headers = {"Authorization": f"bearer {WEBHOOK_SECRET}"}
    assert (await client.post(URL, json=payload("RENEWAL"), headers=headers)).status_code == 200


async def test_unset_secret_in_production_is_503(client, monkeypatch):
    monkeypatch.setattr(settings, "revenuecat_webhook_secret", "")
    resp = await send(client, payload("RENEWAL"))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "webhook_not_configured"


async def test_unset_secret_in_debug_is_processed(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "revenuecat_webhook_secret", "")
    monkeypatch.setattr(settings, "debug", True)
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    resp = await client.post(URL, json=payload("INITIAL_PURCHASE"))
    assert resp.status_code == 200
    assert (await reload(session_maker, user)).is_premium is True


# Buckets


@pytest.mark.parametrize(
    "event_type", ["INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION"]
)
async def test_sync_events_grant_premium(client, session_maker, event_type):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    resp = await send(client, payload(event_type))
    assert resp.json() == {"status": "ok", "bucket": "sync"}
    row = await reload(session_maker, user)
    assert row.is_premium is True
    assert row.premium_expires_at.replace(tzinfo=UTC) == datetime(2030, 1, 1, tzinfo=UTC)
    assert row.rc_last_event_at is not None


async def test_sync_without_expiration_is_lifetime(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("INITIAL_PURCHASE", expiration_at_ms=None))
    row = await reload(session_maker, user)
    assert row.is_premium is True
    assert row.premium_expires_at is None


async def test_expiration_revokes_premium(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    resp = await send(client, payload("EXPIRATION"))
    assert resp.json()["bucket"] == "expire"
    assert (await reload(session_maker, user)).is_premium is False


@pytest.mark.parametrize("event_type", ["CANCELLATION", "BILLING_ISSUE", "SUBSCRIPTION_PAUSED"])
async def test_info_only_events_keep_access(client, session_maker, event_type):
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    resp = await send(client, payload(event_type))
    assert resp.json()["bucket"] == "info_only"
    assert (await reload(session_maker, user)).is_premium is True
    assert [e.event_type for e in await event_rows(session_maker)] == [event_type]


async def test_unknown_event_is_recorded_only(client, session_maker, caplog):
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    with caplog.at_level(logging.INFO):
        resp = await send(client, payload("SUBSCRIBER_ALIAS"))
    assert resp.json() == {"status": "ok", "bucket": "unknown"}
    assert (await reload(session_maker, user)).is_premium is True
    assert [e.bucket for e in await event_rows(session_maker)] == ["unknown"]
    assert "Unhandled RevenueCat event type 'SUBSCRIBER_ALIAS'" in caplog.text


async def test_dashboard_test_event_is_accepted(client):
    body = {"event": {"id": "test_evt", "type": "TEST", "app_user_id": "test_user"}}
    assert (await send(client, body)).json() == {"status": "ok", "bucket": "unknown"}


async def test_lowercase_event_type_is_classified(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("renewal"))
    assert (await reload(session_maker, user)).is_premium is True


# Sandbox


async def test_sandbox_ignored_in_production(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    resp = await send(client, payload("INITIAL_PURCHASE", environment="SANDBOX"))
    assert resp.json() == {"status": "ok", "bucket": "sandbox_ignored"}
    assert (await reload(session_maker, user)).is_premium is False
    assert await event_rows(session_maker) == []


async def test_sandbox_processed_when_allowed(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "allow_sandbox_subscriptions", True)
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("INITIAL_PURCHASE", environment="SANDBOX"))
    assert (await reload(session_maker, user)).is_premium is True


# Matching


async def test_matches_by_alias(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="$RCAnonymousID:abc")
    body = payload("RENEWAL", app_user_id="real_id")
    body["event"]["aliases"] = ["real_id", "$RCAnonymousID:abc"]
    await send(client, body)
    assert (await reload(session_maker, user)).is_premium is True


async def test_matches_by_original_app_user_id(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="original")
    body = payload("RENEWAL", app_user_id="new_id", original_app_user_id="original", aliases=[])
    await send(client, body)
    assert (await reload(session_maker, user)).is_premium is True


async def test_unbound_anonymous_id_is_skipped_with_200(client, session_maker, caplog):
    other = await make_user(session_maker, rc_app_user_id="someone_else")
    with caplog.at_level(logging.INFO):
        resp = await send(client, payload("INITIAL_PURCHASE", app_user_id="$RCAnonymousID:xyz"))
    assert resp.status_code == 200
    assert (await reload(session_maker, other)).is_premium is False
    assert "No user bound to anonymous id" in caplog.text


async def test_webhook_never_creates_bindings(client, session_maker):
    user = await make_user(session_maker)
    await send(client, payload("INITIAL_PURCHASE", app_user_id="free_id"))
    row = await reload(session_maker, user)
    assert (row.rc_app_user_id, row.is_premium) == (None, False)


async def test_bogus_ids_are_ignored(client, session_maker):
    body = payload("RENEWAL", app_user_id="", original_app_user_id=None, aliases="not a list")
    assert (await send(client, body)).status_code == 200
    assert (await event_rows(session_maker))[0].app_user_id is None


# Idempotency and ordering


async def test_duplicate_event_is_processed_once(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    body = payload("INITIAL_PURCHASE")
    first = await send(client, body)
    assert first.json()["status"] == "ok"

    # Revoke by hand, then replay the same delivery. Nothing may change.
    async with session_maker() as s:
        row = await s.get(type(user), user.id)
        row.is_premium = False
        await s.commit()
    second = await send(client, body)
    assert second.status_code == 200
    assert second.json() == {"status": "duplicate", "bucket": "sync"}
    assert (await reload(session_maker, user)).is_premium is False
    assert len(await event_rows(session_maker)) == 1


async def test_stale_event_is_ignored(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("RENEWAL", event_timestamp_ms=EVENT_MS))
    late = payload("EXPIRATION", event_timestamp_ms=EVENT_MS - 60_000)
    assert (await send(client, late)).status_code == 200
    assert (await reload(session_maker, user)).is_premium is True


async def test_newer_event_applies(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("RENEWAL", event_timestamp_ms=EVENT_MS))
    await send(client, payload("EXPIRATION", event_timestamp_ms=EVENT_MS + 60_000))
    assert (await reload(session_maker, user)).is_premium is False


async def test_expiry_never_moves_backwards(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    later = datetime(2031, 1, 1, tzinfo=UTC)
    await send(client, payload("RENEWAL", expiration_at_ms=ms(later)))
    await send(
        client, payload("RENEWAL", event_timestamp_ms=EVENT_MS + 1, expiration_at_ms=EXPIRES_MS)
    )
    row = await reload(session_maker, user)
    assert row.premium_expires_at.replace(tzinfo=UTC) == later


async def test_expiry_moves_forward(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("RENEWAL"))
    later = datetime(2031, 1, 1, tzinfo=UTC)
    await send(
        client, payload("RENEWAL", event_timestamp_ms=EVENT_MS + 1, expiration_at_ms=ms(later))
    )
    row = await reload(session_maker, user)
    assert row.premium_expires_at.replace(tzinfo=UTC) == later


async def test_event_without_timestamp_still_applies(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("RENEWAL", event_timestamp_ms=None))
    row = await reload(session_maker, user)
    assert row.is_premium is True
    assert row.rc_last_event_at is None


# TRANSFER


def transfer(sources: list[str], targets: list[str], **extra) -> dict:
    body = payload("TRANSFER", transferred_from=sources, transferred_to=targets, **extra)
    for key in ("app_user_id", "original_app_user_id", "aliases", "expiration_at_ms"):
        body["event"].pop(key)
    return body


async def test_transfer_moves_premium(client, session_maker):
    expires = datetime.now(UTC) + timedelta(days=20)
    old = await make_user(
        session_maker, rc_app_user_id="old_id", is_premium=True, premium_expires_at=expires
    )
    new = await make_user(session_maker, rc_app_user_id="new_id")
    resp = await send(client, transfer(["old_id"], ["new_id"]))
    assert resp.json() == {"status": "ok", "bucket": "transfer"}
    assert (await reload(session_maker, old)).is_premium is False
    new_row = await reload(session_maker, new)
    assert new_row.is_premium is True
    assert new_row.premium_expires_at.replace(tzinfo=UTC) == expires


async def test_transfer_keeps_lifetime(client, session_maker):
    await make_user(session_maker, rc_app_user_id="old_id", is_premium=True)
    new = await make_user(session_maker, rc_app_user_id="new_id")
    await send(client, transfer(["old_id"], ["new_id"]))
    row = await reload(session_maker, new)
    assert (row.is_premium, row.premium_expires_at) == (True, None)


async def test_transfer_to_unbound_id_only_revokes(client, session_maker):
    old = await make_user(session_maker, rc_app_user_id="old_id", is_premium=True)
    await send(client, transfer(["old_id"], ["nobody"]))
    row = await reload(session_maker, old)
    assert (row.rc_app_user_id, row.is_premium) == ("old_id", False)


async def test_transfer_from_free_user_grants_nothing(client, session_maker):
    await make_user(session_maker, rc_app_user_id="old_id")
    new = await make_user(session_maker, rc_app_user_id="new_id")
    await send(client, transfer(["old_id"], ["new_id"]))
    assert (await reload(session_maker, new)).is_premium is False


async def test_stale_transfer_is_ignored(client, session_maker):
    old = await make_user(session_maker, rc_app_user_id="old_id")
    new = await make_user(session_maker, rc_app_user_id="new_id")
    await send(client, payload("RENEWAL", app_user_id="old_id", event_timestamp_ms=EVENT_MS))
    await send(client, payload("RENEWAL", app_user_id="new_id", event_timestamp_ms=EVENT_MS))
    await send(client, transfer(["old_id"], ["new_id"], event_timestamp_ms=EVENT_MS - 1))
    assert (await reload(session_maker, old)).is_premium is True
    assert (await reload(session_maker, new)).is_premium is True


# Failure handling


async def test_database_error_returns_503_so_revenuecat_retries(client, session_maker, monkeypatch):
    from app.services import webhook_processor

    async def lock_timeout(*args, **kwargs):
        raise OperationalError("SELECT ... FOR UPDATE", {}, Exception("lock timeout"))

    user = await make_user(session_maker, rc_app_user_id="rc_1")
    body = payload("INITIAL_PURCHASE")
    monkeypatch.setattr(webhook_processor, "lock_users", lock_timeout)
    resp = await send(client, body)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "webhook_retry"
    assert await event_rows(session_maker) == []

    # The retry succeeds once the database is back.
    monkeypatch.undo()
    monkeypatch.setattr(settings, "revenuecat_webhook_secret", WEBHOOK_SECRET)
    assert (await send(client, body)).json()["status"] == "ok"
    assert (await reload(session_maker, user)).is_premium is True


async def test_rollback_failure_still_returns_503(client, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    async def boom(*args, **kwargs):
        raise RuntimeError("handler failed")

    async def rollback_fails(self):
        raise RuntimeError("rollback failed")

    monkeypatch.setattr("app.api.routes.webhooks.process_event", boom)
    monkeypatch.setattr(AsyncSession, "rollback", rollback_fails)
    assert (await send(client, payload("RENEWAL"))).status_code == 503


async def test_invalid_json_is_ignored_with_200(client):
    headers = {**bearer(), "Content-Type": "application/json"}
    resp = await client.post(URL, content=b"{not json", headers=headers)
    assert resp.json() == {"status": "ignored"}


@pytest.mark.parametrize(
    "body",
    [
        {"api_version": "1.0"},
        {"event": "nope"},
        {"event": {"type": "RENEWAL"}},
        {"event": {"id": "x" * 65, "type": "RENEWAL"}},
        {"event": {"id": "evt", "type": ""}},
        [1, 2, 3],
    ],
)
async def test_unusable_payloads_are_ignored_with_200(client, body):
    resp = await client.post(URL, json=body, headers=bearer())
    assert resp.json() == {"status": "ignored"}


async def test_bad_expiration_does_not_break_processing(client, session_maker):
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await send(client, payload("RENEWAL", expiration_at_ms="soon"))
    assert (await reload(session_maker, user)).is_premium is True


# Side effects


async def test_event_is_stored(client, session_maker):
    body = payload("RENEWAL")
    await send(client, body)
    (row,) = await event_rows(session_maker)
    assert (row.event_id, row.event_type, row.bucket, row.app_user_id) == (
        body["event"]["id"],
        "RENEWAL",
        "sync",
        "rc_1",
    )
    assert row.expiration_at is not None
    assert row.event_at is not None
    assert row.payload is not None


async def test_payload_storage_can_be_disabled(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "store_webhook_payloads", False)
    await send(client, payload("RENEWAL"))
    (row,) = await event_rows(session_maker)
    assert row.payload is None


async def test_entitlement_cache_is_busted(client, redis):
    for key in ("rc_1", "alias_2"):
        await redis.set(cache_key(key), '{"status": "inactive", "expires_at": null}')
    body = payload("RENEWAL")
    body["event"]["aliases"] = ["rc_1", "alias_2"]
    await send(client, body)
    assert await redis.get(cache_key("rc_1")) is None
    assert await redis.get(cache_key("alias_2")) is None


# Pure helpers


def test_classify_buckets():
    bucket = webhook_events.Bucket
    assert webhook_events.classify("INITIAL_PURCHASE") is bucket.SYNC
    assert webhook_events.classify("EXPIRATION") is bucket.EXPIRE
    assert webhook_events.classify("TRANSFER") is bucket.TRANSFER
    assert webhook_events.classify("BILLING_ISSUE") is bucket.INFO_ONLY
    assert webhook_events.classify("SUBSCRIPTION_PAUSED") is bucket.INFO_ONLY
    assert webhook_events.classify("TEST") is bucket.UNKNOWN
    assert webhook_events.classify("") is bucket.UNKNOWN


def test_parse_event_dedupes_ids_in_order():
    raw = {
        "id": "e",
        "type": "renewal",
        "app_user_id": "a",
        "original_app_user_id": "b",
        "aliases": ["b", "a", "c", 7],
    }
    event = webhook_events.parse_event(raw)
    assert event is not None
    assert event.app_user_ids == ("a", "b", "c")
    assert event.type == "RENEWAL"
    assert event.environment == "PRODUCTION"


@pytest.mark.parametrize("raw", [None, "", 0, True, "soon", 10**20])
def test_parse_ms_rejects_garbage(raw):
    assert webhook_events.parse_ms(raw) is None
