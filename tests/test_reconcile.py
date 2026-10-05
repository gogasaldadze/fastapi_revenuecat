import json

from app.services.reconcile import reconcile
from scripts import reconcile as reconcile_script
from tests.conftest import make_user, reload
from tests.rc_fake import active, expired


async def test_reports_drift_without_fixing(session_maker, http, rc):
    rc.set("paying", 200, active())
    rc.set("lapsed", 200, expired())
    rc.set("fine", 200, active())
    missed_purchase = await make_user(session_maker, rc_app_user_id="paying")
    missed_expiry = await make_user(session_maker, rc_app_user_id="lapsed", is_premium=True)
    await make_user(session_maker, rc_app_user_id="fine", is_premium=True)
    await make_user(session_maker)

    report = await reconcile(session_maker, http, batch_size=2)

    assert report.checked == 3
    assert {d.app_user_id for d in report.drift} == {"paying", "lapsed"}
    assert report.fixed == 0
    assert (await reload(session_maker, missed_purchase)).is_premium is False
    assert (await reload(session_maker, missed_expiry)).is_premium is True


async def test_fix_applies_revenuecat_state(session_maker, http, rc):
    rc.set("paying", 200, active())
    rc.set("lapsed", 200, expired())
    missed_purchase = await make_user(session_maker, rc_app_user_id="paying")
    missed_expiry = await make_user(session_maker, rc_app_user_id="lapsed", is_premium=True)

    report = await reconcile(session_maker, http, fix=True)

    assert report.fixed == 2
    row = await reload(session_maker, missed_purchase)
    assert row.is_premium is True
    assert row.premium_expires_at is not None
    assert (await reload(session_maker, missed_expiry)).is_premium is False


async def test_unknown_is_skipped(session_maker, http, rc):
    rc.set("u1", 503, {})
    await make_user(session_maker, rc_app_user_id="u1", is_premium=True)
    report = await reconcile(session_maker, http, fix=True)
    assert (report.checked, report.unknown, report.drift) == (1, 1, [])


async def test_fix_skips_rows_rebound_meanwhile(session_maker, http, rc, monkeypatch):
    import app.services.reconcile as module

    rc.set("paying", 200, active())
    user = await make_user(session_maker, rc_app_user_id="paying")
    real_fetch = module.fetch_entitlement

    async def fetch_then_rebind(client, app_user_id):
        result = await real_fetch(client, app_user_id)
        async with session_maker() as s:
            row = await s.get(type(user), user.id)
            row.rc_app_user_id = "someone_new"
            await s.commit()
        return result

    monkeypatch.setattr(module, "fetch_entitlement", fetch_then_rebind)
    report = await reconcile(session_maker, http, fix=True)
    assert len(report.drift) == 1
    assert report.fixed == 0


async def test_script_prints_summary_and_exit_code(session_maker, rc, monkeypatch, capsys):
    rc.set("paying", 200, active())
    await make_user(session_maker, rc_app_user_id="paying")
    monkeypatch.setattr(reconcile_script, "SessionLocal", session_maker)

    assert await reconcile_script.main([]) == 1
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary == {"checked": 1, "unknown": 0, "drift": 1, "fixed": 0}

    assert await reconcile_script.main(["--fix"]) == 1
    assert await reconcile_script.main([]) == 0
