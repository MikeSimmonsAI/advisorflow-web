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


# ── Tenant isolation on inbound SMS (POC on the platform account, customers on their own) ──

def _other_org(db, sid, token):
    from app.models.models import Organization
    from app.utils.crypto import encrypt_value
    o = Organization(name="Other Customer %s" % sid[-4:], slug="other-%s" % sid[-4:].lower(), plan="standard",
                     industry="funeral", org_twilio_account_sid=sid,
                     org_twilio_auth_token_encrypted=encrypt_value(token))
    db.add(o)
    db.commit()
    return o


def test_tenant_signed_sms_cannot_reach_another_orgs_pool_number(db_session, sample_org, twilio_webhook, monkeypatch):
    from app.models.models import Reply
    poc = _other_org(db_session, "ACpoc000000000000000000000000001", "poc-token")
    db_session.add(PhoneNumber(e164="+12052001234", organization_id=poc.id, workspace_id=None,
                               label="pool:pool-205-birmingham", cap_sms=True, cap_voice_inbound=True,
                               cap_voicemail=True, is_active=True))
    db_session.commit()
    data = {"From": "+12145550123", "To": "+12052001234", "Body": "hello", "MessageSid": "SMiso1"}
    # sample_org's own (valid) account signs a message naming the POC org's number: refused
    r = twilio_webhook("/sms/webhook/inbound", data=data)
    assert r.status_code == 403
    assert db_session.query(Reply).count() == 0
    # the owning org's own account is accepted
    ok = twilio_webhook("/sms/webhook/inbound", data=dict(data, MessageSid="SMiso2"),
                        account_sid="ACpoc000000000000000000000000001", auth_token="poc-token")
    assert ok.status_code == 200
    # the PLATFORM account (EvoSys Pro, which buys the POC numbers) is trusted platform-wide
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACplatform00000000000000000000001")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "platform-token")
    pf = twilio_webhook("/sms/webhook/inbound", data=dict(data, MessageSid="SMiso3"),
                        account_sid="ACplatform00000000000000000000001", auth_token="platform-token")
    assert pf.status_code == 200


def test_org_shared_number_still_accepted_for_its_own_account(db_session, sample_org, twilio_webhook):
    from tests.conftest import TEST_ORG_TWILIO_NUMBER
    r = twilio_webhook("/sms/webhook/inbound", data={"From": "+12145550124", "To": TEST_ORG_TWILIO_NUMBER,
                                                     "Body": "hi", "MessageSid": "SMown1"})
    assert r.status_code == 200
