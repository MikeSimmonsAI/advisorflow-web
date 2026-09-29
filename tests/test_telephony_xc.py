"""Stream XC telephony: number resolution, inbound tenant routing, inbound
voicemail, outbound AMD / approved voicemail drop, the human click-to-call
bridge, dispositions, admin number assignment.

NOTHING HERE REACHES TWILIO. Placing, redirecting and fetching go through
app.services.telephony_twilio.{create_call,update_call,fetch_recording}, which
every test that gets that far replaces with a recorder; the conftest refuses to
construct a real twilio Client anywhere in app.*.
"""
from datetime import datetime, timedelta
import json

import pytest

import app.models.telephony_models  # noqa: F401  (tables for create_all)
from app.models.models import Lead, LeadStatus, Organization, Platform, User, VoiceCall
from app.models.telephony_models import (InboundCallLog, OrgVoicemailDrop, PhoneNumber,
                                         TelephonyUserSetting, Voicemail)
from app.models.work_models import LeadTask
from app.services import number_resolution as NR
from app.services import telephony_service as TS
from app.services import telephony_twilio as TT
from app.services.auth_service import create_access_token, hash_password
from tests.conftest import TEST_ORG_TWILIO_NUMBER, TEST_TWILIO_ACCOUNT_SID

ORG_B_SID = "ACtestB0000000000000000000000000"
ORG_B_TOKEN = "org-b-test-token-not-a-secret"
ORG_B_NUMBER = "+19998886666"
CALLER = "+12145559999"          # sample_lead's number, E.164 spelling


def _mount():
    from app.main import app
    from app.routers.telephony_router import router
    if not any(getattr(r, "path", "") == "/voicemails" for r in app.routes):
        app.include_router(router)
    from app.routers.work_router import router as work
    if not any(getattr(r, "path", "") == "/communications/replies" for r in app.routes):
        app.include_router(work)


_mount()


def _re(n):
    """A well-formed Twilio RecordingSid."""
    return "RE%032x" % n


def _code_from(recorder):
    import re as _r
    twiml = recorder["create"][-1][1]["twiml"]
    return "".join(_r.findall(r"\d", twiml.split("code is")[1].split(".")[0]))


