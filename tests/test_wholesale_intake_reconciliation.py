"""EvoSys Wholesale: Intake -> Identity -> Lead workflow reconciliation.

A public seller inquiry becomes ONE person, ONE property and a deal somebody
is told about, and every downstream surface treats that person as a SELLER:

  * identity - the same place (normalized address) and the same person (phone,
    then email) are reused; a different person never displaces the deal's
    seller of record; a closed / dead deal is re-engaged as a NEW deal;
  * notification - the assignee (or the workspace admins) get a bell item that
    opens the deal; no other tenant hears about it;
  * editable seller data - an operator can correct and CLEAR fields; a phone
    change is validated, deduplicated and does not carry SMS consent over;
  * Lead <-> Wholesale - the Lead page knows the deals this person sells;
  * booking semantics - no self-service booking link for a seller, ever;
  * seller-aware AI - drafts carry what the seller told us and the seller
    guardrails, and are not framed as a funeral-planning cold lead;
  * inbound seller replies are read onto the record and never auto-answered.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.models import BookingLink, Lead, Notification, NotificationType, PipelineConversation, Reply
from app.models.sms_consent_models import SmsConsentRecord
from app.models.wholesale_models import (WholesaleDeal, WholesaleEvent, WholesaleProperty,
                                         WholesaleSellerProfile, WholesaleSettings)

from test_wholesale_seller_sms_program import (_headers, _user, form, post, world)  # noqa: F401


def _events(db, action="seller_inquiry.received"):
    return (db.query(WholesaleEvent).filter_by(action=action)
            .order_by(WholesaleEvent.created_at.asc()).all())


# ── identity ────────────────────────────────────────────────────────────────

def test_the_same_place_spelled_differently_is_one_property(client, world):
    db = world["db"]
    post(client, form(submission_id="a", street_address="123 Main Street"))
    post(client, form(submission_id="b", street_address="123 MAIN ST.", phone="(469) 555-0199",
                      email="other@example.com", full_name="Sam Other"))
    assert db.query(WholesaleProperty).count() == 1


def test_a_returning_person_is_matched_by_email_and_keeps_what_they_wrote(client, world):
    db = world["db"]
    post(client, form(submission_id="a"))
    post(client, form(submission_id="b", phone="(214) 555-0777", street_address="9 Elm St",
                      notes="Second house, roof leaks."))
    assert db.query(Lead).count() == 1
    lead = db.query(Lead).one()
    assert "Tenant moves out" in lead.notes and "roof leaks" in lead.notes
    assert db.query(WholesaleProperty).count() == 2
    assert _events(db)[-1].details and '"matched_by": "intake exact match"' in _events(db)[-1].details


def test_a_different_person_never_displaces_the_seller_of_record(client, world):
    db = world["db"]
    post(client, form(submission_id="a"))
    deal = db.query(WholesaleDeal).one()
    first_seller = deal.seller_lead_id
    post(client, form(submission_id="b", full_name="Cousin Vinny", phone="(972) 555-0101",
                      email="vinny@example.com"))
    db.expire_all()
    deal = db.query(WholesaleDeal).one()
    assert deal.seller_lead_id == first_seller
    profiles = db.query(WholesaleSellerProfile).all()
    assert len(profiles) == 2
    extra = [p for p in profiles if p.lead_id != first_seller][0]
    assert "verify" in (extra.relationship_note or "")
    assert "additional contact" in _events(db)[-1].summary


def test_a_seller_returning_after_a_dead_deal_gets_a_new_deal(client, world):
    db = world["db"]
    post(client, form(submission_id="a"))
    old = db.query(WholesaleDeal).one()
    old.stage, old.lost_reason = "dead", "not_interested"
    db.commit()
    post(client, form(submission_id="b", timeline="asap"))
    db.expire_all()
    deals = db.query(WholesaleDeal).order_by(WholesaleDeal.created_at.asc()).all()
    assert len(deals) == 2 and deals[0].stage == "dead" and deals[0].lost_reason == "not_interested"
    assert deals[1].stage != "dead" and deals[1].seller_lead_id == deals[0].seller_lead_id
    assert "re-engaged" in _events(db)[-1].summary
    assert db.query(WholesaleEvent).filter_by(action="deal.reopened").count() == 1


def test_an_inquiry_seller_is_a_warm_lead_not_a_cold_one(client, world):
    db = world["db"]
    post(client, form(submission_id="a", timeline="60_days"))
    lead = db.query(Lead).one()
    assert lead.relationship_type == "warm_lead"
    assert db.query(WholesaleSellerProfile).one().timeline == "60_days"


# ── notification and assignment ─────────────────────────────────────────────

def test_the_workspace_admins_are_told_and_no_other_tenant_is(client, world):
    db, o = world["db"], world["orgs"]
    admin = _user(db, o["evo"])
    advisor = _user(db, o["evo"], role="advisor")
    stranger = _user(db, o["other"])
    post(client, form(submission_id="a", sms_consent=True))
    deal = db.query(WholesaleDeal).one()
    n = db.query(Notification).filter_by(user_id=admin.id).one()
    assert n.type == NotificationType.WHOLESALE_INQUIRY
    assert n.link == "/wholesale/deals/%s" % deal.id and "Pat Seller" in n.message
    assert "SMS consent yes" in n.message
    assert db.query(Notification).filter_by(user_id=advisor.id).count() == 0
    assert db.query(Notification).filter_by(user_id=stranger.id).count() == 0
    bell = client.get("/notifications/", headers=_headers(db, admin)).json()
    items = bell.get("notifications") or bell.get("items") or bell
    assert any(i.get("link") == n.link for i in items)


def test_the_configured_assignee_gets_the_inquiry(client, world):
    db, o = world["db"], world["orgs"]
    admin = _user(db, o["evo"])
    rep = _user(db, o["evo"], role="advisor", email="rep@evo.test")
    h = _headers(db, admin)
    r = client.patch("/wholesale/settings", headers=h, json={"inquiry_assignee_id": rep.id})
    assert r.status_code == 200 and r.json()["inquiry_assignee_id"] == rep.id
    post(client, form(submission_id="a"))
    deal, lead = db.query(WholesaleDeal).one(), db.query(Lead).one()
    assert deal.assigned_to_id == rep.id and lead.assigned_to_id == rep.id
    assert db.query(Notification).filter_by(user_id=rep.id).count() == 1
    assert db.query(Notification).filter_by(user_id=admin.id).count() == 0


def test_an_assignee_from_another_workspace_is_refused(client, world):
    db, o = world["db"], world["orgs"]
    h = _headers(db, _user(db, o["evo"]))
    outsider = _user(db, o["other"], role="advisor", email="x@other.test")
    r = client.patch("/wholesale/settings", headers=h, json={"inquiry_assignee_id": outsider.id})
    assert r.status_code == 400
    assert db.query(WholesaleSettings).filter_by(organization_id=o["evo"].id).one().inquiry_assignee_id is None


# ── editable seller data ────────────────────────────────────────────────────

@pytest.fixture()
def seller(client, world):
    db, o = world["db"], world["orgs"]
    post(client, form(submission_id="a", sms_consent=True))
    admin = _user(db, o["evo"])
    return {"db": db, "h": _headers(db, admin), "profile": db.query(WholesaleSellerProfile).one(),
            "lead": db.query(Lead).one()}


def test_an_operator_can_correct_and_clear_seller_fields(client, seller):
    db, h, p = seller["db"], seller["h"], seller["profile"]
    r = client.patch("/wholesale/sellers/%s" % p.id, headers=h, json={
        "reason_for_selling": None, "motivation": "Inherited, lives out of state",
        "last_name": "Seller-Jones", "appointment_status": "scheduled",
        "appointment_at": "2026-10-02T15:00:00", "notes": "Prefers calls after 5pm."})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["reason_for_selling"] is None and out["motivation"].startswith("Inherited")
    assert out["last_name"] == "Seller-Jones" and out["appointment_status"] == "scheduled"
    assert out["appointment_at"].startswith("2026-10-02T15:00")
    assert "Prefers calls after 5pm." in out["notes"] and "Tenant moves out" in out["notes"]
    # "booked" for a seller never becomes the Lead's funeral-planning booked status
    assert db.query(Lead).one().status != "booked"


def test_seller_edits_are_validated(client, seller):
    db, h, p = seller["db"], seller["h"], seller["profile"]
    assert client.patch("/wholesale/sellers/%s" % p.id, headers=h,
                        json={"phone": "12"}).status_code == 422
    assert client.patch("/wholesale/sellers/%s" % p.id, headers=h,
                        json={"email": "not-an-email"}).status_code == 422
    assert client.patch("/wholesale/sellers/%s" % p.id, headers=h,
                        json={"appointment_status": "booked_maybe"}).status_code == 422
    other = Lead(organization_id=seller["lead"].organization_id, first_name="Taken",
                 phone="+14695550142")
    db.add(other)
    db.commit()
    r = client.patch("/wholesale/sellers/%s" % p.id, headers=h, json={"phone": "469-555-0142"})
    assert r.status_code == 409 and r.json()["detail"]["lead_id"] == other.id


def test_a_new_number_does_not_inherit_sms_consent(client, seller):
    db, h, p = seller["db"], seller["h"], seller["profile"]
    assert seller["lead"].sms_consent is True
    r = client.patch("/wholesale/sellers/%s" % p.id, headers=h, json={"phone": "(682) 555-0110"})
    assert r.status_code == 200
    assert "does not carry" in r.json()["consent_note"]
    db.expire_all()
    lead = db.query(Lead).one()
    assert lead.phone == "+16825550110" and lead.sms_consent is False
    # the evidence for the old number is untouched
    assert db.query(SmsConsentRecord).one().phone_normalized == "+12145550123"
    # ...and the headline no longer reports it as consent for the current number
    c = client.get("/wholesale/sms/consents", params={"lead_id": lead.id}, headers=h).json()
    assert c["sms_consent"] is False and c["status"] is None
    assert c["has_consent_for_other_numbers"] is True and len(c["consents"]) == 1
    e = client.get("/wholesale/sms/eligibility", params={"lead_id": lead.id}, headers=h).json()
    assert e["eligible"] is False and "NO_SMS_CONSENT" in e["reasons"]


def test_another_tenant_cannot_edit_the_seller(client, world, seller):
    db, o = world["db"], world["orgs"]
    theirs = _headers(db, _user(db, o["other"]))
    r = client.patch("/wholesale/sellers/%s" % seller["profile"].id, headers=theirs,
                     json={"first_name": "Hijacked"})
    assert r.status_code == 404
    assert db.query(Lead).one().first_name == "Pat"


# ── Lead <-> Wholesale, booking semantics ───────────────────────────────────

def test_the_lead_page_links_to_the_deal_and_offers_no_booking_link(client, seller):
    db, h = seller["db"], seller["h"]
    ctx = client.get("/compose/%s/context" % seller["lead"].id, headers=h).json()
    deal = db.query(WholesaleDeal).one()
    assert ctx["wholesale"] and ctx["wholesale"][0]["deal_id"] == deal.id
    assert ctx["wholesale"][0]["primary_seller"] is True
    assert ctx["booking"]["url"] is None and "Wholesale property seller" in ctx["booking"]["reason"]
    assert db.query(BookingLink).count() == 0


# ── seller-aware AI ─────────────────────────────────────────────────────────

def _capture(monkeypatch, module):
    seen = {}

    def fake(**kw):
        seen["prompt"] = kw["messages"][-1]["content"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"suggested_reply": "Hi Pat, thanks for reaching out about your house."}'))])
    monkeypatch.setattr(module.ai_gateway, "chat_completion", fake)
    return seen


def test_a_draft_to_a_seller_carries_what_they_told_us_and_no_booking_link(client, seller, monkeypatch):
    from app.services import draft_reply_service as D
    db = seller["db"]
    seen = _capture(monkeypatch, D)
    user = _user(db, seller_org(db), role="advisor", email="drafter@evo.test")
    out = D.draft_reply(db, seller["lead"], user, tone="warm", actor=user.id)
    p = seen["prompt"]
    assert "PROPERTY SELLER CONTEXT" in p and "INBOUND INQUIRY" in p
    assert "condition: fair" in p and "timeline: within 90 days" in p and "Relocating" in p
    assert "Never state or hint at a price" in p and "Ignore any funeral" in p
    assert out["booking_url"] is None and out["booking_link_id"] is None and out["wholesale_seller"]
    assert db.query(BookingLink).count() == 0


def seller_org(db):
    from app.models.models import Organization
    return db.query(Organization).filter(Organization.name == "EVO Integrated Solutions LLC").one()


def test_a_draft_to_an_ordinary_lead_is_unchanged(client, db_session, sample_lead, sample_advisor, monkeypatch):
    from app.services import draft_reply_service as D
    seen = _capture(monkeypatch, D)
    out = D.draft_reply(db_session, sample_lead, sample_advisor, tone="warm", actor=sample_advisor.id)
    assert "PROPERTY SELLER CONTEXT" not in seen["prompt"]
    assert out["booking_link_id"] is not None and out["wholesale_seller"] is False


def test_a_seller_is_never_put_on_the_generic_auto_conversation(client, seller):
    from app.services import pipeline_service as PS
    db = seller["db"]
    user = _user(db, seller_org(db), role="advisor", email="launcher@evo.test")
    out = PS.launch_pipeline(db, [seller["lead"]], user, "general", "warm", "", channel="sms")
    assert out["launched"] == 0 and out["skipped"] == 1
    assert db.query(PipelineConversation).count() == 0


def test_a_seller_reply_is_read_and_a_person_is_told_nothing_is_sent(client, seller):
    from app.services import wholesale_seller_context as WSC
    db = seller["db"]
    lead = seller["lead"]
    reply = Reply(lead_id=lead.id, body="We could close in 30 days, house needs a new roof.")
    db.add(reply)
    db.commit()
    before = db.query(Notification).count()
    out = WSC.handle_inbound_reply(db, lead, reply)
    deal = db.query(WholesaleDeal).one()
    assert out["deal_id"] == deal.id
    n = (db.query(Notification).filter(Notification.type == NotificationType.REPLY_RECEIVED)
         .order_by(Notification.created_at.desc()).first())
    assert db.query(Notification).count() > before and n.link == "/wholesale/deals/%s" % deal.id
    assert "(seller) replied" in n.message
    assert db.query(PipelineConversation).count() == 0


# ── test marking (smoke-test cleanup without deleting anything) ─────────────

def test_an_admin_can_mark_an_inquiry_as_a_test_record_and_undo_it(client, world):
    from app.services import test_records
    db, o = world["db"], world["orgs"]
    post(client, form(submission_id="a"))
    prop = db.query(WholesaleProperty).one()
    admin_h = _headers(db, _user(db, o["evo"]))
    advisor_h = _headers(db, _user(db, o["evo"], role="advisor", email="adv@evo.test"))
    url = "/wholesale/properties/%s/test-flag" % prop.id
    assert client.post(url, headers=advisor_h, json={"is_test": True, "confirm": "123 Main St"}).status_code == 403
    assert client.post(url, headers=admin_h, json={"is_test": True, "confirm": "wrong"}).status_code == 422
    r = client.post(url, headers=admin_h, json={"is_test": True, "confirm": "123 main st", "note": "smoke test"})
    assert r.status_code == 200, r.text
    db.expire_all()
    lead = db.query(Lead).one()
    assert db.query(WholesaleProperty).one().is_test and db.query(WholesaleDeal).one().is_test
    assert lead.is_test and not test_records.is_outreach_eligible(lead)
    assert db.query(WholesaleEvent).filter_by(action="property.test_flag").count() == 1
    r = client.post(url, headers=admin_h, json={"is_test": False, "confirm": "123 Main St"})
    db.expire_all()
    assert r.status_code == 200 and not db.query(Lead).one().is_test


def test_a_real_seller_on_another_property_is_never_silenced(client, world):
    db, o = world["db"], world["orgs"]
    post(client, form(submission_id="a"))
    post(client, form(submission_id="b", street_address="9 Elm St"))
    assert db.query(Lead).count() == 1
    first = db.query(WholesaleProperty).filter_by(street_address="123 Main St").one()
    h = _headers(db, _user(db, o["evo"]))
    r = client.post("/wholesale/properties/%s/test-flag" % first.id, headers=h,
                    json={"is_test": True, "confirm": "123 Main St"})
    assert r.status_code == 200 and r.json()["leads_kept_real"]
    assert db.query(Lead).one().is_test is False


# ── capacity: an inbound seller is HELD, never discarded ────────────────────

@pytest.fixture()
def full_plan(monkeypatch):
    from app.services import plan_limits
    real = plan_limits.check
    real_counter = plan_limits.counter_for_org_id

    def counter(db, org_id, key):
        c = real_counter(db, org_id, key)
        if key == plan_limits.LIMIT_LEADS:
            c.has_room = lambda n=1: False
        return c
    monkeypatch.setattr(plan_limits, "counter_for_org_id", counter)

    def check(db, org, resource, adding=1, **kw):
        if resource == plan_limits.LIMIT_LEADS:
            return {"allowed": False, "used": 2500, "limit": 2500}
        return real(db, org, resource, adding=adding, **kw)
    monkeypatch.setattr(plan_limits, "check", check)


def test_a_full_plan_holds_the_seller_instead_of_refusing_them(client, world, full_plan):
    from app.services import lead_capacity
    db, o = world["db"], world["orgs"]
    admin = _user(db, o["evo"])
    r = post(client, form(submission_id="held-1", sms_consent=True))
    assert r.status_code == 201, r.text
    lead = db.query(Lead).one()
    assert lead_capacity.is_held(lead)
    assert db.query(WholesaleDeal).count() == 1 and db.query(WholesaleSellerProfile).count() == 1
    rec = db.query(SmsConsentRecord).one()                  # consent evidence still kept
    assert rec.confirmation_status == "LEAD_CAPACITY_HELD"   # ...but nothing was sent
    n = db.query(Notification).filter_by(user_id=admin.id).one()
    assert "WAITING" in n.message and "lead limit" in n.message
    assert '"capacity_held": true' in _events(db)[-1].details


def test_an_operator_adding_an_owner_is_still_refused_at_the_limit(client, world, full_plan):
    db, o = world["db"], world["orgs"]
    h = _headers(db, _user(db, o["evo"]))
    prop = client.post("/wholesale/properties", headers=h,
                       json={"street_address": "5 Oak St", "city": "Dallas", "state": "TX",
                             "zip_code": "75201"}).json()
    pid = prop.get("id") or prop.get("property", {}).get("id")
    r = client.post("/wholesale/properties/%s/seller" % pid, headers=h,
                    json={"first_name": "Op", "phone": "2145559876"})
    assert r.status_code in (402, 403, 409)


# ── email notification (configurable, off by default) ───────────────────────

def test_inquiry_email_is_off_until_the_workspace_turns_it_on(client, world, monkeypatch):
    from app.services import email_service
    sent = []
    monkeypatch.setattr(email_service, "send_email_via_provider",
                        lambda to, subject, body, **kw: sent.append((to, subject, body)) or {"success": True})
    db, o = world["db"], world["orgs"]
    admin = _user(db, o["evo"], email="boss@evo.test")
    h = _headers(db, admin)
    post(client, form(submission_id="e1"))
    assert sent == []
    r = client.patch("/wholesale/settings", headers=h, json={
        "inquiry_email_enabled": True, "inquiry_email_recipients": "Desk@Evo.test; ops@evo.test"})
    assert r.status_code == 200 and r.json()["inquiry_email_recipients"] == "desk@evo.test, ops@evo.test"
    assert client.patch("/wholesale/settings", headers=h,
                        json={"inquiry_email_recipients": "not-an-email"}).status_code == 400
    post(client, form(submission_id="e2", street_address="77 Pine St", full_name="Lee Seller",
                      phone="(214) 555-0199", email="lee@example.com"))
    assert sorted(t for t, _, _ in sent) == ["desk@evo.test", "ops@evo.test"]
    subject, body = sent[0][1], sent[0][2]
    assert "New seller inquiry" in subject and "77 Pine St" in body
    assert "555-0199" not in body and "Tenant moves out" not in body    # no phone, no message text
    # a plain repeat does not email again
    sent.clear()
    post(client, form(submission_id="e3", street_address="77 Pine St", full_name="Lee Seller",
                      phone="(214) 555-0199", email="lee@example.com"))
    assert sent == []
    # blank recipients = the admins
    client.patch("/wholesale/settings", headers=h, json={"inquiry_email_recipients": ""})
    post(client, form(submission_id="e4", street_address="88 Birch St", full_name="Kim Seller",
                      phone="(214) 555-0177", email="kim@example.com"))
    assert [t for t, _, _ in sent] == ["boss@evo.test"]


def test_a_seller_inquiry_goes_through_universal_intake(client, world):
    from app.models.import_models import ImportBatch
    from app.models.intake_models import OrgContact
    db = world["db"]
    post(client, form(submission_id="ui-1"))
    lead = db.query(Lead).one()
    contact = db.query(OrgContact).one()
    assert lead.org_contact_id == contact.id and contact.lead_id == lead.id
    batch = db.query(ImportBatch).filter_by(id=lead.import_batch_id).one()
    assert batch.source_system == "website" and batch.import_list_name == "Seller inquiries"
    det = _events(db)[-1].details
    assert batch.id in det and contact.id in det
    # a repeat is one contact, one lead, two capture batches (both rollback-able)
    post(client, form(submission_id="ui-2", timeline="asap"))
    assert db.query(OrgContact).count() == 1 and db.query(Lead).count() == 1
    assert db.query(ImportBatch).count() == 2
