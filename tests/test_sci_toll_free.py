"""SCI on the existing toll-free line +1 844-917-2171 (revised orders, 2026-10-08).

  * toll-free approval (Toll-Free Verification) is separate from 10DLC, and
    promotional content needs promotional scope
  * existing contacts count through owner-attested consent - no re-signup -
    while STOP / suppression / DNC are preserved
  * SMS replies to the toll-free line route to the contact's own cemetery;
    unknown senders go to the toll-free review queue
  * every call to the toll-free line goes straight to voicemail: the caller's
    cemetery greeting, neutral when unknown or ambiguous; the voicemail is saved
    to the contact and the assigned representative is notified
  * outbound templates carry the cemetery's phone / booking / planning-guide
    links only when the sender is approved for them

Nothing reaches Twilio: the SCI sender is patched wherever a send is exercised.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import Lead, Notification
from app.models.sms_consent_models import SmsConsentRecord
from app.models.telephony_models import InboundCallLog, PhoneNumber, Voicemail
from app.services import sms_campaigns, sms_programs, wholesale_sms
from app.services.programs import regional_pools as rp
from tests.test_outreach_program import _god, _h  # noqa: F401
from tests.test_telephony_xc import _mount, _re

PLATFORM_SID = "ACplatform00000000000000000000001"
PLATFORM_TOKEN = "platform-token"
TF = "+18449172171"
SIM_ACTIVE = "+12055550102"       # Eastern Gate Memorial Gardens
SIM_CAMPUS = "+12055550101"       # Eastern Gate Memorial Funeral Home
STRANGER = "+12055550199"

TF_VERIFIED = {"key": "TF-8449172171", "kind": "toll_free", "numbers": [TF],
               "verification_status": "TWILIO_APPROVED", "has_embedded_links": True,
               "has_embedded_phone": True, "programs": ["sci"], "approved_scope": ["informational"]}


def _env(monkeypatch, registry=None):
    monkeypatch.setenv("STAGING_TEST_HARNESS", "on")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("API_BASE_URL", "https://sci-staging-backend.onrender.com")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", PLATFORM_SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", PLATFORM_TOKEN)
    if registry is not None:
        monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps(registry))


def _seed(client, db, monkeypatch, registry=None):
    from tests.test_sci_pool_staging import _seed as pool_seed
    _env(monkeypatch, registry)
    h = pool_seed(client, db, monkeypatch, sims=True)
    _env(monkeypatch, registry)
    r = client.post("/god/staging/sci/toll-free", headers=h, json={"apply": True})
    assert r.status_code == 200, r.text
    org_id = db.query(PhoneNumber).filter(PhoneNumber.e164 == TF).one().organization_id
    return h, org_id


def _lead(db, org_id, phone):
    return db.query(Lead).filter(Lead.organization_id == org_id, Lead.phone == phone.lstrip("+")).one()


# ── 1. toll-free approval is its own thing ──────────────────────────────────

def test_toll_free_is_separate_from_10dlc_and_fails_closed(monkeypatch):
    monkeypatch.delenv(sms_campaigns.ENV_REGISTRY, raising=False)
    tf = sms_campaigns.for_sender(TF)
    assert sms_campaigns.kind(tf) == "toll_free" and not sms_campaigns.is_approved(tf)
    assert sms_campaigns.features(tf) == {"links": False, "phone": False}
    # a 10DLC-style "status: VERIFIED" does not approve a toll-free entry
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([dict(TF_VERIFIED, verification_status=None,
                                                                    status="VERIFIED")]))
    assert not sms_campaigns.is_approved(sms_campaigns.for_sender(TF))
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([TF_VERIFIED]))
    tf = sms_campaigns.for_sender(TF)
    assert sms_campaigns.is_approved(tf) and sms_campaigns.features(tf) == {"links": True, "phone": True}
    # CO3YNIF unchanged
    co = sms_campaigns.for_sender("+14692241155")
    assert sms_campaigns.kind(co) == "a2p_10dlc" and sms_campaigns.features(co) == {"links": False, "phone": False}


def test_promotional_content_needs_promotional_scope(monkeypatch):
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([TF_VERIFIED]))
    tf = sms_campaigns.for_sender(TF)
    assert sms_campaigns.scope_allows(tf, "informational")
    assert not sms_campaigns.scope_allows(tf, "promotional")
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps(
        [dict(TF_VERIFIED, approved_scope=["informational", "promotional"])]))
    assert sms_campaigns.scope_allows(sms_campaigns.for_sender(TF), "promotional")


# ── 2. existing contacts: owner-attested consent, STOP preserved ────────────

def test_reconcile_existing_contacts_without_resignup(client, db_session, monkeypatch):
    from app.services.compliance_service import add_suppression_entry_from_reply
    h, org_id = _seed(client, db_session, monkeypatch, [TF_VERIFIED])
    db = db_session
    # one suppressed, one DNC, one with an earlier SCI opt-out on record
    add_suppression_entry_from_reply(db, org_id, SIM_CAMPUS, reason="Replied: STOP")
    extra = Lead(organization_id=org_id, first_name="Dee", last_name="Ncee", phone="12055550150", status="dnc")
    old = Lead(organization_id=org_id, first_name="Otto", last_name="Out", phone="12055550151", status="new")
    db.add_all([extra, old])
    db.commit()
    wholesale_sms.record_consent(db, org_id, phone_raw=old.phone, disclosure_text="x", disclosure_version="v",
                                 form_version=None, source_url=None, ip=None, user_agent=None,
                                 program="sci_poc_sms")
    wholesale_sms.record_opt_out(db, org_id, old.phone, keyword="STOP", program="sci_poc_sms")
    db.commit()

    body = {"attested_by": "Mike Simmons", "evidence_reference": "EvoSys Pro opt-in export 2026-10"}
    dry = client.post("/god/sms-consent/sci/reconcile", headers=h, json=body).json()
    assert dry["apply"] is False and dry["created"] == 0
    assert dry["skipped"].get("suppressed") == 1 and dry["skipped"].get("dnc") == 1
    assert dry["skipped"].get("opted_out_before") == 1
    assert dry["eligible"] >= 1                       # SIM-ACTIVE at least
    assert db.query(SmsConsentRecord).filter_by(consent_method="owner_attested_import").count() == 0

    applied = client.post("/god/sms-consent/sci/reconcile", headers=h, json=dict(body, apply=True)).json()
    assert applied["created"] == dry["eligible"]
    rec = (db.query(SmsConsentRecord).filter_by(organization_id=org_id, program="sci_poc_sms",
                                                phone_normalized=SIM_ACTIVE).one())
    assert rec.consent_method == "owner_attested_import" and "Mike Simmons" in rec.disclosure_text
    assert rec.lead_id == _lead(db, org_id, SIM_ACTIVE).id
    again = client.post("/god/sms-consent/sci/reconcile", headers=h, json=dict(body, apply=True)).json()
    assert again["created"] == 0 and again["skipped"].get("already_consented") == applied["created"]

    # the gate accepts attested consent - no re-signup - on the approved toll-free
    monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
    ok = sms_programs.sci_check(db, org_id, SIM_ACTIVE, from_number=TF)
    assert ok["eligible"] is True and ok["consent_method"] == "owner_attested_import"
    # STOP afterwards ends it
    wholesale_sms.record_opt_out(db, org_id, SIM_ACTIVE, keyword="STOP")
    db.commit()
    assert "OPTED_OUT" in sms_programs.sci_check(db, org_id, SIM_ACTIVE, from_number=TF)["reasons"]


def test_reconcile_requires_attestation(client, db_session, monkeypatch):
    h, _ = _seed(client, db_session, monkeypatch)
    r = client.post("/god/sms-consent/sci/reconcile", headers=h,
                    json={"attested_by": " ", "evidence_reference": "x", "apply": True})
    assert r.status_code == 422


def test_sci_gate_on_toll_free(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    db = db_session
    sms_programs.reconcile_existing(db, org_id, attested_by="Mike", evidence_reference="ref", apply=True)
    monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
    # toll-free not yet confirmed as verified -> refused
    assert sms_programs.sci_check(db, org_id, SIM_ACTIVE, from_number=TF)["reasons"] == ["CAMPAIGN_NOT_APPROVED"]
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([TF_VERIFIED]))
    assert sms_programs.sci_check(db, org_id, SIM_ACTIVE, from_number=TF)["eligible"]
    # promotional content on an informational-only toll-free -> refused
    out = sms_programs.sci_check(db, org_id, SIM_ACTIVE, from_number=TF, category="promotional")
    assert out["reasons"] == ["SENDER_SCOPE_NOT_APPROVED"]
    # never from CO3YNIF's number
    assert "CAMPAIGN_NOT_APPROVED" in sms_programs.sci_check(
        db, org_id, SIM_ACTIVE, from_number="+14692241155")["reasons"]


def test_sci_send_uses_the_toll_free_line_only(client, db_session, monkeypatch):
    from app.models.models import User
    from app.services import sms_service
    h, org_id = _seed(client, db_session, monkeypatch, [TF_VERIFIED])
    db = db_session
    lead = _lead(db, org_id, SIM_ACTIVE)
    advisor = db.query(User).filter(User.id == lead.assigned_to_id).one()
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(sid="SMx", status="queued", error_code=None, error_message=None)
    lead.is_test = False                               # exercise the real gate, not the test-record guard
    db.commit()
    with patch.object(sms_programs, "sci_sender", return_value=(fake, TF)), \
            patch("app.services.programs.identity.send_refusal", return_value=None):
        with pytest.raises(ValueError) as exc:         # switch off, no consent
            sms_service.send_sms(db, advisor, lead, "Hi {first_name}. Reply STOP to opt out.")
        assert "SCI_SMS_DISABLED" in str(exc.value) and "NO_SMS_CONSENT" in str(exc.value)
        fake.messages.create.assert_not_called()
        sms_programs.reconcile_existing(db, org_id, attested_by="Mike", evidence_reference="ref", apply=True)
        monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
        sms_service.send_sms(db, advisor, lead, "Hi {first_name}. Reply STOP to opt out.")
    assert fake.messages.create.call_count == 1
    assert fake.messages.create.call_args.kwargs["from_"] == TF


# ── 3. SMS replies to the toll-free line ────────────────────────────────────

def test_sms_reply_routes_to_the_contacts_own_cemetery(client, db_session, monkeypatch, twilio_webhook):
    from app.models.models import Reply
    from app.models.program_models import ProgramUnmatchedReply
    h, org_id = _seed(client, db_session, monkeypatch)
    r = twilio_webhook("/sms/webhook/inbound", data={"From": SIM_ACTIVE, "To": TF, "Body": "Yes, call me",
                                                     "MessageSid": "SMtf1"},
                       account_sid=PLATFORM_SID, auth_token=PLATFORM_TOKEN)
    assert r.status_code == 200, r.text
    lead = _lead(db_session, org_id, SIM_ACTIVE)
    assert db_session.query(Reply).filter(Reply.lead_id == lead.id).count() == 1
    r = twilio_webhook("/sms/webhook/inbound", data={"From": STRANGER, "To": TF, "Body": "Who is this?",
                                                     "MessageSid": "SMtf2"},
                       account_sid=PLATFORM_SID, auth_token=PLATFORM_TOKEN)
    assert r.status_code == 200
    q = db_session.query(ProgramUnmatchedReply).filter(ProgramUnmatchedReply.from_address == STRANGER).one()
    assert q.location_id is None


# ── 4. calls: voicemail only, the caller's cemetery greeting ────────────────

def _call(twilio_webhook, frm, sid):
    return twilio_webhook("/voice/inbound", {"To": TF, "From": frm, "CallSid": sid},
                          account_sid=PLATFORM_SID, auth_token=PLATFORM_TOKEN)


def test_known_caller_hears_their_cemetery_and_goes_straight_to_voicemail(client, db_session, monkeypatch,
                                                                        twilio_webhook):
    _mount()
    h, org_id = _seed(client, db_session, monkeypatch)
    r = _call(twilio_webhook, SIM_ACTIVE, "CAtf1")
    assert r.status_code == 200, r.text
    assert "<Record" in r.text and "<Dial" not in r.text and "<Connect" not in r.text
    assert "Eastern Gate Memorial Gardens" in r.text


def test_custom_cemetery_greeting_is_played(client, db_session, monkeypatch, twilio_webhook):
    from app.models.program_models import LocationProfile
    _mount()
    h, org_id = _seed(client, db_session, monkeypatch)
    prof = (db_session.query(LocationProfile)
            .filter(LocationProfile.organization_id == org_id,
                    LocationProfile.official_name == "Eastern Gate Memorial Gardens").one())
    prof.brand_settings = json.dumps({"voicemail_greeting": "You have reached the Gardens office. Leave a message."})
    db_session.commit()
    r = _call(twilio_webhook, SIM_ACTIVE, "CAtf2")
    assert "You have reached the Gardens office." in r.text and "<Dial" not in r.text


def test_unknown_caller_hears_a_neutral_greeting(client, db_session, monkeypatch, twilio_webhook):
    _mount()
    _seed(client, db_session, monkeypatch)
    r = _call(twilio_webhook, STRANGER, "CAtf3")
    assert "<Record" in r.text and "<Dial" not in r.text
    assert "Eastern Gate" not in r.text and "planning line" in r.text


def test_ambiguous_caller_is_neutral_and_saved_to_no_contact(client, db_session, monkeypatch, twilio_webhook):
    _mount()
    h, org_id = _seed(client, db_session, monkeypatch)
    # make SIM-CAMPUS's number also belong to SIM-ACTIVE (two cemeteries)
    _lead(db_session, org_id, SIM_CAMPUS).phone = SIM_ACTIVE.lstrip("+")
    db_session.commit()
    r = _call(twilio_webhook, SIM_ACTIVE, "CAtf4")
    assert "Eastern Gate" not in r.text and "<Record" in r.text
    row = db_session.query(InboundCallLog).filter_by(call_sid="CAtf4").one()
    assert row.lead_id is None


def test_voicemail_is_saved_to_the_contact_and_the_rep_is_notified(client, db_session, monkeypatch,
                                                                 twilio_webhook):
    _mount()
    h, org_id = _seed(client, db_session, monkeypatch)
    _call(twilio_webhook, SIM_ACTIVE, "CAtf5")
    row = db_session.query(InboundCallLog).filter_by(call_sid="CAtf5").one()
    rec = _re(5)
    r = twilio_webhook("/voice/inbound/voicemail-recording?log_id=%s" % row.id,
                       {"CallSid": "CAtf5", "RecordingSid": rec, "RecordingStatus": "completed",
                        "RecordingUrl": "https://api.twilio.com/2010-04-01/Accounts/X/Recordings/%s" % rec,
                        "RecordingDuration": "12"}, account_sid=PLATFORM_SID, auth_token=PLATFORM_TOKEN)
    assert r.status_code == 200, r.text
    lead = _lead(db_session, org_id, SIM_ACTIVE)
    vm = db_session.query(Voicemail).filter_by(recording_sid=rec).one()
    assert vm.lead_id == lead.id and vm.recording_url
    n = db_session.query(Notification).filter(Notification.user_id == lead.assigned_to_id,
                                              Notification.lead_id == lead.id).all()
    assert any("Voicemail from" in x.message and "call back" in x.message for x in n)


def test_toll_free_route_never_rings_even_if_configured_to(client, db_session, monkeypatch, twilio_webhook):
    _mount()
    h, org_id = _seed(client, db_session, monkeypatch)
    num = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == TF).one()
    num.default_inbound_route = json.dumps({"mode": "ai_agent", "ring_user_ids": ["x"]})
    db_session.commit()
    r = _call(twilio_webhook, SIM_ACTIVE, "CAtf6")
    assert "<Record" in r.text and "<Dial" not in r.text and "<Connect" not in r.text


def test_toll_free_registration_is_staging_only_and_idempotent(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    again = client.post("/god/staging/sci/toll-free", headers=h, json={"apply": True}).json()
    assert again["plan"]["action"] == "unchanged"
    num = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == TF).one()
    assert num.label == "pool:" + rp.TOLL_FREE_POOL["pool_id"] and not num.cap_voice_outbound
    monkeypatch.setenv("APP_ENV", "production")
    assert client.post("/god/staging/sci/toll-free", headers=h, json={"apply": True}).status_code == 404


# ── 5. per-cemetery template fields, subject to capability ──────────────────

def test_template_fields_and_capability_gating(client, db_session, monkeypatch):
    from app.models.program_models import LocationProfile
    from app.services.programs import identity
    from app.services.sms_content_policy import enforce_sms_content_policy
    h, org_id = _seed(client, db_session, monkeypatch)
    lead = _lead(db_session, org_id, SIM_ACTIVE)
    prof = identity.location_profile_for_lead(db_session, lead)
    prof.facility_phone, prof.appointment_link = "(205) 555-0177", "https://book.example.com/gardens"
    db_session.commit()
    f = identity.context_for(db_session, lead)["fields"]
    assert f["location_phone"] == "(205) 555-0177" and f["booking_link"] == "https://book.example.com/gardens"
    assert f["planning_guide_link"] == "https://evosyspro.live/planning-guide"
    tpl = identity.render("Book: {booking_link} Guide: {planning_guide_link} Call {location_phone}.", f)
    # toll-free not confirmed verified: links and phone numbers stripped
    out = enforce_sms_content_policy(tpl, **sms_programs.content_allowance(db_session, lead))
    assert "http" not in out and "555-0177" not in out
    # verified: carried
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([TF_VERIFIED]))
    out = enforce_sms_content_policy(tpl, **sms_programs.content_allowance(db_session, lead))
    assert "https://book.example.com/gardens" in out and "555-0177" in out
    # a non-SCI lead keeps the CO3YNIF rule
    assert sms_programs.content_allowance(db_session, Lead(organization_id="other")) == {}