def _verify_phone(client, headers, recorder, phone="2145557000"):
    r = client.put("/telephony/me/callback-phone", json={"phone": phone}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post("/telephony/me/callback-phone/verify", json={"code": _code_from(recorder)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    monkeypatch.setenv("API_BASE_URL", "https://api.example.test")


@pytest.fixture()
def recorder(monkeypatch):
    calls = {"create": [], "update": [], "fetch": []}

    def _create(creds, **params):
        calls["create"].append((creds, params))
        return "CAfake%03d" % len(calls["create"])

    def _update(creds, sid, twiml):
        calls["update"].append((creds, sid, twiml))

    def _fetch(creds, url):
        calls["fetch"].append((creds, url))
        return b"ID3fake-mp3", "audio/mpeg"

    monkeypatch.setattr(TT, "create_call", _create)
    monkeypatch.setattr(TT, "update_call", _update)
    monkeypatch.setattr(TT, "fetch_recording", _fetch)
    return calls


@pytest.fixture()
def org_b(db_session):
    from app.utils.crypto import encrypt_value
    org = Organization(name="Other Funeral Home", slug="other-fh", plan="standard",
                       org_twilio_account_sid=ORG_B_SID,
                       org_twilio_auth_token_encrypted=encrypt_value(ORG_B_TOKEN),
                       org_twilio_phone_number=ORG_B_NUMBER)
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="b@other.com", password_hash=hash_password("x"),
             full_name="Other Admin", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="Bea", last_name="Other",
                phone=CALLER)                        # SAME caller number as sample_lead
    db_session.add(lead)
    db_session.commit()
    return {"org": org, "user": u, "lead": lead}


@pytest.fixture()
def god(db_session):
    u = User(organization_id=None, email="god@platform.test", password_hash=hash_password("x"),
             full_name="God", role="god_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture()
def org_admin(db_session, sample_org):
    u = User(organization_id=sample_org.id, email="boss@restland.com", password_hash=hash_password("x"),
             full_name="Boss Admin", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


# ── 1. number resolution ─────────────────────────────────────────────────────

def test_resolver_uses_legacy_org_column_when_no_record(db_session, sample_org):
    r = NR.resolve_voice_number(db_session, sample_org)
    assert r.ok and r.e164 == TEST_ORG_TWILIO_NUMBER and r.source == "org_column"
    assert r.level == "organization"


def test_resolver_precedence_workspace_org_brand_platform(db_session, sample_org):
    plat = Platform(name="Brand", slug="brand-x")
    db_session.add(plat)
    db_session.commit()
    sample_org.platform_id = plat.id
    sample_org.org_twilio_phone_number = None
    db_session.add_all([
        PhoneNumber(e164="+12145550001", cap_voice_outbound=True),                       # platform
        PhoneNumber(e164="+12145550002", platform_id=plat.id, cap_voice_outbound=True),  # brand
    ])
    db_session.commit()
    assert NR.resolve_voice_number(db_session, sample_org).e164 == "+12145550002"
    db_session.add(PhoneNumber(e164="+12145550003", organization_id=sample_org.id,
                               cap_voice_outbound=True))
    db_session.commit()
    assert NR.resolve_voice_number(db_session, sample_org).level == "organization"
    db_session.add(PhoneNumber(e164="+12145550004", organization_id=sample_org.id,
                               workspace_id="loc-1", cap_voice_outbound=True))
    db_session.commit()
    r = NR.resolve_voice_number(db_session, sample_org, workspace_id="loc-1")
    assert r.e164 == "+12145550004" and r.level == "workspace"
    # Without that workspace the org number wins.
    assert NR.resolve_voice_number(db_session, sample_org).e164 == "+12145550003"


def test_resolver_requires_voice_capability_and_active(db_session, sample_org):
    sample_org.org_twilio_phone_number = None
    db_session.add(PhoneNumber(e164="+12145550011", organization_id=sample_org.id, cap_sms=True))
    db_session.add(PhoneNumber(e164="+12145550012", organization_id=sample_org.id,
                               cap_voice_outbound=True, is_active=False))
    db_session.commit()
    r = NR.resolve_voice_number(db_session, sample_org)
    assert not r.ok and r.e164 is None and "No active voice-capable" in r.reason


def test_record_governs_its_own_legacy_number(db_session, sample_org):
    """A deactivated record for the org's legacy number is not bypassed."""
    db_session.add(PhoneNumber(e164=TEST_ORG_TWILIO_NUMBER, organization_id=sample_org.id,
                               cap_voice_outbound=True, is_active=False))
    db_session.commit()
    assert not NR.resolve_voice_number(db_session, sample_org).ok


def test_owner_by_called_number(db_session, sample_org, org_b):
    assert NR.resolve_owner_by_called_number(db_session, TEST_ORG_TWILIO_NUMBER).organization_id == sample_org.id
    assert NR.resolve_owner_by_called_number(db_session, "9998886666").organization_id == org_b["org"].id
    assert NR.resolve_owner_by_called_number(db_session, "+12145550077") is None
    db_session.add(PhoneNumber(e164="+12145550078", cap_voice_inbound=True))   # platform pool
    db_session.commit()
    assert NR.resolve_owner_by_called_number(db_session, "+12145550078") is None


def test_ambiguous_legacy_number_routes_nowhere(db_session, sample_org, org_b):
    org_b["org"].org_twilio_phone_number = TEST_ORG_TWILIO_NUMBER
    db_session.commit()
    assert NR.resolve_owner_by_called_number(db_session, TEST_ORG_TWILIO_NUMBER) is None


def test_no_sender_literal_left_in_voice_code():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for rel in ("routers/voice_router.py", "services/voice_orchestrator.py",
                "services/telephony_service.py", "routers/telephony_router.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "14692241155" not in src and "TWILIO_FROM_NUMBER" not in src, rel


# ── 2. inbound tenant routing ────────────────────────────────────────────────

def _inbound(twilio_webhook, to, frm=CALLER, sid="CAin1", **kw):
    return twilio_webhook("/voice/inbound", {"To": to, "From": frm, "CallSid": sid}, **kw)


def test_same_caller_in_two_orgs_resolves_only_the_called_org(
        twilio_webhook, db_session, sample_org, sample_lead, org_b):
    r = _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-A")
    assert r.status_code == 200, r.text
    a = db_session.query(InboundCallLog).filter(InboundCallLog.call_sid == "CA-A").one()
    assert a.organization_id == sample_org.id and a.lead_id == sample_lead.id
    assert a.caller_state == "known"

    r = _inbound(twilio_webhook, ORG_B_NUMBER, sid="CA-B", account_sid=ORG_B_SID, auth_token=ORG_B_TOKEN)
    assert r.status_code == 200, r.text
    b = db_session.query(InboundCallLog).filter(InboundCallLog.call_sid == "CA-B").one()
    assert b.organization_id == org_b["org"].id and b.lead_id == org_b["lead"].id
    # The inbound VoiceCall rows sit in their own orgs too.
    vcs = db_session.query(VoiceCall).filter(VoiceCall.direction == "inbound").all()
    assert {(v.organization_id, v.lead_id) for v in vcs} == {
        (sample_org.id, sample_lead.id), (org_b["org"].id, org_b["lead"].id)}


def test_account_cannot_route_into_another_orgs_number(twilio_webhook, db_session, sample_org, org_b):
    # Signed with org A's account, claiming org B's number.
    r = _inbound(twilio_webhook, ORG_B_NUMBER, sid="CA-X")
    assert r.status_code == 403
    assert db_session.query(InboundCallLog).count() == 0


def test_unsigned_inbound_is_refused(twilio_webhook, db_session, sample_org):
    assert _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sign=False).status_code == 403
    assert db_session.query(InboundCallLog).count() == 0


def test_unknown_called_number_gets_generic_answer_and_no_lookup(twilio_webhook, db_session, sample_org,
                                                               sample_lead):
    r = _inbound(twilio_webhook, "+12145550099", sid="CA-U")
    assert r.status_code == 200 and "not accepting calls" in r.text
    assert db_session.query(InboundCallLog).count() == 0
    assert db_session.query(VoiceCall).count() == 0


def test_unknown_caller_logged_in_org_and_sent_to_voicemail(twilio_webhook, db_session, sample_org):
    r = _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, frm="+12145550123", sid="CA-N")
    assert r.status_code == 200
    assert "<Record" in r.text and "Restland" in r.text
    row = db_session.query(InboundCallLog).one()
    assert row.organization_id == sample_org.id and row.caller_state == "unknown" and row.lead_id is None
    assert row.status == "voicemail"
    assert db_session.query(Lead).filter(Lead.phone.in_(NR.phone_forms("+12145550123"))).count() == 0


def test_suppressed_and_dnc_callers_are_flagged(twilio_webhook, db_session, sample_org, sample_lead):
    from app.services.compliance_service import add_suppression_entry
    add_suppression_entry(db_session, sample_org.id, "12145550124", "test")
    db_session.commit()
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, frm="+12145550124", sid="CA-S")
    assert db_session.query(InboundCallLog).filter_by(call_sid="CA-S").one().caller_state == "suppressed"
    sample_lead.status = LeadStatus.DNC
    db_session.commit()
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-D")
    assert db_session.query(InboundCallLog).filter_by(call_sid="CA-D").one().caller_state == "dnc"


def test_inbound_retry_is_idempotent(twilio_webhook, db_session, sample_org, sample_lead):
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-R")
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-R")
    assert db_session.query(InboundCallLog).count() == 1
    assert db_session.query(VoiceCall).count() == 1


def test_route_rings_configured_user_then_voicemail(twilio_webhook, db_session, sample_org,
                                                    sample_advisor, sample_lead):
    db_session.add(PhoneNumber(e164="+12145550200", organization_id=sample_org.id,
                               cap_voice_inbound=True, cap_voicemail=True, cap_voice_outbound=True,
                               default_inbound_route=json.dumps({"ring_user_ids": [sample_advisor.id],
                                                                 "timeout_seconds": 15,
                                                                 "greeting_text": "Custom hello"})))
    db_session.add(TelephonyUserSetting(user_id=sample_advisor.id, callback_e164="+12145557777",
                                        verified_at=datetime.utcnow()))
    db_session.commit()
    r = _inbound(twilio_webhook, "+12145550200", sid="CA-RING")
    assert "<Dial" in r.text and "+12145557777" in r.text and 'timeout="15"' in r.text
    row = db_session.query(InboundCallLog).one()
    r2 = twilio_webhook("/voice/inbound/dial-status?log_id=%s" % row.id,
                        {"DialCallStatus": "no-answer", "CallSid": "CA-RING"})
    assert r2.status_code == 200 and "<Record" in r2.text and "Custom hello" in r2.text
    r3 = twilio_webhook("/voice/inbound/dial-status?log_id=%s" % row.id,
                        {"DialCallStatus": "completed", "CallSid": "CA-RING"})
    assert "<Record" not in r3.text and "<Hangup" in r3.text


def test_number_without_voicemail_capability_does_not_record(twilio_webhook, db_session, sample_org):
    db_session.add(PhoneNumber(e164="+12145550201", organization_id=sample_org.id,
                               cap_voice_inbound=True, cap_voicemail=False))
    db_session.commit()
    r = _inbound(twilio_webhook, "+12145550201", sid="CA-NV")
    assert "<Record" not in r.text and "<Hangup" in r.text


# ── 3. inbound voicemail ─────────────────────────────────────────────────────

def _leave_voicemail(twilio_webhook, db_session, to=TEST_ORG_TWILIO_NUMBER, frm=CALLER, sid="CA-VM",
                     rec=_re(1), **kw):
    _inbound(twilio_webhook, to, frm=frm, sid=sid, **kw)
    row = db_session.query(InboundCallLog).filter_by(call_sid=sid).one()
    r = twilio_webhook("/voice/inbound/voicemail-recording?log_id=%s" % row.id,
                       {"CallSid": sid, "RecordingSid": rec, "RecordingStatus": "completed",
                        "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/X/Recordings/%s" % rec,
                        "RecordingDuration": "17"}, **kw)
    assert r.status_code == 200, r.text
    return db_session.query(Voicemail).filter_by(recording_sid=rec).one()


def test_voicemail_stored_with_task_and_thread_event(client, twilio_webhook, db_session, sample_org,
                                                     sample_lead, auth_headers):
    vm = _leave_voicemail(twilio_webhook, db_session)
    assert vm.organization_id == sample_org.id and vm.lead_id == sample_lead.id
    assert vm.duration_seconds == 17 and vm.transcript is None and vm.transcript_status == "not_enabled"
    task = db_session.query(LeadTask).filter(LeadTask.id == vm.task_id).one()
    assert task.source == "voicemail" and task.lead_id == sample_lead.id
    assert task.organization_id == sample_org.id
    # A Twilio retry of the same recording does not duplicate anything.
    row = db_session.query(InboundCallLog).one()
    twilio_webhook("/voice/inbound/voicemail-recording?log_id=%s" % row.id,
                   {"CallSid": "CA-VM", "RecordingSid": _re(1), "RecordingStatus": "completed",
                    "RecordingUrl": "https://api.twilio.com/x", "RecordingDuration": "17"})
    assert db_session.query(Voicemail).count() == 1 and db_session.query(LeadTask).count() == 1

    thread = client.get("/communications/thread/%s" % sample_lead.id, headers=auth_headers).json()
    vms = [e for e in thread["events"] if e["type"] == "voicemail"]
    assert len(vms) == 1 and vms[0]["audio_path"] == "/voicemails/%s/audio" % vm.id
    assert "twilio.com" not in json.dumps(thread)


def test_unmatched_voicemail_task_has_no_lead(twilio_webhook, db_session, sample_org):
    vm = _leave_voicemail(twilio_webhook, db_session, frm="+12145550300", sid="CA-UV", rec=_re(2))
    assert vm.lead_id is None and vm.caller_state == "unknown"
    task = db_session.query(LeadTask).one()
    assert task.lead_id is None and task.source == "voicemail"


def test_dnc_caller_voicemail_task_says_no_call_back(twilio_webhook, db_session, sample_org, sample_lead):
    sample_lead.status = LeadStatus.DNC
    db_session.commit()
    _leave_voicemail(twilio_webhook, db_session, sid="CA-DV", rec=_re(3))
    assert "no call back" in db_session.query(LeadTask).one().title


def test_voicemail_api_scoped_and_audio_proxied(client, twilio_webhook, db_session, sample_org,
                                                sample_lead, auth_headers, org_b, recorder):
    vm = _leave_voicemail(twilio_webhook, db_session)
    vm_b = _leave_voicemail(twilio_webhook, db_session, to=ORG_B_NUMBER, sid="CA-VB", rec=_re(11),
                            account_sid=ORG_B_SID, auth_token=ORG_B_TOKEN)
    assert vm_b.organization_id == org_b["org"].id and vm_b.lead_id == org_b["lead"].id

    listing = client.get("/voicemails", headers=auth_headers).json()
    assert [i["id"] for i in listing["items"]] == [vm.id] and listing["total"] == 1
    assert "recording_url" not in listing["items"][0]
    assert "twilio.com" not in json.dumps(listing)

    audio = client.get("/voicemails/%s/audio" % vm.id, headers=auth_headers)
    assert audio.status_code == 200 and audio.content == b"ID3fake-mp3"
    assert audio.headers["cache-control"] == "private, no-store"
    assert recorder["fetch"][0][0][0] == TEST_TWILIO_ACCOUNT_SID     # org A's own account

    hb = _h(db_session, org_b["user"])
    assert client.get("/voicemails/%s" % vm.id, headers=hb).status_code == 404
    assert client.get("/voicemails/%s/audio" % vm.id, headers=hb).status_code == 404
    assert client.post("/voicemails/%s/review" % vm.id, json={}, headers=hb).status_code == 404
    assert len(recorder["fetch"]) == 1

    r = client.post("/voicemails/%s/review" % vm.id, json={"reviewed": True}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "reviewed"
    assert client.get("/voicemails?status=new", headers=auth_headers).json()["total"] == 0


def test_voicemail_callback_from_other_account_refused(twilio_webhook, db_session, sample_org, org_b):
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-Z")
    row = db_session.query(InboundCallLog).one()
    r = twilio_webhook("/voice/inbound/voicemail-recording?log_id=%s" % row.id,
                       {"CallSid": "CA-Z", "RecordingSid": _re(26), "RecordingUrl": "https://api.twilio.com/z",
                        "RecordingDuration": "3"}, account_sid=ORG_B_SID, auth_token=ORG_B_TOKEN)
    assert r.status_code == 403
    assert db_session.query(Voicemail).count() == 0


# ── 4. outbound AMD / approved voicemail drop ───────────────────────────────

def test_outbound_params_amd_only_with_callback():
    p = TT.outbound_call_params(to="+12145550001", from_="+12145550002", url="u", status_callback="s")
    assert p["machine_detection"] == "DetectMessageEnd" and "async_amd" not in p
    p = TT.outbound_call_params(to="+12145550001", from_="+12145550002", url="u", status_callback="s",
                                amd_callback="https://x/voice/amd?call_id=1")
    assert p["async_amd"] == "true" and p["async_amd_status_callback"].endswith("call_id=1")


def _vcall(db, org, lead, advisor, **kw):
    c = VoiceCall(lead_id=lead.id, advisor_id=advisor.id, organization_id=org.id, to_phone=lead.phone,
                  from_phone=TEST_ORG_TWILIO_NUMBER, status="ringing", direction="outbound",
                  call_sid="CAout1", created_at=datetime.utcnow(), **kw)
    db.add(c)
    db.commit()
    return c


def _approve(db, org, text="Hello from Restland, please call us back."):
    d = OrgVoicemailDrop(organization_id=org.id, message_text=text, status="approved",
                         approved_at=datetime.utcnow(), approved_by_id="someone")
    db.add(d)
    db.commit()
    return d


def test_machine_without_approved_message_hangs_up(db_session, sample_org, sample_lead, sample_advisor):
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    twiml = TS.machine_twiml(db_session, c, "machine_end_beep")
    assert "<Say" not in twiml and "<Hangup" in twiml
    assert c.voicemail_left is not True and c.answered_by == "voicemail" and c.outcome == "machine_no_drop"


def test_draft_message_is_never_played(db_session, sample_org, sample_lead, sample_advisor):
    db_session.add(OrgVoicemailDrop(organization_id=sample_org.id, message_text="draft", status="draft"))
    db_session.commit()
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    assert "draft" not in TS.machine_twiml(db_session, c, "machine_end_beep")


def test_machine_with_approved_message_plays_it(db_session, sample_org, sample_lead, sample_advisor):
    d = _approve(db_session, sample_org)
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    twiml = TS.machine_twiml(db_session, c, "machine_end_beep")
    assert "please call us back" in twiml and "<Hangup" in twiml
    assert c.voicemail_left is True and c.voicemail_drop_id == d.id and c.outcome == "voicemail_dropped"


def test_no_drop_to_dnc_suppressed_or_paused(db_session, sample_org, sample_lead, sample_advisor):
    from app.services import wholesale_ops as OPS
    _approve(db_session, sample_org)
    OPS.apply_control(db_session, sample_org.id, sample_lead, None, "pause")
    db_session.commit()
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    assert "call us back" not in TS.machine_twiml(db_session, c, "machine_end_beep")
    OPS.apply_control(db_session, sample_org.id, sample_lead, None, "resume")
    sample_lead.status = LeadStatus.DNC
    db_session.commit()
    c2 = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    assert "call us back" not in TS.machine_twiml(db_session, c2, "machine_end_other")
    assert c2.voicemail_left is not True


def test_machine_start_waits_and_human_is_left_alone(db_session, sample_org, sample_lead,
                                                     sample_advisor, recorder):
    _approve(db_session, sample_org)
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    assert TS.handle_async_amd(db_session, c, "machine_start", "CAout1") == "waiting"
    assert TS.handle_async_amd(db_session, c, "human", "CAout1") == "human"
    assert recorder["update"] == [] and c.answered_by == "human" and c.is_live_conversation


def test_async_amd_webhook_redirects_live_call_with_drop(twilio_webhook, db_session, sample_org,
                                                         sample_lead, sample_advisor, recorder):
    _approve(db_session, sample_org)
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    r = twilio_webhook("/voice/amd?call_id=%s" % c.id, {"CallSid": "CAout1", "AnsweredBy": "machine_end_beep"})
    assert r.status_code == 200
    assert len(recorder["update"]) == 1
    creds, sid, twiml = recorder["update"][0]
    assert sid == "CAout1" and creds[0] == TEST_TWILIO_ACCOUNT_SID and "call us back" in twiml


def test_sync_amd_on_twiml_hangs_up_on_machine(twilio_webhook, db_session, sample_org, sample_lead,
                                               sample_advisor):
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    r = twilio_webhook("/voice/twiml/%s?call_id=%s&advisor_id=%s" % (sample_lead.id, c.id, sample_advisor.id),
                       {"CallSid": "CAout1", "AnsweredBy": "machine_end_beep"})
    assert r.status_code == 200 and "<Hangup" in r.text and "Stream" not in r.text
    db_session.refresh(c)
    assert c.answered_by == "voicemail"


def test_campaign_runner_dials_from_resolved_number_with_amd(db_session, sample_org, sample_advisor,
                                                           monkeypatch):
    import time
    from app.routers import voice_router
    from app.models.models import VoiceCallCampaign
    _approve(db_session, sample_org)
    lead = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="C",
                phone="12145550401")
    db_session.add(lead)
    db_session.commit()
    camp = VoiceCallCampaign(organization_id=sample_org.id, advisor_id=sample_advisor.id, name="c",
                             lead_ids=json.dumps([lead.id]), total_leads=1, concurrent_calls=5,
                             call_window_start="00:00", call_window_end="24:00", status="running",
                             created_at=datetime.utcnow())
    db_session.add(camp)
    db_session.commit()
    made = []

    class _Call:
        sid = "CAcamp"
        status = "completed"

    class _Calls:
        def create(self, **kw):
            made.append(kw)
            return _Call()

        def __call__(self, sid):
            class _F:
                def fetch(self_inner):
                    return _Call()
            return _F()

    class _Client:
        def __init__(self, sid, tok):
            made.append(("client", sid))
            self.calls = _Calls()

    import twilio.rest
    monkeypatch.setattr(twilio.rest, "Client", _Client)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr("app.deps.SessionLocal", lambda: db_session)
    voice_router._run_campaign_background(camp.id, sample_advisor.id, sample_org.id)
    assert made[0] == ("client", TEST_TWILIO_ACCOUNT_SID)
    kw = made[1]
    assert kw["from_"] == TEST_ORG_TWILIO_NUMBER and kw["to"] == "+12145550401"
    assert kw["async_amd"] == "true" and "/voice/amd?call_id=" in kw["async_amd_status_callback"]


def test_campaign_refused_without_a_voice_number(client, db_session, sample_org, sample_lead,
                                                 sample_advisor, auth_headers, monkeypatch):
    from app.routers import voice_router
    started = []
    monkeypatch.setattr(voice_router.threading, "Thread",
                        lambda *a, **k: started.append(k) or type("T", (), {"start": lambda s: None})())
    sample_org.org_twilio_phone_number = None
    sample_advisor.twilio_phone_number = None       # the legacy user-number fallback too
    db_session.commit()
    r = client.post("/voice/campaigns", headers=auth_headers,
                    json={"name": "x", "lead_ids": [sample_lead.id]})
    assert r.status_code == 409 and "No active voice-capable" in r.text
    assert started == []


# ── 5. human dialer ─────────────────────────────────────────────────────────

def test_human_call_readiness_names_what_is_missing(client, db_session, sample_org, sample_lead,
                                                    auth_headers):
    sample_org.org_twilio_phone_number = None
    db_session.commit()
    r = client.get("/calls/human/readiness/%s" % sample_lead.id, headers=auth_headers).json()
    assert r["ready"] is False and r["browser_calling"] is False
    bad = {c["key"] for c in r["checks"] if not c["ok"]}
    assert {"org_number", "callback_phone"} <= bad
    assert "org_number" in r["provider_config_required"]
    assert all(c.get("fix") for c in r["checks"] if not c["ok"] and c["key"] != "compliance")


def test_human_call_places_bridge_user_first(client, db_session, sample_org, sample_lead,
                                             sample_advisor, auth_headers, recorder):
    r = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 409 and "callback phone" in r.text
    assert recorder["create"] == []
    r = client.put("/telephony/me/callback-phone", json={"phone": "(214) 555-7000"}, headers=auth_headers)
    assert r.json()["callback_phone"] is None and r.json()["pending_phone"] == "+12145557000"
    # Saved but NOT verified: still refused, and the only provider call was the code delivery.
    r = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 409 and "Verify your callback phone" in r.text
    assert len(recorder["create"]) == 1 and recorder["create"][0][1]["to"] == "+12145557000"
    r = client.post("/telephony/me/callback-phone/verify", json={"code": _code_from(recorder)},
                    headers=auth_headers)
    assert r.status_code == 200 and r.json()["callback_phone"] == "+12145557000"
    r = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["is_human_call"] and body["from_phone"] == TEST_ORG_TWILIO_NUMBER
    creds, params = recorder["create"][1]
    assert creds[0] == TEST_TWILIO_ACCOUNT_SID
    assert params["to"] == "+12145557000" and params["from_"] == TEST_ORG_TWILIO_NUMBER
    assert params["url"].startswith("https://api.example.test/voice/human/bridge?call_id=")
    call = db_session.query(VoiceCall).filter(VoiceCall.id == body["id"]).one()
    assert call.advisor_id == sample_advisor.id and call.call_sid == "CAfake002"
    assert call.to_phone == "+12145559999"


def test_human_call_bridge_and_dial_status(twilio_webhook, client, db_session, sample_org, sample_lead,
                                           auth_headers, recorder):
    _verify_phone(client, auth_headers, recorder)
    cid = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers).json()["id"]
    r = twilio_webhook("/voice/human/bridge?call_id=%s" % cid, {"CallSid": "CAfake001"})
    assert r.status_code == 200
    assert 'callerId="%s"' % TEST_ORG_TWILIO_NUMBER in r.text and "<Number>+12145559999</Number>" in r.text
    r = twilio_webhook("/voice/human/dial-status?call_id=%s" % cid,
                       {"CallSid": "CAfake001", "DialCallStatus": "completed", "DialCallDuration": "84"})
    assert r.status_code == 200
    c = client.get("/calls/%s" % cid, headers=auth_headers).json()
    assert c["outcome"] == "connected" and c["duration_seconds"] == 84 and c["answered_by"] == "human"


def test_human_call_refused_for_dnc_and_rechecked_at_bridge(twilio_webhook, client, db_session,
                                                            sample_org, sample_lead, auth_headers,
                                                            recorder):
    _verify_phone(client, auth_headers, recorder)
    cid = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers).json()["id"]
    sample_lead.status = LeadStatus.DNC                   # STOP arrives while the user's phone rings
    db_session.commit()
    r = twilio_webhook("/voice/human/bridge?call_id=%s" % cid, {"CallSid": "CAfake001"})
    assert "<Dial" not in r.text and "<Hangup" in r.text
    r = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 409 and "DNC" in r.text
    assert len(recorder["create"]) == 2          # the verification call + the one bridge, nothing more


