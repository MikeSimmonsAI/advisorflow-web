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
    # built-in: the console-confirmed approval (2026-08-22, Account Notifications)
    monkeypatch.delenv(sms_campaigns.ENV_REGISTRY, raising=False)
    tf = sms_campaigns.for_sender(TF)
    assert sms_campaigns.kind(tf) == "toll_free" and sms_campaigns.is_approved(tf)
    assert tf["use_case"] == "ACCOUNT_NOTIFICATIONS" and tf["approved_scope"] == ["informational"]
    assert not sms_campaigns.scope_allows(tf, "promotional")
    # an unconfirmed status fails closed
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([dict(TF_VERIFIED, verification_status="UNCONFIRMED")]))
    tf = sms_campaigns.for_sender(TF)
    assert not sms_campaigns.is_approved(tf)
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
    assert dry["apply"] is False and dry["created"] == 0 and dry["evidence_supplied"] is False
    assert dry["skipped"].get("suppressed") == 1 and dry["skipped"].get("dnc") == 1
    assert dry["skipped"].get("opted_out_before") == 1
    assert dry["eligible"] >= 1                       # SIM-ACTIVE at least
    # NO automatic attestation: apply without the opt-in evidence is refused
    r = client.post("/god/sms-consent/sci/reconcile", headers=h, json=dict(body, apply=True))
    assert r.status_code == 422
    assert db.query(SmsConsentRecord).filter_by(consent_method="owner_attested_import").count() == 0

    # with evidence: only numbers that appear in the opt-in records are attested
    ev = dict(body, apply=True, evidence_phones=["(205) 555-0102", "+12055550151", "2055550199"])
    applied = client.post("/god/sms-consent/sci/reconcile", headers=h, json=ev).json()
    assert applied["created"] == 1 and applied["skipped"].get("no_opt_in_evidence", 0) >= 0
    assert applied["skipped"].get("opted_out_before") == 1        # +12055550151 had opted out: still skipped
    rec = (db.query(SmsConsentRecord).filter_by(organization_id=org_id, program="sci_poc_sms",
                                                phone_normalized=SIM_ACTIVE).one())
    assert rec.consent_method == "owner_attested_import" and "Mike Simmons" in rec.disclosure_text
    assert rec.lead_id == _lead(db, org_id, SIM_ACTIVE).id
    again = client.post("/god/sms-consent/sci/reconcile", headers=h, json=ev).json()
    assert again["created"] == 0 and again["skipped"].get("already_consented") == 1

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
    sms_programs.reconcile_existing(db, org_id, attested_by="Mike", evidence_reference="ref", apply=True,
                                     evidence_phones=[SIM_ACTIVE])
    monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
    # toll-free status unconfirmed -> refused
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([dict(TF_VERIFIED, verification_status="UNCONFIRMED")]))
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
        sms_programs.reconcile_existing(db, org_id, attested_by="Mike", evidence_reference="ref", apply=True,
                                     evidence_phones=[SIM_ACTIVE])
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
    assert f["planning_guide_link"] == identity.default_planning_guide() \
        == "https://sci-staging-backend.onrender.com/planning-guide"
    tpl = identity.render("Book: {booking_link} Guide: {planning_guide_link} Call {location_phone}.", f)
    # toll-free not confirmed verified: links and phone numbers stripped
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([dict(TF_VERIFIED, verification_status="UNCONFIRMED")]))
    out = enforce_sms_content_policy(tpl, **sms_programs.content_allowance(db_session, lead))
    assert "http" not in out and "555-0177" not in out
    # verified: carried
    monkeypatch.setenv(sms_campaigns.ENV_REGISTRY, json.dumps([TF_VERIFIED]))
    out = enforce_sms_content_policy(tpl, **sms_programs.content_allowance(db_session, lead))
    assert "https://book.example.com/gardens" in out and "555-0177" in out
    # a non-SCI lead keeps the CO3YNIF rule
    assert sms_programs.content_allowance(db_session, Lead(organization_id="other")) == {}


# ── 6. Phase 2: staging verification endpoints ──────────────────────────────

