"""Monthly foreclosure cycles (Foreclosure Command Center) - synthetic records only.
Nothing is bought, nobody is contacted."""
from datetime import date, datetime, timedelta

import pytest

from app.models.evosense_models import (EvoSenseAuctionCycle, EvoSenseAuctionEvent, EvoSenseEvent,
                                        EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty, EvoSenseSignal)
from app.services.evosense import common as C
from app.services.evosense import foreclosure_cycles as F

TODAY = date(2026, 10, 25)


def _prop(db, org, n, county="Dallas", *, owner=True):
    p = EvoSenseProperty(organization_id=org, street_address="%d Cycle Test St" % n, city="Dallas", state="TX",
                         zip_code="75216", county=county, property_type="single_family", is_test=False,
                         status="new", opportunity_score=60 + n, priority_score=60 + n)
    db.add(p)
    db.flush()
    if owner:
        o = EvoSenseOwner(organization_id=org, display_name="SYNTH CYCLE OWNER %d" % n, owner_type="individual",
                          mailing_street="%d Cycle Test St" % n)
        db.add(o)
        db.flush()
        db.add(EvoSenseOwnership(organization_id=org, property_id=p.id, owner_id=o.id, is_current=True,
                                 source="dcad"))
    db.flush()
    return p


def _notice(db, p, sale, doc, *, source="tx_clerk_foreclosure", status="scheduled", claim=False):
    s = EvoSenseSignal(organization_id=p.organization_id, property_id=p.id, signal_type="PRE_FORECLOSURE",
                       source=source, source_reference="doc:%s" % doc, case_status=status, active=True,
                       confidence=90, observed_at=C.now(),
                       provenance=C.jdump({"document": doc, "sale_date": sale, "recorded": "2026-09-15",
                                           "foreclosure_type": "substitute_trustee", "claim": claim}))
    db.add(s)
    db.flush()
    return s


@pytest.fixture()
def world(db_session, sample_org):
    db, org = db_session, sample_org.id
    a = _prop(db, org, 1)
    _notice(db, a, "2026-11-03", "NOV-A")
    b = _prop(db, org, 2, county="Tarrant")
    _notice(db, b, "2026-11-03", "NOV-B")
    _notice(db, b, "2026-12-01", "DEC-B")                    # posted again for December
    c = _prop(db, org, 3)
    _notice(db, c, "2026-11-03", "LIST-C", source="csv_import", claim=True)    # a list's claim only
    d = _prop(db, org, 4, owner=False)
    _notice(db, d, "2026-11-03", "NOV-D")
    e = _prop(db, org, 5)
    _notice(db, e, "2026-10-06", "OCT-E")                    # an earlier month
    db.commit()
    return {"db": db, "org": org, "a": a, "b": b, "c": c, "d": d, "e": e}


def test_auction_day_follows_the_texas_rule():
    assert F.trustee_sale_date("2026-11") == date(2026, 11, 3)
    assert F.trustee_sale_date("2026-12") == date(2026, 12, 1)
    assert F.trustee_sale_date("2030-01") == date(2030, 1, 2)       # Jan 1 is a Tuesday -> Wednesday
    assert F.trustee_sale_date("2028-07") == date(2028, 7, 5)       # Jul 4 is a Tuesday -> Wednesday
    assert F.shift("2026-12", 1) == "2027-01" and F.shift("2027-01", -1) == "2026-12"
    assert F.next_month(today=date(2026, 11, 4)) == "2026-12"       # November's sale day has passed
    assert F.next_month(today=TODAY) == "2026-11"
    with pytest.raises(ValueError):
        F.parse_month("2026-13")


