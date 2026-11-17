# -*- coding: utf-8 -*-
"""Portfolio Acquisitions (My Deal Room): seller portfolio, review-before-commit
import, per-property evaluation on the EXISTING underwriting, purchase packages
and their comparison, exports, and access.

Synthetic only: an EvoSys (My Deal Room) workspace and a BookaBoost workspace
from the shared Wholesale fixtures, invented addresses on invented streets.
Nothing is sent - the email provider is replaced and counted (must stay 0)."""
import io
from decimal import Decimal

import pytest

from app.models.wholesale_models import WholesaleComp, WholesaleDeal, WholesaleEvent, WholesaleProperty
from app.models.wholesale_portfolio_models import (WholesalePortfolio, WholesalePortfolioItem,
                                                   WholesalePortfolioPackageItem)
from app.services import wholesale_analysis as WA
from app.services import wholesale_service as svc
from tests.test_wholesale_access_builder import _invite_new, _login_new, _ok, _profile, sent, ws  # noqa: F401
from tests.test_bookaboost_invites import w  # noqa: F401
from tests.test_deal_room import room  # noqa: F401

CSV = (
    "No.,Address,City,State,Zip,Price,Rent,Type,Notes,Tenant Name,Tenant Phone\n"
    "1,101 Synthetic Oak St,Testville,TX,75001,85000,950,SFR,roof older,Pat Example,555-0100\n"
    "2,202 Synthetic Elm Ave,Testville,TX,75001,120000,1250,Duplex,,,\n"
    "3,Synthetic Ridge Rd,Testville,TX,75001,25000,,LAND,2 acres,,\n"
    "4,101 Synthetic Oak Street,Testville,TX,75001,85000,950,SFR,listed twice?,,\n"
    "5,,,TX,,40000,500,SFR,,,\n"
    "6,606 Synthetic Pine Dr,Testville,TX,75001,abc,800,SFR,,,\n"
)


@pytest.fixture()
def pf(ws):
    """The EvoSys workspace with its Wholesale settings and a sandbox portfolio."""
    db = ws["db"]
    settings = svc.resolve_settings(db, ws["evo"].id)
    db.commit()
    c, h = ws["c"], ws["owner_h"]
    p = _ok(c.post("/wholesale/portfolios", headers=h, json={
        "name": "Synthetic seller portfolio", "seller_name": "Example Holdings LLC (synthetic)",
        "source": "printed list (synthetic)", "is_test": True}))["portfolio"]
    return dict(ws, p=p, settings=settings)


def _stage(pf, body=CSV, name="seller.csv"):
    return _ok(pf["c"].post("/wholesale/portfolios/%s/imports" % pf["p"]["id"], headers=pf["owner_h"],
                            files={"file": (name, body.encode() if isinstance(body, str) else body, "text/csv")},
                            data={"reference": "synthetic sheet 1 of 2"}))


def _row(src, n):
    return next(r for r in src["rows"] if r["n"] == n)


def _fix_and_commit(pf, src):
    c, h, pid = pf["c"], pf["owner_h"], pf["p"]["id"]
    base = "/wholesale/portfolios/%s/imports/%s/rows/" % (pid, src["id"])
    _ok(c.patch(base + "4", headers=h, json={"decision": "skip"}))
    _ok(c.patch(base + "5", headers=h, json={"address": "505 Synthetic Cedar Ln", "city": "Testville",
                                             "zip_code": "75001"}))
    src = _ok(c.patch(base + "6", headers=h, json={"seller_price": "60000"}))
    assert src["ready"], src["counts"]
    return _ok(c.post("/wholesale/portfolios/%s/imports/%s/commit" % (pid, src["id"]), headers=h))


def _detail(pf):
    return _ok(pf["c"].get("/wholesale/portfolios/%s" % pf["p"]["id"], headers=pf["owner_h"]))


def _imported(pf):
    _fix_and_commit(pf, _stage(pf))
    return _detail(pf)


# ── portfolios ──────────────────────────────────────────────────────────────

