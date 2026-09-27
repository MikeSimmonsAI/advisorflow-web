"""Contactability and communication eligibility.

The rules held here:
  * HAVING contact data is not PERMISSION to use it - a found mobile number is
    never SMS consent, a found email address is never permission to email;
  * a seller who wrote to us may be called back by a person, and texted only
    with consent of record on an operational program;
  * DNC / opt-out / suppression make a person DO_NOT_CONTACT on every channel;
  * conflicting identity stops automation (REVIEW_REQUIRED);
  * nothing crosses tenants: consent in one workspace is nothing in another.
"""
from app.models.models import Lead, SuppressionEntry
from app.models.wholesale_models import WholesaleSettings
from app.services import communication_eligibility as CE
from app.services import contactability as CB

from test_wholesale_seller_sms_program import (DAYTIME, enable, form, post,  # noqa: F401
                                               world)


def _seller(db):
    return db.query(Lead).one()


def _phone(value="+12145550123", **kw):
    return dict({"id": "cp-" + value, "kind": "phone", "value": value, "status": "active",
                 "line_type": "mobile", "confidence": 80, "source": "provider"}, **kw)


def _email(value="owner@example.com", **kw):
    return dict({"id": "cp-" + value, "kind": "email", "value": value, "status": "active",
                 "confidence": 80, "source": "provider"}, **kw)


# ── seller-originated inquiries ─────────────────────────────────────────────

