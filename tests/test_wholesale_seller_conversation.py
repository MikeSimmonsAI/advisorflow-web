"""The seller conversation found on production 2026-10-24 (controlled test on
4317 Kings Cir S, internal test seller):

  1. "I got a letter about the foreclosure sale... I might sell"
  2. "3 bedroom, needs a new roof... as soon as possible... around $150,000"
  3. "That sounds low, another investor told me more. Can someone come look at
     it this week? Thursday afternoon works."

The live AI read message 3 as timeline "30 days" (it replaced ASAP), did not
flag the visit request, and the reply draft was a cold introduction. These
tests replay the AI's actual readings (no model call) and pin:

* an established timeline is not replaced by a message that states none;
  an explicit new timeline still replaces it; the conflict is kept for review
* a visit request becomes "appointment requested" and a needs-a-person item
* a price push-back needs a person
* the reply draft knows it is mid-conversation (no re-introduction), carries
  what the seller said, and an opted-out seller gets no draft at all
* new appointments get a rep by the workspace's rules, or are explicitly
  unassigned with the admins told; reassignment is audited
"""
import json

import pytest

from app.models.models import Lead, Notification, NotificationType
from app.models.wholesale_models import WholesaleDeal, WholesaleSellerProfile

ES = "/wholesale/evosense"

M1 = "I got a letter about the foreclosure sale on November 3. Are you buying houses? I might sell if it stops the auction."
M2 = ("It's a 3 bedroom. It needs a new roof and the kitchen is original. I have to be out before the sale, "
      "so as soon as possible. I'd want around $150,000.")
M3 = "That sounds low, another investor told me more. Can someone come look at it this week? Thursday afternoon works."

# what the live model returned for each message (production, 2026-10-24)
READ = {
    M1: {"intent": "maybe_later", "considering_selling": True, "summary": "might sell", "confidence": 70},
    M2: {"intent": "interested", "is_available": True, "considering_selling": True, "asking_price": 150000,
         "timeline": "asap", "property_condition": "fair", "major_repairs": "needs a new roof",
         "summary": "3 bed, roof, asap, $150k", "confidence": 90},
    M3: {"intent": "maybe_later", "timeline": "30_days", "best_callback_time": "Thursday afternoon",
         "summary": "thinks offer is low, wants a visit Thursday", "confidence": 70},
}


def ok(r, what=""):
    assert r.status_code in (200, 201), "%s: %s %s" % (what, r.status_code, r.text[:400])
    return r.json()


@pytest.fixture
def ai_reads(monkeypatch):
    from app.services import wholesale_ai

    def fake(text, **kw):
        base = {k: None for k in ("is_available", "considering_selling", "asking_price", "timeline",
                                  "property_condition", "major_repairs", "occupancy", "motivation",
                                  "reason_for_selling", "mortgage_note", "decision_makers",
                                  "best_callback_time", "needs_human", "needs_human_reason")}
        r = dict(base, **READ.get(text, {"intent": "unknown"}))
        r.setdefault("needs_human", False)
        r["source"] = "ai"
        return r
    monkeypatch.setattr(wholesale_ai, "extract_from_message", fake)


def _deal(client, H, street="90 Talk Test Ln"):
    pid = ok(client.post(ES + "/properties", headers=H, json={
        "street_address": street, "city": "Fort Worth", "state": "TX", "zip_code": "76111", "county": "Tarrant",
        "property_type": "single_family", "is_test": True, "signals": ["VACANT"]}), "finding")["property_id"]
    ok(client.post(ES + "/properties/%s/owner" % pid, headers=H, json={"name": "Talk Test Owner"}))
    ok(client.post(ES + "/properties/%s/contacts" % pid, headers=H,
                   json={"kind": "email", "value": "talk.test@example.test", "person_name": "Talk Test Owner"}))
    deal_id = ok(client.post(ES + "/properties/%s/promote" % pid, headers=H, json={}), "promote")["deal_id"]
    return pid, deal_id


def _say(client, H, deal_id, text):
    return ok(client.post("/wholesale/deals/%s/seller-reply" % deal_id, headers=H,
                          json={"message": text, "mode": "manual"}), "reply")


def _profile(db, deal_id):
    db.expire_all()
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id).first()
    return deal, db.query(WholesaleSellerProfile).filter(WholesaleSellerProfile.id == deal.seller_profile_id).first()


def _events(client, H, deal_id):
    return {e["action"]: e for e in ok(client.get("/wholesale/deals/%s" % deal_id, headers=H))["events"]}


# ── timeline ────────────────────────────────────────────────────────────────

