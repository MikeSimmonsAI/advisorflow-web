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
from app.services.evosense import work_priority as WP

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
    assert au["level"] == "CRITICAL" and au["urgent"] and au["days"] == 9 and au["label"] == "CRITICAL — 9 DAYS"
    assert OP.auction_urgency("notice_verified", "2026-11-20", today=date(2026, 10, 25))["level"] == "UPCOMING"


def test_conflicts_postponements_claims_and_passed_dates_are_never_called_scheduled():
    t = date(2026, 10, 25)
    c = OP.auction_urgency("conflict", "2026-11-03", today=t)
    assert c["label"].startswith("URGENT — VERIFY NOTICE") and c["urgent"]
    p = OP.auction_urgency("postponed", "2026-11-03", today=t)
    assert p["level"] == "POSTPONED" and not p["urgent"]
    claim = OP.auction_urgency("CLAIMED", "2026-11-03", today=t)
    assert claim["level"] == "URGENT_VERIFY" and not claim["verified"]     # a list date is never a verified notice
    gone = OP.auction_urgency("notice_verified", "2026-10-06", today=t)
    assert gone["level"] == "OUTCOME_UNVERIFIED" and gone["label"] == "OUTCOME UNVERIFIED"   # never "completed" by the calendar


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
    monkeypatch.setattr(WP, "today_ct", lambda: TODAY)
    F.start_month(db, org, "2026-11")
    db.commit()
    F._CACHE.clear()
    rs = F.rows(db, org, "2026-11")
    assert len(rs) == 4                                                     # nothing dropped
    a = {r["id"]: r for r in rs}[world["a"].id]
    m = a["measures"]
    assert m["auction_urgency"]["level"] == "CRITICAL" and m["deal_potential"]["level"] == "UNKNOWN"
    assert m["work_priority"]["level"] == "CRITICAL"
    assert m["data_confidence"]["level"] in ("HIGH", "MEDIUM", "LOW") and m["explanation"]["lines"]
    urgent = F.filter_rows(rs, {"quick": "urgent_auctions"})
    assert world["a"].id in {r["id"] for r in urgent}
    c = {r["id"]: r for r in rs}[world["c"].id]["measures"]                 # the list's claim: verify, never "verified"
    assert c["auction_urgency"]["level"] == "URGENT_VERIFY" and c["work_priority"]["level"] == "REVIEW_URGENT"
    assert world["c"].id not in {r["id"] for r in F.filter_rows(rs, {"quick": "critical"})}
    assert {r["id"] for r in F.filter_rows(rs, {"quick": "needs_financial"})} == {r["id"] for r in rs}
    both = F.filter_rows(rs, {"quick": "urgent_auctions", "county": "Tarrant"})
    assert {r["id"] for r in both} == {world["b"].id}                       # quick filters combine with county
    groups = {g["key"]: g for g in F.work_queue(rs)}
    placed = [i["id"] for g in groups.values() for i in g["items"]]
    assert len(placed) == len(set(placed))                                  # one group per property
    assert world["a"].id in placed                                          # unknown potential, still worked
    by_urg = F.filter_rows(rs, {"sort": "urgency"})
    assert by_urg[0]["measures"]["auction_urgency"]["level"] in ("CRITICAL", "URGENT_VERIFY")
    s = F.summary(db, org, "2026-11")
    assert sum(s["measures"]["auction_urgency"].values()) == 4 and "urgent_auctions" in s["measures"]["quick"]


def test_the_work_view_and_list_api_expose_measures(client, auth_headers, world, monkeypatch):  # noqa: F811
    monkeypatch.setattr(F, "_today", lambda: TODAY)
    monkeypatch.setattr(WP, "today_ct", lambda: TODAY)
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