def test_human_takeover_does_not_block_a_person(client, db_session, sample_org, sample_lead,
                                                auth_headers, recorder):
    from app.services import wholesale_ops as OPS
    OPS.apply_control(db_session, sample_org.id, sample_lead, None, "takeover")
    db_session.commit()
    _verify_phone(client, auth_headers, recorder)
    r = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 201, r.text


def test_human_call_cross_tenant_404(client, db_session, sample_org, org_b, auth_headers, recorder):
    _verify_phone(client, auth_headers, recorder)
    r = client.post("/calls/human", json={"lead_id": org_b["lead"].id}, headers=auth_headers)
    assert r.status_code == 404 and len(recorder["create"]) == 1     # only the verification call


def test_disposition_with_callback_task_and_thread(client, db_session, sample_org, sample_lead,
                                                   sample_advisor, auth_headers, org_b):
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor, is_human_call=True)
    due = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()
    r = client.post("/calls/%s/disposition" % c.id, headers=auth_headers,
                    json={"outcome": "callback_requested", "notes": "Wants Tuesday", "callback_at": due})
    assert r.status_code == 200, r.text
    assert r.json()["callback"]["kind"] == "task"
    t = db_session.query(LeadTask).one()
    assert t.source == "callback" and t.lead_id == sample_lead.id and t.organization_id == sample_org.id
    assert client.post("/calls/%s/disposition" % c.id, headers=auth_headers,
                       json={"outcome": "made_up"}).status_code == 422
    ev = [e for e in client.get("/communications/thread/%s" % sample_lead.id, headers=auth_headers)
          .json()["events"] if e["type"] == "call"]
    assert ev[0]["disposition"] == "callback_requested" and ev[0]["is_human_call"] is True
    hb = _h(db_session, org_b["user"])
    assert client.post("/calls/%s/disposition" % c.id, headers=hb,
                       json={"outcome": "connected"}).status_code == 404
    assert client.get("/calls/%s" % c.id, headers=hb).status_code == 404


