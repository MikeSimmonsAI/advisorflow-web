"""Identity / consent regressions found in the intake audit.

1. An explicit capture (the /sell form, an operator adding an owner) must pass
   the SAME Lead-activation gate as the engine: a contact-only record that is
   do-not-contact never gets a fresh, contactable Lead.
2. SMS consent is per NUMBER: it is mirrored onto a Lead only when the Lead's
   own phone is the number that consented.
3. Editing a lead's phone drops the SMS consent that was given for the old one.
4. EvoSense's direct Lead fallback is linked to the contact intake kept.
5. The /sell fallback Lead is linked to the contact intake already committed.
6. Inbound CRM / social / fiber paths dedupe on the normalized phone and a
   case-insensitive email.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.intake_models import OrgContact
from app.models.models import Lead, Organization, Platform
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import (WholesaleEvent, WholesaleProperty,
                                         WholesaleSellerProfile)

from test_public_write_paths import crm_table  # noqa: F401
from test_wholesale_seller_sms_program import form, post, world  # noqa: F401


def _dnc_contact(db, org, **over):
    """A contact-only record (e.g. a buyer or partner) that is do-not-contact."""
    base = dict(organization_id=org.id, first_name="Pat", last_name="Seller",
                phone="+12145550123", email="pat@example.com", sms_status="dnc",
                email_status="suppressed")
    base.update(over)
    c = OrgContact(**base)
    db.add(c)
    db.commit()
    return c


# ── 1. DNC bypass on an explicit capture ────────────────────────────────────

def test_capture_one_refuses_a_lead_for_a_dnc_contact(world):
    from app.services.intake.capture import capture_one
    db, org = world["db"], world["orgs"]["evo"]
    c = _dnc_contact(db, org)
    cap = capture_one(db, org, {"first_name": "Pat", "last_name": "Seller",
                                "email": "pat@example.com", "phone": "(214) 555-0123"},
                      classification="new_inquiry", external=True)
    assert cap.lead is None and cap.lead_created is False
    assert cap.contact_id == c.id
    assert any(n.startswith("lead_not_activated:") for n in cap.notes)
    assert db.query(Lead).filter_by(organization_id=org.id).count() == 0


def test_a_dnc_contact_submitting_sell_is_saved_but_gets_no_contactable_lead(client, world):
    db, org = world["db"], world["orgs"]["evo"]
    c = _dnc_contact(db, org)
    r = post(client, form(submission_id="dnc-1", sms_consent=True))
    assert r.status_code == 201, r.text
    db.expire_all()
    # the inquiry itself is kept: property + seller profile
    prop = db.query(WholesaleProperty).filter_by(organization_id=org.id).one()
    profile = db.query(WholesaleSellerProfile).filter_by(property_id=prop.id).one()
    leads = db.query(Lead).filter_by(organization_id=org.id).all()
    assert len(leads) == 1
    lead = leads[0]
    assert profile.lead_id == lead.id
    # ...but the Lead is the DNC person's record, not a fresh contactable one
    assert lead.status == "dnc"
    assert lead.org_contact_id == c.id
    assert db.query(OrgContact).filter_by(id=c.id).one().lead_id == lead.id
    assert db.query(OrgContact).filter_by(organization_id=org.id).count() == 1
    # a refusal is not an intake error
    assert db.query(WholesaleEvent).filter_by(action="seller_inquiry.intake_error").count() == 0
    # consent evidence is still recorded, but no send path may use a DNC lead
    assert db.query(SmsConsentRecord).count() == 1
    assert lead.allow_sms is False and lead.allow_email is False


def test_an_operator_added_owner_who_is_dnc_is_not_made_contactable(world):
    from app.services import wholesale_service as svc
    db, org = world["db"], world["orgs"]["evo"]
    c = _dnc_contact(db, org, first_name="Owen", last_name="Owner", phone="+12145550188",
                     email="owen@example.com")
    prop = svc.create_property(db, org.id, None, {"street_address": "9 Oak St", "city": "Dallas",
                                                  "state": "TX", "zip_code": "75201"})
    db.commit()
    profile = svc.add_owner(db, org.id, None, prop,
                            {"first_name": "Owen", "last_name": "Owner",
                             "phone": "(214) 555-0188", "email": "owen@example.com"})
    db.commit()
    lead = db.query(Lead).filter_by(id=profile.lead_id).one()
    assert lead.status == "dnc" and lead.org_contact_id == c.id


# ── 2. consent mirrored only onto the consenting number ─────────────────────

def test_record_consent_does_not_mirror_onto_a_lead_with_another_number(world):
    from app.services import wholesale_sms as ws
    db, org = world["db"], world["orgs"]["evo"]
    lead = Lead(organization_id=org.id, first_name="A", last_name="B", phone="12145550999",
                status="new")
    db.add(lead)
    db.commit()
    rec = ws.record_consent(db, org.id, phone_raw="(214) 555-0123", disclosure_text="x",
                            disclosure_version="1", form_version="1", source_url=None, ip=None,
                            user_agent=None, lead=lead)
    assert rec.phone_normalized == "+12145550123"
    assert not lead.sms_consent and lead.sms_consent_timestamp is None
    # the matching number is still mirrored
    lead2 = Lead(organization_id=org.id, first_name="C", last_name="D", phone="12145550123",
                 status="new")
    db.add(lead2)
    db.commit()
    ws.record_consent(db, org.id, phone_raw="214.555.0123", disclosure_text="x",
                      disclosure_version="1", form_version="1", source_url=None, ip=None,
                      user_agent=None, lead=lead2)
    assert lead2.sms_consent is True and lead2.sms_consent_text == "x"


def test_a_returning_seller_consenting_on_a_new_number_does_not_mark_the_old_one(client, world):
    db = world["db"]
    post(client, form(submission_id="a"))
    post(client, form(submission_id="b", phone="(214) 555-0777", street_address="9 Elm St",
                      sms_consent=True))
    db.expire_all()
    lead = db.query(Lead).one()                     # matched by email
    assert lead.phone == "12145550123"
    assert not lead.sms_consent
    rec = db.query(SmsConsentRecord).one()
    assert rec.phone_normalized == "+12145550777"


def test_public_capture_consent_is_not_mirrored_onto_a_different_number():
    from app.services.public_capture import Consent, Submission, _apply_consent
    sub = Submission(kind="sms_optin", phone="(214) 555-0123",
                     consent=Consent(given=True, text="I agree"))
    other = SimpleNamespace(id="l1", phone="12145550999", sms_consent=False,
                            sms_consent_timestamp=None, custom_fields=None)
    _apply_consent(other, sub, datetime.utcnow())
    assert other.sms_consent is False and other.sms_consent_timestamp is None
    same = SimpleNamespace(id="l2", phone="12145550123", sms_consent=False,
                           sms_consent_timestamp=None, custom_fields=None)
    _apply_consent(same, sub, datetime.utcnow())
    assert same.sms_consent is True and same.sms_consent_text == "I agree"


# ── 3. generic lead edit: a new phone drops consent ─────────────────────────

def _consented(db, lead):
    lead.sms_consent = True
    lead.sms_consent_timestamp = datetime(2026, 9, 1, 12, 0)
    lead.sms_consent_ip = "203.0.113.9"
    lead.sms_consent_text = "I agree to texts"
    lead.sms_consent_source = "web form"
    db.commit()


def test_editing_a_lead_phone_clears_sms_consent(client, db_session, sample_lead, auth_headers):
    _consented(db_session, sample_lead)
    r = client.patch("/leads/%s" % sample_lead.id, headers=auth_headers,
                     json={"phone": "(214) 555-7777"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    lead = db_session.query(Lead).filter_by(id=sample_lead.id).one()
    assert lead.phone == "12145557777"
    assert not lead.sms_consent
    assert lead.sms_consent_timestamp is None and lead.sms_consent_text is None
    assert lead.sms_consent_ip is None and lead.sms_consent_source is None
    assert "does not carry" in (lead.notes or "")


def test_reformatting_the_same_phone_keeps_sms_consent(client, db_session, sample_lead,
                                                      auth_headers):
    _consented(db_session, sample_lead)                  # phone 12145559999
    r = client.patch("/leads/%s" % sample_lead.id, headers=auth_headers,
                     json={"phone": "(214) 555-9999"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    lead = db_session.query(Lead).filter_by(id=sample_lead.id).one()
    assert lead.sms_consent is True and lead.sms_consent_text == "I agree to texts"


# ── 4. EvoSense fallback links the contact ──────────────────────────────────

def test_evosense_fallback_lead_is_linked_to_the_existing_contact(db_session, sample_org,
                                                                   sample_advisor):
    from app.services.evosense import outreach as OUT
    db = db_session
    c = OrgContact(organization_id=sample_org.id, first_name="Rita", last_name="Rowe",
                   phone="+12145550401", sms_status="dnc")
    db.add(c)
    db.commit()
    prop = SimpleNamespace(id="p-1", organization_id=sample_org.id, street_address="44 Rd",
                           city="Dallas", state="TX", zip_code="75201", is_test=False)
    person = SimpleNamespace(full_name="Rita Rowe", lead_id=None, converged_contact_ref=None)
    cp = SimpleNamespace(kind="phone", value="+12145550401", converged_contact_ref=None)
    lead = OUT.ensure_lead(db, prop, person, cp, user=sample_advisor)
    db.commit()
    db.expire_all()
    assert db.query(Lead).filter_by(organization_id=sample_org.id).count() == 1
    assert db.query(OrgContact).filter_by(organization_id=sample_org.id).count() == 1
    lead = db.query(Lead).filter_by(id=lead.id).one()
    assert lead.org_contact_id == c.id
    assert db.query(OrgContact).filter_by(id=c.id).one().lead_id == lead.id
    assert lead.status == "dnc"
    assert person.lead_id == lead.id and person.converged_contact_ref == c.id


# ── 5. /sell fallback links the contact intake committed ────────────────────

def test_sell_fallback_links_the_committed_contact(client, world, monkeypatch):
    from app.services.intake import capture as CAP
    db, org = world["db"], world["orgs"]["evo"]
    made = {}

    def no_lead(db_, org_, record, **k):
        # intake committed a contact and then produced no Lead
        c = OrgContact(organization_id=org_.id, first_name="Pat", last_name="Seller",
                       phone="+12145550123", email="pat@example.com", sms_status="ready")
        db_.add(c)
        db_.commit()
        made["id"] = c.id
        return CAP.CaptureResult(batch_id="b-x", contact_id=c.id, lead=None, match="new")
    monkeypatch.setattr(CAP, "capture_one", no_lead)
    r = post(client, form(submission_id="fb-1"))
    assert r.status_code == 201, r.text
    db.expire_all()
    lead = db.query(Lead).filter_by(organization_id=org.id).one()
    assert lead.org_contact_id == made["id"]
    assert db.query(OrgContact).filter_by(id=made["id"]).one().lead_id == lead.id
    assert lead.status != "dnc"
    assert db.query(WholesaleEvent).filter_by(action="seller_inquiry.intake_error").count() == 1


# ── 6. normalized-phone / case-insensitive-email dedupe ─────────────────────

def _org(db, token=None):
    p = Platform(name="Dedupe", slug="dedupe-%s" % (token or "x"))
    db.add(p)
    db.commit()
    org = Organization(name="Dedupe Org %s" % token, slug="dedupe-org-%s" % token,
                       platform_id=p.id, is_active=True, plan="standard",
                       social_webhook_token=token)
    db.add(org)
    db.commit()
    return org


def test_crm_inbound_dedupes_formatted_phones_and_email_case(db_session, crm_table):
    from app.services.crm_service import import_inbound_leads
    org = _org(db_session, "crm")
    other = _org(db_session, "crm-other")
    r1 = import_inbound_leads(db_session, org.id, [
        {"first_name": "A", "phone": "(214) 555-0101", "email": "A@Example.com"}])
    r2 = import_inbound_leads(db_session, org.id, [
        {"first_name": "A", "phone": "214-555-0101"},
        {"first_name": "A", "phone": "+1 214 555 0101"},
        {"first_name": "B", "email": "a@example.COM"}])
    assert r1["created"] == 1 and r2["created"] == 0
    assert db_session.query(Lead).filter_by(organization_id=org.id).count() == 1
    assert db_session.query(Lead).filter_by(organization_id=org.id).one().phone == "12145550101"
    # org-scoped: the same number in another org is a new lead there
    import_inbound_leads(db_session, other.id, [{"first_name": "A", "phone": "2145550101"}])
    assert db_session.query(Lead).filter_by(organization_id=other.id).count() == 1


def test_social_webhook_dedupes_formatted_phones_and_email_case(db_session):
    from app.routers.social_webhooks_router import _upsert_social_lead
    org = _org(db_session, "soc")
    a = _upsert_social_lead(db_session, org, "A", "B", "(214) 555-0202", None, "facebook")
    db_session.commit()
    b = _upsert_social_lead(db_session, org, "A", "B", "+12145550202", None, "instagram")
    assert a.id == b.id
    c = _upsert_social_lead(db_session, org, "C", "D", None, "Casey@Example.com", "tiktok")
    db_session.commit()
    d = _upsert_social_lead(db_session, org, "C", "D", None, "casey@example.com", "facebook")
    assert c.id == d.id
    assert db_session.query(Lead).filter_by(organization_id=org.id).count() == 2
