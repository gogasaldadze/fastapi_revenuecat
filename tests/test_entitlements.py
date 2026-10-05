import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.db.models import User
from app.services.entitlement_cache import cache_key
from app.services.entitlements import (
    EntitlementStatus,
    check_entitlement,
    fetch_entitlement,
    read_entitlement,
)
from app.services.premium import normalize_app_user_id, premium_for_user
from tests.rc_fake import ENTITLEMENT, active, expired, future, past, subscriber

NOW = datetime.now(UTC)


def read(body: dict, *, allow_sandbox: bool = False):
    return read_entitlement(body["subscriber"], ENTITLEMENT, NOW, allow_sandbox=allow_sandbox)


# Parsing a subscriber object


def test_active_with_future_expiry():
    result = read(active())
    assert result.status is EntitlementStatus.ACTIVE
    assert result.expires_at > NOW


def test_expired_is_inactive():
    assert read(expired()).status is EntitlementStatus.INACTIVE


def test_null_expiry_is_lifetime():
    result = read(subscriber(None))
    assert result.status is EntitlementStatus.ACTIVE
    assert result.expires_at is None


def test_grace_period_counts():
    result = read(subscriber(past(), grace=future(3)))
    assert result.status is EntitlementStatus.ACTIVE
    assert result.expires_at > NOW


def test_expired_grace_period_is_inactive():
    assert read(subscriber(past(5), grace=past(1))).status is EntitlementStatus.INACTIVE


def test_missing_entitlement_is_inactive():
    assert read(subscriber(has_entitlement=False)).status is EntitlementStatus.INACTIVE


def test_other_entitlement_is_inactive():
    assert read(subscriber(future(), entitlement="other")).status is EntitlementStatus.INACTIVE


def test_sandbox_rejected_by_default():
    assert read(subscriber(future(), sandbox=True)).status is EntitlementStatus.INACTIVE


def test_sandbox_allowed_when_configured():
    body = subscriber(future(), sandbox=True)
    assert read(body, allow_sandbox=True).status is EntitlementStatus.ACTIVE


def test_sandbox_lifetime_purchase_rejected():
    body = subscriber(None, product="lifetime")
    body["subscriber"]["subscriptions"] = {}
    body["subscriber"]["non_subscriptions"] = {"lifetime": [{"is_sandbox": True}]}
    assert read(body).status is EntitlementStatus.INACTIVE


def test_unparseable_expiry_is_inactive():
    assert read(subscriber("not a date")).status is EntitlementStatus.INACTIVE


# HTTP status mapping


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, active(), EntitlementStatus.ACTIVE),
        (200, expired(), EntitlementStatus.INACTIVE),
        (404, {"code": 7259}, EntitlementStatus.NOT_FOUND),
        (500, {}, EntitlementStatus.UNKNOWN),
        (401, {}, EntitlementStatus.UNKNOWN),
        (429, {}, EntitlementStatus.UNKNOWN),
    ],
)
async def test_fetch_status_mapping(rc, http, status, body, expected):
    rc.set("u1", status, body)
    assert (await fetch_entitlement(http, "u1")).status is expected


async def test_network_error_is_unknown(rc, http):
    rc.network_error = True
    assert (await fetch_entitlement(http, "u1")).status is EntitlementStatus.UNKNOWN
    assert rc.calls == ["u1", "u1"]


async def test_server_error_is_retried_once(http):
    import httpx
    import respx

    with respx.mock:
        route = respx.get(url__startswith="https://api.revenuecat.com").mock(
            side_effect=[httpx.Response(502), httpx.Response(200, json=active())]
        )
        assert (await fetch_entitlement(http, "u1")).active
        assert route.call_count == 2


async def test_client_errors_are_not_retried(rc, http):
    rc.set("u1", 401, {})
    await fetch_entitlement(http, "u1")
    assert rc.calls == ["u1"]


async def test_missing_secret_key_is_unknown(rc, http, monkeypatch):
    monkeypatch.setattr(settings, "revenuecat_secret_key", "")
    assert (await fetch_entitlement(http, "u1")).status is EntitlementStatus.UNKNOWN
    assert rc.calls == []


async def test_non_json_body_is_unknown(http):
    import httpx
    import respx

    with respx.mock:
        respx.get(url__startswith="https://api.revenuecat.com").mock(
            return_value=httpx.Response(200, content=b"<html>")
        )
        assert (await fetch_entitlement(http, "u1")).status is EntitlementStatus.UNKNOWN


async def test_id_is_url_encoded(rc, http):
    rc.set("$RCAnonymousID:a/b", 200, active())
    assert (await fetch_entitlement(http, "$RCAnonymousID:a/b")).active


# Cache


