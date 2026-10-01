"""Human dialer + calling architecture (stream S10, 2026-10-01).

Identity resolution per tenant (public contact vs outreach, no cross-tenant
number), disposition + notes persisted and audited, follow-up task, next-call
queue honouring DNC / suppression / call permission, webhook replay
idempotency, and permission checks.

NOTHING HERE REACHES TWILIO: telephony_twilio.create_call is replaced with a
recorder and the conftest refuses to construct a real twilio Client.
"""
from datetime import datetime, timedelta

import pytest

import app.models.telephony_models  # noqa: F401  (tables for create_all)
from app.models.models import AuditLogEntry, Lead, LeadStatus, Organization, User, VoiceCall
from app.models.telephony_models import InboundCallLog
from app.models.work_models import LeadTask
from app.services import number_resolution as NR
from app.services import telephony_twilio as TT
from app.services.auth_service import create_access_token, hash_password
from tests.conftest import TEST_ORG_TWILIO_NUMBER

ORG_B_SID = "ACtestB0000000000000000000000001"
ORG_B_TOKEN = "org-b-test-token-not-a-secret-2"
ORG_B_NUMBER = "+19998886667"


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://api.example.test")


@pytest.fixture()
def recorder(monkeypatch):
    calls = {"create": []}

    def _create(creds, **params):
        calls["create"].append((creds, params))
        return "CAfake%03d" % len(calls["create"])

    monkeypatch.setattr(TT, "create_call", _create)
    monkeypatch.setattr(TT, "update_call", lambda *a, **k: None)
    return calls


@pytest.fixture()
def org_b(db_session):
    from app.utils.crypto import encrypt_value
    org = Organization(name="Other Home", slug="other-home-s10", plan="standard",
                       org_twilio_account_sid=ORG_B_SID,
                       org_twilio_auth_token_encrypted=encrypt_value(ORG_B_TOKEN),
                       org_twilio_phone_number=ORG_B_NUMBER, org_phone="(972) 555-0101")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="b-s10@other.com", password_hash=hash_password("x"),
             full_name="Other Advisor", role="advisor", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="Bea", last_name="Other",
                phone="+12145558888")
    db_session.add(lead)
    db_session.commit()
    return {"org": org, "user": u, "lead": lead}