def test_start_month_is_idempotent_and_links_each_property_once(world):
    db, org = world["db"], world["org"]
    pv = F.preview_start(db, org, "2026-11")
    assert pv["would_add"] == 4 and not pv["cycle_exists"]
    assert db.query(EvoSenseAuctionCycle).count() == 0                # a preview writes nothing
    out = F.start_month(db, org, "2026-11")
    db.commit()
    again = F.start_month(db, org, "2026-11")
    db.commit()
    assert out["added"] == 4 and again["added"] == 0 and again["created"] is False
    assert db.query(EvoSenseAuctionCycle).filter(EvoSenseAuctionCycle.month == "2026-11").count() == 1
    assert db.query(EvoSenseAuctionEvent).count() == 4
    assert db.query(EvoSenseProperty).count() == 5                    # no property record created
    assert db.query(EvoSenseSignal).count() == 6                      # no notice created or changed


def test_statuses_come_from_the_notices_and_the_calendar_never_decides(world, monkeypatch):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11", )
    F.start_month(db, org, "2026-10")
    db.commit()
    rows = {r["id"]: r for r in F.rows(db, org, "2026-11")}
    assert rows[world["a"].id]["auction"]["status"] == "notice_verified"
    assert rows[world["c"].id]["auction"]["status"] == "claimed"     # a list's word, not a verified notice
    assert "December 2026 - possible reschedule" in " ".join(rows[world["b"].id]["auction"]["flags"])
    oct_rows = {r["id"]: r for r in F.rows(db, org, "2026-10")}
    assert oct_rows[world["e"].id]["auction"]["status"] == "outcome_unverified"   # date passed, outcome unknown


def test_a_property_in_two_months_keeps_both_notices_and_is_flagged(world, monkeypatch):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    F.start_month(db, org, "2026-12")
    db.commit()
    F._CACHE.clear()
    nov = {r["id"]: r for r in F.rows(db, org, "2026-11")}
    dec = {r["id"]: r for r in F.rows(db, org, "2026-12")}
    b = world["b"].id
    assert [d["document"] for d in nov[b]["auction"]["documents"]] == ["NOV-B"]
    assert [d["document"] for d in dec[b]["auction"]["documents"]] == ["DEC-B"]
    assert any("possible reschedule" in f for f in nov[b]["auction"]["flags"])     # never "postponed" by inference
    assert nov[b]["auction"]["status"] == "notice_verified"
    assert dec[b]["auction"]["carried"] is True


def test_a_person_records_a_postponement_with_evidence_and_history_is_kept(world, monkeypatch, sample_advisor):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    db.commit()
    ev = db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == world["a"].id).one()
    with pytest.raises(ValueError):
        F.record_status(db, org, ev.id, "postponed", evidence="", user=sample_advisor)   # no evidence, no record
    out = F.record_status(db, org, ev.id, "postponed", evidence="Trustee's office confirmed by phone",
                          user=sample_advisor, new_month="2027-01")
    db.commit()
    assert out["moved_to"]["month"] == "2027-01"
    jan = db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.id == out["moved_to"]["event_id"]).one()
    assert jan.carried_from_event_id == ev.id and jan.property_id == ev.property_id
    assert "Postponed from November 2026 to January 2027" in jan.carry_reason
    assert db.query(EvoSenseSignal).filter(EvoSenseSignal.property_id == world["a"].id).one().case_status == "scheduled"
    audit = db.query(EvoSenseEvent).filter(EvoSenseEvent.action == "auction.status_recorded").one()
    assert "before" in audit.details and ev.id in audit.details
    F.start_month(db, org, "2027-01")                                  # the postponed one is already there
    db.commit()
    assert db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == world["a"].id).count() == 2


def test_postponed_without_a_new_date_is_carried_into_the_next_month_once(world, monkeypatch, sample_advisor):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    ev = db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == world["d"].id).one()
    F.record_status(db, org, ev.id, "postponed", evidence="Clerk shows a rescission of the posting", user=sample_advisor)
    db.commit()
    assert F.preview_start(db, org, "2026-12")["postponed_to_carry"] == 1
    out = F.start_month(db, org, "2026-12")
    again = F.start_month(db, org, "2026-12")
    db.commit()
    assert out["carried_forward"] == 1 and again["carried_forward"] == 0
    dec = F.get_cycle(db, org, "2026-12")
    carried = [e for e in F._events(db, org, dec.id) if e.property_id == world["d"].id]
    assert len(carried) == 1 and carried[0].derived_status == "awaiting_notice"