def test_disposition_callback_for_wholesale_seller_uses_seller_callback(
        client, db_session, sample_org, sample_lead, sample_advisor, auth_headers):
    from app.models.wholesale_ops_models import WholesaleSellerCallback
    sample_lead.source_category = "wholesale"
    db_session.commit()
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor, is_human_call=True)
    due = (datetime.utcnow() + timedelta(days=1)).replace(microsecond=0).isoformat()
    r = client.post("/calls/%s/disposition" % c.id, headers=auth_headers,
                    json={"outcome": "interested", "callback_at": due})
    assert r.status_code == 200, r.text
    assert r.json()["callback"]["kind"] == "wholesale_callback"
    cb = db_session.query(WholesaleSellerCallback).one()
    assert cb.organization_id == sample_org.id and cb.source_ref == c.id


# ── 6. admin ────────────────────────────────────────────────────────────────

def test_god_assigns_numbers_and_org_admin_reads(client, db_session, sample_org, sample_advisor,
                                                 god, org_admin, auth_headers):
    hg = _h(db_session, god)
    r = client.post("/god/telephony/numbers", headers=hg, json={
        "e164": "(214) 555-0600", "organization_id": sample_org.id, "cap_voice_outbound": True,
        "cap_voice_inbound": True, "cap_voicemail": True, "label": "Main line",
        "inbound_route": {"ring_user_ids": [sample_advisor.id], "timeout_seconds": 25,
                          "greeting_text": "Thanks for calling Restland"}})
    assert r.status_code == 201, r.text
    num = r.json()
    assert num["e164"] == "+12145550600" and num["scope"] == "organization"
    assert num["inbound_route"]["ring_user_ids"] == [sample_advisor.id]
    assert client.post("/god/telephony/numbers", headers=hg,
                       json={"e164": "2145550600"}).status_code == 409
    # ring targets must be members of that org
    r = client.patch("/god/telephony/numbers/%s" % num["id"], headers=hg,
                     json={"inbound_route": {"ring_user_ids": [god.id]}})
    assert r.status_code == 422
    r = client.post("/god/telephony/orgs/%s/voicemail-drop" % sample_org.id, headers=hg,
                    json={"message_text": "Please call us back.", "approve": True})
    assert r.status_code == 201 and r.json()["status"] == "approved"

    detail = client.get("/god/telephony/orgs/%s" % sample_org.id, headers=hg).json()
    assert detail["resolved"]["outbound"]["e164"] == "+12145550600"
    assert detail["voicemail_drop"]["approved"]["message_text"] == "Please call us back."
    assert detail["webhooks"]["voice_url"] == "https://api.example.test/voice/inbound"

    ha = _h(db_session, org_admin)
    mine = client.get("/telephony/numbers", headers=ha).json()
    assert [n["e164"] for n in mine["numbers"]] == ["+12145550600"] and "pool_numbers" not in mine
    assert client.get("/telephony/numbers", headers=auth_headers).status_code == 403
    assert client.post("/god/telephony/numbers", headers=ha, json={"e164": "2145550601"}).status_code == 403
    assert client.get("/god/telephony/orgs/%s" % sample_org.id, headers=ha).status_code == 403


