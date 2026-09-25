"""Wholesale Phase 7.1 — the autonomous loop, end to end.

    scheduler decides a strategy is due → hunt (no click) → discover → dedupe →
    signals → Property Opportunity → enrichment decision → budget → contact →
    Contact Confidence → eligibility → SANDBOX outbound → seller reply through
    the REAL signed Twilio inbound webhook → persisted → routed to EvoSense →
    facts → Seller Intent → NEEDS YOU → no binding offer.

Every reply below enters through POST /sms/webhook/inbound with a valid
Twilio signature (the `twilio_webhook` fixture), exactly as Twilio delivers it.
"""
import os
import threading
import time

import pytest

from app.models.evosense_models import (EvoSenseContactPoint, EvoSenseCostEntry, EvoSenseEngagement,
                                        EvoSenseEvent, EvoSenseFact, EvoSenseHandoff,
                                        EvoSenseHuntSchedule, EvoSenseMessage, EvoSenseObservation,
                                        EvoSenseProperty, EvoSenseRun, EvoSenseStrategy)
from app.models.models import Lead, Message, Organization, Reply, SuppressionEntry, User
from app.services.evosense import common as C
from app.services.evosense import conversation as CV
from app.services.evosense import evaluate as EV
from app.services.evosense import hunt as HU
from app.services.evosense import outreach as OU
from app.services.evosense import sandbox_seed as SS
from app.services.evosense import scheduler as SCH

ORG_NUMBER = "+19998887777"       # sample_org's own sending number (tests/conftest.py)
FLAGSHIP = "1418 Cedar Springs Rd"


@pytest.fixture()
def hunted(db_session, sample_org, sample_advisor):
    """An organization with an ACTIVE strategy and nothing else. Nobody hunts."""
    SS.enable_sandbox(db_session, sample_org.id)
    s = SS.create_strategy(db_session, sample_org.id, sample_advisor, SS.DFW)
    db_session.commit()
    return {"org": sample_org.id, "strategy": s, "user": sample_advisor}


def _lead_phone(db, org_id, street):
    prop = SS.prop_at(db, org_id, street)
    eng = SS.engagement_for(db, prop)
    lead = db.query(Lead).filter(Lead.id == eng.lead_id).first()
    return prop, eng, lead, "+" + lead.phone.lstrip("+")


def sms(twilio_webhook, frm, body, sid, to=ORG_NUMBER):
    r = twilio_webhook("/sms/webhook/inbound", data={"From": frm, "To": to, "Body": body,
                                                      "MessageSid": sid})
    assert r.status_code == 200, r.text[:300]
    return r


# ── 30. the primary acceptance journey ─────────────────────────────────────