def test_portfolio_numbering_edit_archive_and_delete_safeguard(pf):
    c, h = pf["c"], pf["owner_h"]
    assert pf["p"]["label"] == "Seller Portfolio #001" and pf["p"]["is_test"]
    second = _ok(c.post("/wholesale/portfolios", headers=h, json={"name": "Second"}))["portfolio"]
    assert second["number"] == 2
    up = _ok(c.patch("/wholesale/portfolios/%s" % second["id"], headers=h,
                     json={"status": "negotiating", "target_offer_total": 250000}))["portfolio"]
    assert up["status_label"] == "Negotiating" and up["target_offer_total"] == 250000
    assert c.patch("/wholesale/portfolios/%s" % second["id"], headers=h, json={"status": "bogus"}).status_code == 422
    # an empty portfolio can be deleted; one with properties cannot (archive instead)
    _ok(c.delete("/wholesale/portfolios/%s" % second["id"], headers=h))
    _imported(pf)
    r = c.delete("/wholesale/portfolios/%s" % pf["p"]["id"], headers=h)
    assert r.status_code == 409 and "Archive" in r.json()["detail"]
    _ok(c.post("/wholesale/portfolios/%s/archive" % pf["p"]["id"], headers=h, json={"archived": True}))
    assert _ok(c.get("/wholesale/portfolios", headers=h))["portfolios"] == []
    listed = _ok(c.get("/wholesale/portfolios?include_archived=true", headers=h))["portfolios"]
    assert listed[0]["archived"] and listed[0]["summary"]["properties"] == 5


# ── import: preview before commit ───────────────────────────────────────────

def test_import_preview_flags_duplicates_land_missing_and_bad_numbers_and_drops_personal_columns(pf):
    src = _stage(pf)
    assert src["status"] == "preview" and src["row_count"] == 6
    assert set(src["dropped_columns"]) == {"Tenant Name", "Tenant Phone"}
    assert "Pat Example" not in str(src["rows"]) and "555-0100" not in str(src["rows"])
    assert _row(src, 1)["decision"] == "create" and _row(src, 1)["source_notes"] == "roof older"
    land = _row(src, 3)
    assert land["is_land"] and any(i["code"] == "ambiguous_address" for i in land["issues"]) is False
    assert any(i["code"] == "repeat_in_list" for i in _row(src, 4)["issues"]) and _row(src, 4)["decision"] == "decide"
    assert _row(src, 5)["decision"] == "fix" and any(i["code"] == "missing_address" for i in _row(src, 5)["issues"])
    assert _row(src, 6)["decision"] == "fix" and any(i["code"] == "bad_number" for i in _row(src, 6)["issues"])
    assert not src["ready"]
    # stated totals are labelled as the seller's, never as values
    assert src["stated_totals"]["label"].startswith("Seller stated")
    # nothing exists yet
    assert pf["db"].query(WholesalePortfolioItem).count() == 0
    r = pf["c"].post("/wholesale/portfolios/%s/imports/%s/commit" % (pf["p"]["id"], src["id"]), headers=pf["owner_h"])
    assert r.status_code == 409 and "need a decision" in r.json()["detail"]


def test_commit_creates_properties_links_items_keeps_provenance_and_sends_nothing(pf):
    db = pf["db"]
    out = _fix_and_commit(pf, _stage(pf))
    assert out["result"] == {"properties_created": 5, "properties_linked": 0, "items_added": 5, "skipped": 1}
    d = _detail(pf)
    rows = {r["source_row"]: r for r in d["rows"]}
    assert set(rows) == {1, 2, 3, 5, 6}
    one = rows[1]
    assert one["seller_price"] == 85000 and one["seller_rent"] == 950 and one["source_ref"] == "synthetic sheet 1 of 2"
    assert one["arv"] is None and one["status"] == "research_needed" and one["status_label"] == "Research needed"
    land = rows[3]
    assert land["is_land"] and land["property_type"] == "land" and land["seller_rent"] is None
    assert land["bedrooms"] is None
    # every property is a normal Wholesale property with its deal, flagged sandbox like the portfolio
    for r in d["rows"]:
        prop = db.query(WholesaleProperty).get(r["property_id"])
        assert prop.is_test and prop.acquisition_source == "portfolio:#001"
        assert db.query(WholesaleDeal).filter(WholesaleDeal.property_id == prop.id).count() == 1
    assert pf["sent"] == []
    s = d["summary"]
    assert s["properties"] == 5 and s["awaiting_valuation"] == 5 and s["evaluated"] == 0
    assert s["seller_asking"]["total"] == 85000 + 120000 + 25000 + 40000 + 60000
    # land is neither counted nor "missing" for rent: 4 rentable properties, all with a stated rent
    assert s["seller_rent_monthly"] == {"total": 950.0 + 1250 + 500 + 800, "counted": 4, "missing": 0,
                                        "partial": False, "of": 4}
    assert s["estimated_value"]["total"] is None and s["estimated_spread"]["total"] is None