def test_staging_toll_free_round_trip_passes_end_to_end(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    with patch("twilio.rest.Client") as tw:
        out = client.post("/god/staging/sci/simulate/toll_free_round_trip", headers=h).json()
        tw.assert_not_called()
    assert out["pass"] is True, out
    assert out["sent"] == 0


def test_telephony_readout_reports_without_secrets(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    out = client.get("/god/sms-consent/sci/telephony", headers=h).json()
    assert out["env"]["TWILIO_AUTH_TOKEN"] is True and PLATFORM_TOKEN not in json.dumps(out)
    assert out["number_row"]["route_mode"] == "voicemail_only" and out["number_row"]["organization_is_sci"]
    assert out["sender_approved"] is True and out["sci_send_enabled"] is False
    assert out["expected_webhooks"]["voice_url"].endswith("/voice/inbound")
    # live read uses GET only; mocked here
    fake = MagicMock()
    fake.incoming_phone_numbers.list.return_value = [MagicMock(
        sid="PNx", sms_url="https://sci-staging-backend.onrender.com/sms/webhook/inbound", sms_method="POST",
        voice_url="https://elsewhere.example/voice", voice_method="POST", messaging_service_sid=None)]
    fake.messaging.v1.tollfree_verifications.list.return_value = [MagicMock(
        status="TWILIO_APPROVED", use_case_categories=["ACCOUNT_NOTIFICATIONS"], message_volume="1,000",
        date_updated="2026-09-01")]
    with patch("twilio.rest.Client", return_value=fake):
        live = client.get("/god/sms-consent/sci/telephony?live=true", headers=h).json()["twilio_live"]
    assert live["sms_webhook_matches"] is True and live["voice_webhook_matches"] is False
    assert live["toll_free_verification"][0]["status"] == "TWILIO_APPROVED"
    assert not fake.incoming_phone_numbers.create.called and not fake.incoming_phone_numbers.return_value.update.called


def test_locations_audit_lists_every_location_and_gaps(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    out = client.get("/god/sms-consent/sci/locations", headers=h).json()
    assert out["totals"]["locations"] == len(out["locations"]) >= 1
    gardens = next(r for r in out["locations"] if r["location"] == "Eastern Gate Memorial Gardens")
    assert gardens["greeting"] == "default" and "Eastern Gate Memorial Gardens" in gardens["greeting_text"]
    assert gardens["planning_guide_link"].startswith("https://")


def test_planning_guide_prefers_the_hosted_guide(client, db_session, monkeypatch):
    import uuid
    from app.models.program_models import LocationProfile, ProgramAsset
    from app.services.programs import identity
    h, org_id = _seed(client, db_session, monkeypatch)
    prof = (db_session.query(LocationProfile).filter(LocationProfile.organization_id == org_id,
                                                     LocationProfile.official_name == "Eastern Gate Memorial Gardens").one())
    assert identity.planning_guide_link(prof, db=db_session) == identity.default_planning_guide()
    tok = uuid.uuid4().hex
    db_session.add(ProgramAsset(organization_id=org_id, kind="flyer", title="Veteran Planning Guide",
                                campaign_family="veteran_planning_guide", category="veteran_planning_guide",
                                location_id=None, public_token=tok, is_active=True, version=1,
                                filename="guide.pdf", content_type="application/pdf", data=b"%PDF-1.4"))
    db_session.commit()
    link = identity.planning_guide_link(prof, db=db_session)
    assert link.endswith("/program-assets/%s" % tok) and link.startswith("https://")
    prof.brand_settings = json.dumps({"planning_guide_link": "https://example.org/gardens-guide"})
    db_session.commit()
    assert identity.planning_guide_link(prof, db=db_session) == "https://example.org/gardens-guide"


def test_public_planning_guide_page_is_served_without_login(client, monkeypatch):
    r = client.get("/planning-guide")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Your Pre-Planning Guide" in r.text and "<script" not in r.text.lower()
    assert "default-src 'none'" in r.headers.get("content-security-policy", "")
    monkeypatch.setenv("SCI_PLANNING_GUIDE_URL", "https://evosyspro.live/planning-guide")
    from app.services.programs import identity
    assert identity.default_planning_guide() == "https://evosyspro.live/planning-guide"


def test_voicemail_notifies_admins_when_no_rep_or_primary_contact(client, db_session, monkeypatch, twilio_webhook):
    from app.models.models import User
    _mount()
    h, org_id = _seed(client, db_session, monkeypatch)
    lead = _lead(db_session, org_id, SIM_ACTIVE)
    lead.assigned_to_id = None
    admin = User(organization_id=org_id, email="sci-admin@example.com", full_name="SCI Admin",
                 password_hash="x", role="org_admin", is_active=True, must_change_password=False)
    db_session.add(admin)
    db_session.commit()
    _call(twilio_webhook, SIM_ACTIVE, "CAtf9")
    row = db_session.query(InboundCallLog).filter_by(call_sid="CAtf9").one()
    rec = _re(9)
    twilio_webhook("/voice/inbound/voicemail-recording?log_id=%s" % row.id,
                   {"CallSid": "CAtf9", "RecordingSid": rec, "RecordingStatus": "completed",
                    "RecordingUrl": "https://api.twilio.com/x/%s" % rec, "RecordingDuration": "5"},
                   account_sid=PLATFORM_SID, auth_token=PLATFORM_TOKEN)
    assert db_session.query(Notification).filter(Notification.user_id == admin.id,
                                                 Notification.message.like("Voicemail from%")).count() >= 1


def test_oaklawn_contacts_are_held_from_sci_texts(db_session, sample_org, sample_lead, monkeypatch):
    from app.services.programs import identity
    from types import SimpleNamespace
    sample_org.name = sms_programs.SCI_ORG_NAME
    db_session.commit()
    monkeypatch.setattr(identity, "location_profile_for_lead",
                        lambda db, lead: SimpleNamespace(official_name="Oaklawn Central Care Center"))
    assert "LOCATION_UNVERIFIED" in sms_programs.sci_send_refusal(db_session, sample_lead,
                                                                 from_number=TF)
    monkeypatch.setattr(identity, "location_profile_for_lead",
                        lambda db, lead: SimpleNamespace(official_name="Alabama Heritage Cemetery"))
    assert "LOCATION_UNVERIFIED" not in (sms_programs.sci_send_refusal(db_session, sample_lead,
                                                                      from_number=TF) or [])


def test_designated_test_phone_is_staging_only_and_never_clashes(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    dry = client.post("/god/staging/sci/test-phone", headers=h, json={"phone": "(214) 555-0161"}).json()
    assert dry["dry_run"] is True and dry["plan"]["phone_last4"] == "0161"
    assert client.post("/god/staging/sci/test-phone", headers=h,
                       json={"phone": SIM_ACTIVE, "apply": True}).status_code == 409
    ok = client.post("/god/staging/sci/test-phone", headers=h, json={"phone": "2145550161", "apply": True}).json()
    assert ok["dry_run"] is False
    monkeypatch.setenv("APP_ENV", "production")
    assert client.post("/god/staging/sci/test-phone", headers=h, json={"phone": "2145550161"}).status_code == 404


def test_send_test_sms_reaches_only_the_test_contact_through_every_gate(client, db_session, monkeypatch):
    h, org_id = _seed(client, db_session, monkeypatch)
    assert client.post("/god/staging/sci/send-test-sms", headers=h, json={}).status_code in (409, 422)
    client.post("/god/staging/sci/test-phone", headers=h, json={"phone": "2145550162", "apply": True})
    # no consent / sending off -> refused before the provider
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(sid="SMlive", status="queued", error_code=None, error_message=None)
    with patch.object(sms_programs, "sci_sender", return_value=(fake, TF)):
        r = client.post("/god/staging/sci/send-test-sms", headers=h, json={})
        assert r.status_code == 422 and "SCI_SMS_DISABLED" in r.json()["detail"]
        fake.messages.create.assert_not_called()
        sms_programs.reconcile_existing(db_session, org_id, attested_by="Mike", evidence_reference="test phone",
                                        apply=True, evidence_phones=["2145550162"])
        monkeypatch.setenv(sms_programs.SCI_SEND_ENV, "on")
        r = client.post("/god/staging/sci/send-test-sms", headers=h, json={})
        assert r.status_code == 200, r.text
        assert fake.messages.create.call_count == 1
        kw = fake.messages.create.call_args.kwargs
        assert kw["from_"] == TF and kw["to"].endswith("2145550162")
        assert client.post("/god/staging/sci/send-test-sms", headers=h, json={}).status_code == 409