def test_an_objection_with_a_visit_request_does_not_replace_asap(client, auth_headers, db_session, ai_reads):
    _, deal_id = _deal(client, auth_headers)
    for m in (M1, M2, M3):
        _say(client, auth_headers, deal_id, m)
    _, p = _profile(db_session, deal_id)
    assert p.timeline == "asap"                      # the seller's own words stand
    assert float(p.asking_price) == 150000 and p.major_repairs == "needs a new roof"
    ev = _events(client, auth_headers, deal_id)
    conflict = ev["seller.timeline_conflict"]
    assert "asap" in conflict["summary"] and "30_days" in conflict["summary"]


def test_an_explicit_new_timeline_still_replaces_the_old_one(client, auth_headers, db_session, ai_reads):
    _, deal_id = _deal(client, auth_headers, street="91 Talk Test Ln")
    _say(client, auth_headers, deal_id, M2)
    later = "Actually we worked something out with the bank, so no rush now - maybe sell in 6 months."
    READ[later] = {"intent": "maybe_later", "timeline": "6_months", "confidence": 80}
    try:
        _say(client, auth_headers, deal_id, later)
    finally:
        READ.pop(later)
    _, p = _profile(db_session, deal_id)
    assert p.timeline == "6_months"


def test_a_person_can_correct_the_timeline(client, auth_headers, db_session, ai_reads):
    _, deal_id = _deal(client, auth_headers, street="92 Talk Test Ln")
    _say(client, auth_headers, deal_id, M2)
    _, p = _profile(db_session, deal_id)
    ok(client.patch("/wholesale/sellers/%s" % p.id, headers=auth_headers, json={"timeline": "30_days"}))
    _, p = _profile(db_session, deal_id)
    assert p.timeline == "30_days"


# ── visit request and objection ─────────────────────────────────────────────

def test_a_visit_request_goes_to_scheduling_and_a_price_pushback_to_a_person(
        client, auth_headers, db_session, ai_reads):
    _, deal_id = _deal(client, auth_headers, street="93 Talk Test Ln")
    _say(client, auth_headers, deal_id, M2)
    _, p = _profile(db_session, deal_id)
    assert p.appointment_status in (None, "none") and not p.needs_human
    _say(client, auth_headers, deal_id, M3)
    _, p = _profile(db_session, deal_id)
    assert p.appointment_status == "requested"
    assert p.needs_human and "visit" in p.needs_human_reason and "price" in p.needs_human_reason
    assert p.best_callback_time == "Thursday afternoon"
    ev = _events(client, auth_headers, deal_id)
    assert "seller.appointment_requested" in ev and "Thursday afternoon" in ev["seller.appointment_requested"]["summary"]
    assert "seller.price_objection" in ev


def test_readers_on_their_own():
    from app.services import wholesale_seller_intel as SI
    assert SI.asks_for_visit(M3) and SI.price_objection(M3) and not SI.states_timeline(M3)
    assert SI.states_timeline(M2) and not SI.asks_for_visit(M2)
    assert SI.states_timeline("We need to close within 30 days")
    assert SI.states_timeline("no rush at all")
    assert not SI.states_timeline("Call me this week")
    assert not SI.price_objection("Sounds good to me")


# ── the reply draft ─────────────────────────────────────────────────────────

@pytest.fixture
def prompts(monkeypatch):
    from app.services import ai_gateway, draft_reply_service
    seen = []

    class _R:
        def __init__(self, text):
            self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]

    def fake(**kw):
        seen.append(kw["messages"][0]["content"])
        return _R(json.dumps({"suggested_reply": "Thanks - Thursday afternoon noted; someone will confirm."}))
    monkeypatch.setattr(ai_gateway, "chat_completion", fake)
    monkeypatch.setattr(draft_reply_service, "_get_client", lambda: object())
    return seen


def test_the_draft_knows_the_conversation_and_what_the_seller_said(
        client, auth_headers, db_session, ai_reads, prompts):
    _, deal_id = _deal(client, auth_headers, street="94 Talk Test Ln")
    deal, _ = _profile(db_session, deal_id)
    # before any reply: a records-based owner gets the no-prior-contact framing
    ok(client.post("/sms/draft-reply/%s" % deal.seller_lead_id, headers=auth_headers, json={"tone": "warm"}))
    assert "NO PRIOR CONTACT" in prompts[-1]
    for m in (M1, M2, M3):
        _say(client, auth_headers, deal_id, m)
    d = ok(client.post("/sms/draft-reply/%s" % deal.seller_lead_id, headers=auth_headers, json={"tone": "warm"}))
    p = prompts[-1]
    assert d["source"] == "ai" and d["booking_url"] is None
    assert "CONVERSATION IN PROGRESS" in p and "NO PRIOR CONTACT" not in p
    assert "Thursday afternoon works" in p                         # the latest message is in front of it
    assert "as soon as possible" in p and "$150,000" in p and "needs a new roof" in p
    assert "Appointment: requested" in p
    assert "Do not confirm a time yourself" in p and "without naming any number" in p


