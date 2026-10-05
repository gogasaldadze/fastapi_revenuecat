from datetime import UTC, datetime, timedelta

from app.db.models import User
from app.services.subscription_state import forward_expiry, grant, is_stale, revoke

T0 = datetime(2030, 1, 1, tzinfo=UTC)
LATER = T0 + timedelta(days=30)


def user(**kwargs) -> User:
    return User(**{"is_premium": False, "premium_expires_at": None, **kwargs})


def test_first_grant_takes_new_expiry():
    assert forward_expiry(user(), T0) == T0


def test_expiry_moves_forward():
    assert forward_expiry(user(is_premium=True, premium_expires_at=T0), LATER) == LATER


def test_expiry_does_not_move_backwards():
    assert forward_expiry(user(is_premium=True, premium_expires_at=LATER), T0) == LATER


def test_lifetime_is_kept():
    assert forward_expiry(user(is_premium=True), T0) is None


def test_new_lifetime_wins():
    assert forward_expiry(user(is_premium=True, premium_expires_at=T0), None) is None


def test_naive_stored_expiry_is_treated_as_utc():
    stored = LATER.replace(tzinfo=None)
    assert forward_expiry(user(is_premium=True, premium_expires_at=stored), T0) == LATER


def test_staleness():
    row = user(rc_last_event_at=LATER)
    assert is_stale(row, T0) is True
    assert is_stale(row, LATER) is False
    assert is_stale(row, None) is False
    assert is_stale(user(), T0) is False


def test_grant_and_revoke_track_event_time():
    row = user()
    grant(row, T0, event_at=T0)
    assert (row.is_premium, row.premium_expires_at, row.rc_last_event_at) == (True, T0, T0)
    revoke(row, event_at=LATER)
    assert (row.is_premium, row.premium_expires_at, row.rc_last_event_at) == (False, T0, LATER)


def test_grant_without_event_time_leaves_marker():
    row = user(rc_last_event_at=T0)
    grant(row, LATER)
    assert row.rc_last_event_at == T0