# ── 7. security review follow-up ───────────────────────────────────────────

def test_verification_code_wrong_expired_and_capped(client, db_session, sample_org, sample_advisor,
                                                    auth_headers, recorder):
    client.put("/telephony/me/callback-phone", json={"phone": "2145557000"}, headers=auth_headers)
    row = db_session.query(TelephonyUserSetting).filter_by(user_id=sample_advisor.id).one()
    assert row.code_hash and _code_from(recorder) not in (row.code_hash or "")   # stored hashed
    for _ in range(5):
        r = client.post("/telephony/me/callback-phone/verify", json={"code": "000000"}, headers=auth_headers)
        assert r.status_code == 422
    r = client.post("/telephony/me/callback-phone/verify", json={"code": _code_from(recorder)},
                    headers=auth_headers)
    assert r.status_code == 422 and "Too many" in r.text                 # capped even with the right code
    # resend is throttled
    r = client.put("/telephony/me/callback-phone", json={"phone": "2145557000"}, headers=auth_headers)
    assert r.status_code == 429
    # expiry
    row.code_sent_at = datetime.utcnow() - timedelta(minutes=5)
    db_session.commit()
    client.put("/telephony/me/callback-phone", json={"phone": "2145557000"}, headers=auth_headers)
    db_session.refresh(row)
    row.code_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()
    r = client.post("/telephony/me/callback-phone/verify", json={"code": _code_from(recorder)},
                    headers=auth_headers)
    assert r.status_code == 422 and "expired" in r.text
    assert TS.user_callback_phone(db_session, sample_advisor.id) is None