def test_the_whole_loop_runs_without_anyone_pressing_anything(db_session, hunted, twilio_webhook):
    db, org, strategy = db_session, hunted["org"], hunted["strategy"]
    assert db.query(EvoSenseRun).count() == 0

    # SCHEDULER: the platform loop's pass. No Run hunt, no script.
    report = SCH.run_due(db)
    assert report["due"] == 1 and report["ran"] == 1
    run = db.query(EvoSenseRun).filter(EvoSenseRun.strategy_id == strategy.id).one()
    assert run.trigger == "schedule" and run.status == "succeeded"
    sched = db.query(EvoSenseHuntSchedule).filter_by(strategy_id=strategy.id).one()
    assert sched.cadence == "daily" and sched.next_due_at > C.now()
    actions = {e.action for e in db.query(EvoSenseEvent).filter_by(strategy_id=strategy.id)}
    assert {"hunt.queued", "hunt.started", "hunt.succeeded"} <= actions

    # DISCOVERED + DEDUPED + SIGNALS + SCORED
    props = db.query(EvoSenseProperty).filter(EvoSenseProperty.street_key.like("1418 CEDAR%")).all()
    assert len(props) == 1
    prop = props[0]
    assert db.query(EvoSenseObservation).filter_by(property_id=prop.id).count() == 3
    assert prop.opportunity_score >= 80
    # ENRICHMENT + BUDGET + CONTACT + CONTACT CONFIDENCE
    ledger = db.query(EvoSenseCostEntry).filter_by(property_id=prop.id, status="charged").all()
    assert sorted(e.total_cents for e in ledger) == [1, 18]
    assert prop.contact_confidence >= 80
    # ELIGIBILITY → SANDBOX OUTBOUND
    prop, eng, lead, phone = _lead_phone(db, org, FLAGSHIP)
    outs = db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="outbound").all()
    assert len(outs) == 1 and outs[0].delivery == "sandbox_simulated"

    # SELLER REPLY through the real signed inbound webhook
    sms(twilio_webhook, phone, SS.FLAGSHIP_REPLY, "SMhot0001")
    db.expire_all()
    replies = db.query(Reply).filter_by(lead_id=lead.id).all()
    assert len(replies) == 1 and replies[0].twilio_sid == "SMhot0001"        # persisted by the platform
    msg = db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="inbound").one()
    assert msg.platform_ref == replies[0].id and msg.delivery == "received"   # routed to EvoSense
    assert msg.outcome == C.O_INTERESTED
    facts = {f.fact_type: f for f in db.query(EvoSenseFact).filter_by(property_id=prop.id)}
    assert {"estate_context", "occupancy", "condition", "asking_price", "wants_quick_close",
            "willing_to_sell"} <= set(facts)
    assert facts["asking_price"].value == "150000" and all(f.message_id == msg.id for f in facts.values())
    prop = db.query(EvoSenseProperty).get(prop.id)
    assert prop.seller_intent >= 70
    h = db.query(EvoSenseHandoff).filter_by(property_id=prop.id).one()
    assert h.status == "open" and prop.status == C.S_NEEDS_YOU
    assert {e.action for e in db.query(EvoSenseEvent).filter_by(property_id=prop.id)} >= {
        "inbound.routed", "reply.received", "reply.read", "handoff.opened"}
    # EVOSENSE STOPS HERE: no further outbound, no platform AI reply, no offer, no deal.
    assert db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="outbound").count() == 1
    assert db.query(Message).filter_by(lead_id=lead.id).count() == 0
    assert prop.promoted_deal_id is None
    from app.models.wholesale_models import WholesaleOffer
    assert db.query(WholesaleOffer).count() == 0


def test_the_platform_loop_owns_the_hunt_on_the_backend_only(monkeypatch):
    from app import service_role
    from app.models.job_models import JobName, LOOP_JOB_NAMES
    import app.main as main
    assert JobName.EVOSENSE_HUNT in LOOP_JOB_NAMES
    monkeypatch.setenv("SERVICE_ROLE", "backend")
    assert service_role.owns(JobName.EVOSENSE_HUNT)
    for role in ("voice", "job", ""):
        monkeypatch.setenv("SERVICE_ROLE", role)
        assert not service_role.owns(JobName.EVOSENSE_HUNT)
    assert callable(main._evosense_hunt_loop)


def test_cadence_manual_and_interval(db_session, hunted):
    db, s = db_session, hunted["strategy"]
    SCH.set_cadence(db, s, "manual")
    db.commit()
    assert SCH.run_due(db)["manual_only"] == 1 and db.query(EvoSenseRun).count() == 0
    SCH.set_cadence(db, s, "interval", 6)
    db.commit()
    SCH.run_due(db)
    assert db.query(EvoSenseRun).filter_by(trigger="schedule").count() == 1
    sched = SCH.schedule_for(db, s)
    assert 5.9 * 3600 < (sched.next_due_at - C.now()).total_seconds() <= 6 * 3600
    assert SCH.run_due(db)["ran"] == 0, "not due again for six hours"
    with pytest.raises(ValueError):
        SCH.set_cadence(db, s, "interval", 0)


# ── 31. wrong person ────────────────────────────────────────────────────────

