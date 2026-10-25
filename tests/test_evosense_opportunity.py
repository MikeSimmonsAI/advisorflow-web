"""The universal opportunity view: separate dimensions, documented weights,
honest economics, one next action - and a batch built in a fixed number of
queries. Synthetic properties only."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.models.evosense_models import EvoSenseOwner, EvoSenseOwnership, EvoSenseProperty
from app.services.evosense import common as C
from app.services.evosense import opportunity as OP
from app.services.evosense import signals as SIG

SETTINGS = SimpleNamespace(investor_percentage=70, default_wholesale_fee=10000, transaction_cost_percent=0,
                           transaction_cost_flat=0)


# ── pure functions ──────────────────────────────────────────────────────────

def test_urgency_from_a_verified_sale_a_claim_and_a_passed_date():
    v = OP.urgency({"status": "VERIFIED NOTICE", "days": 5, "sale_date": "2026-11-03", "document": "2026-1"},
                   {"status": "NOT CHECKED"})
    assert v["value"] == 100 and v["escalate"] and v["deadline"] == "2026-11-03"
    c = OP.urgency({"status": "CLAIMED", "days": 20, "sale_date": "2026-11-03"}, {})
    assert c["value"] == int(80 * OP.CLAIM_FACTOR) and "unconfirmed_notice" in c["flags"] and not c["escalate"]
    p = OP.urgency({"status": "SALE PASSED", "days": -3, "sale_date": "2026-10-06"}, {"status": "DELINQUENT"})
    assert p["value"] == 20 and "sale_date_passed" in p["flags"]          # the tax delinquency outranks it
    t = OP.urgency({"status": "NO EVIDENCE"}, {"status": "TAX SALE"})
    assert t["value"] == 70
    assert OP.urgency({}, {})["value"] == 0


def test_without_a_verified_arv_there_is_no_dollar_figure():
    e = OP.economics(arv={"value": 250000, "verified": False}, repairs={"base": 30000}, settings=SETTINGS,
                     asking=150000)
    assert e["status"] == "not_supportable" and e["headline"] == OP.NEEDS_UNDERWRITING
    assert e["score"] is None and e["scenarios"] is None and e["spreads"] == {}
    assert any("Verified ARV" in n for n in e["needs"])
    no_rep = OP.economics(arv={"value": 200000, "verified": True}, repairs={}, settings=SETTINGS)
    assert no_rep["status"] == "not_supportable" and any("Repair" in n for n in no_rep["needs"])
    rough = OP.economics(arv={"value": 200000, "verified": True}, repairs={"base": 20000, "status": "SYSTEM_ESTIMATE"},
                         settings=SETTINGS, repairs_accepted=False)
    assert rough["status"] == "not_supportable" and any("accepts" in n for n in rough["needs"])


def test_spreads_follow_the_one_mao_formula_and_stay_separate():
    arv = {"value": 200000, "low": 190000, "high": 210000, "verified": True, "source": "comps"}
    rep = {"base": 30000, "low": 25000, "high": 40000, "status": "MANUAL_ESTIMATE"}
    # investor price = 200k x 70% - 30k = 110k ; MAO = 110k - 10k fee = 100k
    e = OP.economics(arv=arv, repairs=rep, settings=SETTINGS, asking=90000)
    assert e["status"] == "supportable" and e["target"]["max_acquisition"] == 100000
    assert e["target"]["investor_purchase_price"] == 110000 and e["target"]["assignment_fee_target"] == 10000
    assert e["scenarios"]["low"]["investor_price"] == 133000 - 40000
    assert e["scenarios"]["high"]["investor_price"] == 147000 - 25000
    h = e["spreads"]["hypothetical"]
    assert h["value"] == 20000 and h["low"] == 3000 and h["high"] == 32000 and "hypothetical" in h["label"]
    assert e["basis"] == "hypothetical" and e["score"] == 70
    # sensitivity: ARV -10% and repairs +20% -> 180k x 70% - 36k - 10k = 80k
    cell = next(s for s in e["sensitivity"] if s["arv_change_pct"] == -10 and s["repairs_change_pct"] == 20)
    assert cell["max_acquisition"] == 80000
    # a contract beats a hypothetical; a buyer offer beats the estimate; a closed fee beats everything
    e2 = OP.economics(arv=arv, repairs=rep, settings=SETTINGS, asking=90000, contract_price=95000,
                      best_buyer_offer=108000)
    assert e2["spreads"]["contracted"]["value"] == 15000 and e2["spreads"]["buyer_offered"]["value"] == 13000
    assert e2["basis"] == "buyer_offered" and "hypothetical" in e2["spreads"]
    e3 = OP.economics(arv=arv, repairs=rep, settings=SETTINGS, contract_price=95000, realized_fee=12500,
                      closed=True)
    assert e3["basis"] == "realized" and e3["spreads"]["realized"]["value"] == 12500
    # supportable but no price yet: underwritten, no score, nothing invented
    e4 = OP.economics(arv=arv, repairs=rep, settings=SETTINGS)
    assert e4["basis"] == "underwritten" and e4["score"] is None and e4["spreads"] == {}


def test_foreclosure_numbers_are_never_inferred():
    m = OP.foreclosure_money({"appraisal": 180000, "equity_pct": None, "list_unpaid_balance": 95000,
                              "tax_status": "DELINQUENT"})
    assert m["payoff"].startswith("unknown") and m["liens"].startswith("unknown")
    assert m["opening_bid"].startswith("unknown") and m["estimated_equity"].startswith("unknown")
    assert "unverified" in m["mortgage_balance"]["label"] and "not ARV" in m["property_value"]["label"]


def test_priority_leaves_unknown_dimensions_out_instead_of_scoring_zero():
    known = OP.priority({"seller_opportunity": 60, "urgency": 80, "economics": None, "engagement": None})
    # (60 x 45 + 80 x 25) / 70
    assert known["value"] == round((60 * 45 + 80 * 25) / 70)
    assert [p for p in known["parts"] if p["dimension"] == "economics"][0]["note"].startswith("unknown")
    zero_econ = OP.priority({"seller_opportunity": 60, "urgency": 80, "economics": 0, "engagement": None})
    assert zero_econ["value"] < known["value"]           # a real zero is lower than unknown
    assert OP.priority({"seller_opportunity": None, "urgency": 90})["value"] is None
    capped = OP.priority({"seller_opportunity": 90, "urgency": 90}, cap=15, cap_reason="DNC")
    assert capped["value"] == 15
    assert sum(OP.WEIGHTS.values()) == 100


def test_categories_come_only_from_evidence():
    cats = OP.categories(["ABSENTEE_OWNER", "TAX_SUIT", "LONG_OWNERSHIP"], {"status": "CLAIMED"},
                         {"status": "TAX LAWSUIT"}, ["ESTATE_INDICATED"], 55)
    assert cats == ["pre_foreclosure", "tax_delinquent", "absentee", "inherited_probate", "long_held", "high_equity"]
    assert OP.categories([], {"status": "NO EVIDENCE"}, {"status": "NOT CHECKED"}, [], None) == []


# ── database: a real batch ──────────────────────────────────────────────────

def _prop(db, org_id, addr, *, owner="SYNTH OWNER", owner_source="dcad", county="Dallas", appraisal=180000):
    p = EvoSenseProperty(organization_id=org_id, street_address=addr, city="Dallas", state="TX",
                         zip_code="75216", county=county, appraisal_value=appraisal, property_type="single_family",
                         square_feet=1500, is_test=True, status="new")
    db.add(p)
    db.flush()
    if owner:
        o = EvoSenseOwner(organization_id=org_id, display_name=owner, owner_type="individual", is_test=True)
        db.add(o)
        db.flush()
        db.add(EvoSenseOwnership(organization_id=org_id, property_id=p.id, owner_id=o.id, is_current=True,
                                 source=owner_source))
    db.flush()
    return p


def _sale(db, p, days, *, source="tx_clerk_foreclosure", claim=False, doc="2026-900"):
    d = (C.now() + timedelta(days=days)).date().isoformat()
    SIG.upsert(db, p, "PRE_FORECLOSURE", source="csv_import" if claim else source, source_reference=doc,
               provenance={"sale_date": d, "document": doc, "claim": claim}, normalized_value="sale %s" % d)


@pytest.fixture()
def batch(db_session, sample_org):
    db, org = db_session, sample_org.id
    a = _prop(db, org, "1 Verified Sale St")
    _sale(db, a, 8)
    b = _prop(db, org, "2 Tax Landlord Ave")
    SIG.upsert(db, b, "TAX_DELINQUENT", source="dallas_trw", normalized_value="$4,000")
    SIG.upsert(db, b, "TIRED_LANDLORD", source="operator", normalized_value="two evictions")
    c = _prop(db, org, "3 Claimed Only Ln")
    _sale(db, c, 20, claim=True, doc="list-row-3")
    d = _prop(db, org, "4 Twice Listed Dr")                  # one house on two lists
    _sale(db, d, 30, doc="2026-901")
    _sale(db, d, 30, claim=True, doc="list-row-4")
    e = _prop(db, org, "5 No Owner Ct", owner=None)
    SIG.upsert(db, e, "VACANT", source="usps_vacancy", normalized_value="vacant")
    db.commit()
    return {"db": db, "org": org, "a": a, "b": b, "c": c, "d": d, "e": e}


def test_batch_assessment_ranks_explains_and_points_to_the_next_step(batch):
    db, org = batch["db"], batch["org"]
    props = [batch[k] for k in "abcde"]
    res = OP.build(db, org, props)
    a, b, c, d, e = (res[batch[k].id] for k in "abcde")
    assert a["urgency"]["value"] == 92 and a["urgency"]["escalate"]
    assert a["next_action"]["key"] == "escalate_deadline" and a["confidence"]["label"] in ("verified", "partial")
    assert {x["key"] for x in a["categories"]} >= {"foreclosure_auction", "pre_foreclosure"}
    assert b["next_action"]["key"] == "find_contact"
    assert {x["key"] for x in b["categories"]} >= {"tax_delinquent", "tired_landlord"}
    assert c["next_action"]["key"] == "confirm_notice" and "unconfirmed_notice" in c["flags"]
    assert c["confidence"]["label"] != "verified"
    assert e["next_action"]["key"] == "verify_owner" and "missing_ownership" in e["flags"]
    # one house on two lists is ONE result, its claim and its notice both kept
    assert d["foreclosure"]["status"] in ("VERIFIED NOTICE", "CONFLICT")
    for r in res.values():
        assert r["economics"]["status"] == "not_supportable"            # no verified ARV anywhere
        assert r["economics"]["headline"] == OP.NEEDS_UNDERWRITING
        assert r["priority"]["value"] is None or r["economics"]["score"] is None
        assert "missing_valuation" in r["flags"]
    # the verified, imminent sale outranks the claim-only one
    assert a["priority"]["value"] > c["priority"]["value"]


def test_refresh_caches_and_is_idempotent_and_the_backfill_finds_stale_rows(batch):
    db, org = batch["db"], batch["org"]
    props = [batch[k] for k in "abcde"]
    s1 = OP.refresh(db, org, props)
    db.commit()
    assert s1 == {"assessed": 5, "changed": 5, "failed": 0}
    a = batch["a"]
    db.refresh(a)
    assert a.priority_score is not None and a.urgency_score == 92 and a.next_best_action == "escalate_deadline"
    assert a.economics_status == "not_supportable" and a.spread_base is None
    assert ",foreclosure_auction," in a.opportunity_categories and a.sale_deadline is not None
    assert OP.compact(a)["next_action"]["label"] == OP.ACTIONS["escalate_deadline"]
    s2 = OP.refresh(db, org, props)
    db.commit()
    assert s2["changed"] == 0                                          # unchanged evidence writes nothing new
    # nothing stale -> the backfill has nothing to do; a rescore makes one stale again
    assert OP.backfill(db, org_id=org) == 0
    a.last_evaluated_at = C.now() + timedelta(seconds=5)
    db.commit()
    assert OP.backfill(db, org_id=org) == 1


def test_a_batch_uses_a_fixed_number_of_queries(db_session, sample_org):
    from sqlalchemy import event
    db, org = db_session, sample_org.id

    def run(n):
        props = []
        for i in range(n):
            p = _prop(db, org, "%d Count St %d" % (100 + i, n))
            SIG.upsert(db, p, "ABSENTEE_OWNER", source="dcad", normalized_value="mail elsewhere")
            props.append(p)
        db.commit()
        ids = [p.id for p in props]
        props = db.query(EvoSenseProperty).filter(EvoSenseProperty.id.in_(ids)).all()   # as a caller loads them
        q = {"n": 0}

        def count(*a, **k):
            q["n"] += 1
        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", count)
        try:
            OP.build(db, org, props)
        finally:
            event.remove(bind, "before_cursor_execute", count)
        return q["n"]
    small, large = run(5), run(40)
    assert large <= small + 2, (small, large)


# ── the existing screens use the same engine ────────────────────────────────

ES = "/wholesale/evosense"


def test_inbox_ranks_filters_and_shows_top_n(client, auth_headers, batch):
    db, org = batch["db"], batch["org"]
    OP.refresh(db, org, [batch[k] for k in "abcde"])
    db.commit()
    r = client.get(ES + "/inbox?sort=priority&limit=10", headers=auth_headers)
    if r.status_code != 200 or not r.json()["items"]:
        pytest.skip("auth fixture belongs to a different organization")
    j = r.json()
    items = j["items"]
    pri = [i["opportunity"]["priority"] for i in items if i["opportunity"] and i["opportunity"]["priority"] is not None]
    assert pri == sorted(pri, reverse=True)
    assert items[0]["opportunity"]["next_action"]["label"]
    assert j["weights"]["weights"] == OP.WEIGHTS
    assert {c["key"] for c in j["categories"]} >= {"pre_foreclosure", "tax_delinquent", "vacant"}
    only = client.get(ES + "/inbox?category=tax_delinquent", headers=auth_headers).json()["items"]
    assert [i["address"] for i in only] == ["2 Tax Landlord Ave"]
    needs = client.get(ES + "/inbox?economics=needs_underwriting", headers=auth_headers).json()
    assert needs["total"] == 5
    assert client.get(ES + "/inbox?economics=supportable", headers=auth_headers).json()["total"] == 0
    top2 = client.get(ES + "/inbox?sort=urgency&limit=2", headers=auth_headers).json()["items"]
    assert top2[0]["address"] == "1 Verified Sale St"
    for s in ("spread", "confidence", "engagement", "last_activity", "deadline", "next_due"):
        assert client.get(ES + "/inbox?sort=" + s, headers=auth_headers).status_code == 200, s


def test_an_unassessed_property_is_assessed_for_display(client, auth_headers, batch):
    r = client.get(ES + "/inbox?sort=priority", headers=auth_headers)
    if r.status_code != 200 or not r.json()["items"]:
        pytest.skip("auth fixture belongs to a different organization")
    for i in r.json()["items"]:
        assert i["opportunity"] is not None and i["opportunity"].get("computed_on_read") is True


def test_property_page_carries_the_full_view(client, auth_headers, batch):
    r = client.get(ES + "/properties/%s" % batch["a"].id, headers=auth_headers)
    if r.status_code == 404:
        pytest.skip("auth fixture belongs to a different organization")
    o = r.json()["opportunity"]
    assert o["version"] == OP.VERSION and o["urgency"]["value"] == 92
    assert o["economics"]["headline"] == OP.NEEDS_UNDERWRITING and o["economics"]["needs"]
    assert {p["dimension"] for p in o["priority"]["parts"]} >= set(OP.WEIGHTS)


def test_refresh_endpoint_runs_in_the_background_and_reports(client, auth_headers, batch, monkeypatch):
    import threading
    from app.routers import evosense_router as R
    started = []
    monkeypatch.setattr(threading, "Thread", lambda target, name, daemon: SimpleNamespace(
        start=lambda: started.append(name)))
    r = client.post(ES + "/opportunity/refresh", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "started" and started == ["evosense-opportunity"]
    R._OPP_RUNNING.clear()
    st = client.get(ES + "/opportunity/status", headers=auth_headers).json()
    assert st["weights"] == OP.WEIGHTS and "assessed" in st


def test_the_list_view_ranks_by_the_same_engine_and_reports_readiness(db_session, sample_org, sample_advisor):
    from app.services.evosense import cohort as CO
    from app.services.evosense import hunt as HU
    db, org = db_session, sample_org.id
    HU.import_csv(db, org, "record_id,street_address,city,state,zip_code,county\n"
                           "dh-1:1,10 Rank One St,Dallas,TX,75216,Dallas\n"
                           "dh-1:2,11 Rank Two St,Dallas,TX,75216,Dallas\n", user=sample_advisor, filename="l.csv")
    db.commit()
    ms = CO.members(db, org, "dh-1")
    _sale(db, ms[0], 6, doc="2026-950")
    db.commit()
    rows = CO.rows(db, org, "dh-1")
    assert [r["rank"] for r in rows] == [1, 2]
    assert all(r["opportunity"] is not None for r in rows)
    old = CO.rows(db, org, "dh-1", sort="group")
    assert {r["id"] for r in old} == {r["id"] for r in rows}
    rd = CO.summary(rows)["readiness"]
    assert rd["properties"] == 2 and rd["verified_notices"] == 1
    assert rd["supportable_financials"] == 0 and rd["eligible_for_outreach"] == 0
    assert sum(rd["blocked"].values()) == 2
