from datetime import timedelta

import pytest

from app.api.deps import charge
from app.core.config import settings
from app.core.timeutil import utcnow
from app.services.quota import QUOTA_TTL_SECONDS, consume_units, quota_key, utc_day
from tests.conftest import auth, make_user
from tests.rc_fake import active, expired

PREMIUM = "/examples/premium"
BASIC = "/examples/basic"
HEAVY = "/examples/heavy"


# require_premium


async def test_free_user_gets_403(client, session_maker):
    user = await make_user(session_maker)
    resp = await client.get(PREMIUM, headers=auth(user))
    assert resp.status_code == 403
    assert resp.json() == {
        "error": {"code": "premium_required", "message": "Premium subscription required"}
    }


async def test_verified_user_gets_200(client, session_maker, rc):
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    assert (await client.get(PREMIUM, headers=auth(user))).status_code == 200


async def test_expired_subscription_gets_403(client, session_maker, rc):
    rc.set("rc_1", 200, expired())
    user = await make_user(session_maker, rc_app_user_id="rc_1", is_premium=True)
    assert (await client.get(PREMIUM, headers=auth(user))).status_code == 403


async def test_manually_granted_user_gets_200(client, session_maker):
    user = await make_user(session_maker, is_premium=True)
    assert (await client.get(PREMIUM, headers=auth(user))).status_code == 200


async def test_manual_grant_respects_expiry(client, session_maker):
    expires = utcnow() - timedelta(hours=1)
    user = await make_user(session_maker, is_premium=True, premium_expires_at=expires)
    assert (await client.get(PREMIUM, headers=auth(user))).status_code == 403


async def test_client_header_cannot_claim_premium(client, session_maker, rc):
    rc.set("someone_elses_id", 200, active())
    user = await make_user(session_maker)
    headers = {**auth(user), "X-RC-User-ID": "someone_elses_id", "X-Is-Premium": "true"}
    assert (await client.get(PREMIUM, headers=headers)).status_code == 403


async def test_revenuecat_checked_once_per_request(client, session_maker, rc, redis):
    rc.set("rc_1", 200, active())
    user = await make_user(session_maker, rc_app_user_id="rc_1")
    await client.post(HEAVY, headers=auth(user))
    assert rc.calls == ["rc_1"]


# charge()


async def test_basic_charges_weight(client, session_maker, redis):
    user = await make_user(session_maker)
    await client.post(BASIC, headers=auth(user))
    await client.post(BASIC, headers=auth(user))
    key = quota_key(str(user.id), utc_day())
    assert await redis.get(key) == "2"
    assert 0 < await redis.ttl(key) <= QUOTA_TTL_SECONDS


async def test_free_limit_returns_429(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "free_daily_units", 3)
    user = await make_user(session_maker)
    codes = [(await client.post(BASIC, headers=auth(user))).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    resp = await client.post(BASIC, headers=auth(user))
    assert resp.json()["error"]["code"] == "daily_limit_reached"


async def test_denied_request_is_refunded(client, session_maker, redis, monkeypatch):
    monkeypatch.setattr(settings, "free_daily_units", 1)
    user = await make_user(session_maker)
    await client.post(BASIC, headers=auth(user))
    await client.post(BASIC, headers=auth(user))
    assert await redis.get(quota_key(str(user.id), utc_day())) == "1"


async def test_premium_gets_higher_limit(client, session_maker, monkeypatch):
    monkeypatch.setattr(settings, "free_daily_units", 1)
    user = await make_user(session_maker, is_premium=True)
    codes = [(await client.post(BASIC, headers=auth(user))).status_code for _ in range(3)]
    assert codes == [200, 200, 200]


async def test_heavy_uses_its_weight(client, session_maker, redis, monkeypatch):
    monkeypatch.setattr(settings, "premium_daily_units", 25)
    user = await make_user(session_maker, is_premium=True)
    codes = [(await client.post(HEAVY, headers=auth(user))).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert await redis.get(quota_key(str(user.id), utc_day())) == "20"


async def test_free_user_is_not_charged_for_premium_route(client, session_maker, redis):
    user = await make_user(session_maker)
    assert (await client.post(HEAVY, headers=auth(user))).status_code == 403
    assert await redis.get(quota_key(str(user.id), utc_day())) is None


@pytest.fixture
async def client_without_redis(client, broken_redis):
    from app.core.clients import get_redis
    from app.main import app

    app.dependency_overrides[get_redis] = lambda: broken_redis
    return client


async def test_redis_down_fails_open_for_cheap_feature(client_without_redis, session_maker):
    user = await make_user(session_maker)
    assert (await client_without_redis.post(BASIC, headers=auth(user))).status_code == 200


async def test_redis_down_fails_closed_for_costly_feature(client_without_redis, session_maker):
    user = await make_user(session_maker, is_premium=True)
    resp = await client_without_redis.post(HEAVY, headers=auth(user))
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"


def test_unknown_feature_fails_at_startup():
    with pytest.raises(ValueError, match="No weight configured"):
        charge("does_not_exist")


async def test_refund_failure_is_logged(redis, monkeypatch, caplog):
    async def broken_decrby(*args, **kwargs):
        raise ConnectionError("gone")

    monkeypatch.setattr(redis, "decrby", broken_decrby)
    assert await consume_units(redis, "u", 5, 1) is False
    assert "Quota refund failed" in caplog.text