async def test_active_result_is_cached(rc, http, redis):
    rc.set("u1", 200, active())
    await check_entitlement(http, redis, "u1")
    await check_entitlement(http, redis, "u1")
    assert rc.calls == ["u1"]


async def test_not_found_is_cached(rc, http, redis):
    await check_entitlement(http, redis, "u1")
    result = await check_entitlement(http, redis, "u1")
    assert result.status is EntitlementStatus.NOT_FOUND
    assert rc.calls == ["u1"]


async def test_unknown_is_never_cached(rc, http, redis):
    rc.set("u1", 503, {})
    await check_entitlement(http, redis, "u1")
    await check_entitlement(http, redis, "u1")
    # One retry per check, and nothing cached in between.
    assert rc.calls == ["u1"] * 4
    assert await redis.get(cache_key("u1")) is None


async def test_fresh_skips_cache(rc, http, redis):
    rc.set("u1", 200, expired())
    await check_entitlement(http, redis, "u1")
    rc.set("u1", 200, active())
    assert (await check_entitlement(http, redis, "u1", fresh=True)).active
    assert len(rc.calls) == 2


async def test_cache_ttl_capped_by_expiry(rc, http, redis):
    soon = (datetime.now(UTC) + timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rc.set("u1", 200, subscriber(soon))
    await check_entitlement(http, redis, "u1")
    assert 0 < await redis.ttl(cache_key("u1")) <= 30


async def test_cache_round_trip_keeps_expiry(rc, http, redis):
    rc.set("u1", 200, active())
    first = await check_entitlement(http, redis, "u1")
    second = await check_entitlement(http, redis, "u1")
    assert second == first


async def test_garbage_in_cache_is_refetched(rc, http, redis):
    await redis.set(cache_key("u1"), json.dumps({"status": "weird"}))
    rc.set("u1", 200, active())
    assert (await check_entitlement(http, redis, "u1")).active
    assert rc.calls == ["u1"]


async def test_redis_down_still_checks(rc, http, broken_redis):
    rc.set("u1", 200, active())
    assert (await check_entitlement(http, broken_redis, "u1")).active


# Premium for a stored user


def user(**kwargs) -> User:
    return User(**{"rc_app_user_id": None, "is_premium": False, **kwargs})


async def test_unbound_user_uses_row(http, redis):
    assert await premium_for_user(http, redis, user(is_premium=True)) is True
    assert await premium_for_user(http, redis, user()) is False


async def test_unbound_user_with_past_expiry_is_not_premium(http, redis):
    row = user(is_premium=True, premium_expires_at=NOW - timedelta(minutes=1))
    assert await premium_for_user(http, redis, row) is False


async def test_bound_user_trusts_revenuecat_over_row(rc, http, redis):
    rc.set("u1", 200, expired())
    assert await premium_for_user(http, redis, user(rc_app_user_id="u1", is_premium=True)) is False


async def test_outage_keeps_prior_premium(rc, http, redis):
    rc.set("u1", 500, {})
    row = user(rc_app_user_id="u1", is_premium=True, premium_expires_at=NOW + timedelta(days=1))
    assert await premium_for_user(http, redis, row) is True


async def test_outage_does_not_grant_premium(rc, http, redis):
    rc.set("u1", 500, {})
    assert await premium_for_user(http, redis, user(rc_app_user_id="u1")) is False


async def test_outage_respects_stored_expiry(rc, http, redis):
    rc.set("u1", 500, {})
    row = user(rc_app_user_id="u1", is_premium=True, premium_expires_at=NOW - timedelta(days=1))
    assert await premium_for_user(http, redis, row) is False


async def test_not_found_fails_closed(rc, http, redis):
    assert await premium_for_user(http, redis, user(rc_app_user_id="u1", is_premium=True)) is False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  abc  ", "abc"),
        ("", None),
        (None, None),
        ("   ", None),
        ("null", None),
        ("Undefined", None),
        ("bad\nid", None),
        ("x" * 256, None),
    ],
)
def test_normalize_app_user_id(raw, expected):
    assert normalize_app_user_id(raw) == expected


async def test_cache_helpers_survive_redis_errors(broken_redis):
    from app.services.entitlement_cache import cache_bust, cache_get, cache_set

    assert await cache_get(broken_redis, "u1") is None
    await cache_set(broken_redis, "u1", {"status": "active"}, 10)
    await cache_bust(broken_redis, ["u1"])
    await cache_bust(broken_redis, [])


async def test_cache_ignores_non_object_json(redis):
    from app.services.entitlement_cache import cache_get

    await redis.set(cache_key("u1"), "[1, 2]")
    assert await cache_get(redis, "u1") is None
    await redis.set(cache_key("u1"), "not json")
    assert await cache_get(redis, "u1") is None