def test_a_seller_with_sms_consent_is_sms_contactable_only_when_the_program_is_on(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    post(client, form(submission_id="c1", sms_consent=True))
    lead = _seller(db)
    before = CB.for_seller(db, evo.id, lead)
    # Consent exists, but the program is off: permitted, not operational. A
    # person may still call them back - they asked us to.
    assert before["channels"]["sms"]["state"] == "NOT_OPERATIONAL"
    assert before["channels"]["sms"]["permitted"] is True
    assert before["state"] == CB.CONTACTABLE_OTHER
    enable(db, evo)
    after = CB.for_seller(db, evo.id, lead)
    assert after["state"] == CB.CONTACTABLE_SMS
    assert after["channels"]["sms"]["permission_basis"] == "SMS_CONSENT_OF_RECORD"


def test_a_seller_without_sms_consent_is_never_sms_contactable(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    post(client, form(submission_id="c2", sms_consent=False))
    res = CB.for_seller(db, evo.id, _seller(db))
    assert res["channels"]["sms"]["state"] == "NO_PERMISSION"
    assert "NO_SMS_CONSENT" in res["channels"]["sms"]["missing"]
    assert res["state"] == CB.CONTACTABLE_OTHER          # a person may call them back
    # email: they wrote to us (permitted), but nothing can send seller email yet
    assert res["channels"]["email"]["permitted"] is True
    assert res["channels"]["email"]["state"] == "NOT_OPERATIONAL"


def test_stop_makes_the_seller_do_not_contact_everywhere(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    post(client, form(submission_id="c3", sms_consent=True))
    lead = _seller(db)
    lead.status = "dnc"
    db.commit()
    res = CB.for_seller(db, evo.id, lead)
    assert res["state"] == CB.DO_NOT_CONTACT
    for ch in (CE.SMS, CE.EMAIL, CE.VOICE):
        verdict = getattr(CE, ch)(db, evo.id, lead.phone if ch != CE.EMAIL else lead.email, lead=lead)
        assert verdict["eligible"] is False and "DNC" in verdict["blocks"]


def test_a_suppressed_number_is_blocked_even_with_consent(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    post(client, form(submission_id="c4", sms_consent=True))
    db.add(SuppressionEntry(organization_id=evo.id, phone="12145550123", reason="test",
                            source="manual"))
    db.commit()
    v = CE.sms(db, evo.id, "+12145550123", lead=_seller(db), now=DAYTIME)
    assert v["state"] == "BLOCKED" and "SUPPRESSED" in v["blocks"]


def test_quiet_hours_are_timing_not_ineligibility(client, world):
    from test_wholesale_seller_sms_program import NIGHT
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    post(client, form(submission_id="c5", sms_consent=True))
    v = CE.sms(db, evo.id, "+12145550123", lead=_seller(db), now=NIGHT)
    assert v["eligible"] is True and v["timing"] == ["QUIET_HOURS"]


# ── owners EvoSys found (never wrote to us) ─────────────────────────────────

def test_a_found_mobile_number_is_contact_data_not_permission(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    enable(db, evo)
    res = CB.assess(db, evo.id, candidates=[_phone("+12145550188")], seller_initiated=False)
    assert res["state"] == CB.CONTACT_DATA_FOUND
    assert res["channels"]["sms"]["missing"] == ["NO_SMS_CONSENT"]
    assert res["channels"]["voice"]["state"] == "NO_PERMISSION"


def test_cold_email_needs_the_workspace_to_confirm_it(client, world):
    db, evo = world["db"], world["orgs"]["evo"]
    res = CB.assess(db, evo.id, candidates=[_email()], seller_initiated=False)
    assert res["channels"]["email"]["missing"] == ["NO_EMAIL_PERMISSION"]
    s = db.query(WholesaleSettings).filter_by(organization_id=evo.id).one()
    s.cold_seller_email_confirmed = True
    db.commit()
    res = CB.assess(db, evo.id, candidates=[_email()], seller_initiated=False)
    assert res["channels"]["email"]["permission_basis"] == "WORKSPACE_CONFIRMED"
    # permitted - and still honestly not operational: nothing sends seller email
    assert res["channels"]["email"]["state"] == "NOT_OPERATIONAL"
    assert res["state"] == CB.CONTACT_DATA_FOUND


def test_a_strong_property_with_no_contact_data_needs_enrichment(world):
    db, evo = world["db"], world["orgs"]["evo"]
    assert CB.assess(db, evo.id, candidates=[])["state"] == CB.ENRICHMENT_NEEDED
    assert CB.assess(db, evo.id, candidates=[], enrichment_possible=False)["state"] == CB.UNKNOWN
    assert CB.assess(db, evo.id, candidates=[_phone(status="wrong_party")])["state"] \
        == CB.ENRICHMENT_NEEDED


def test_every_number_opted_out_means_do_not_contact(world):
    db, evo = world["db"], world["orgs"]["evo"]
    res = CB.assess(db, evo.id, candidates=[_phone(status="opted_out"),
                                            _email(status="suppressed")])
    assert res["state"] == CB.DO_NOT_CONTACT


def test_ownership_ambiguity_requires_review(world):
    db, evo = world["db"], world["orgs"]["evo"]
    res = CB.assess(db, evo.id, candidates=[_phone()], identity_review=True)
    assert res["state"] == CB.REVIEW_REQUIRED
    assert CB.assess(db, evo.id, candidates=[_phone()],
                     owner_identified=False)["state"] == CB.UNKNOWN


# ── tenants ─────────────────────────────────────────────────────────────────

def test_consent_in_one_workspace_is_nothing_in_another(client, world):
    db, o = world["db"], world["orgs"]
    enable(db, o["evo"])
    enable(db, o["other"])
    post(client, form(submission_id="c9", sms_consent=True))      # EVO's form
    assert CE.sms(db, o["evo"].id, "+12145550123", now=DAYTIME)["eligible"] is True
    other = CE.sms(db, o["other"].id, "+12145550123", now=DAYTIME)
    assert other["eligible"] is False and "NO_SMS_CONSENT" in other["missing"]
    # and the other workspace's DNC does not reach EVO's seller
    db.add(Lead(organization_id=o["other"].id, first_name="X", phone="12145550123", status="dnc"))
    db.commit()
    assert CE.sms(db, o["evo"].id, "+12145550123", now=DAYTIME)["eligible"] is True