def test_wrong_person_through_the_webhook(db_session, hunted, twilio_webhook, sample_advisor):
    db, org = db_session, hunted["org"]
    SCH.run_due(db)
    prop, eng, lead, phone = _lead_phone(db, org, "4915 Live Oak St")
    cp = db.query(EvoSenseContactPoint).get(eng.contact_point_id)
    before = EV.CT.score_contact_point(db, prop, cp)["value"]
    sms(twilio_webhook, phone, SS.WRONG_PERSON_REPLY, "SMwrong01")
    db.expire_all()
    assert db.query(Reply).filter_by(lead_id=lead.id).count() == 1
    msgs = db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="inbound").all()
    assert len(msgs) == 1 and msgs[0].outcome == C.O_WRONG_PERSON
    cp = db.query(EvoSenseContactPoint).get(cp.id)
    assert cp.status == "wrong_party"
    assert EV.CT.score_contact_point(db, db.query(EvoSenseProperty).get(prop.id), cp)["value"] < before
    assert db.query(SuppressionEntry).filter_by(organization_id=org).filter(
        SuppressionEntry.phone == lead.phone).count() == 1
    assert db.query(Lead).get(lead.id).status == "dnc"
    assert db.query(EvoSenseFact).filter_by(property_id=prop.id).count() == 0, "no invented facts"
    assert db.query(EvoSenseProperty).get(prop.id).seller_intent == 0
    # a second strategy, hunting on its own, never contacts it again
    s2 = SS.create_strategy(db, org, sample_advisor, dict(SS.DFW, name="Second (TEST)"))
    db.commit()
    SCH.run_due(db)
    assert db.query(EvoSenseMessage).filter_by(property_id=prop.id, direction="outbound").count() == 1
    assert OU.start(db, db.query(EvoSenseProperty).get(prop.id), s2)["started"] is False


# ── 17. not now ─────────────────────────────────────────────────────────────

def test_not_now_through_the_webhook(db_session, hunted, twilio_webhook):
    db, org = db_session, hunted["org"]
    SCH.run_due(db)
    prop, eng, lead, phone = _lead_phone(db, org, "7302 Ferguson Rd")
    sms(twilio_webhook, phone, SS.NOT_NOW_REPLY, "SMnotnow1")
    db.expire_all()
    eng = db.query(EvoSenseEngagement).get(eng.id)
    assert eng.status == "nurture" and eng.nurture_until.month == 1
    assert "after the holidays" in eng.nurture_reason
    assert db.query(SuppressionEntry).filter(SuppressionEntry.phone == lead.phone).count() == 0
    assert db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="outbound").count() == 1


# ── 33. duplicate webhook delivery ─────────────────────────────────────────

def test_the_same_provider_message_twice_is_one_of_everything(db_session, hunted, twilio_webhook):
    db, org = db_session, hunted["org"]
    SCH.run_due(db)
    prop, eng, lead, phone = _lead_phone(db, org, FLAGSHIP)
    for _ in range(2):
        sms(twilio_webhook, phone, SS.FLAGSHIP_REPLY, "SMdup00001")
    db.expire_all()
    assert db.query(Reply).filter_by(lead_id=lead.id).count() == 1
    assert db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="inbound").count() == 1
    assert db.query(EvoSenseEvent).filter_by(property_id=prop.id, action="reply.read").count() == 1
    assert db.query(EvoSenseHandoff).filter_by(property_id=prop.id).count() == 1
    facts = db.query(EvoSenseFact).filter_by(property_id=prop.id).count()
    sms(twilio_webhook, phone, SS.FLAGSHIP_REPLY, "SMdup00001")
    assert db.query(EvoSenseFact).filter_by(property_id=prop.id).count() == facts


# ── 32. AI failure ──────────────────────────────────────────────────────────

