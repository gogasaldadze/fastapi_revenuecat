import uuid
from datetime import timedelta

import jwt

from app.core.config import settings
from app.core.timeutil import utcnow
from tests.conftest import auth, make_user


def token(sub: object, *, expires_in: int = 60, secret: str | None = None) -> str:
    claims = {"sub": sub, "exp": utcnow() + timedelta(seconds=expires_in)}
    return jwt.encode(claims, secret or settings.jwt_secret, algorithm="HS256")


def bearer(value: str) -> dict:
    return {"Authorization": f"Bearer {value}"}


async def test_dev_token_hidden_in_production(client):
    resp = await client.post("/auth/dev_token")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_dev_token_works_in_debug(client, monkeypatch):
    monkeypatch.setattr(settings, "debug", True)
    resp = await client.post("/auth/dev_token")
    assert resp.status_code == 200
    body = resp.json()
    me = await client.get("/me", headers=bearer(body["access_token"]))
    assert me.json() == {"id": body["user_id"], "is_premium": False}


async def test_valid_token(client, session_maker):
    user = await make_user(session_maker)
    resp = await client.get("/me", headers=auth(user))
    assert resp.status_code == 200


async def test_missing_token(client):
    resp = await client.get("/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"


async def test_garbage_token(client):
    assert (await client.get("/me", headers=bearer("not.a.token"))).status_code == 401


async def test_expired_token(client, session_maker):
    user = await make_user(session_maker)
    resp = await client.get("/me", headers=bearer(token(str(user.id), expires_in=-10)))
    assert resp.status_code == 401


async def test_wrong_signature(client, session_maker):
    user = await make_user(session_maker)
    forged = token(str(user.id), secret="another_secret_that_is_long_enough_123")
    assert (await client.get("/me", headers=bearer(forged))).status_code == 401


async def test_subject_not_a_uuid(client):
    assert (await client.get("/me", headers=bearer(token("admin")))).status_code == 401


async def test_unknown_user(client):
    resp = await client.get("/me", headers=bearer(token(str(uuid.uuid4()))))
    assert resp.status_code == 401


async def test_missing_jwt_secret_is_503(client, session_maker, monkeypatch):
    user = await make_user(session_maker)
    headers = auth(user)
    monkeypatch.setattr(settings, "jwt_secret", "")
    resp = await client.get("/me", headers=headers)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "auth_not_configured"