def test_existing_property_is_offered_as_a_possible_duplicate_and_can_be_linked(pf):
    db, c, h = pf["db"], pf["c"], pf["owner_h"]
    existing = svc.create_property(db, pf["evo"].id, None, {"street_address": "202 Synthetic Elm Avenue",
                                                            "city": "Testville", "zip_code": "75001", "is_test": True})
    db.commit()
    src = _stage(pf)
    r2 = _row(src, 2)
    assert r2["decision"] == "decide" and r2["matches"][0]["property_id"] == existing.id
    base = "/wholesale/portfolios/%s/imports/%s/rows/" % (pf["p"]["id"], src["id"])
    _ok(c.patch(base + "2", headers=h, json={"decision": "link"}))
    out = _fix_and_commit(pf, src)
    assert out["result"]["properties_linked"] == 1 and out["result"]["properties_created"] == 4
    linked = next(r for r in _detail(pf)["rows"] if r["source_row"] == 2)
    assert linked["property_id"] == existing.id
    assert db.query(WholesaleProperty).filter(WholesaleProperty.street_address.ilike("%Elm%")).count() == 1


def test_undo_removes_untouched_properties_and_keeps_worked_ones(pf):
    db, c, h, pid = pf["db"], pf["c"], pf["owner_h"], pf["p"]["id"]
    src = _stage(pf)
    _fix_and_commit(pf, src)
    worked = next(r for r in _detail(pf)["rows"] if r["source_row"] == 1)
    db.add(WholesaleComp(organization_id=pf["evo"].id, deal_id=worked["deal_id"], street_address="9 Comp St",
                         sale_price=Decimal("150000"), source="manual"))
    db.commit()
    out = _ok(c.post("/wholesale/portfolios/%s/imports/%s/undo" % (pid, src["id"]), headers=h))["result"]
    assert out["properties_deleted"] == 4 and len(out["properties_kept"]) == 1
    assert _detail(pf)["rows"] == []
    assert db.query(WholesaleProperty).get(worked["property_id"]) is not None
    assert c.post("/wholesale/portfolios/%s/imports/%s/undo" % (pid, src["id"]), headers=h).status_code == 409


