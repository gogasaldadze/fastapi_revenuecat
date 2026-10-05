"""Sync with REVENUECAT_REQUIRE_USER_ID_MATCH on, the default."""

import logging

from app.core.config import settings
from tests.conftest import auth, make_user, reload
from tests.rc_fake import active, expired, with_ids

URL = "/subscription/sync"


def test_strict_mode_is_the_default():
    assert type(settings).model_fields["revenuecat_require_user_id_match"].default is True


async def test_binds_own_id_without_a_body(client, session_maker, rc):
    user = await make_user(session_maker)
    rc.set(str(user.id), 200, active())
    resp = await client.post(URL, json={}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    assert (await reload(session_maker, user)).rc_app_user_id == str(user.id)


async def test_own_id_in_body_is_fine(client, session_maker, rc):
    user = await make_user(session_maker)
    rc.set(str(user.id), 200, active())
    resp = await client.post(URL, json={"app_user_id": str(user.id)}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    assert rc.calls == [str(user.id)]


async def test_someone_elses_active_id_is_refused(client, session_maker, rc, caplog):
    victim = await make_user(session_maker, rc_app_user_id="victim_id", is_premium=True)
    attacker = await make_user(session_maker)
    rc.set("victim_id", 200, with_ids(active(), original="victim_id", aliases=["victim_id"]))
    with caplog.at_level(logging.WARNING):
        resp = await client.post(URL, json={"app_user_id": "victim_id"}, headers=auth(attacker))
    assert resp.json() == {"is_premium": False}
    assert (await reload(session_maker, victim)).rc_app_user_id == "victim_id"
    assert (await reload(session_maker, attacker)).rc_app_user_id is None
    assert "belongs to another customer" in caplog.text


async def test_aliased_anonymous_id_is_accepted(client, session_maker, rc):
    """A purchase made before logIn: RevenueCat aliases the anonymous id to the user."""
    user = await make_user(session_maker)
    anon = "$RCAnonymousID:abc"
    rc.set(anon, 200, with_ids(active(), original=anon, aliases=[anon, str(user.id)]))
    resp = await client.post(URL, json={"app_user_id": anon}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    assert (await reload(session_maker, user)).rc_app_user_id == anon


async def test_original_app_user_id_counts_as_owner(client, session_maker, rc):
    user = await make_user(session_maker)
    rc.set("later_id", 200, with_ids(active(), original=str(user.id)))
    resp = await client.post(URL, json={"app_user_id": "later_id"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}


async def test_refused_claim_falls_back_to_own_id(client, session_maker, rc):
    user = await make_user(session_maker)
    rc.set("someone_else", 200, active())
    rc.set(str(user.id), 200, active())
    resp = await client.post(URL, json={"app_user_id": "someone_else"}, headers=auth(user))
    assert resp.json() == {"is_premium": True}
    assert (await reload(session_maker, user)).rc_app_user_id == str(user.id)


async def test_own_id_not_active(client, session_maker, rc):
    user = await make_user(session_maker)
    rc.set(str(user.id), 200, expired())
    resp = await client.post(URL, json={}, headers=auth(user))
    assert resp.json() == {"is_premium": False}
    assert (await reload(session_maker, user)).rc_app_user_id is None


async def test_own_id_never_purchased(client, session_maker, rc):
    user = await make_user(session_maker)
    assert (await client.post(URL, json={}, headers=auth(user))).json() == {"is_premium": False}


async def test_requires_auth(client):
    assert (await client.post(URL, json={})).status_code == 401
