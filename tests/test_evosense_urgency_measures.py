"""Auction urgency, deal potential and data confidence are three separate
answers; the score explains itself from the engine's own factors; unknown is
never bad and never profit; urgent auctions stay visible and actionable.
Synthetic records only - nothing is bought, nobody is contacted."""
from datetime import date, timedelta

from app.models.evosense_models import EvoSenseProperty, EvoSenseScore
from app.services.evosense import evaluate as EV
from app.services.evosense import foreclosure_cycles as F
from app.services.evosense import opportunity as OP
from app.services.evosense import scoring as SC

from tests.test_evosense_discovery_types import _P, _stacked
from tests.test_evosense_foreclosure_cycles import TODAY, world  # noqa: F401 - fixture


def _fc(sale="2026-11-03", verified=True):
    ev = {"source": "tx_clerk_foreclosure" if verified else "csv_import",
          "provenance": {"sale_date": sale, "claim": not verified}}
    return {"signal_type": "PRE_FORECLOSURE", "freshness": "current", "sources": [ev["source"]],
            "record_sources": [ev["source"]], "evidence": [ev], "observed_at": None}


def _top100_like(**kw):
    base = dict(equity_pct=86, equity_basis="computed", ownership_years=18,
                estimated_value=300000, estimated_value_source="csv_import (imported list (value and unpaid "
                                                               "balance as the list states them))",
                mortgage_source="csv_import", property_category="residential")
    base.update(kw)
    return _P(**base)


def test_the_top100_formula_reproduces_and_list_equity_counts_as_a_claim():
    soon = (date.today() + timedelta(days=9)).isoformat()
    stacked = [_fc(soon)] + _stacked("LONG_OWNERSHIP")
    stacked[1]["sources"] = ["evosense_derived"]
    stacked[1]["record_sources"] = ["dcad"]
    po = SC.property_opportunity(_top100_like(), stacked, None)
    pts = {f["label"].split(" ")[0]: f["points"] for f in po["factors"] if f["points"]}
    equity = [f for f in po["factors"] if f["label"].startswith("Estimated equity")][0]
    assert equity["points"] == 10 and "imported list" in equity["label"] and equity["evidence"]["claimed"]
    assert pts["Pre-foreclosure"] == 16 and pts["Owned"] == 10
    sale = [f for f in po["factors"] if f["label"].startswith("Foreclosure sale scheduled")][0]
    assert sale["points"] == 12                                  # verified notice, 14 days or less
    assert po["value"] == sum(f["points"] for f in po["factors"])
    verified_equity = SC.property_opportunity(_top100_like(estimated_value_source="rentcast", mortgage_source="attom"),
                                              stacked, None)
    assert verified_equity["value"] == po["value"] + 4            # 14 vs 10: only the claim discount differs


def test_every_score_explains_itself_with_the_engines_points():
    stacked = [_fc()] + _stacked("ABSENTEE_OWNER", "RECENT_SALE")
    po = SC.property_opportunity(_top100_like(), stacked, None)
    ex = OP.explain(po)
    assert sum(line["points"] for line in ex["lines"]) == ex["factor_total"]
    groups = {line["group"] for line in ex["lines"]}
    assert {"Verified foreclosure", "Absentee owner", "Equity", "Adjustments"} <= groups
    if ex["factor_total"] != po["value"]:
        assert ex["note"]


def test_a_verified_imminent_auction_is_high_urgency_whatever_the_numbers():
    au = OP.auction_urgency("VERIFIED NOTICE", "2026-11-03", today=date(2026, 10, 25))
    assert au["level"] == "HIGH" and au["urgent"] and au["days"] == 9
    assert OP.auction_urgency("notice_verified", "2026-11-20", today=date(2026, 10, 25))["level"] == "MEDIUM"


def test_conflicts_postponements_claims_and_passed_dates_are_never_called_scheduled():
    t = date(2026, 10, 25)
    c = OP.auction_urgency("conflict", "2026-11-03", today=t)
    assert c["label"] == "URGENT - VERIFY NOTICE" and c["urgent"]
    p = OP.auction_urgency("postponed", "2026-11-03", today=t)
    assert p["level"] == "POSTPONED" and not p["urgent"] and "NOT SCHEDULED" in p["label"]
    claim = OP.auction_urgency("CLAIMED", "2026-11-03", today=t)
    assert claim["level"] == "UNVERIFIED" and not claim["urgent"]           # a list date is not a verified notice
    gone = OP.auction_urgency("notice_verified", "2026-10-06", today=t)
    assert gone["level"] == "PASSED" and "NOT VERIFIED" in gone["label"]    # never "completed" by the calendar


