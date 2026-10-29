"""PERMANENT REGRESSION SUITE - FORECLOSURE WORK-PRIORITY POLICY (owner requirement, 2026-10-26).

Policy: docs/wholesale/FORECLOSURE_PRIORITY_POLICY.md. Code: app/services/evosense/work_priority.py.

Every verified imminent foreclosure auction is worked first, whatever its
investment evidence score and however incomplete its money, owner or contact
data. Auction urgency is not profitability. Unknown value is not a bad deal.

DO NOT REMOVE OR WEAKEN THESE TESTS. A change that needs them changed is a
change to the owner's business rule and needs the owner's decision first.

Synthetic records only - nothing is bought, nobody is contacted.
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.models.evosense_models import (EvoSenseAuctionEvent, EvoSenseEngagement, EvoSenseHandoff,
                                        EvoSenseMessage, EvoSenseProperty, EvoSenseSignal)
from app.services.evosense import common as C
from app.services.evosense import foreclosure_cycles as F
from app.services.evosense import opportunity as OP
from app.services.evosense import views as V
from app.services.evosense import work_priority as WP
from app.services.evosense import cohort as CO

from tests.test_evosense_foreclosure_cycles import _notice, _prop

TODAY = date(2026, 10, 26)            # the owner's report: Monday, Oct 26 2026
SALE = "2026-11-03"                   # the first-Tuesday trustee sale, 8 days away


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(WP, "today_ct", lambda: TODAY)
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F._CACHE.clear()


def _policy(score, *, status="notice_verified", sale=SALE, contact=False, wf="DISCOVERED", deal="UNKNOWN",
            owner_review=False, kind="mortgage"):
    au = WP.auction_urgency(status, sale, kind=kind, today=TODAY)
    wp = WP.work_priority(au, workflow_state=wf, contact_ready=contact, owner_review=owner_review,
                          deal_level=deal, score=score, prop_id="p")
    return au, wp


# A ── verified Nov 3, score 38 → CRITICAL / CRITICAL ───────────────────────
def test_A_verified_nov3_with_score_38_is_critical():
    au, wp = _policy(38)
    assert au["level"] == WP.A_CRITICAL and au["days"] == 8 and au["label"] == "CRITICAL — 8 DAYS"
    assert wp["level"] == WP.W_CRITICAL and wp["label"] == "CRITICAL — ACT NOW"


# B ── the same sale at 74: the deadline does not move ─────────────────────
def test_B_a_higher_investment_score_does_not_change_the_deadline():
    a38, w38 = _policy(38)
    a74, w74 = _policy(74)
    assert a38 == a74 and w38["level"] == w74["level"] == WP.W_CRITICAL and w38["rank"] == w74["rank"]
    assert w74["sort_key"] < w38["sort_key"]           # the score only breaks the tie


# C ── no payoff, no comps: CRITICAL, potential UNKNOWN, no invented profit ─
def test_C_missing_money_is_unknown_never_low_and_never_profit():
    econ = OP.economics(arv=None, repairs=None, settings=None)
    dp = OP.deal_potential(econ, {"value": 41}, "residential")
    au, wp = _policy(41, deal=dp["level"])
    assert wp["level"] == WP.W_CRITICAL
    assert dp["level"] == "UNKNOWN" and dp["label"] == "POTENTIAL UNKNOWN - RESEARCH REQUIRED"
    assert econ["spreads"] == {} and econ["score"] is None


# D ── missing phone: still CRITICAL, next action contact research ────────
def test_D_no_phone_is_critical_with_find_contact_and_nothing_is_sent(db_session, sample_org):
    au, wp = _policy(41, contact=False)
    assert wp["level"] == WP.W_CRITICAL and wp["next_action"] == "Find contact"
    au, wp = _policy(41, contact=True)
    assert wp["next_action"].startswith("Call the owner")
    p = _prop(db_session, sample_org.id, 40)
    _notice(db_session, p, SALE, "D-1")
    db_session.commit()
    OP.refresh(db_session, sample_org.id, [p])
    db_session.commit()
    assert db_session.query(EvoSenseMessage).filter(EvoSenseMessage.organization_id == sample_org.id).count() == 0
    assert db_session.query(EvoSenseEngagement).filter(EvoSenseEngagement.property_id == p.id).count() == 0


# E ── an imported Nov 3 date with no official notice ────────────────────
def test_E_a_list_date_is_urgent_to_verify_never_verified_never_hidden(db_session, sample_org):
    au, wp = _policy(38, status="claimed")
    assert au["level"] == WP.A_URGENT_VERIFY and au["label"].startswith("URGENT — VERIFY NOTICE")
    assert not au["verified"] and au["urgent"] and wp["level"] == WP.W_REVIEW_URGENT
    p = _prop(db_session, sample_org.id, 41)
    _notice(db_session, p, SALE, "LIST-E", source="csv_import", claim=True)
    db_session.commit()
    OP.refresh(db_session, sample_org.id, [p])
    db_session.commit()
    assert p.work_priority == WP.W_REVIEW_URGENT and p.status == C.S_NEEDS_YOU


# F ── confirmed postponement to a later month ───────────────────────────
def test_F_a_postponement_moves_urgency_to_the_new_date_and_keeps_november(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    p = _prop(db, org, 42)
    _notice(db, p, SALE, "F-NOV")
    db.commit()
    F.start_month(db, org, "2026-11")
    db.commit()
    ev = db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == p.id).one()
    F.record_status(db, org, ev.id, "postponed", evidence="Trustee confirmed postponement", user=sample_advisor,
                    new_month="2026-12")
    db.commit()
    res = OP.build(db, org, [p])[p.id]["measures"]
    assert res["auction_urgency"]["sale_date"] == "2026-12-01" and res["auction_urgency"]["level"] != WP.A_CRITICAL
    assert res["work_priority"]["level"] != WP.W_CRITICAL
    assert db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == p.id).count() == 2
    assert db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.id == ev.id).one().manual_status == "postponed"


# G ── canceled with evidence ────────────────────────────────────────────
def test_G_a_cancellation_leaves_active_work_but_keeps_history(db_session, sample_org, sample_advisor):
    db, org = db_session, sample_org.id
    p = _prop(db, org, 43)
    _notice(db, p, SALE, "G-NOV")
    db.commit()
    F.start_month(db, org, "2026-11")
    db.commit()
    ev = db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == p.id).one()
    F.record_status(db, org, ev.id, "canceled", evidence="Lender rescinded the sale", user=sample_advisor)
    db.commit()
    m = OP.build(db, org, [p])[p.id]["measures"]
    assert m["auction_urgency"]["level"] == WP.A_CANCELED and m["work_priority"]["level"] == WP.W_CLOSED
    F._CACHE.clear()
    rs = F.rows(db, org, "2026-11")
    assert p.id in {r["id"] for r in rs}                                    # history kept
    assert p.id not in {r["id"] for r in F.filter_rows(rs, {"quick": "critical"})}


# H ── a low score never drops a sale out of the critical filters ─────────
def test_H_low_scores_stay_in_every_critical_filter(db_session, sample_org):
    db, org = db_session, sample_org.id
    p = _prop(db, org, 44)
    p.opportunity_score = 12
    _notice(db, p, SALE, "H-NOV")
    db.commit()
    F.start_month(db, org, "2026-11")
    db.commit()
    OP.refresh(db, org, [p])
    db.commit()
    rs = F.rows(db, org, "2026-11")
    assert p.id in {r["id"] for r in F.filter_rows(rs, {"quick": "critical"})}
    assert p.id in {r["id"] for r in F.filter_rows(rs, {"priority": "CRITICAL"})}
    inbox = V.inbox(db, org, filters={"priority": "CRITICAL"})
    assert p.id in {i["id"] for i in inbox["items"]}


# I + J ── the API answer equals the displayed answer, on every screen ────
def test_I_J_one_property_one_answer_on_every_screen(client, auth_headers, db_session, sample_org):
    db, org = db_session, sample_org.id
    p = _prop(db, org, 45)
    _notice(db, p, SALE, "IJ-NOV")
    db.commit()
    F.start_month(db, org, "2026-11")
    db.commit()
    OP.refresh(db, org, [p])
    db.commit()
    fresh = OP.build(db, org, [p])[p.id]["measures"]["work_priority"]["level"]
    inbox = {i["id"]: i for i in V.inbox(db, org)["items"]}
    fcc = {r["id"]: r for r in F.rows(db, org, "2026-11")}
    top = {r["id"]: r for r in CO.rows_for(db, org, [p])}
    assert fresh == WP.W_CRITICAL
    assert inbox[p.id]["work"]["work_priority"] == fresh                 # Inbox
    assert fcc[p.id]["measures"]["work_priority"]["level"] == fresh       # Foreclosures
    assert top[p.id]["work"]["work_priority"] == fresh                    # Top 100 / lists
    assert p.work_priority == fresh and p.status == C.S_NEEDS_YOU         # the cached column and the Inbox bucket
    handoffs = db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id == p.id).all()
    assert len(handoffs) == 1 and handoffs[0].status in ("open", "acknowledged")   # the VA / manager queue
    r = client.get("/wholesale/evosense/inbox", headers=auth_headers)
    if r.status_code == 200 and any(i["id"] == p.id for i in r.json()["items"]):
        api = {i["id"]: i for i in r.json()["items"]}[p.id]
        assert api["work"]["work_priority"] == fresh                     # the API the screen renders
        assert r.json()["items"][0]["work"]["work_priority"] in (WP.W_CRITICAL, WP.W_REVIEW_URGENT)


# K ── 800 records: counts, classes and a deterministic order ─────────────
def test_K_an_800_record_population_classifies_and_sorts_deterministically():
    mix = [("notice_verified", SALE)] * 300 + [("claimed", SALE)] * 150 + [("conflict", SALE)] * 50 + \
          [("postponed", SALE)] * 50 + [("notice_verified", "2026-12-01")] * 100 + [(None, None)] * 100 + \
          [("canceled", SALE)] * 50
    rows = []
    for i, (st, sale) in enumerate(mix):
        au = WP.auction_urgency(st, sale, today=TODAY)
        wp = WP.work_priority(au, workflow_state="DISCOVERED", contact_ready=i % 7 == 0, owner_review=False,
                              deal_level="UNKNOWN", score=(i * 37) % 100, prop_id="p%04d" % i)
        rows.append((au, wp))
    from collections import Counter
    levels = Counter(wp["level"] for _, wp in rows)
    assert len(rows) == 800
    assert levels == {WP.W_CRITICAL: 300, WP.W_REVIEW_URGENT: 200, WP.W_HIGH: 100, WP.W_NORMAL: 150,
                      WP.W_CLOSED: 50}
    ordered = sorted(rows, key=lambda r: r[1]["sort_key"])
    assert all(wp["level"] == WP.W_CRITICAL for _, wp in ordered[:300])
    assert all(wp["level"] == WP.W_REVIEW_URGENT for _, wp in ordered[300:500])
    assert ordered == sorted(rows, key=lambda r: r[1]["sort_key"])        # deterministic


# L ── reruns do not duplicate anything ──────────────────────────────────
def test_L_reruns_never_duplicate_events_properties_or_tasks(db_session, sample_org):
    db, org = db_session, sample_org.id
    p = _prop(db, org, 46)
    _notice(db, p, SALE, "L-NOV")
    db.commit()
    for _ in range(3):
        F.start_month(db, org, "2026-11")
        OP.refresh(db, org, [p])
        OP.refresh_auction_properties(db, org_id=org)
        db.commit()
    assert db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == p.id).count() == 1
    assert db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id == p.id).count() == 1
    assert db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org,
                                             EvoSenseProperty.street_address == p.street_address).count() == 1


# M ── Central Time, not the server's UTC ────────────────────────────────
def test_M_the_day_boundary_is_central_time(monkeypatch):
    monkeypatch.undo()                                       # the real clock function, a fixed instant

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            # 11:30 PM CDT on Nov 2 is already 04:30 UTC on Nov 3
            return datetime(2026, 11, 3, 4, 30, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(WP, "datetime", FakeDT)
    assert WP.today_ct() == date(2026, 11, 2)
    au = WP.auction_urgency("notice_verified", SALE, today=WP.today_ct())
    assert au["days"] == 1 and au["label"] == "CRITICAL — 1 DAY"


# N ── every property type is worked by its deadline, not house-only rules ─
def test_N_every_property_type_gets_the_same_deadline_rule():
    from app.services.evosense import scoring as SC
    for cat in ("residential", "commercial", "multifamily", "industrial", "land", "agricultural"):
        econ = OP.economics(arv=None, repairs=None, settings=None)
        dp = OP.deal_potential(econ, {"value": 30}, cat)
        au, wp = _policy(30, deal=dp["level"])
        assert wp["level"] == WP.W_CRITICAL, cat
        if cat != "residential":
            assert dp["level"] == "INSUFFICIENT", cat               # no house model applied
            assert SC.type_excluded("ABSENTEE_OWNER", cat) or cat == "residential"


# tax sales are their own kind of auction, never confused with trustee sales
def test_tax_sales_are_labelled_tax_sales_and_are_critical_when_posted_for_nov3():
    au = WP.auction_urgency("tax_sale_posted", SALE, kind="tax", today=TODAY)
    assert au["level"] == WP.A_CRITICAL and au["kind_label"] == "Tax sale" and au["verified"]
    both = WP.controlling([WP.auction_urgency("notice_verified", "2026-12-01", today=TODAY), au])
    assert both is au


# O ── tenant isolation ─────────────────────────────────────────────────
def test_O_another_workspace_never_sees_these_priorities(db_session, sample_org):
    from app.models.models import Organization
    db, org = db_session, sample_org.id
    p = _prop(db, org, 47)
    _notice(db, p, SALE, "O-NOV")
    db.commit()
    OP.refresh(db, org, [p])
    db.commit()
    other = Organization(name="Synthetic Other Workspace", slug="synthetic-other-ws-prio")
    db.add(other)
    db.commit()
    assert p.id not in {i["id"] for i in V.inbox(db, other.id, filters={"priority": "CRITICAL"})["items"]}
    assert OP.refresh_auction_properties(db, org_id=other.id) == 0


# P — the dashboard shown in the owner's screenshot MUST also obey the policy.
# Previous regressions checked the Inbox/Foreclosures/Top 100 but missed
# GET /wholesale/evosense/command-center ("Recent opportunities" and hero).
def test_acquisition_command_uses_deadline_first_not_the_green_investment_score(db_session, sample_org):
    db, org = db_session, sample_org.id
    urgent = _prop(db, org, 950)
    routine = _prop(db, org, 951)
    _notice(db, urgent, SALE, "ACQUISITION-COMMAND-NOV3")
    db.commit()
    OP.refresh(db, org, [urgent])
    db.commit()

    # An urgent sale with a low evidence score beats a routine property
    # with a much higher evidence score, in BOTH dashboard panels.
    urgent.opportunity_score = 38
    routine.opportunity_score = 99
    routine.status = C.S_HIGH
    routine.work_priority = WP.W_NORMAL
    routine.work_priority_rank = WP.work_priority(
        WP.auction_urgency(None, None, today=TODAY),
        workflow_state="DISCOVERED", contact_ready=False, owner_review=False,
        deal_level="UNKNOWN", score=99, prop_id=routine.id)["rank"]
    routine.sale_deadline = None
    db.add(EvoSenseHandoff(organization_id=org, property_id=routine.id,
                          reasons="[]", priority=999, next_action="Routine follow-up",
                          status="open"))
    db.commit()

    assert urgent.work_priority_rank < routine.work_priority_rank
    body = V.command_center(db, org)
    ids = [p["id"] for p in body["found"]]
    assert ids.index(urgent.id) < ids.index(routine.id)
    assert body["found"][0]["work"]["work_priority"] == WP.W_CRITICAL
    assert body["found"][0]["work"]["auction_date"] == SALE
    assert body["found"][0]["opportunity_score"] == 38
    assert body["needs_you"][0]["property_id"] == urgent.id
    assert body["needs_you"][0]["work"]["work_priority"] == WP.W_CRITICAL