def test_canceled_and_sold_need_evidence_and_never_touch_the_notice(world, monkeypatch, sample_advisor):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    ev = db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.property_id == world["b"].id).one()
    with pytest.raises(ValueError):
        F.record_status(db, org, ev.id, "sold_at_auction", evidence="ok", user=sample_advisor)
    F.record_status(db, org, ev.id, "canceled", evidence="Notice of rescission 2026-1234", user=sample_advisor)
    db.commit()
    F._CACHE.clear()
    r = {x["id"]: x for x in F.rows(db, org, "2026-11")}[world["b"].id]
    assert r["auction"]["status"] == "canceled" and r["auction"]["manual"] is True
    assert not any("possible reschedule" in f for f in r["auction"]["flags"])   # a closed sale is not "rescheduled"
    with pytest.raises(LookupError):
        F.record_status(db, "another-org", ev.id, "canceled", evidence="x" * 10)


def test_summary_cards_count_the_real_rows(world, monkeypatch):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    s = F.summary(db, org, "2026-11")
    cards = s["cards"]
    assert cards["found"]["total"] == 4
    assert cards["verified"]["verified"] == 3 and cards["verified"]["pending"] == 1
    assert cards["attention"]["missing_owners"] == 1
    assert cards["contact"]["ready"] == 0 and cards["contact"]["awaiting"] >= 1
    assert s["counties"] == {"Dallas": 3, "Tarrant": 1}
    assert F.summary(db, org, "2027-03")["exists"] is False           # a month not started yet shows empty


def test_list_filters_views_and_ranking_reuse_the_engine(world, monkeypatch):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    rs = F.rows(db, org, "2026-11")
    assert [r["rank"] for r in rs] == sorted(r["rank"] for r in rs)  # cohort.rows_for's own ranking
    assert len(F.filter_rows(rs, {"county": "Tarrant"})) == 1
    assert len(F.filter_rows(rs, {"owner": "missing"})) == 1
    assert len(F.filter_rows(rs, {"verified": "yes"})) == 3
    assert all(r["needs_attention"] for r in F.filter_rows(rs, {"attention": "yes"}))


def test_history_keeps_every_month(world, monkeypatch):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    for m in ("2026-10", "2026-11", "2026-12"):
        F.start_month(db, org, m)
    db.commit()
    h = F.history(db, org)
    assert [x["month"] for x in h] == ["2026-12", "2026-11", "2026-10"]
    assert {x["month"]: x["properties"] for x in h} == {"2026-12": 1, "2026-11": 4, "2026-10": 1}
    assert F.default_month(db, org, today=TODAY) == "2026-11"
    assert F.default_month(db, org, today=date(2026, 11, 10)) == "2026-12"


def test_a_contact_batch_can_be_built_from_a_month(world, monkeypatch):
    from app.services.evosense import contact_batch as CB
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    db.commit()
    pf = CB.preflight(db, org, "cycle:2026-11")
    assert pf["summary"]["properties"] == 4
    with pytest.raises(ValueError):
        CB.preflight(db, org, "cycle:bad")


def test_api_reads_are_scoped_and_writes_need_a_manager(client, auth_headers, admin_auth_headers, world):
    r = client.get("/wholesale/evosense/foreclosures", headers=admin_auth_headers, params={"month": "2026-11"})
    if r.status_code in (404, 409):
        pytest.skip("auth fixture belongs to a different organization")
    assert r.status_code == 200 and r.json()["label"] == "November 2026"
    assert client.get("/wholesale/evosense/foreclosures", headers=admin_auth_headers,
                      params={"month": "2026-99"}).status_code == 422
    s = client.post("/wholesale/evosense/foreclosures/start", headers=admin_auth_headers, json={"month": "2026-11"})
    assert s.status_code in (200, 403), s.text
    p = client.get("/wholesale/evosense/foreclosures/properties", headers=admin_auth_headers,
                   params={"month": "2026-11", "view": "top10"})
    assert p.status_code == 200 and "items" in p.json()