@pytest.fixture()
def real_conversation(db_session, sample_org, sample_advisor):
    """A NON-sandbox owner (so the AI reader is used), worked through the
    platform cadence with the compliance attestation recorded."""
    db, org = db_session, sample_org.id
    s = SS.create_strategy(db, org, sample_advisor, dict(
        SS.DFW, name="Real (not test)", min_contact_confidence=0,
        outreach_policy={"auto_outreach": False, "cold_outreach_compliance_confirmed": True}))
    s.is_test = False
    r = HU.add_manual(db, org, {"street_address": "900 Real St", "city": "Dallas", "state": "TX",
                                "zip_code": "75201", "county": "Dallas", "property_type": "single_family",
                                "estimated_value": 250000, "mortgage_balance": 50000,
                                "last_sale_date": "2001-01-01", "owner_name": "Pat Q Realowner",
                                "mailing_street": "1 Elsewhere Ln", "mailing_city": "Austin",
                                "mailing_state": "TX", "mailing_zip": "78701",
                                "signals": ["VACANT", "TAX_DELINQUENT"]}, user=sample_advisor, strategy=s)
    prop = db.query(EvoSenseProperty).get(r["property_id"])
    from app.services import wholesale_enrichment as WE
    EV.CT.apply_result(db, EV.CT.primary_owner(db, prop),
                       WE.EnrichmentResult(status="succeeded", provider="manual",
                                           phones=[WE.EnrichmentPhone(number="2145550100")],
                                           message="person:Pat Q Realowner|owner"),
                       __import__("app.services.evosense.providers", fromlist=["x"]).PROVIDERS["manual"])
    EV.rescore(db, prop, s)
    out = OU.start(db, prop, s)
    assert out["started"], out
    db.commit()
    return {"prop": prop, "strategy": s, "phone": "+12145550100"}


def test_ai_failure_keeps_the_message_guesses_nothing_and_retries(db_session, real_conversation,
                                                                twilio_webhook, monkeypatch):
    from app.services import wholesale_ai
    db, prop = db_session, real_conversation["prop"]

    def broken(*a, **k):
        raise RuntimeError("AI gateway unavailable")
    monkeypatch.setattr(wholesale_ai, "extract_from_message", broken)
    text = "The roof guy is coming Tuesday"
    sms(twilio_webhook, real_conversation["phone"], text, "SMaifail1")
    sms(twilio_webhook, real_conversation["phone"], text, "SMaifail1")      # provider retry
    db.expire_all()
    lead_ids = [e.lead_id for e in db.query(EvoSenseEngagement).filter_by(property_id=prop.id)]
    assert db.query(Reply).filter(Reply.lead_id.in_(lead_ids)).count() == 1
    msg = db.query(EvoSenseMessage).filter_by(property_id=prop.id, direction="inbound").one()
    assert msg.body == text and msg.outcome is None                       # kept, not interpreted
    assert db.query(EvoSenseFact).filter_by(property_id=prop.id).count() == 0
    h = db.query(EvoSenseHandoff).filter_by(property_id=prop.id).one()
    assert "AI_REVIEW_PENDING" in {r["code"] for r in C.jload(h.reasons)}
    assert db.query(EvoSenseProperty).get(prop.id).status == C.S_NEEDS_YOU
    # still failing: the scheduler's retry is safe and changes nothing
    SCH.run_due(db)
    assert db.query(EvoSenseMessage).get(msg.id).outcome is None
    # the AI is back: the next pass reads it once
    monkeypatch.setattr(wholesale_ai, "extract_from_message",
                        lambda *a, **k: {"intent": "maybe_later", "confidence": 80, "source": "ai",
                                         "summary": "family deciding"})
    assert SCH.run_due(db)["replies_retried"] == 1
    db.expire_all()
    msg = db.query(EvoSenseMessage).get(msg.id)
    assert msg.outcome is not None
    h = db.query(EvoSenseHandoff).get(h.id)
    assert "AI_REVIEW_PENDING" not in {r["code"] for r in C.jload(h.reasons)}
    assert SCH.run_due(db)["replies_retried"] == 0, "read once, never twice"


def test_pausing_ai_replies_means_no_model_call(db_session, real_conversation, twilio_webhook,
                                               monkeypatch):
    from app.services import wholesale_ai
    db, prop = db_session, real_conversation["prop"]
    C.controls(db, prop.organization_id).paused_ai_replies = True
    db.commit()
    called = []
    monkeypatch.setattr(wholesale_ai, "extract_from_message", lambda *a, **k: called.append(1))
    sms(twilio_webhook, real_conversation["phone"], "The weather is lovely", "SMaipaus1")
    db.expire_all()
    msg = db.query(EvoSenseMessage).filter_by(property_id=prop.id, direction="inbound").one()
    assert not called and msg.outcome == C.O_UNKNOWN          # rules read it; a person reviews it