def test_callback_phone_refuses_customer_dnc_and_suppressed_numbers(client, db_session, sample_org,
                                                                   sample_lead, auth_headers, recorder):
    from app.services.compliance_service import add_suppression_entry
    r = client.put("/telephony/me/callback-phone", json={"phone": CALLER}, headers=auth_headers)
    assert r.status_code == 422 and "lead" in r.text                      # a customer's number
    add_suppression_entry(db_session, sample_org.id, "12145557001", "test")
    db_session.add(Lead(organization_id=sample_org.id, first_name="D", phone="12145557002",
                        status=LeadStatus.DNC))
    db_session.commit()
    assert client.put("/telephony/me/callback-phone", json={"phone": "2145557001"},
                      headers=auth_headers).status_code == 422
    assert client.put("/telephony/me/callback-phone", json={"phone": "2145557002"},
                      headers=auth_headers).status_code == 422
    assert recorder["create"] == []                                       # nothing was dialled


def test_verification_needs_provider_config(client, db_session, sample_org, sample_advisor,
                                            auth_headers, recorder):
    sample_org.org_twilio_phone_number = None
    sample_advisor.twilio_phone_number = None
    db_session.commit()
    r = client.put("/telephony/me/callback-phone", json={"phone": "2145557000"}, headers=auth_headers)
    assert r.status_code == 409 and "Provider/config required" in r.text
    assert db_session.query(TelephonyUserSetting).filter(TelephonyUserSetting.code_hash != None).count() == 0


