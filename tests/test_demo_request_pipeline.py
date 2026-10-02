"""BOOK A DEMO MUST NEVER DISAPPEAR.

Found 2026-10-02: a visitor who could not (or chose not to) pick a time used
"Send My Information Instead" and got only a marketing Lead in the intake
workspace. No Opportunity, no owner, no follow-up due - nothing in Sales
Operations showed it. A booked demo had all of those. These tests pin the
unbooked request into the same pipeline, at most once per submission, and keep
the website's challenge wording and attribution intact on both paths.
"""
import itertools
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import Lead, Organization, Platform, User
from app.models.sales_models import (BrandSalesOrg, Membership, Opportunity,
                                     OpportunityEvent, DiscoveryRecord,
                                     SCOPE_BRAND_SALES_ORG, ROLE_SALES_REP)
from app.services import idempotency
from app.services import public_booking as pb
from app.services.auth_service import hash_password


SLUG = "evosyspro"
TZ = "America/Chicago"
_SEQ = itertools.count(1)

SITE_FORM = {
    "submission_id": None,
    "submitted_at": "2026-10-02T14:05:00+00:00",
    "name": "Testy McDemo",
    "company": "ZZTEST Demo Co",
    "email": "testy.demo@example.test",
    "phone": "(214) 555-0177",
    "industry": "Insurance",
    "pain_point": "Responding to new leads fast enough",   # the site's wording
    "interest": "Insurance / Agency Automation",
    "locations": "2–5",
    "leads": "Under 2,500",
    "current_system": "spreadsheets",
    "goals": "We lose web leads overnight.",
    "visitor_timezone": "America/Chicago",
    "sms_consent": "NO",
    "utm_source": "google",
    "utm_medium": "cpc",
    "utm_campaign": "fall-demo",
    "cta": "hero",
    "page_url": "https://evosyspro.live/request-demo/",
}


def _user(db, name):
    u = User(organization_id=None, email="drp%d@test.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role="advisor",
             must_change_password=False)
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def brand(db_session, monkeypatch):
    db = db_session
    platform = Platform(name="EvoSys Pro", slug=SLUG, website_url="https://evosyspro.live",
                        support_email="support@evosyspro.live")
    db.add(platform)
    db.commit()
    org = Organization(name="EVO Integrated Solutions LLC", slug="cust-" + uuid.uuid4().hex[:8],
                       plan="standard", platform_id=platform.id, is_active=True)
    db.add(org)
    db.commit()
    platform.public_intake_organization_id = org.id
    bso = BrandSalesOrg(platform_id=platform.id, name="EvoSys Sales",
                        slug="drp-bso-%d" % next(_SEQ), timezone=TZ)
    db.add(bso)
    db.commit()
    rep = _user(db, "Default Rep")
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=bso.id,
                      role=ROLE_SALES_REP, is_active=True))
    db.commit()
    bso.default_inbound_owner_user_id = rep.id
    db.commit()

    sent = []

    class _R:
        internal_sent = False
        customer_sent = False

    def fake_notify(db, *, platform, lead, payload):
        sent.append(lead.id)
        return _R()

    import app.services.public_demo_notifications as notif
    monkeypatch.setattr(notif, "notify_demo_request", fake_notify)
    return {"platform": platform, "org": org, "bso": bso, "rep": rep, "sent": sent}


def _post(client, body):
    return client.post("/site-intake/%s/demo-request" % SLUG, json=body)


def _form(**kw):
    f = dict(SITE_FORM, submission_id=uuid.uuid4().hex)
    f.update(kw)
    return f


def test_an_unbooked_request_lands_in_the_sales_pipeline_with_a_follow_up(client, db_session, brand):
    r = _post(client, _form())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["pipeline"]["filed"] is True
    opp = db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == brand["bso"].id).one()
    assert opp.owner_user_id == brand["rep"].id
    assert opp.email == "testy.demo@example.test"
    assert opp.next_action == pb.REQUEST_NEXT_ACTION
    assert opp.next_action_due_at is not None
    assert opp.next_action_due_at <= datetime.utcnow() + timedelta(hours=3)
    ev = db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == opp.id,
        OpportunityEvent.event_type == "demo_requested").one()
    assert "Insurance / Agency Automation" in (ev.detail or "")
    assert ev.actor_user_id is None
    # the site's wording became the KNOWN challenge, not "other"
    rec = db_session.query(DiscoveryRecord).filter(DiscoveryRecord.opportunity_id == opp.id).one()
    assert pb.CHALLENGE_LABELS["response_speed"] in rec.bottlenecks
    assert "Interest: Insurance / Agency Automation" in (rec.opportunity_notes or "")
    # the marketing lead still exists, with attribution kept
    lead = db_session.query(Lead).filter(Lead.id == body["lead_id"]).one()
    cf = json.loads(lead.custom_fields)
    assert cf["utm_source"] == "google" and cf["utm_campaign"] == "fall-demo"
    assert cf["cta"] == "hero" and cf["interest"] == "Insurance / Agency Automation"


def test_a_replayed_submission_is_answered_once_and_notifies_once(client, db_session, brand):
    f = _form()
    first = _post(client, f)
    again = _post(client, f)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json().get("replayed") is True
    assert again.json()["lead_id"] == first.json()["lead_id"]
    assert len(brand["sent"]) == 1
    opp = db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == brand["bso"].id).one()
    assert db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == opp.id,
        OpportunityEvent.event_type == "demo_requested").count() == 1


