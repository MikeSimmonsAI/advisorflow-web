"""EvoSys Wholesale seller inquiry + seller SMS program (A2P Low Volume Mixed).

The rules this file holds the platform to:

  * a public seller inquiry lands in exactly the organization its configured
    intake key names - never one the payload names, never another tenant;
  * SMS consent is separate, optional and recorded only when given, with the
    SERVER's timestamp and the verbatim disclosure;
  * PHONE KNOWN IS NOT PERMISSION: every Wholesale send path refuses a seller
    without program consent, with machine-readable reasons, and nothing - not
    an AI, a strategy, a cadence - can skip the gate;
  * STOP / DNC / suppression / quiet hours / no Messaging Service all fail closed.
"""
from datetime import datetime, timedelta

import pytest

from app.models.models import Lead, Organization, Platform, SuppressionEntry, User
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import (WholesaleDeal, WholesaleEvent, WholesaleProperty,
                                         WholesaleSellerProfile, WholesaleSettings)
from app.services import wholesale_sms as ws
from app.services.auth_service import create_access_token, hash_password

KEY_A = "evo-test-intake-key-aaaaaaaaaaaa"
KEY_B = "other-test-intake-key-bbbbbbbbbb"
MG = "MG" + "a" * 32
CM = "CM" + "b" * 32
DISCLOSURE = ("By checking this box, I agree to receive SMS text messages from EVO Integrated "
              "Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property "
              "inquiry, including follow-up questions, appointment scheduling, and transaction "
              "updates. Message frequency varies. Message and data rates may apply. Reply STOP "
              "to unsubscribe, HELP for help. Consent is not a condition of any service. View "
              "our Privacy Policy and Terms.")
# 10:00 in Dallas (CDT, UTC-5) - inside the recipient's window.
DAYTIME = datetime(2026, 9, 25, 15, 0)
NIGHT = datetime(2026, 9, 25, 8, 0)          # 03:00 in Dallas


def form(**over):
    base = {"full_name": "Pat Seller", "phone": "(214) 555-0123", "email": "pat@example.com",
            "street_address": "123 Main St", "city": "Dallas", "state": "TX",
            "zip_code": "75201", "property_condition": "fair", "timeline": "90_days",
            "reason_for_selling": "Relocating", "preferred_contact_method": "phone",
            "notes": "Tenant moves out in November.", "source_url": "https://evosyspro.live/sell",
            "disclosure_version": "evo-wholesale-2026-09-25", "form_version": "sell-v1",
            "disclosure_text": DISCLOSURE, "ip": "203.0.113.9", "user_agent": "pytest-browser",
            "submission_id": "sub-" + str(id(over)) + str(len(over))}
    base.update(over)
    return base


def _org(db, platform, name, key=None, *, twilio=True, program=False, mg=None):
    from app.utils.crypto import encrypt_value
    org = Organization(name=name, slug=name.lower().replace(" ", "-"), plan="standard",
                       industry="real_estate", platform_id=platform.id)
    if twilio:
        org.org_twilio_account_sid = "AC" + "0" * 32
        org.org_twilio_auth_token_encrypted = encrypt_value("token")
    db.add(org)
    db.flush()
    db.add(WholesaleSettings(organization_id=org.id, public_intake_key=key,
                             sms_program_enabled=program, sms_messaging_service_sid=mg,
                             public_contact_email="wholesale@evosyspro.live"))
    db.commit()
    return org