def test_human_call_rate_limited(client, db_session, sample_org, sample_lead, sample_advisor,
                                 auth_headers, recorder):
    _verify_phone(client, auth_headers, recorder)
    for i in range(5):
        db_session.add(VoiceCall(lead_id=sample_lead.id, advisor_id=sample_advisor.id,
                                 organization_id=sample_org.id, to_phone="x", is_human_call=True,
                                 created_at=datetime.utcnow()))
    db_session.commit()
    n = len(recorder["create"])
    r = client.post("/calls/human", json={"lead_id": sample_lead.id}, headers=auth_headers)
    assert r.status_code == 429 and len(recorder["create"]) == n


def test_unverified_ring_target_is_skipped(twilio_webhook, db_session, sample_org, sample_advisor):
    db_session.add(PhoneNumber(e164="+12145550210", organization_id=sample_org.id,
                               cap_voice_inbound=True, cap_voicemail=True,
                               default_inbound_route=json.dumps({"ring_user_ids": [sample_advisor.id]})))
    db_session.add(TelephonyUserSetting(user_id=sample_advisor.id, callback_e164="+12145557777"))
    db_session.commit()
    r = _inbound(twilio_webhook, "+12145550210", sid="CA-UNV")
    assert "<Dial" not in r.text and "+12145557777" not in r.text and "<Record" in r.text