def test_missing_financials_never_invent_profit_and_unknown_is_not_low():
    econ = OP.economics(arv=None, repairs=None, settings=None)
    po = {"value": 59, "label": "medium", "factors": []}
    dp = OP.deal_potential(econ, po, "residential")
    assert dp["level"] == "UNKNOWN" and dp["label"] == "POTENTIAL UNKNOWN - RESEARCH REQUIRED"
    assert not dp["assessed"] and "unknown, not bad" in dp["reason"]
    assert econ["spreads"] == {} and econ["score"] is None                  # no number was made up
    assert dp["indicators"]["reading"] == "moderate"


def test_non_house_types_are_insufficiently_assessed_not_scored_like_houses():
    econ = OP.economics(arv=None, repairs=None, settings=None)
    for cat in ("commercial", "agricultural", "land", "multifamily"):
        dp = OP.deal_potential(econ, {"value": 70}, cat)
        assert dp["level"] == "INSUFFICIENT" and "No validated" in dp["reason"]


def test_missing_inputs_are_listed_separately():
    econ = OP.economics(arv=None, repairs=None, settings=None)
    miss = OP.missing_inputs(econ=econ, contacts={}, owner={"status": "UNVERIFIED"},
                             fc={"status": "VERIFIED NOTICE"}, category=None)
    assert miss == ["Missing verified comparable sales", "Missing repair estimate", "Missing payoff or arrears",
                    "Owner contact unavailable", "Ownership requires review", "Property type needs review"]


def test_fcc_rows_carry_the_three_measures_and_urgent_unknown_potential_stays_in_the_queue(world, monkeypatch):  # noqa: F811
    db, org = world["db"], world["org"]
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    rs = F.rows(db, org, "2026-11")
    assert len(rs) == 4                                                     # nothing dropped
    a = {r["id"]: r for r in rs}[world["a"].id]
    m = a["measures"]
    assert m["auction_urgency"]["level"] == "HIGH" and m["deal_potential"]["level"] == "UNKNOWN"
    assert m["data_confidence"]["level"] in ("HIGH", "MEDIUM", "LOW") and m["explanation"]["lines"]
    urgent = F.filter_rows(rs, {"quick": "urgent_auctions"})
    assert world["a"].id in {r["id"] for r in urgent}
    assert world["c"].id not in {r["id"] for r in urgent}                   # the list's claim is not urgent-verified
    assert {r["id"] for r in F.filter_rows(rs, {"quick": "needs_financial"})} == {r["id"] for r in rs}
    both = F.filter_rows(rs, {"quick": "urgent_auctions", "county": "Tarrant"})
    assert {r["id"] for r in both} == {world["b"].id}                       # quick filters combine with county
    groups = {g["key"]: g for g in F.work_queue(rs)}
    placed = [i["id"] for g in groups.values() for i in g["items"]]
    assert len(placed) == len(set(placed))                                  # one group per property
    assert world["a"].id in placed                                          # unknown potential, still worked
    by_urg = F.filter_rows(rs, {"sort": "urgency"})
    assert by_urg[0]["measures"]["auction_urgency"]["level"] in ("HIGH", "VERIFY")
    s = F.summary(db, org, "2026-11")
    assert sum(s["measures"]["auction_urgency"].values()) == 4 and "urgent_auctions" in s["measures"]["quick"]


def test_the_work_view_and_list_api_expose_measures(client, auth_headers, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    db, org = world["db"], world["org"]
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    r = client.get("/wholesale/evosense/foreclosures/work?month=2026-11", headers=auth_headers)
    if r.status_code == 200:
        body = r.json()
        assert [g["key"] for g in body["groups"]] == [k for k, _ in F.WORK]
        lst = client.get("/wholesale/evosense/foreclosures/properties?month=2026-11&quick=urgent_auctions&sort=urgency",
                         headers=auth_headers).json()
        assert all(i["measures"]["auction_urgency"]["urgent"] for i in lst["items"])
    else:
        assert r.status_code in (403, 404)                                  # another workspace's month: nothing leaks


def test_outdated_scores_are_refreshed_once_and_old_scores_stay_as_history(db_session, sample_org):
    p = EvoSenseProperty(organization_id=sample_org.id, street_address="9 Rescore Test St", city="Dallas",
                         state="TX", zip_code="75216", county="Dallas", is_test=False, status="new")
    db_session.add(p)
    db_session.flush()
    db_session.add(EvoSenseScore(organization_id=sample_org.id, property_id=p.id, subject_type="property",
                                 subject_id=p.id, score_type="property_opportunity",
                                 version="property_opportunity/v3", value=40, is_current=True))
    db_session.commit()
    assert EV.rescore_outdated(db_session, sample_org.id) >= 1
    assert EV.rescore_outdated(db_session, sample_org.id) == 0                # idempotent
    rows = db_session.query(EvoSenseScore).filter(EvoSenseScore.property_id == p.id,
                                                  EvoSenseScore.score_type == "property_opportunity").all()
    assert any(r.version == "property_opportunity/v3" for r in rows)        # history kept
    assert [r.version for r in rows if r.is_current] == [SC.version_for(None, None)]
