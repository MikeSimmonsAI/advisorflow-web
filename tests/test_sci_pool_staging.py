"""SCI regional pool numbers on staging: inbound routing proof + number recording.

No Twilio call, no purchase, no send. The pool simulation runs the real inbound
SMS handler with the pool id the webhook passes for a "pool:" number; the
pool-number endpoints only record numbers Mike has ALREADY bought.
"""
from unittest.mock import patch

from app.models.models import Platform
from app.models.telephony_models import PhoneNumber
from app.services.programs import campuses
from tests.test_outreach_program import _god, _h  # noqa: F401


def _seed(client, db, monkeypatch, sims=True):
    monkeypatch.setenv("STAGING_TEST_HARNESS", "on")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("API_BASE_URL", "https://sci-staging-backend.onrender.com")
    if not db.query(Platform).filter_by(slug="evosyspro").first():
        db.add(Platform(id="plt-evosyspro-pool", name="EvoSys Pro", slug="evosyspro",
                        support_email="support@evosyspro.live"))
        db.commit()
    h = _h(db, _god(db))
    client.post("/god/staging/sci/seed", headers=h, json={"test_email": "tester@example.net",
                                                          "simulation_contacts": sims})
    return h


def test_pool_round_trip_routes_by_contact_never_by_number(client, db_session, monkeypatch):
    h = _seed(client, db_session, monkeypatch)
    with patch("app.services.sms_service.Client") as tw:
        out = client.post("/god/staging/sci/simulate/pool_round_trip", headers=h).json()
        tw.assert_not_called()
    assert out["sent"] == 0
    assert out["pool"]["pool_id"] == "pool-205-birmingham"
    k = out["known_sender"]
    assert k["reply_attached"] == 1 and k["location_is_contacts_own"] is True
    u = out["unknown_sender"]
    assert u["result"]["status"] == "no_lead"
    assert u["queued"] is not None and u["queued"]["location_id"] is None and u["queued"]["status"] == "open"
    assert out["stop_from_unknown"]["suppressed"] is True
    # the simulation creates no phone number row
    assert db_session.query(PhoneNumber).filter(PhoneNumber.label.like("pool:%")).count() == 0


def test_pool_numbers_dry_run_validate_apply_and_idempotent(client, db_session, monkeypatch):
    h = _seed(client, db_session, monkeypatch, sims=False)
    g = client.get("/god/staging/sci/pool-numbers", headers=h).json()
    assert [p["area_code"] for p in g["pools"]] == ["205", "334", "850", "251", "706", "318"]
    assert all(p["status"] == "not provisioned" for p in g["pools"])
    assert g["targets"]["sms_url"].endswith("/sms/webhook/inbound")
    assert g["targets"]["voice_url"].endswith("/voice/inbound")

    bad = client.post("/god/staging/sci/pool-numbers", headers=h,
                      json={"numbers": {"205": "+13345550123", "334": "+13345550123", "999": "+19995550000"},
                            "apply": True})
    assert bad.status_code == 400 and bad.json()["detail"]["nothing_written"] is True
    assert db_session.query(PhoneNumber).filter(PhoneNumber.label.like("pool:%")).count() == 0

    good = {"205": {"e164": "+12052001234", "sid": "PNtest205"}, "850": "+18502001234"}
    dry = client.post("/god/staging/sci/pool-numbers", headers=h, json={"numbers": good}).json()
    assert dry["dry_run"] is True and [p["action"] for p in dry["plan"]] == ["create", "create"]
    assert db_session.query(PhoneNumber).filter(PhoneNumber.label.like("pool:%")).count() == 0

    applied = client.post("/god/staging/sci/pool-numbers", headers=h, json={"numbers": good, "apply": True}).json()
    assert applied["dry_run"] is False
    rows = db_session.query(PhoneNumber).filter(PhoneNumber.label.like("pool:%")).all()
    assert len(rows) == 2
    for r in rows:
        assert r.workspace_id is None and r.cap_sms and r.cap_voice_inbound and r.cap_voicemail
        assert not r.cap_voice_outbound
    again = client.post("/god/staging/sci/pool-numbers", headers=h, json={"numbers": good, "apply": True}).json()
    assert [p["action"] for p in again["plan"]] == ["unchanged", "unchanged"]
    assert db_session.query(PhoneNumber).filter(PhoneNumber.label.like("pool:%")).count() == 2

    g2 = client.get("/god/staging/sci/pool-numbers", headers=h).json()
    st = {p["area_code"]: p["status"] for p in g2["pools"]}
    assert st["205"] == "provisioned" and st["850"] == "provisioned" and st["334"] == "not provisioned"
    org_id = rows[0].organization_id
    pooled = {r["pool_id"] for r in campuses.plan(db_session, org_id) if r.get("number_status") == "pooled"}
    assert {"pool-205-birmingham", "pool-850-pensacola"} <= pooled


def test_pool_numbers_refuse_outside_staging(client, db_session, monkeypatch):
    h = _seed(client, db_session, monkeypatch, sims=False)
    monkeypatch.setenv("APP_ENV", "production")
    assert client.get("/god/staging/sci/pool-numbers", headers=h).status_code == 404
    assert client.post("/god/staging/sci/pool-numbers", headers=h,
                       json={"numbers": {"205": "+12052001234"}, "apply": True}).status_code == 404