def test_org_less_tenant_account_fails_closed(twilio_webhook, db_session, sample_org):
    """A signed request from an advisor account with no organization cannot act on any org."""
    from app.utils.crypto import encrypt_value
    u = User(organization_id=None, email="loose@x.test", password_hash=hash_password("x"),
             full_name="Loose", role="advisor", twilio_account_sid="AClooseaccount000000000000000000",
             twilio_auth_token_encrypted=encrypt_value("loose-token"))
    db_session.add(u)
    db_session.commit()
    r = _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-L",
                 account_sid="AClooseaccount000000000000000000", auth_token="loose-token")
    assert r.status_code == 403
    assert db_session.query(InboundCallLog).count() == 0


def test_platform_sid_resolves_to_platform_not_tenant(db_session, sample_org, monkeypatch):
    from app.utils import twilio_webhook_guard as G
    from app.utils.crypto import encrypt_value
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACplatform0000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "platform-token")
    db_session.add(User(organization_id=sample_org.id, email="hijack@x.test", password_hash="x",
                        full_name="H", twilio_account_sid="ACplatform0000000000000000000000",
                        twilio_auth_token_encrypted=encrypt_value("attacker-token")))
    db_session.commit()
    r = G.resolve_account_by_sid(db_session, "ACplatform0000000000000000000000")
    assert r.source == "platform" and r.auth_token == "platform-token" and r.organization_id is None


def test_settings_refuse_platform_or_foreign_sid(client, db_session, sample_org, sample_advisor, org_b,
                                                 admin_auth_headers, monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACplatform0000000000000000000000")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "platform-token")
    url = "/settings/admin/twilio/%s" % sample_advisor.id
    body = {"twilio_phone_number": "+12145551111", "twilio_account_sid": "ACplatform0000000000000000000000",
            "twilio_auth_token": "t"}
    assert client.put(url, json=body, headers=admin_auth_headers).status_code == 409
    body["twilio_account_sid"] = ORG_B_SID                                 # another org's account
    assert client.put(url, json=body, headers=admin_auth_headers).status_code == 409
    prof = "/settings/admin/profile/%s" % sample_advisor.id
    assert client.patch(prof, json={"twilio_account_sid": ORG_B_SID},
                        headers=admin_auth_headers).status_code == 409
    body["twilio_account_sid"] = TEST_TWILIO_ACCOUNT_SID                   # own org's account: fine
    r = client.put(url, json=body, headers=admin_auth_headers)
    assert r.status_code == 200, r.text


def test_twiml_refuses_other_orgs_advisor(twilio_webhook, db_session, sample_org, sample_lead,
                                          sample_advisor, org_b):
    r = twilio_webhook("/voice/twiml/%s?advisor_id=%s" % (sample_lead.id, sample_advisor.id),
                       {"CallSid": "CAx"}, account_sid=ORG_B_SID, auth_token=ORG_B_TOKEN)
    assert r.status_code == 403


def test_recording_url_built_from_sid_only():
    good = _re(5)
    url = TT.recording_media_url(TEST_TWILIO_ACCOUNT_SID, good)
    assert url == "https://api.twilio.com/2010-04-01/Accounts/%s/Recordings/%s.mp3" % (TEST_TWILIO_ACCOUNT_SID, good)
    for bad_sid in ("RE123", "https://evil.example/x", _re(5) + "?x=1", "RE" + "g" * 32):
        with pytest.raises(ValueError):
            TT.recording_media_url(TEST_TWILIO_ACCOUNT_SID, bad_sid)
    with pytest.raises(ValueError):
        TT.recording_media_url("ACx?y#0000000000000000000000000000", good)


def test_voicemail_with_bad_recording_sid_not_stored(twilio_webhook, db_session, sample_org):
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-BAD")
    row = db_session.query(InboundCallLog).one()
    twilio_webhook("/voice/inbound/voicemail-recording?log_id=%s" % row.id,
                   {"CallSid": "CA-BAD", "RecordingSid": "RE../../x", "RecordingUrl": "https://evil.example/a",
                    "RecordingDuration": "3"})
    assert db_session.query(Voicemail).count() == 0


def test_amd_refuses_mismatched_callsid(twilio_webhook, db_session, sample_org, sample_lead,
                                        sample_advisor, recorder):
    _approve(db_session, sample_org)
    c = _vcall(db_session, sample_org, sample_lead, sample_advisor)
    twilio_webhook("/voice/amd?call_id=%s" % c.id, {"CallSid": "CAsomeoneelse", "AnsweredBy": "machine_end_beep"})
    assert recorder["update"] == []
    db_session.refresh(c)
    assert not c.voicemail_left


def test_human_call_honours_paused_all_not_paused_voice(db_session, sample_org, sample_lead):
    from app.services import voice_bulk_gate
    from app.services.evosense import common as C
    ctl = C.controls(db_session, sample_org.id)
    ctl.paused_voice = True
    db_session.commit()
    assert voice_bulk_gate.call_refusal(db_session, sample_lead, sample_org.id, human=True) is None
    assert voice_bulk_gate.call_refusal(db_session, sample_lead, sample_org.id) is not None
    ctl.paused_all = True
    db_session.commit()
    assert "paused" in voice_bulk_gate.call_refusal(db_session, sample_lead, sample_org.id, human=True)


def test_voicemail_done_checks_org(twilio_webhook, db_session, sample_org, org_b):
    _inbound(twilio_webhook, TEST_ORG_TWILIO_NUMBER, sid="CA-DN")
    row = db_session.query(InboundCallLog).one()
    r = twilio_webhook("/voice/inbound/voicemail-done?log_id=%s" % row.id, {"CallSid": "CA-DN"},
                       account_sid=ORG_B_SID, auth_token=ORG_B_TOKEN)
    assert r.status_code == 403
    r = twilio_webhook("/voice/inbound/voicemail-done?log_id=%s" % row.id, {"CallSid": "CA-DN"})
    assert r.status_code == 200