def test_the_same_person_again_is_one_deal_with_two_requests_on_its_timeline(client, db_session, brand):
    _post(client, _form())
    _post(client, _form(interest="EvoSys Wholesale", phone="214.555.0177"))
    opps = db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == brand["bso"].id).all()
    assert len(opps) == 1
    events = db_session.query(OpportunityEvent).filter(
        OpportunityEvent.opportunity_id == opps[0].id,
        OpportunityEvent.event_type == "demo_requested").all()
    assert len(events) == 2
    assert any("EvoSys Wholesale" in (e.detail or "") for e in events)
    assert db_session.query(Lead).filter(
        Lead.organization_id == brand["org"].id).count() == 1


def test_a_next_action_a_person_set_is_never_overwritten(client, db_session, brand):
    _post(client, _form())
    opp = db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == brand["bso"].id).one()
    opp.next_action = "Call Tuesday 2pm - she asked"
    due = datetime.utcnow() + timedelta(days=4)
    opp.next_action_due_at = due
    db_session.commit()
    _post(client, _form())
    db_session.refresh(opp)
    assert opp.next_action == "Call Tuesday 2pm - she asked"


def test_with_no_owner_configured_the_request_is_kept_and_says_why(client, db_session, brand):
    brand["bso"].default_inbound_owner_user_id = None
    db_session.commit()
    r = _post(client, _form())
    assert r.status_code == 201
    assert r.json()["pipeline"] == {"filed": False, "reason": "no_inbound_owner_configured"} \
        or r.json()["pipeline"]["filed"] is False
    assert db_session.query(Lead).filter(Lead.organization_id == brand["org"].id).count() == 1
    assert db_session.query(Opportunity).filter(
        Opportunity.brand_sales_org_id == brand["bso"].id).count() == 0


def test_a_demo_request_is_never_sms_consent(client, db_session, brand):
    r = _post(client, _form(sms_consent="NO"))
    lead = db_session.query(Lead).filter(Lead.id == r.json()["lead_id"]).one()
    assert getattr(lead, "sms_consent", None) in (None, False)


def test_challenge_key_reads_both_wordings():
    assert pb.challenge_key("Responding to new leads fast enough") == "response_speed"
    assert pb.challenge_key("Reporting / knowing what’s actually happening") == "reporting"
    assert pb.challenge_key("Our current CRM or tools aren’t working well") == "tools_not_working"
    assert pb.challenge_key("lead_followup") == "lead_followup"
    assert pb.challenge_key("Following up with leads consistently") == "lead_followup"
    assert pb.challenge_key("Something else") == "other"
    assert pb.challenge_key("We sell boats") is None


# ── the shared guard itself ────────────────────────────────────────────────

def test_claim_is_first_writer_wins_and_survives_the_callers_transaction(db_session):
    assert idempotency.claim(db_session, "t.scope", "k-1") is True
    db_session.commit()
    assert idempotency.claim(db_session, "t.scope", "k-1") is False
    # losing the claim did not poison the session
    assert idempotency.claim(db_session, "t.scope", "k-2") is True
    db_session.commit()
    # an empty key is never refused
    assert idempotency.claim(db_session, "t.scope", "") is True
    assert idempotency.claim(db_session, "t.scope", None) is True
    # scopes are independent
    assert idempotency.claim(db_session, "other.scope", "k-1") is True
    db_session.commit()


def test_a_rolled_back_claim_allows_a_retry(db_session):
    # pysqlite releases a SAVEPOINT as a commit (no BEGIN is emitted first), so
    # this transactional property is only observable on a real database. The
    # platform runs on Postgres; the first-writer-wins test above holds on both.
    if db_session.bind.dialect.name == "sqlite":
        pytest.skip("SQLite driver commits released savepoints; Postgres-only property")
    assert idempotency.claim(db_session, "t.retry", "x") is True
    db_session.rollback()            # the protected work failed
    assert idempotency.claim(db_session, "t.retry", "x") is True
    db_session.commit()


def test_the_booking_form_maps_the_site_wording_and_keeps_interest():
    from app.routers.public_booking_router import BookRequest, _clean_form, _utm
    req = BookRequest(start_utc="2026-10-05T15:00:00Z", full_name="A B", company="C",
                      email="a@b.test", primary_challenge="Responding to new leads fast enough",
                      interest="EvoSys Wholesale", cta="header", utm_source="newsletter",
                      utm_campaign="oct")
    form = _clean_form(req)
    assert form["primary_challenge"] == "response_speed"
    assert form["primary_challenge_label"] == pb.CHALLENGE_LABELS["response_speed"]
    assert form["interest"] == "EvoSys Wholesale"
    assert _utm(req) == {"utm_source": "newsletter", "utm_campaign": "oct"}
    # unknown wording is kept as free text, never invented into a key
    req2 = BookRequest(start_utc="2026-10-05T15:00:00Z", full_name="A B", company="C",
                       email="a@b.test", primary_challenge="We sell boats")
    f2 = _clean_form(req2)
    assert f2["primary_challenge"] == "other" and f2["primary_challenge_label"] == "We sell boats"