# ── 35. pause ───────────────────────────────────────────────────────────────

def test_pause_stops_hunting_but_never_stops_safety(db_session, hunted, twilio_webhook):
    db, org = db_session, hunted["org"]
    SCH.run_due(db)
    ctl = C.controls(db, org)
    ctl.paused_all = True
    sched = SCH.schedule_for(db, hunted["strategy"])
    sched.next_due_at = C.now()                      # due again
    db.commit()
    runs = db.query(EvoSenseRun).count()
    report = SCH.run_due(db)
    assert report["skipped_paused"] == 1 and db.query(EvoSenseRun).count() == runs
    assert db.query(EvoSenseEvent).filter_by(action="hunt.skipped_paused").count() == 1
    SCH.run_due(db)
    assert db.query(EvoSenseEvent).filter_by(action="hunt.skipped_paused").count() == 1, "logged once"

    # an opt-out while paused is honoured at once
    prop, eng, lead, phone = _lead_phone(db, org, "7302 Ferguson Rd")
    sms(twilio_webhook, phone, "STOP", "SMpause01")
    db.expire_all()
    assert db.query(Reply).filter_by(lead_id=lead.id).count() == 1
    assert db.query(SuppressionEntry).filter(SuppressionEntry.phone == lead.phone).count() == 1
    msg = db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="inbound").one()
    assert msg.outcome == C.O_DNC
    assert db.query(EvoSenseContactPoint).get(eng.contact_point_id).status in ("opted_out", "suppressed")

    # anything else is saved and HELD, then read after resume
    prop, eng, lead, phone = _lead_phone(db, org, FLAGSHIP)
    sms(twilio_webhook, phone, SS.FLAGSHIP_REPLY, "SMpause02")
    db.expire_all()
    held = db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="inbound").one()
    assert held.outcome is None and C.jload(held.reading)["pending"] == "held"
    assert db.query(EvoSenseFact).filter_by(property_id=prop.id).count() == 0
    C.controls(db, org).paused_all = False
    db.commit()
    SCH.run_due(db)
    db.expire_all()
    assert db.query(EvoSenseMessage).get(held.id).outcome == C.O_INTERESTED
    assert db.query(EvoSenseProperty).get(prop.id).status == C.S_NEEDS_YOU


def test_pause_paid_data_hunts_without_buying(db_session, hunted):
    db, org = db_session, hunted["org"]
    C.controls(db, org).paused_paid_data = True
    db.commit()
    SCH.run_due(db)
    assert db.query(EvoSenseRun).filter_by(trigger="schedule", status="succeeded").count() == 1
    assert db.query(EvoSenseProperty).count() > 0
    assert db.query(EvoSenseCostEntry).count() == 0


# ── 13. ambiguous routing ───────────────────────────────────────────────────

def test_ambiguous_sender_goes_to_routing_review_not_to_a_guess(db_session, hunted, twilio_webhook,
                                                               client, auth_headers):
    db, org = db_session, hunted["org"]
    SCH.run_due(db)
    prop, eng, lead, phone = _lead_phone(db, org, FLAGSHIP)
    # the same number is also the seller on another EvoSense conversation, as a different Lead
    other = SS.prop_at(db, org, "7302 Ferguson Rd")
    other_eng = SS.engagement_for(db, other)
    twin = Lead(organization_id=org, first_name="Twin", phone=lead.phone, status="new", is_test=True)
    db.add(twin)
    db.flush()
    other_eng.lead_id = twin.id
    db.commit()
    sms(twilio_webhook, phone, "Yes call me", "SMambig01")
    db.expire_all()
    assert db.query(EvoSenseMessage).filter(EvoSenseMessage.direction == "inbound").count() == 0
    reviews = db.query(EvoSenseEvent).filter_by(action="inbound.routing_review").all()
    assert len(reviews) == 1
    cc = client.get("/wholesale/evosense/command-center", headers=auth_headers).json()
    assert cc["automation"]["inbound"]["routing_review"] == 1
    r = client.post("/wholesale/evosense/routing-reviews/%s" % reviews[0].id, headers=auth_headers,
                    json={"engagement_id": eng.id})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(EvoSenseMessage).filter_by(engagement_id=eng.id, direction="inbound").count() == 1
    assert db.query(EvoSenseMessage).filter_by(engagement_id=other_eng.id, direction="inbound").count() == 0
    assert client.get("/wholesale/evosense/command-center",
                      headers=auth_headers).json()["automation"]["inbound"]["routing_review"] == 0