@pytest.fixture()
def advisor2(db_session, sample_org):
    u = User(organization_id=sample_org.id, email="advisor2-s10@restland.com",
             password_hash=hash_password("x"), full_name="Advisor Two", role="advisor",
             must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


def _lead(db, org, owner, phone, **kw):
    l = Lead(organization_id=org.id, assigned_to_id=owner.id, first_name=kw.pop("first", "Q"),
             last_name=kw.pop("last", "Lead"), phone=phone, status=kw.pop("status", LeadStatus.NEW), **kw)
    db.add(l)
    db.commit()
    return l


def _call(db, org, lead, advisor, **kw):
    c = VoiceCall(lead_id=lead.id, advisor_id=advisor.id, organization_id=org.id,
                  to_phone=lead.phone, from_phone=TEST_ORG_TWILIO_NUMBER,
                  status=kw.pop("status", "completed"), direction="outbound", provider="twilio", created_at=datetime.utcnow(), **kw)
    db.add(c)
    db.commit()
    return c


# ── 1. identity resolution per tenant ───────────────────────────────────────

def test_identity_is_per_tenant_and_public_is_separate(client, db_session, sample_org,
                                                       sample_advisor, auth_headers, org_b):
    a = client.get("/dialer/identity", headers=auth_headers).json()
    assert a["organization_id"] == sample_org.id
    assert a["voice_outbound"]["e164"] == TEST_ORG_TWILIO_NUMBER
    assert a["voice_outbound"]["provider_ready"] is True and a["calling_mode"] == "provider_bridge"
    # No public number set: said so, and the outreach number is NOT reused for it.
    assert a["public_contact"]["ok"] is False and a["public_contact"]["display"] == "Not configured"
    b = client.get("/dialer/identity", headers=_h(db_session, org_b["user"])).json()
    assert b["organization_id"] == org_b["org"].id
    assert b["voice_outbound"]["e164"] == ORG_B_NUMBER
    assert b["public_contact"]["e164"] == "+19725550101"
    assert TEST_ORG_TWILIO_NUMBER not in str(b) and ORG_B_NUMBER not in str(a)
    # No secret leaves the endpoint.
    assert ORG_B_TOKEN not in str(b) and ORG_B_SID not in str(b)


def test_identity_not_configured_offers_device_fallback(client, db_session, sample_org,
                                                        auth_headers):
    sample_org.org_twilio_phone_number = None
    db_session.commit()
    r = client.get("/dialer/identity", headers=auth_headers).json()
    assert r["voice_outbound"]["configured"] is False
    assert r["voice_outbound"]["display"] == "Not configured" and r["voice_outbound"]["reason"]
    assert r["calling_mode"] == "device_tel_fallback"


def test_communication_identity_never_reads_another_orgs_user_sender(db_session, sample_org,
                                                                     org_b):
    # An org-B user passed while resolving org A: the SMS sender is not looked up.
    ident = NR.communication_identity(db_session, sample_org, user=org_b["user"])
    assert ident["sms_outbound"]["from_number"] is None
    assert ident["voice_outbound"]["e164"] == TEST_ORG_TWILIO_NUMBER


def test_no_person_or_business_literal_in_voice_prompt_defaults():
    from app.services.voice_service import build_voice_system_prompt
    p = build_voice_system_prompt({"first_name": "Ann"}, {}, 1)
    assert "Mike Simmons" not in p and "Greenland" not in p


def test_ai_callback_number_is_public_contact_not_user_sender(db_session, sample_org):
    sample_org.org_phone = "214-555-0142"
    db_session.commit()
    pub = NR.resolve_public_contact_number(db_session, sample_org.id)
    assert pub["e164"] == "+12145550142"
    assert pub["e164"] != NR.resolve_voice_number(db_session, sample_org).e164


# ── 2. disposition + notes saved and audited ───────────────────────────────

def test_disposition_notes_persisted_audited_and_follow_up(client, db_session, sample_org,
                                                          sample_lead, sample_advisor, auth_headers):
    c = _call(db_session, sample_org, sample_lead, sample_advisor, is_human_call=True)
    r = client.post("/calls/%s/disposition" % c.id, headers=auth_headers,
                    json={"outcome": "interested", "notes": "Asked about pricing",
                          "follow_up": True, "follow_up_title": "Send price sheet"})
    assert r.status_code == 200, r.text
    db_session.refresh(c)
    assert c.disposition == "interested" and c.disposition_notes == "Asked about pricing"
    assert c.disposition_by_id == sample_advisor.id and c.disposition_at is not None
    t = db_session.query(LeadTask).one()
    assert t.title == "Send price sheet" and t.lead_id == sample_lead.id
    assert t.organization_id == sample_org.id and t.source == "call_follow_up"
    a = db_session.query(AuditLogEntry).filter(AuditLogEntry.action == "voice.call_disposition").one()
    assert a.organization_id == sample_org.id and a.target_id == c.id
    assert a.actor_user_id == sample_advisor.id


# ── 3. next-call queue ─────────────────────────────────────────────────────

def test_queue_excludes_dnc_suppressed_and_no_call(client, db_session, sample_org, sample_lead,
                                                   sample_advisor, auth_headers, advisor2):
    from app.services.compliance_service import add_suppression_entry
    ok = _lead(db_session, sample_org, sample_advisor, "+12145550201", first="Okay")
    _lead(db_session, sample_org, sample_advisor, "+12145550202", first="Dnc", status=LeadStatus.DNC)
    sup = _lead(db_session, sample_org, sample_advisor, "+12145550203", first="Supp")
    add_suppression_entry(db_session, sample_org.id, sup.phone, "test")
    _lead(db_session, sample_org, sample_advisor, "+12145550204", first="NoVoice", allow_voice=False)
    wrong = _lead(db_session, sample_org, sample_advisor, "+12145550205", first="Wrong")
    _call(db_session, sample_org, wrong, sample_advisor, disposition="wrong_number")
    _lead(db_session, sample_org, advisor2, "+12145550206", first="NotMine")
    db_session.commit()
    r = client.get("/dialer/queue", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {i["lead_id"] for i in body["items"]}
    assert ids == {sample_lead.id, ok.id}
    assert sum(e["count"] for e in body["excluded"]) == 4
    # A due follow-up goes to the top.
    db_session.add(LeadTask(organization_id=sample_org.id, lead_id=ok.id, title="call",
                            due_at=datetime.utcnow() - timedelta(minutes=5), status="open"))
    db_session.commit()
    first = client.get("/dialer/queue", headers=auth_headers).json()["items"][0]
    assert first["lead_id"] == ok.id and first["reason"] == "Follow-up due"


# ── 4. webhook replay idempotency ──────────────────────────────────────────

def test_inbound_same_callsid_twice_is_one_record(twilio_webhook, db_session, sample_org,
                                                  sample_lead):
    for _ in range(2):
        r = twilio_webhook("/voice/inbound", {"To": TEST_ORG_TWILIO_NUMBER, "From": "+12145559999",
                                              "CallSid": "CA-REPLAY-1"})
        assert r.status_code == 200
    assert db_session.query(InboundCallLog).filter_by(call_sid="CA-REPLAY-1").count() == 1
    assert db_session.query(VoiceCall).filter_by(call_sid="CA-REPLAY-1").count() == 1


def test_dial_status_replay_does_not_duplicate(twilio_webhook, client, db_session, sample_org,
                                               sample_lead, sample_advisor, auth_headers):
    c = _call(db_session, sample_org, sample_lead, sample_advisor, is_human_call=True,
              status="in_progress", call_sid="CAfake900")
    for _ in range(2):
        twilio_webhook("/voice/human/dial-status?call_id=%s" % c.id,
                       {"CallSid": "CAfake900", "DialCallStatus": "completed",
                        "DialCallDuration": "61"})
    assert db_session.query(VoiceCall).filter(VoiceCall.lead_id == sample_lead.id).count() == 1
    j = client.get("/calls/%s" % c.id, headers=auth_headers).json()
    assert j["duration_seconds"] == 61 and j["outcome"] == "connected" and j["status"] == "completed"


# ── 5. permissions ─────────────────────────────────────────────────────────

def test_advisor_cannot_touch_another_advisors_lead_or_call(client, db_session, sample_org,
                                                            sample_lead, sample_advisor, advisor2,
                                                            org_b, recorder):
    c = _call(db_session, sample_org, sample_lead, sample_advisor, is_human_call=True)
    h2 = _h(db_session, advisor2)
    assert client.get("/dialer/leads/%s/history" % sample_lead.id, headers=h2).status_code == 404
    assert client.post("/dialer/calls/manual", json={"lead_id": sample_lead.id},
                       headers=h2).status_code == 404
    assert client.post("/calls/human", json={"lead_id": sample_lead.id},
                       headers=h2).status_code == 404
    assert client.post("/calls/%s/disposition" % c.id, json={"outcome": "connected"},
                       headers=h2).status_code == 404
    hb = _h(db_session, org_b["user"])
    assert client.get("/dialer/leads/%s/history" % sample_lead.id, headers=hb).status_code == 404
    assert recorder["create"] == []


# ── 6. tel: fallback log + history + voicemail state ───────────────────────

def test_manual_call_log_history_and_voicemail_state(client, db_session, sample_org, sample_lead,
                                                     sample_advisor, auth_headers, recorder):
    r = client.post("/dialer/calls/manual", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["provider"] == "manual" and m["from_phone"] is None and m["advisor_id"] == sample_advisor.id
    assert recorder["create"] == []                  # no provider call
    r = client.post("/calls/%s/disposition" % m["id"], headers=auth_headers,
                    json={"outcome": "left_voicemail", "notes": "VM 2pm"})
    assert r.json()["call"]["voicemail_state"] == "left"
    h = client.get("/dialer/leads/%s/history" % sample_lead.id, headers=auth_headers).json()
    assert h["calls"][0]["id"] == m["id"] and h["calls"][0]["disposition_notes"] == "VM 2pm"
    assert db_session.query(AuditLogEntry).filter(
        AuditLogEntry.action == "voice.manual_call_logged").count() == 1


def test_manual_call_log_refused_for_dnc(client, db_session, sample_lead, auth_headers):
    sample_lead.status = LeadStatus.DNC
    db_session.commit()
    r = client.post("/dialer/calls/manual", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 409
    assert db_session.query(VoiceCall).count() == 0