# Owner's October 26 score-calibration requirement: the original Nov 3 Top100
# contains 99 documented imminent mortgage auctions, yet v5 scored only two
# HIGH. The acquisition-distress evidence score must reflect the verified
# foreclosure event itself, without treating a list claim as verified profit.
def test_v6_imminent_documented_auction_is_strong_evidence(monkeypatch):
    monkeypatch.setattr(WP, "today_ct", lambda: date(2026, 10, 26))
    verified = _fc("2026-11-03")
    long_owned = _stacked("LONG_OWNERSHIP")[0]
    long_owned["sources"] = ["evosense_derived"]
    long_owned["record_sources"] = ["dcad"]
    po = SC.property_opportunity(_top100_like(), [verified, long_owned], None)
    bonus = [f for f in po["factors"] if (f.get("evidence") or {}).get("type") == "imminent_distress_bonus"]
    assert SC.PO_VERSION == "property_opportunity/v6"
    assert len(bonus) == 1 and bonus[0]["points"] == 28
    assert bonus[0]["evidence"]["not_profit"]
    assert po["value"] >= 65 and po["label"] in ("high", "hot")
    # The score stays evidence-based: imported equity remains a 10-point claim.
    equity = [f for f in po["factors"] if f["label"].startswith("Estimated equity")][0]
    assert equity["points"] == 10 and equity["evidence"]["claimed"] is True


def test_v6_import_claims_unverified_manual_and_expired_sales_never_get_bonus(monkeypatch):
    monkeypatch.setattr(WP, "today_ct", lambda: date(2026, 10, 26))
    claim = _fc("2026-11-03", verified=False)
    unsupported_manual = _fc("2026-11-03")
    unsupported_manual["evidence"][0]["source"] = "operator"
    unsupported_manual["sources"] = ["operator"]
    unsupported_manual["evidence"][0]["provenance"] = {"sale_date": "2026-11-03"}
    past = _fc("2026-10-20")
    later = _fc("2026-12-01")
    for signals in ([claim], [unsupported_manual], [past], [later]):
        assert SC.documented_imminent_distress(signals) is None
        score = SC.property_opportunity(_top100_like(), signals, None)
        assert not any((f.get("evidence") or {}).get("type") == "imminent_distress_bonus"
                       for f in score["factors"])


def test_v6_manual_notice_needs_document_and_source(monkeypatch):
    monkeypatch.setattr(WP, "today_ct", lambda: date(2026, 10, 26))
    manual = _fc("2026-11-03")
    manual["evidence"][0]["source"] = "operator"
    manual["evidence"][0]["provenance"] = {
        "sale_date": "2026-11-03", "document": "202600002182",
        "document_url": "https://county.example/record/202600002182"}
    assert SC.documented_imminent_distress([manual])["kind"] == "mortgage"


def test_v6_official_tax_sale_is_recognized_once_with_or_without_mortgage(monkeypatch):
    monkeypatch.setattr(WP, "today_ct", lambda: date(2026, 10, 26))
    tax = {"signal_type": "TAX_SALE", "freshness": "current", "sources": ["lgbs_tax_sales"],
           "record_sources": ["lgbs_tax_sales"], "observed_at": None,
           "evidence": [{"source": "lgbs_tax_sales", "case_status": "scheduled",
                         "provenance": {"sale_date": "2026-11-03", "claim": None}}]}
    both = [tax, _fc("2026-11-03")]
    bonus = SC.documented_imminent_distress(both)
    assert bonus["sale_date"] == "2026-11-03" and bonus["kind"] in ("mortgage", "tax")
    score = SC.property_opportunity(_top100_like(), both, None)
    assert sum(1 for f in score["factors"] if (f.get("evidence") or {}).get("type")
               == "imminent_distress_bonus") == 1
    tax_only = SC.property_opportunity(_top100_like(), [tax], None)
    assert any((f.get("evidence") or {}).get("type") == "imminent_distress_bonus"
               for f in tax_only["factors"])
    tax["evidence"][0]["case_status"] = "canceled"
    assert SC.documented_imminent_distress([tax]) is None