def test_a_normal_lead_reply_is_not_touched_by_evosense(db_session, hunted, twilio_webhook, sample_lead):
    sample_lead.phone = "12145557777"
    db_session.commit()
    sms(twilio_webhook, "+12145557777", "Yes I'm interested", "SMnormal1")
    assert db_session.query(EvoSenseMessage).count() == 0
    assert db_session.query(Reply).filter_by(lead_id=sample_lead.id).count() == 1


# ── 22. tenant safety ───────────────────────────────────────────────────────

def test_a_reply_to_org_a_never_touches_org_b(db_session, hunted, twilio_webhook):
    db, org = db_session, hunted["org"]
    SCH.run_due(db)
    b = Organization(name="Org B (TEST)", slug="org-b-evo", plan="standard", industry="real_estate",
                     org_twilio_phone_number="+19995550000")
    db.add(b)
    db.commit()
    ub = User(organization_id=b.id, email="b@b.test", password_hash="x", full_name="B", role="org_admin",
              must_change_password=False)
    db.add(ub)
    db.commit()
    SS.seed_review(db, b.id, ub, replies=False)       # the SAME sandbox sellers, in Org B
    _, eng_b, lead_b, _ = _lead_phone(db, b.id, "7302 Ferguson Rd")
    prop_a, eng_a, lead_a, phone = _lead_phone(db, org, "7302 Ferguson Rd")
    assert lead_a.phone == lead_b.phone
    sms(twilio_webhook, phone, SS.NOT_NOW_REPLY, "SMtenant1")      # arrives on Org A's number
    db.expire_all()
    assert db.query(EvoSenseEngagement).get(eng_a.id).status == "nurture"
    assert db.query(EvoSenseEngagement).get(eng_b.id).status == "active"
    assert db.query(EvoSenseMessage).filter_by(organization_id=b.id, direction="inbound").count() == 0
    assert db.query(Reply).filter_by(lead_id=lead_b.id).count() == 0
    assert db.query(Lead).get(lead_b.id).status != "dnc"


# ── 34. scheduler concurrency ───────────────────────────────────────────────

def test_a_held_lock_means_the_second_hunt_is_skipped(db_session, hunted):
    db, s = db_session, hunted["strategy"]
    token = SCH.claim(db, s)
    assert token
    report = SCH.run_due(db)
    assert report["skipped_lock"] == 1
    assert db.query(EvoSenseRun).filter(EvoSenseRun.status != "skipped").count() == 0
    assert db.query(EvoSenseProperty).count() == 0 and db.query(EvoSenseCostEntry).count() == 0
    SCH.release(db, s, token)
    assert SCH.run_due(db)["ran"] == 1


def _backends():
    yield "sqlite"
    if os.environ.get("EVOSENSE_TEST_PG_URL"):
        yield "postgres"