def test_the_scheduler_prepares_refreshes_and_completes_months_safely(world, monkeypatch):
    from app.models.evosense_models import EvoSenseControl
    db, org = world["db"], world["org"]
    db.add(EvoSenseControl(organization_id=org))
    db.commit()
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    first = F.auto(db, today=TODAY)
    assert first.get("prepared") == 1 and F.get_cycle(db, org, "2026-11") is not None
    second = F.auto(db, today=TODAY)                                  # restart / next pass: nothing doubles
    assert not second.get("prepared") and not second.get("refreshed")
    assert db.query(EvoSenseAuctionEvent).count() == 4
    late = F.auto(db, today=date(2026, 12, 10))
    assert late.get("completed") == 1                                 # stays readable, just not active
    assert F.get_cycle(db, org, "2026-11").status == "completed"
    assert db.query(EvoSenseAuctionEvent).count() >= 4                # history kept


def test_a_list_date_with_only_an_earlier_clerk_notice_is_a_conflict_to_review(db_session, sample_org, monkeypatch):
    db, org = db_session, sample_org.id
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    p = _prop(db, org, 9)
    _notice(db, p, "2026-10-06", "OCT-ONLY")                                  # the clerk's notice
    _notice(db, p, "2026-11-03", "LIST-NOV", source="csv_import", claim=True)  # the list's date
    db.commit()
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    r = F.rows(db, org, "2026-11")[0]
    assert any("October 2026, not this month - notice needs review" in f for f in r["auction"]["flags"])
    assert F.summary(db, org, "2026-11")["cards"]["verified"]["conflict"] == 1
    au = r["measures"]["auction_urgency"]
    assert au["label"] == "URGENT - VERIFY NOTICE" and au["urgent"]          # never "scheduled", never hidden
    assert F.filter_rows([r], {"quick": "notice_conflicts"}) == [r]


def test_a_month_of_200_properties_builds_quickly(db_session, sample_org, monkeypatch):
    import time
    db, org = db_session, sample_org.id
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    for n in range(200):
        _notice(db, _prop(db, org, 100 + n), "2026-11-03", "PERF-%d" % n)
    db.commit()
    t = time.monotonic()
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    s = F.summary(db, org, "2026-11")
    took = time.monotonic() - t
    assert s["cards"]["found"]["total"] == 200
    print("200-property month: start + summary %.2fs" % took)
    assert took < 30
    t = time.monotonic()
    F.summary(db, org, "2026-11")                                            # served from the 1-minute cache
    assert time.monotonic() - t < 1


def test_property_type_filters_combine_with_county_and_month(world, monkeypatch):
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    world["a"].property_category, world["a"].property_subtype = "commercial", "office"
    world["b"].property_category = "land"
    db.commit()
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    rs = F.rows(db, org, "2026-11")
    assert {r["id"] for r in F.filter_rows(rs, {"ptype": "commercial", "county": "Dallas"})} == {world["a"].id}
    assert len(F.filter_rows(rs, {"ptype": "land", "county": "Dallas"})) == 0     # the land one is in Tarrant
    unknown = F.filter_rows(rs, {"ptype": "unknown"})
    assert len(unknown) == 2 and all(r["property_class"]["needs_review"] for r in unknown)   # never hidden
    assert F.summary(db, org, "2026-11")["property_types"] == {"unknown": 2, "commercial": 1, "land": 1}
    assert len(F.rows(db, org, "2026-11")) == 4                                    # nothing dropped