def test_xlsx_and_photo_transcribed_rows(pf):
    from openpyxl import Workbook
    c, h, pid = pf["c"], pf["owner_h"], pf["p"]["id"]
    wb = Workbook()
    ws_ = wb.active
    ws_.append(["Property Address", "City", "Zip", "Asking Price", "Monthly Rent"])
    ws_.append(["707 Synthetic Birch Ct", "Testville", "75001", 99000, 1100])
    buf = io.BytesIO()
    wb.save(buf)
    src = _ok(c.post("/wholesale/portfolios/%s/imports" % pid, headers=h,
                     files={"file": ("list.xlsx", buf.getvalue(),
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}))
    assert src["kind"] == "xlsx" and _row(src, 1)["seller_price"] == "99000" and src["ready"]
    # photo rows: the image must be named, and they start low-confidence, needing review
    bad = c.post("/wholesale/portfolios/%s/imports/rows" % pid, headers=h, json={"kind": "photo", "rows": [{}]})
    assert bad.status_code == 422
    ph = _ok(c.post("/wholesale/portfolios/%s/imports/rows" % pid, headers=h, json={
        "kind": "photo", "reference": "seller sheet photo 2 of 2 (synthetic)",
        "rows": [{"source_row": "31", "address": "808 Synthetic Maple Way", "city": "Testville",
                  "zip_code": "75001", "seller_price": "70000", "seller_rent": "875"}]}))
    assert _row(ph, 1)["extraction_confidence"] == "low"
    _ok(c.post("/wholesale/portfolios/%s/imports/%s/commit" % (pid, ph["id"]), headers=h))
    row = _detail(pf)["rows"][0]
    assert row["source_row"] == 31 and row["review_status"] == "needs_review" and row["extraction_confidence"] == "low"


# ── evaluation: the existing underwriting ──────────────────────────────────

def _evaluate(pf, item, **body):
    return pf["c"].post("/wholesale/portfolios/%s/items/%s/evaluation" % (pf["p"]["id"], item), headers=pf["owner_h"],
                        json=body)


def test_override_needs_a_source_goes_through_the_deal_and_matches_the_mao_formula(pf):
    db = pf["db"]
    row = next(r for r in _imported(pf)["rows"] if r["source_row"] == 1)
    assert _evaluate(pf, row["item_id"], arv=160000).status_code == 422          # no source/reason
    ev = _ok(_evaluate(pf, row["item_id"], arv=160000, repairs=25000, note="three closed sales on Synthetic St"))
    r = ev["row"]
    deal = db.query(WholesaleDeal).get(row["deal_id"])
    db.refresh(deal)
    assert deal.arv == Decimal("160000") and deal.arv_source == "manual"
    assert r["repairs"] == 25000 and r["repair_status"] == "MANUAL_ESTIMATE"
    s = pf["settings"]
    costs = Decimal(160000) * Decimal(str(s.transaction_cost_percent)) / 100 + Decimal(str(s.transaction_cost_flat))
    assert r["transaction_costs"] == float(costs)
    gated = svc.gated_summary(deal, s)
    assert r["mao"] == gated["max_allowable_offer"]               # the deal room's own MAO, gate applied
    if r["mao"] is not None:
        calc = WA.calculate_offer(160000, 25000, s.investor_percentage, s.default_wholesale_fee,
                                  s.transaction_cost_percent, s.transaction_cost_flat)
        assert r["mao"] == float(calc["mao"])
    assert r["status"] == "offer_needed" and r["estimated_profit"] is None
    ev = _ok(_evaluate(pf, row["item_id"], target_offer=80000))
    r = ev["row"]
    assert r["status"] == "evaluated"
    assert r["estimated_profit"] == float(Decimal(160000) - 80000 - 25000 - costs)
    assert r["discount_from_asking"] == 5000
    assert r["gross_rent_yield_pct"] == round(950 * 12 / 80000 * 100, 2)
    # history kept: the override event carries before/after and the note
    e = (db.query(WholesaleEvent).filter(WholesaleEvent.action == "portfolio.evaluation_override")
         .order_by(WholesaleEvent.created_at.asc()).first())
    assert '"arv": null' in e.before_state and "three closed sales" in e.details
    assert ev["history"] and ev["repair_history"][-1]["notes"] == "three closed sales on Synthetic St"
    # rental scenario uses the seller's STATED rent, and says so
    assert "STATED rent" in ev["rent_note"] and ev["analysis"]["assumptions"]["rent_monthly"] == 950.0
    assert ev["notice"].startswith("Internal investment estimate")


def test_summary_is_partial_until_everything_is_valued(pf):
    rows = _imported(pf)["rows"]
    _ok(_evaluate(pf, rows[0]["item_id"], arv=160000, repairs=25000, target_offer=80000, note="synthetic comps"))
    s = _detail(pf)["summary"]
    assert s["estimated_value"] == {"total": 160000.0, "counted": 1, "missing": 4, "partial": True, "of": 5}
    assert s["evaluated"] == 1 and s["awaiting_valuation"] == 4 and s["progress_pct"] == 20
    assert s["estimated_spread"]["partial"] and s["estimated_spread"]["counted"] == 1


def test_seller_price_change_is_kept_in_the_price_history(pf):
    c, h = pf["c"], pf["owner_h"]
    row = _imported(pf)["rows"][0]
    _ok(c.patch("/wholesale/portfolios/%s/items/%s" % (pf["p"]["id"], row["item_id"]), headers=h,
                json={"seller_price": 79000, "price_note": "seller came down on the phone"}))
    ev = _ok(c.get("/wholesale/portfolios/%s/items/%s/evaluation" % (pf["p"]["id"], row["item_id"]), headers=h))
    hist = ev["seller"]["price_history"]
    assert [x["price"] for x in hist] == [85000.0, 79000.0] and hist[-1]["note"] == "seller came down on the phone"


# ── packages ────────────────────────────────────────────────────────────────

def _valued(pf):
    rows = _imported(pf)["rows"]
    figures = {1: (160000, 25000, 80000), 2: (210000, 30000, 110000), 3: (40000, 0, 18000),
               5: (90000, 20000, 35000), 6: (100000, 15000, 50000)}
    for r in rows:
        a, rep, t = figures[r["source_row"]]
        _ok(_evaluate(pf, r["item_id"], arv=a, repairs=rep, target_offer=t, note="synthetic"))
    return {r["source_row"]: r for r in _detail(pf)["rows"]}


def _pkgs(pf):
    return _ok(pf["c"].get("/wholesale/portfolios/%s/packages" % pf["p"]["id"], headers=pf["owner_h"]))


def _pkg(pf, name, **body):
    out = _ok(pf["c"].post("/wholesale/portfolios/%s/packages" % pf["p"]["id"], headers=pf["owner_h"],
                           json=dict({"name": name}, **body)))
    return next(k for k in out["packages"] if k["id"] == out["created_id"])


def _patch_pkg(pf, k, **body):
    out = _ok(pf["c"].patch("/wholesale/portfolios/%s/packages/%s" % (pf["p"]["id"], k["id"]),
                            headers=pf["owner_h"], json=body))
    return next(x for x in out["packages"] if x["id"] == k["id"])


def test_package_totals_discounts_and_profit(pf):
    rows = _valued(pf)
    ids = [rows[n]["item_id"] for n in (1, 2)]
    a = _pkg(pf, "Two houses", item_ids=ids)
    t = a["totals"]
    asking, arv, rep = 85000 + 120000, 160000 + 210000, 25000 + 30000
    costs = sum(rows[n]["transaction_costs"] for n in (1, 2))
    assert t["properties"] == 2 and t["seller_asking"] == asking and t["estimated_value"] == arv
    assert t["individual_offers"] == 190000 and t["package_offer"] == 190000      # no discount = sum of offers
    assert t["profit_at_individual_offers"] == round(arv - 190000 - rep - costs, 2)
    a = _patch_pkg(pf, a, discount_mode="percent", discount_value=10)
    assert a["totals"]["package_offer"] == asking * 0.9
    assert a["totals"]["discount_from_asking"] == asking * 0.1
    assert a["totals"]["profit_at_package_offer"] == round(arv - asking * 0.9 - rep - costs, 2)
    assert a["totals"]["package_vs_individual"] == round(190000 - asking * 0.9, 2)
    a = _patch_pkg(pf, a, discount_mode="amount", discount_value=15000)
    assert a["totals"]["package_offer"] == asking - 15000
    a = _patch_pkg(pf, a, discount_mode="total", discount_value=175000)
    assert a["totals"]["package_offer"] == 175000 and a["totals"]["package_offer_basis"] == "negotiated package total"
    bad = pf["c"].patch("/wholesale/portfolios/%s/packages/%s" % (pf["p"]["id"], a["id"]), headers=pf["owner_h"],
                        json={"discount_mode": "percent", "discount_value": 150})
    assert bad.status_code == 422


def test_any_selection_or_the_whole_portfolio_and_profit_only_when_all_valued(pf):
    rows = {r["source_row"]: r for r in _imported(pf)["rows"]}
    _ok(_evaluate(pf, rows[1]["item_id"], arv=160000, repairs=25000, target_offer=80000, note="synthetic"))
    whole = _pkg(pf, "Entire portfolio", all=True)
    assert whole["totals"]["properties"] == 5 and whole["totals"]["land"] == 1
    assert whole["totals"]["profit_at_package_offer"] is None and not whole["totals"]["complete"]
    offers = next(r for r in _pkgs(pf)["comparison"]["rows"] if r["key"] == "individual_offers")
    assert offers["values"][0] == 80000 and offers["partial"][0] is True       # 4 of 5 offers missing: marked
    assert "still need" in whole["totals"]["why_no_profit"]
    three = _pkg(pf, "Pick three", item_ids=[rows[n]["item_id"] for n in (1, 3, 6)])
    assert three["totals"]["properties"] == 3


def test_scenarios_stay_independent_duplicates_are_copies_and_stale_figures_are_flagged(pf):
    rows = _valued(pf)
    ids = [rows[n]["item_id"] for n in (1, 2, 3)]
    a = _pkg(pf, "Package A", item_ids=ids)
    b = _pkg(pf, "Package B", item_ids=ids)
    i1 = rows[1]["item_id"]
    a = _patch_pkg(pf, a, offers=[{"item_id": i1, "offer_amount": 70000}])
    b = next(k for k in _pkgs(pf)["packages"] if k["id"] == b["id"])
    assert next(x for x in a["lines"] if x["item_id"] == i1)["offer"] == 70000
    assert next(x for x in b["lines"] if x["item_id"] == i1)["offer"] == 80000     # untouched
    copy_out = _ok(pf["c"].post("/wholesale/portfolios/%s/packages/%s/duplicate" % (pf["p"]["id"], a["id"]),
                                headers=pf["owner_h"], json={"name": "Package A2"}))
    a2 = next(k for k in copy_out["packages"] if k["id"] == copy_out["created_id"])
    assert next(x for x in a2["lines"] if x["item_id"] == i1)["offer"] == 70000
    a2 = _patch_pkg(pf, a2, offers=[{"item_id": i1, "offer_amount": 65000}])
    a = next(k for k in _pkgs(pf)["packages"] if k["id"] == a["id"])
    assert next(x for x in a["lines"] if x["item_id"] == i1)["offer"] == 70000
    # the property's ARV changes: saved scenarios are flagged, not rewritten
    _ok(_evaluate(pf, i1, arv=150000, note="new comp came in"))
    a = next(k for k in _pkgs(pf)["packages"] if k["id"] == a["id"])
    line = next(x for x in a["lines"] if x["item_id"] == i1)
    assert line["stale"] == ["arv", "transaction_costs"] and line["offer"] == 70000
    assert line["stale_detail"]["arv"] == {"was": 160000.0, "now": 150000.0} and a["totals"]["stale_lines"] == 1
    a = _patch_pkg(pf, a, refresh_snapshots=True)
    assert next(x for x in a["lines"] if x["item_id"] == i1)["stale"] == []
    # changing the property's target offer does not move a saved scenario's offer
    _ok(_evaluate(pf, i1, target_offer=60000))
    b = next(k for k in _pkgs(pf)["packages"] if k["id"] == b["id"])
    l1 = next(x for x in b["lines"] if x["item_id"] == i1)
    assert l1["offer"] == 80000 and l1["target_changed"]


def test_remove_from_package_archive_and_portfolio_removal_guard(pf):
    rows = _valued(pf)
    i1, i2 = rows[1]["item_id"], rows[2]["item_id"]
    a = _pkg(pf, "A", item_ids=[i1, i2])
    a = _patch_pkg(pf, a, remove_item_ids=[i2])
    assert [x["item_id"] for x in a["lines"]] == [i1] and a["totals"]["properties"] == 1
    r = pf["c"].post("/wholesale/portfolios/%s/items/remove" % pf["p"]["id"], headers=pf["owner_h"],
                     json={"item_ids": [i1]})
    assert r.status_code == 409 and "'A'" not in r.json()["detail"] and "A" in r.json()["detail"]
    out = _ok(pf["c"].post("/wholesale/portfolios/%s/items/remove" % pf["p"]["id"], headers=pf["owner_h"],
                           json={"item_ids": [i1], "also_from_packages": True}))
    assert out["removed"] == 1 and out["packages_updated"] == 1
    assert pf["db"].query(WholesaleProperty).get(rows[1]["property_id"]) is not None     # record kept
    a = _patch_pkg(pf, a, status="archived")
    assert a["status"] == "archived"
    assert all(k["id"] != a["id"] for k in _pkgs(pf)["comparison"]["packages"])
    blocked = pf["c"].patch("/wholesale/portfolios/%s/packages/%s" % (pf["p"]["id"], a["id"]), headers=pf["owner_h"],
                            json={"name": "renamed"})
    assert blocked.status_code == 409


def test_comparison_and_exports_are_labelled_internal_estimates(pf):
    rows = _valued(pf)
    _pkg(pf, "Five", all=True)
    _pkg(pf, "Five at 10% off", all=True, discount_mode="percent", discount_value=10)
    cmp_ = _pkgs(pf)["comparison"]
    assert [k["name"] for k in cmp_["packages"]] == ["Five", "Five at 10% off"]
    labels = [r["label"] for r in cmp_["rows"]]
    yield_row = next(r for r in cmp_["rows"] if r["key"] == "gross_rent_yield_pct")
    assert all(v is not None for v in yield_row["values"])          # land has no rent; it is not "missing"
    assert "Est. profit at package offer" in labels and "recommend" not in str(cmp_).lower()
    c, h, pid = pf["c"], pf["owner_h"], pf["p"]["id"]
    for path in ("/export.csv", "/items/%s/export.csv" % rows[1]["item_id"], "/packages/compare/export.csv",
                 "/packages/%s/export.csv" % cmp_["packages"][0]["id"]):
        r = c.get("/wholesale/portfolios/%s%s" % (pid, path), headers=h)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv"), path
        assert "not an appraisal" in r.text.lower(), path
    inv = c.get("/wholesale/portfolios/%s/export.csv" % pid, headers=h).text
    assert "Seller asking (stated)" in inv and "Pat Example" not in inv and "555-0100" not in inv


# ── linking existing records ────────────────────────────────────────────────

def test_link_existing_properties_without_duplicating_them(pf):
    db, c, h = pf["db"], pf["c"], pf["owner_h"]
    prop = svc.create_property(db, pf["evo"].id, None, {"street_address": "909 Synthetic Walnut St", "is_test": True})
    db.commit()
    out = _ok(c.post("/wholesale/portfolios/%s/items/add" % pf["p"]["id"], headers=h,
                     json={"property_ids": [prop.id]}))
    assert out["added"] == 1 and out["rows"][0]["property_id"] == prop.id
    again = _ok(c.post("/wholesale/portfolios/%s/items/add" % pf["p"]["id"], headers=h,
                       json={"property_ids": [prop.id]}))
    assert again["added"] == 0 and again["already_in_portfolio"] == 1
    assert db.query(WholesalePortfolioItem).filter(WholesalePortfolioItem.property_id == prop.id).count() == 1


# ── access ──────────────────────────────────────────────────────────────────

def _custom_login(pf, email, perms, scope):
    prof = _profile(pf, "P " + email, "custom", permissions=perms, record_scope=scope)
    return _login_new(pf, _invite_new(pf, email, prof["id"]), email)


def test_restricted_researcher_sees_only_assigned_properties_and_no_mixed_packages(pf):
    c, h, pid = pf["c"], pf["owner_h"], pf["p"]["id"]
    rows = _valued(pf)
    va_h = _custom_login(pf, "va.portfolio@example.com", {"portfolios": ["view", "edit"], "underwriting": ["view"]},
                         "assigned")
    from app.models.models import User
    va = pf["db"].query(User).filter(User.email == "va.portfolio@example.com").first()
    # before assignment: the VA sees no portfolio at all
    assert _ok(c.get("/wholesale/portfolios", headers=va_h))["portfolios"] == []
    mine = [rows[1]["item_id"], rows[3]["item_id"]]
    out = _ok(c.post("/wholesale/portfolios/%s/assign" % pid, headers=h, json={"item_ids": mine, "user_id": va.id}))
    assert out["assigned"] == 2
    d = _ok(c.get("/wholesale/portfolios/%s" % pid, headers=va_h))
    assert sorted(r["item_id"] for r in d["rows"]) == sorted(mine)
    assert d["summary"]["seller_asking"]["total"] == 85000 + 25000          # only what they may see
    hidden = rows[2]["item_id"]
    assert c.get("/wholesale/portfolios/%s/items/%s/evaluation" % (pid, hidden), headers=va_h).status_code == 404
    _pkg(pf, "Mixed", item_ids=[mine[0], hidden])
    _pkg(pf, "Only mine", item_ids=mine)
    names = [k["name"] for k in _ok(c.get("/wholesale/portfolios/%s/packages" % pid, headers=va_h))["packages"]]
    assert names == ["Only mine"]
    # no export, no create, no delete in this access
    assert c.get("/wholesale/portfolios/%s/export.csv" % pid, headers=va_h).status_code == 403
    assert c.post("/wholesale/portfolios", headers=va_h, json={"name": "x"}).status_code == 403
    assert c.post("/wholesale/portfolios/%s/packages" % pid, headers=va_h, json={"name": "x"}).status_code == 403


def test_a_login_without_the_module_is_refused(pf):
    va_h = _custom_login(pf, "noportfolio@example.com", {"properties": ["view"]}, "workspace")
    r = pf["c"].get("/wholesale/portfolios", headers=va_h)
    assert r.status_code == 403 and "Portfolio Acquisitions" in r.json()["detail"]


def test_other_workspaces_cannot_see_or_touch_a_portfolio(pf):
    c, pid = pf["c"], pf["p"]["id"]
    rows = _imported(pf)["rows"]
    bab_h = pf["bab_h"]
    assert _ok(c.get("/wholesale/portfolios", headers=bab_h))["portfolios"] == []
    assert c.get("/wholesale/portfolios/%s" % pid, headers=bab_h).status_code == 404
    assert c.get("/wholesale/portfolios/%s/export.csv" % pid, headers=bab_h).status_code == 404
    assert c.post("/wholesale/portfolios/%s/items/remove" % pid, headers=bab_h,
                  json={"item_ids": [rows[0]["item_id"]]}).status_code == 404
    # a BookaBoost portfolio is numbered on its own and cannot link an EvoSys property
    bp = _ok(c.post("/wholesale/portfolios", headers=bab_h, json={"name": "BAB synthetic"}))["portfolio"]
    assert bp["number"] == 1
    out = _ok(c.post("/wholesale/portfolios/%s/items/add" % bp["id"], headers=bab_h,
                     json={"property_ids": [rows[0]["property_id"]]}))
    assert out["added"] == 0 and out["not_found"] == 1


def test_investor_portal_tokens_cannot_reach_portfolios(client, room):
    """A Deal Room investor's own login is not a staff login: no portfolio route answers it."""
    from tests.test_deal_room import _accept, _invite
    _inv, token = _invite(client, room["A"], "Riley Synth", "riley.pf@practice.invalid")
    inv_h = _accept(client, token, full_name="Riley Synth", kind="individual")
    for path in ("/wholesale/portfolios", "/wholesale/portfolios/anything/packages"):
        assert client.get(path, headers=inv_h).status_code in (401, 403), path


def test_nothing_is_published_or_sent_by_any_portfolio_action(pf):
    rows = _valued(pf)
    _pkg(pf, "All", all=True)
    db = pf["db"]
    for r in rows.values():
        d = db.query(WholesaleDeal).get(r["deal_id"])
        assert not d.buyer_room_published and not d.seller_room_published
    assert pf["sent"] == []
    assert db.query(WholesalePortfolio).count() == 1
    assert db.query(WholesalePortfolioPackageItem).count() == 5