@pytest.mark.parametrize("backend", list(_backends()))
def test_two_workers_start_the_same_due_strategy_at_once(backend, tmp_path):
    """Two real threads, two sessions, one barrier: ONE hunt, one set of costs."""
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import sessionmaker
    from app.models.models import Base
    from tests.test_evosense_budget_concurrency import _engine
    engine = _engine(backend, tmp_path)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    s = Session()
    org = Organization(name="Race Org", slug="race-evo", plan="standard", industry="real_estate")
    s.add(org)
    s.flush()
    SS.enable_sandbox(s, org.id)
    strat = SS.create_strategy(s, org.id, None, dict(SS.DFW, outreach_policy={"auto_outreach": False}))
    s.commit()
    org_id, sid = org.id, strat.id
    s.close()

    barrier = threading.Barrier(2)
    results, lock = [], threading.Lock()

    def worker():
        barrier.wait()
        for _ in range(400):
            db = Session()
            try:
                st = db.query(EvoSenseStrategy).get(sid)
                run = HU.run_strategy(db, org_id, st, trigger="schedule")
                with lock:
                    results.append((run.status, run.error))
                return
            except OperationalError as exc:
                db.rollback()
                if "locked" not in str(exc):
                    with lock:
                        results.append(("error", str(exc)))
                    return
                time.sleep(0.02)
            finally:
                db.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    statuses = sorted(r[0] for r in results)
    assert statuses == ["skipped", "succeeded"], results
    db = Session()
    assert db.query(EvoSenseRun).filter_by(status="succeeded").count() == 1
    single = sum(e.total_cents for e in db.query(EvoSenseCostEntry).filter_by(status="charged"))
    props = db.query(EvoSenseProperty).count()
    charged = [e.owner_id for e in db.query(EvoSenseCostEntry)
               .filter_by(capability="CONTACT_ENRICHMENT", status="charged")]
    assert len(charged) == len(set(charged)), "no owner bought twice"
    assert single > 0 and props == 29
    db.close()
    engine.dispose()


# ── 8. failure behaviour ────────────────────────────────────────────────────

def test_a_failed_hunt_is_recorded_surfaced_and_retried_without_duplicates(db_session, hunted,
                                                                          monkeypatch):
    db, s = db_session, hunted["strategy"]
    real = HU.EN.run
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("provider exploded mid-hunt")
        return real(*a, **k)
    monkeypatch.setattr(HU.EN, "run", flaky)
    report = SCH.run_due(db)
    assert report["failed"] == 1
    run = db.query(EvoSenseRun).filter_by(strategy_id=s.id).one()
    assert run.status == "failed" and "provider exploded" in run.error
    sched = SCH.schedule_for(db, s)
    assert sched.last_status == "failed" and sched.locked_until is None
    assert 0 < (sched.next_due_at - C.now()).total_seconds() <= 3600
    assert db.query(EvoSenseEvent).filter_by(action="hunt.failed").count() == 1
    props_after_fail = db.query(EvoSenseProperty).count()
    charged_after_fail = db.query(EvoSenseCostEntry).filter_by(status="charged").count()
    monkeypatch.setattr(HU.EN, "run", real)
    sched.next_due_at = C.now()
    db.commit()
    assert SCH.run_due(db)["ran"] == 1
    assert db.query(EvoSenseProperty).count() == props_after_fail, "no duplicate property"
    owners = [e.owner_id for e in db.query(EvoSenseCostEntry)
              .filter_by(capability="CONTACT_ENRICHMENT", status="charged")]
    assert len(owners) == len(set(owners)), "no owner charged twice"
    assert db.query(EvoSenseCostEntry).filter_by(status="charged").count() >= charged_after_fail


def test_manual_run_hunt_uses_the_same_lock(db_session, hunted, client, admin_auth_headers):
    db, s = db_session, hunted["strategy"]
    token = SCH.claim(db, s)
    r = client.post("/wholesale/evosense/strategies/%s/hunt" % s.id, headers=admin_auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "skipped"
    assert "already running" in r.json()["error"]
    SCH.release(db, s, token)


def test_command_center_tells_the_truth_about_automation(db_session, hunted, client, auth_headers):
    SCH.run_due(db_session)
    cc = client.get("/wholesale/evosense/command-center", headers=auth_headers).json()
    a = cc["automation"]
    assert a["hunting"] == "active"
    row = a["strategies"][0]
    assert row["cadence"] == "daily" and row["state"] == "scheduled" and row["next_due_at"]
    assert not any("scripts/evosense_hunt.py" in str(n) for n in cc["next"])
    C.controls(db_session, hunted["org"]).paused_all = True
    db_session.commit()
    cc = client.get("/wholesale/evosense/command-center", headers=auth_headers).json()
    assert cc["automation"]["hunting"] == "paused"
    assert cc["automation"]["strategies"][0]["next_due_at"] is None, "no fake timestamp"