def test_an_opted_out_seller_gets_no_draft(client, auth_headers, db_session, ai_reads, prompts):
    _, deal_id = _deal(client, auth_headers, street="95 Talk Test Ln")
    deal, _ = _profile(db_session, deal_id)
    lead = db_session.query(Lead).filter(Lead.id == deal.seller_lead_id).first()
    lead.status = "dnc"
    db_session.commit()
    n = len(prompts)
    d = ok(client.post("/sms/draft-reply/%s" % deal.seller_lead_id, headers=auth_headers, json={"tone": "warm"}))
    assert d["suggested_reply"] == "" and d["source"] == "opted_out" and len(prompts) == n


# ── rep assignment ──────────────────────────────────────────────────────────

def test_a_new_appointment_gets_the_deals_owner_or_is_explicitly_unassigned(
        client, auth_headers, admin_auth_headers, db_session, sample_advisor):
    pid, deal_id = _deal(client, auth_headers, street="96 Talk Test Ln")
    ok(client.post("/wholesale/team/members", headers=admin_auth_headers,
                   json={"user_id": sample_advisor.id, "role": "field_specialist", "confirm_lock": True}), "team")
    deal, _ = _profile(db_session, deal_id)
    deal.assigned_to_id = sample_advisor.id
    db_session.commit()
    a = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-11-05T14:00:00-06:00", "deal_id": deal_id, "evosense_property_id": pid}))
    assert a["assigned_to_id"] == sample_advisor.id
    bell = db_session.query(Notification).filter(Notification.user_id == sample_advisor.id,
                                                 Notification.type == NotificationType.WHOLESALE_APPOINTMENT).count()
    assert bell == 1
    # the same visit again: no second appointment, no second notification
    again = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-11-05T14:00:00-06:00", "deal_id": deal_id, "evosense_property_id": pid}))
    assert again["id"] == a["id"]
    assert db_session.query(Notification).filter(Notification.user_id == sample_advisor.id,
                                                  Notification.type == NotificationType.WHOLESALE_APPOINTMENT).count() == 1
    # rescheduling keeps the rep
    moved = ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers,
                            json={"starts_at": "2026-11-06T10:00:00-06:00"}))
    assert moved["assigned_to_id"] == sample_advisor.id
    # reassignment (to nobody) is audited
    ok(client.patch("/wholesale/appointments/%s" % a["id"], headers=admin_auth_headers, json={"assigned_to_id": ""}))
    ev = _events(client, admin_auth_headers, deal_id)
    assert "appointment.reassigned" in ev and "appointment.booked" in ev
    # an explicit "nobody" at booking: unassigned, and the admins are told
    u = ok(client.post("/wholesale/appointments", headers=admin_auth_headers, json={
        "starts_at": "2026-11-07T09:00:00-06:00", "deal_id": deal_id, "assigned_to_id": None}))
    assert u["assigned_to_id"] is None
    listing = ok(client.get("/wholesale/appointments", headers=admin_auth_headers,
                            params={"rep_id": "unassigned", "when": "all", "include_test": True}))
    assert u["id"] in [x["id"] for x in listing["items"]]


def test_the_default_rep_is_never_from_another_workspace(db_session, sample_org, sample_advisor):
    from app.services import wholesale_appointments as A
    from app.models.models import Organization, User
    from app.models.wholesale_models import WholesaleSettings
    other = Organization(name="Other Co", slug="other-co-rep", plan="standard")
    db_session.add(other)
    db_session.flush()
    stranger = User(organization_id=other.id, email="stranger.rep@example.test", full_name="Stranger",
                    role="advisor", password_hash="x", is_active=True)
    db_session.add(stranger)
    db_session.flush()
    st = db_session.query(WholesaleSettings).filter(WholesaleSettings.organization_id == sample_org.id).first()
    if st is None:
        st = WholesaleSettings(organization_id=sample_org.id)
        db_session.add(st)
    st.inquiry_assignee_id = stranger.id
    db_session.flush()
    deal = type("D", (), {"assigned_to_id": stranger.id})()
    uid, how = A.default_rep(db_session, sample_org.id, sample_advisor, deal, None)
    assert uid != stranger.id