def _user(db, org, role="org_admin", email=None):
    u = User(organization_id=org.id, email=email or "%s@%s.test" % (role, org.slug),
             password_hash=hash_password("TestPass123!"), full_name=role.title(), role=role,
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _headers(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture()
def world(db_session):
    db = db_session
    evo = Platform(name="EvoSys Pro", slug="evosyspro")
    other = Platform(name="Other Brand", slug="otherbrand")
    db.add_all([evo, other])
    db.flush()
    orgs = {
        "evo": _org(db, evo, "EVO Integrated Solutions LLC", KEY_A),
        "other": _org(db, evo, "Other Wholesaler", KEY_B),
        "restland": _org(db, other, "Restland Memorial Test"),
        "atlantis": _org(db, evo, "Atlantis Light and Power"),
        "wupa": _org(db, evo, "WUPA"),
    }
    return {"db": db, "orgs": orgs}


def post(client, payload, key=KEY_A):
    return client.post("/site-intake/wholesale/%s/seller-inquiry" % key, json=payload)


def enable(db, org, mg=MG):
    s = db.query(WholesaleSettings).filter_by(organization_id=org.id).one()
    s.sms_program_enabled = True
    s.sms_messaging_service_sid = mg
    s.sms_campaign_sid = CM
    db.commit()


# ── Intake: consent paths ───────────────────────────────────────────────────

def test_opted_in_submission_creates_canonical_records_and_consent(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    before = datetime.utcnow()
    r = post(client, form(sms_consent=True))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["sms_consent_recorded"] is True and body["reference"].startswith("SI-")
    assert set(body) == {"success", "reference", "sms_consent_recorded"}   # nothing internal

    prop = db.query(WholesaleProperty).filter_by(organization_id=evo.id).one()
    assert prop.acquisition_source == "seller_inquiry" and prop.street_address == "123 Main St"
    deal = db.query(WholesaleDeal).filter_by(property_id=prop.id).one()
    profile = db.query(WholesaleSellerProfile).filter_by(property_id=prop.id).one()
    lead = db.query(Lead).get(profile.lead_id)
    assert lead.organization_id == evo.id and lead.source_category == ws.SOURCE_CATEGORY
    assert deal.seller_lead_id == lead.id
    assert profile.preferred_contact_method == "phone" and profile.timeline == "90_days"

    rec = db.query(SmsConsentRecord).one()
    assert rec.organization_id == evo.id and rec.program == ws.PROGRAM
    assert rec.phone_normalized == "+12145550123" and rec.status == "opted_in"
    assert rec.disclosure_text == DISCLOSURE
    assert rec.disclosure_version == "evo-wholesale-2026-09-25" and rec.form_version == "sell-v1"
    assert rec.source_url == "https://evosyspro.live/sell" and rec.form_id == ws.FORM_ID
    assert rec.ip_address == "203.0.113.9" and rec.user_agent == "pytest-browser"
    assert rec.consent_method == "web_form_checkbox"
    assert rec.lead_id == lead.id and rec.deal_id == deal.id and rec.property_id == prop.id
    assert before - timedelta(seconds=5) <= rec.consented_at <= datetime.utcnow() + timedelta(seconds=5)
    assert lead.sms_consent is True                                   # mirrored
    # no Messaging Service yet: the confirmation was refused, and it says why
    assert "MESSAGING_SERVICE_NOT_CONFIGURED" in rec.confirmation_status
    ev = db.query(WholesaleEvent).filter_by(action="seller_inquiry.received").one()
    assert "SMS consent: YES" in ev.summary


def test_non_opted_in_submission_records_no_consent(client, world):
    db = world["db"]
    r = post(client, form(sms_consent=False))
    assert r.status_code == 201 and r.json()["sms_consent_recorded"] is False
    assert db.query(SmsConsentRecord).count() == 0
    lead = db.query(Lead).filter_by(source_category=ws.SOURCE_CATEGORY).one()
    assert not lead.sms_consent
    assert "SMS consent: NO" in db.query(WholesaleEvent).filter_by(
        action="seller_inquiry.received").one().summary


def test_checkbox_omitted_is_no_consent(client, world):
    payload = form()
    payload.pop("sms_consent", None)
    r = post(client, payload)
    assert r.status_code == 201 and r.json()["sms_consent_recorded"] is False
    assert world["db"].query(SmsConsentRecord).count() == 0


def test_client_supplied_consent_timestamp_is_ignored(client, world):
    r = post(client, form(sms_consent=True, consented_at="2001-01-01T00:00:00Z",
                          submitted_at="2001-01-01T00:00:00Z"))
    assert r.status_code == 201
    rec = world["db"].query(SmsConsentRecord).one()
    assert rec.consented_at.year >= 2026


def test_consent_without_the_shown_disclosure_is_refused(client, world):
    r = post(client, form(sms_consent=True, disclosure_text="yes text me"))
    assert r.status_code == 422 and "sms_consent" in r.json()["detail"]["errors"]
    assert world["db"].query(WholesaleProperty).count() == 0


def test_preferring_text_without_consent_is_refused(client, world):
    r = post(client, form(sms_consent=False, preferred_contact_method="sms"))
    assert r.status_code == 422
    assert "preferred_contact_method" in r.json()["detail"]["errors"]


@pytest.mark.parametrize("phone", ["555-1234", "abc", "(014) 555-0123", "", "12345678901234"])
def test_malformed_phone_is_rejected_and_nothing_is_written(client, world, phone):
    r = post(client, form(phone=phone, sms_consent=True))
    assert r.status_code == 422 and "phone" in r.json()["detail"]["errors"]
    db = world["db"]
    assert db.query(Lead).count() == 0 and db.query(SmsConsentRecord).count() == 0


def test_oversized_and_malformed_fields_are_refused(client, world):
    r = post(client, form(zip_code="ABCDE", state="Texas", timeline="yesterday",
                          property_condition="haunted", email="nope"))
    errs = r.json()["detail"]["errors"]
    assert r.status_code == 422
    assert {"zip_code", "state", "timeline", "property_condition", "email"} <= set(errs)
    r = post(client, form(notes="x" * 50000, full_name="y" * 5000))
    assert r.status_code == 201                                    # truncated, not crashed
    lead = world["db"].query(Lead).one()
    assert len(lead.first_name or "") <= 120


# ── Intake: duplicates, replay ──────────────────────────────────────────────

def test_same_submission_twice_writes_once(client, world):
    db = world["db"]
    p = form(sms_consent=True, submission_id="dup-abc-123")
    a, b = post(client, p), post(client, p)
    assert a.status_code == b.status_code == 201
    assert a.json()["reference"] == b.json()["reference"]
    assert db.query(Lead).count() == 1 and db.query(WholesaleProperty).count() == 1
    assert db.query(SmsConsentRecord).count() == 1
    assert db.query(WholesaleEvent).filter_by(action="seller_inquiry.received").count() == 1


def test_same_seller_same_property_new_submission_reuses_records(client, world):
    db = world["db"]
    post(client, form(submission_id="one"))
    r = post(client, form(submission_id="two", street_address="123  main st.",
                          phone="214.555.0123", timeline="asap"))
    assert r.status_code == 201
    assert db.query(Lead).count() == 1 and db.query(WholesaleProperty).count() == 1
    assert db.query(WholesaleSellerProfile).one().timeline == "asap"
    assert "(repeat)" in db.query(WholesaleEvent).filter_by(
        action="seller_inquiry.received").order_by(WholesaleEvent.created_at.desc()).first().summary


# ── Routing and tenant isolation ────────────────────────────────────────────

def test_submission_lands_only_in_the_key_s_org_and_payload_cannot_choose(client, world):
    db, o = world["db"], world["orgs"]
    r = post(client, form(organization_id=o["other"].id, org_id=o["restland"].id,
                          platform_slug="otherbrand", sms_consent=True))
    assert r.status_code == 201
    assert {l.organization_id for l in db.query(Lead).all()} == {o["evo"].id}
    assert {c.organization_id for c in db.query(SmsConsentRecord).all()} == {o["evo"].id}
    for name in ("other", "restland", "atlantis", "wupa"):
        assert db.query(Lead).filter_by(organization_id=o[name].id).count() == 0


def test_the_other_key_routes_to_the_other_org(client, world):
    db, o = world["db"], world["orgs"]
    assert post(client, form(), key=KEY_B).status_code == 201
    assert db.query(Lead).one().organization_id == o["other"].id


@pytest.mark.parametrize("key", ["unknown-key-xxxxxxxxxxxxxxxxxxxx", "short", "../../etc",
                                 "EVO Integrated Solutions LLC"])
def test_unknown_or_malformed_key_gets_the_neutral_refusal(client, world, key):
    r = post(client, form(), key=key)
    assert r.status_code in (404, 503)
    assert world["db"].query(Lead).count() == 0
    if r.status_code == 503:
        assert "EVO" not in r.text and "Other" not in r.text


def test_inactive_or_unentitled_org_refuses(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    evo.enabled_features = '["leads"]'
    db.commit()
    assert post(client, form()).status_code == 503
    evo.enabled_features = None
    evo.is_active = False
    db.commit()
    assert post(client, form()).status_code == 503
    assert db.query(Lead).count() == 0


def test_consent_records_are_invisible_to_another_tenant(client, world):
    db, o = world["db"], world["orgs"]
    post(client, form(sms_consent=True))
    lead = db.query(Lead).one()
    mine = _headers(db, _user(db, o["evo"]))
    theirs = _headers(db, _user(db, o["other"]))
    ok = client.get("/wholesale/sms/consents", params={"lead_id": lead.id}, headers=mine)
    assert ok.status_code == 200 and ok.json()["sms_consent"] is True
    assert ok.json()["consents"][0]["disclosure_text"] == DISCLOSURE
    deal = db.query(WholesaleDeal).one()
    assert client.get("/wholesale/sms/consents", params={"deal_id": deal.id},
                      headers=mine).json()["status"] == "opted_in"
    assert client.get("/wholesale/sms/consents", params={"lead_id": lead.id},
                      headers=theirs).status_code == 404
    assert client.get("/wholesale/sms/consents", params={"deal_id": deal.id},
                      headers=theirs).status_code == 404
    leak = client.get("/wholesale/sms/consents", params={"phone": "2145550123"}, headers=theirs)
    assert leak.status_code == 200 and leak.json()["consents"] == []
    assert client.get("/wholesale/sms/eligibility", params={"lead_id": lead.id},
                      headers=theirs).status_code == 404
    assert client.get("/wholesale/sms/consents", params={"lead_id": lead.id}).status_code == 401


@pytest.mark.parametrize("name", ["other", "restland", "atlantis", "wupa"])
def test_no_other_org_inherits_evo_consent(world, name):
    db, o = world["db"], world["orgs"]
    enable(db, o["evo"])
    enable(db, o[name])
    ws.record_consent(db, o["evo"].id, phone_raw="2145550123", disclosure_text=DISCLOSURE,
                      disclosure_version="v", form_version="v", source_url=None, ip=None,
                      user_agent=None)
    db.commit()
    assert ws.check_eligibility(db, o["evo"].id, "2145550123", now=DAYTIME)["eligible"]
    res = ws.check_eligibility(db, o[name].id, "2145550123", now=DAYTIME)
    assert not res["eligible"] and ws.NO_SMS_CONSENT in res["reasons"]


# ── THE GATE ────────────────────────────────────────────────────────────────

@pytest.fixture()
def consented(client, world):
    """A seller who opted in through the form, program on, Messaging Service set."""
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    assert post(client, form(sms_consent=True)).status_code == 201
    lead = db.query(Lead).filter_by(organization_id=evo.id).one()
    return {"db": db, "org": evo, "lead": lead, "world": world}


def test_gate_permits_a_consented_seller(consented):
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    res = ws.check_eligibility(db, org.id, lead.phone, lead=lead, now=DAYTIME)
    assert res["eligible"] and res["reasons"] == [] and res["consent_id"]


def test_gate_without_consent(world):
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    res = ws.check_eligibility(db, evo.id, "2145550199", now=DAYTIME)
    assert res["reasons"] == [ws.NO_SMS_CONSENT]


def test_normalized_phone_matching_across_formats(consented):
    db, org = consented["db"], consented["org"]
    for form_ in ("2145550123", "+12145550123", "12145550123", "(214) 555-0123", "214.555.0123"):
        assert ws.check_eligibility(db, org.id, form_, now=DAYTIME)["eligible"], form_


def test_gate_after_stop_reply_through_the_real_inbound_path(consented):
    from app.routers.sms_router import process_inbound_sms
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    process_inbound_sms(db, org_id=org.id, advisor=None, From="+12145550123",
                        Body="STOP", MessageSid="SMstop0001")
    db.expire_all()
    rec = db.query(SmsConsentRecord).one()
    assert rec.status == "opted_out" and rec.opt_out_keyword == "STOP"
    assert rec.opted_out_at is not None and rec.opt_out_source == "reply_stop"
    res = ws.check_eligibility(db, org.id, lead.phone, now=DAYTIME)
    assert {ws.OPTED_OUT, ws.SUPPRESSED, ws.DNC} <= set(res["reasons"])


def test_opt_out_persists_and_a_new_form_cannot_undo_suppression(client, consented):
    from app.routers.sms_router import process_inbound_sms
    db, org = consented["db"], consented["org"]
    process_inbound_sms(db, org_id=org.id, advisor=None, From="+12145550123",
                        Body="stop", MessageSid="SMstop0002")
    assert post(client, form(sms_consent=True, submission_id="again")).status_code == 201
    db.expire_all()
    assert db.query(SmsConsentRecord).count() == 2          # new evidence, old kept
    res = ws.check_eligibility(db, org.id, "2145550123", now=DAYTIME)
    assert not res["eligible"] and ws.SUPPRESSED in res["reasons"]


def test_stop_from_a_number_with_no_lead_is_still_honoured(world):
    from app.routers.sms_router import process_inbound_sms
    db, evo = world["db"], world["orgs"]["evo"]
    ws.record_consent(db, evo.id, phone_raw="2145550177", disclosure_text=DISCLOSURE,
                      disclosure_version="v", form_version="v", source_url=None, ip=None,
                      user_agent=None)
    db.commit()
    out = process_inbound_sms(db, org_id=evo.id, advisor=None, From="+12145550177",
                              Body="STOP", MessageSid="SMnolead1")
    assert out["status"] == "no_lead"
    db.expire_all()
    assert db.query(SmsConsentRecord).one().status == "opted_out"
    assert db.query(SuppressionEntry).filter_by(organization_id=evo.id).count() == 1


def test_gate_with_dnc(consented):
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    lead.status = "dnc"
    db.commit()
    assert ws.DNC in ws.check_eligibility(db, org.id, lead.phone, now=DAYTIME)["reasons"]


def test_gate_with_suppression(consented):
    from app.services.compliance_service import add_suppression_entry
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    add_suppression_entry(db, org.id, lead.phone, reason="manual")
    assert ws.check_eligibility(db, org.id, lead.phone, now=DAYTIME)["reasons"] == [ws.SUPPRESSED]


def test_gate_in_quiet_hours(consented):
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    assert ws.check_eligibility(db, org.id, lead.phone, lead=lead,
                                now=NIGHT)["reasons"] == [ws.QUIET_HOURS]


def test_gate_without_messaging_service_or_program_or_with_kill_switch(consented, monkeypatch):
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    s = db.query(WholesaleSettings).filter_by(organization_id=org.id).one()
    s.sms_messaging_service_sid = None
    db.commit()
    assert ws.check_eligibility(db, org.id, lead.phone, now=DAYTIME)["reasons"] == \
        [ws.MESSAGING_SERVICE_NOT_CONFIGURED]
    s.sms_messaging_service_sid = MG
    s.sms_program_enabled = False
    db.commit()
    assert ws.check_eligibility(db, org.id, lead.phone, now=DAYTIME)["reasons"] == \
        [ws.PROGRAM_DISABLED]
    s.sms_program_enabled = True
    db.commit()
    monkeypatch.setenv(ws.KILL_SWITCH_ENV, "1")
    assert ws.PROGRAM_DISABLED in ws.check_eligibility(db, org.id, lead.phone,
                                                        now=DAYTIME)["reasons"]
    monkeypatch.delenv(ws.KILL_SWITCH_ENV)
    org.org_twilio_auth_token_encrypted = None
    db.commit()
    assert ws.MESSAGING_SERVICE_NOT_CONFIGURED in ws.check_eligibility(
        db, org.id, lead.phone, now=DAYTIME)["reasons"]


def test_gate_program_and_org_mismatch(consented):
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    other = consented["world"]["orgs"]["other"]
    enable(db, other)
    assert ws.PROGRAM_MISMATCH in ws.check_eligibility(
        db, org.id, lead.phone, program="marketing_blast", now=DAYTIME)["reasons"]
    res = ws.check_eligibility(db, other.id, lead.phone, lead=lead, now=DAYTIME)
    assert ws.PROGRAM_MISMATCH in res["reasons"] and ws.NO_SMS_CONSENT in res["reasons"]


def test_found_number_is_not_consent(world):
    """Owner identified, phone found and validated, mobile - still NO."""
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    lead = Lead(organization_id=evo.id, first_name="Found", phone="12145550155",
                status="new", source_category="evosense", is_test=False)
    db.add(lead)
    db.commit()
    res = ws.check_eligibility(db, evo.id, lead.phone, lead=lead, now=DAYTIME)
    assert res["reasons"] == [ws.NO_SMS_CONSENT]


# ── Every send path goes through the gate ───────────────────────────────────

class _FakeTwilio:
    sent = []

    def __init__(self, *a, **k):
        self.messages = self

    def create(self, **kwargs):
        _FakeTwilio.sent.append(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(sid="SM" + "f" * 32, status="queued", error_code=None,
                               error_message=None)


@pytest.fixture()
def fake_twilio(monkeypatch):
    import twilio.rest
    _FakeTwilio.sent = []
    monkeypatch.setattr(twilio.rest, "Client", _FakeTwilio)
    return _FakeTwilio


def _advisor(db, org):
    return _user(db, org, role="advisor", email="adv@%s.test" % org.slug)


@pytest.mark.parametrize("source", [None, "manual", "cadence", "ai_conversation",
                                    "pipeline_auto_reply", "ai_employee"])
def test_no_send_path_can_text_a_seller_without_consent(world, fake_twilio, source):
    from app.services import sms_service
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    lead = Lead(organization_id=evo.id, first_name="Found", phone="12145550155",
                status="new", source_category="evosense", is_test=False, state="TX")
    db.add(lead)
    db.commit()
    with pytest.raises(ws.WholesaleSmsBlocked) as exc:
        sms_service.send_sms(db, _advisor(db, evo), lead, "Hi, want to sell?",
                             send_source=source)
    assert ws.NO_SMS_CONSENT in exc.value.reasons
    assert isinstance(exc.value, ValueError)            # every caller treats it as blocked
    assert fake_twilio.sent == []


def test_consented_seller_is_sent_only_through_the_messaging_service(consented, fake_twilio,
                                                                     monkeypatch):
    from app.services import contact_hours, sms_service
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    monkeypatch.setattr(contact_hours, "check", lambda *a, **k: {"permitted": True, "code": "ok"})
    sms_service.send_sms(db, _advisor(db, org), lead, "EvoSys Wholesale: Following up on your "
                         "property. Reply STOP to unsubscribe.", include_booking_link=True)
    assert len(fake_twilio.sent) == 1
    kw = fake_twilio.sent[0]
    assert kw["messaging_service_sid"] == MG and "from_" not in kw
    assert "http" not in kw["body"]                      # no link was minted


def test_mms_to_a_seller_is_refused(consented, fake_twilio):
    from app.services import sms_service
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    with pytest.raises(ws.WholesaleSmsBlocked) as exc:
        sms_service.send_mms(db, _advisor(db, org), lead, "photo", "https://x.test/a.png")
    assert exc.value.reasons == ["PROGRAM_MMS_NOT_REGISTERED"]
    assert fake_twilio.sent == []


def test_direct_twilio_paths_refuse_sellers_and_leave_others_alone(consented, sample_lead):
    db, org, lead = consented["db"], consented["org"], consented["lead"]
    assert ws.refusal_for_phone(db, org.id, lead.phone, path="appointment_reminder")
    assert ws.refusal_for_phone(db, sample_lead.organization_id, sample_lead.phone,
                                path="appointment_reminder") is None


def test_non_wholesale_leads_are_untouched_by_the_gate(db_session, sample_lead):
    assert ws.enforce_for_lead(db_session, sample_lead) is None


def test_opt_in_confirmation_sends_through_the_gate_when_configured(client, world, fake_twilio,
                                                                     monkeypatch):
    from app.services import contact_hours
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    monkeypatch.setattr(contact_hours, "check", lambda *a, **k: {"permitted": True, "code": "ok"})
    # a seller page with an assigned operator: the confirmation needs a sender user
    op = _user(db, evo, role="org_admin")
    from app.services import wholesale_service
    orig = wholesale_service.attach_seller

    def assigning(db_, org_id, user, prop, data, **kw):
        prop.assigned_to_id = op.id
        return orig(db_, org_id, user, prop, data, **kw)
    monkeypatch.setattr(wholesale_service, "attach_seller", assigning)
    assert post(client, form(sms_consent=True)).status_code == 201
    rec = db.query(SmsConsentRecord).one()
    assert rec.confirmation_status == "sent"
    assert fake_twilio.sent[0]["messaging_service_sid"] == MG
    assert fake_twilio.sent[0]["body"].startswith("EvoSys Wholesale: You're subscribed")
    assert "Reply STOP to opt out, HELP for help." in fake_twilio.sent[0]["body"]


# ── Settings ────────────────────────────────────────────────────────────────

def test_only_admins_configure_the_program_and_values_are_validated(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    adv = _headers(db, _advisor(db, evo))
    adm = _headers(db, _user(db, evo))
    assert client.patch("/wholesale/settings", json={"sms_program_enabled": True},
                        headers=adv).status_code == 403
    assert client.patch("/wholesale/settings", json={"sms_messaging_service_sid": "MG123"},
                        headers=adm).status_code == 400
    assert client.patch("/wholesale/settings", json={"public_intake_key": KEY_B},
                        headers=adm).status_code == 409
    r = client.patch("/wholesale/settings", headers=adm, json={
        "sms_messaging_service_sid": MG, "sms_campaign_sid": CM,
        "sms_sender_number": "(469) 555-0100", "sms_program_enabled": True})
    assert r.status_code == 200, r.text
    got = client.get("/wholesale/settings", headers=adm).json()
    assert got["sms_sender_number"] == "+14695550100" and got["sms_program_enabled"] is True
    assert got["public_intake_key_set"] is True and KEY_A not in str(got)
    st = client.get("/wholesale/sms/status", headers=adm).json()
    assert st["messaging_service_configured"] and st["can_send"]


def test_manual_opt_out_endpoint(client, consented):
    db, org = consented["db"], consented["org"]
    h = _headers(db, _user(db, org))
    r = client.post("/wholesale/sms/opt-out", json={"phone": "214-555-0123"}, headers=h)
    assert r.status_code == 200 and r.json()["consents_withdrawn"] == 1
    assert ws.OPTED_OUT in ws.check_eligibility(db, org.id, "2145550123", now=DAYTIME)["reasons"]


def test_public_intake_is_rate_limited(client, world):
    codes = [post(client, form(submission_id="rl-%d" % i, phone="21455501%02d" % i)).status_code
             for i in range(25)]
    assert 429 in codes and codes[0] == 201
