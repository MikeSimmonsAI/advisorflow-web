"""DEAL INTELLIGENCE ENGINE - one-click underwriting of a Wholesale deal.

"Analyze deal" pulls together what the platform already knows about one
property and turns it into an investment analysis a person can act on:

    profile      the property, its owner, tax value, lien evidence, distress
                 signals - each with its source and date
    comps / ARV  the existing ARV engine's result (arv_engine via deal.arv_detail):
                 comps used and excluded, with reasons; low / base / high
    repairs      the current repair estimate (wholesale_repairs) and its status
    scenarios    wholesale assignment, fix and flip, buy and hold - every line
                 of every formula shown
    offer        maximum allowable offer (the SAME formula as the deal, via
                 wholesale_analysis.calculate_offer), a recommended range,
                 confidence, and what is missing
    workflow     what is done, what is missing, what comes next

NOTHING HERE INVENTS A FACT. A number that is not on file is None and is
listed under "missing". Default assumptions (holding months, selling costs,
interest rate...) are STARTING ASSUMPTIONS, labelled as such and editable on
every run - they are not market data. Comparable sales come only from comps a
person entered or imported with a source; an estimate is never a sale.

Every run that a person asks for is kept (event "underwriting.run" with the
inputs and the results), so "why did the number change" has an answer.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from typing import Any, Dict, List, Optional

VERSION = "underwriting/v1"

# Starting assumptions. Every one is editable per run; none is market data.
DEFAULTS: Dict[str, Any] = {
    # wholesale
    "offer_range_pct": 10,           # recommended range: MAO less this % up to MAO
    # fix and flip (the END BUYER's view of the deal we would assign)
    "flip_buy_closing_pct": 2.0,     # title/escrow/recording on the buy side, % of purchase
    "flip_holding_months": 5,
    "flip_tax_rate_pct": 2.2,        # property tax, % of value per year (edit per county)
    "flip_insurance_monthly": 150,
    "flip_utilities_monthly": 200,
    "flip_loan_pct": 0,              # 0 = all cash; otherwise % of purchase + repairs financed
    "flip_interest_rate_pct": 12.0,  # hard-money style, interest only
    "flip_points_pct": 2.0,
    "flip_selling_pct": 8.0,         # agent commission + seller closing costs, % of ARV
    # buy and hold
    "rent_monthly": None,            # REQUIRED for the rental scenario - never assumed
    "rent_vacancy_pct": 8.0,
    "rent_management_pct": 10.0,
    "rent_maintenance_pct": 8.0,
    "rent_capex_pct": 5.0,
    "rent_insurance_annual": 1800,
    "rent_tax_rate_pct": 2.2,
    "rent_down_pct": 25.0,
    "rent_interest_rate_pct": 7.5,
    "rent_term_years": 30,
    "rent_buy_closing_pct": 2.0,
}

ASSUMPTION_LABELS = {
    "offer_range_pct": "Recommended offer range below MAO (%)",
    "flip_buy_closing_pct": "Buy-side closing costs (% of purchase)",
    "flip_holding_months": "Holding period (months)",
    "flip_tax_rate_pct": "Property tax (% of value per year)",
    "flip_insurance_monthly": "Insurance per month",
    "flip_utilities_monthly": "Utilities per month",
    "flip_loan_pct": "Financed (% of purchase + repairs; 0 = cash)",
    "flip_interest_rate_pct": "Loan interest rate (% per year)",
    "flip_points_pct": "Loan points (%)",
    "flip_selling_pct": "Selling costs (% of ARV)",
    "rent_monthly": "Monthly rent",
    "rent_vacancy_pct": "Vacancy (% of rent)",
    "rent_management_pct": "Management (% of rent)",
    "rent_maintenance_pct": "Maintenance (% of rent)",
    "rent_capex_pct": "Capital reserves (% of rent)",
    "rent_insurance_annual": "Insurance per year",
    "rent_tax_rate_pct": "Property tax (% of value per year)",
    "rent_down_pct": "Down payment (%)",
    "rent_interest_rate_pct": "Mortgage rate (% per year)",
    "rent_term_years": "Mortgage term (years)",
    "rent_buy_closing_pct": "Buy-side closing costs (% of purchase)",
}

STALE_COMP_MONTHS = 6


# ── small helpers ───────────────────────────────────────────────────────────

def _d(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace("$", "").replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def _f(v) -> Optional[float]:
    return None if v is None else float(round(Decimal(v), 2))


def _r500(v: Decimal) -> Decimal:
    """Round DOWN to the nearest $500 - an offer is never rounded up."""
    return (v / 500).to_integral_value(rounding=ROUND_DOWN) * 500


def assumptions(overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """DEFAULTS with a person's overrides; unknown keys are ignored, bad
    numbers raise ValueError with a sentence."""
    a = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        if k not in DEFAULTS:
            continue
        if v is None or v == "":
            a[k] = None if k == "rent_monthly" else DEFAULTS[k]
            continue
        n = _d(v)
        if n is None or n < 0:
            raise ValueError("%s must be a number of zero or more." % ASSUMPTION_LABELS.get(k, k))
        if k.endswith("_pct") and n > 100:
            raise ValueError("%s cannot be over 100%%." % ASSUMPTION_LABELS.get(k, k))
        a[k] = float(n)
    return a


def _monthly_payment(principal: Decimal, rate_pct: Decimal, years: int) -> Decimal:
    if principal <= 0:
        return Decimal(0)
    n = int(years) * 12
    r = rate_pct / Decimal(100) / 12
    if r == 0:
        return principal / n
    return principal * r / (1 - (1 + r) ** (-n))


# ── the three scenarios (pure) ──────────────────────────────────────────────

def wholesale_scenario(arv, repairs, mao: Optional[Decimal], fee, a: Dict[str, Any]) -> Dict[str, Any]:
    if mao is None:
        return {"available": False, "why": "No maximum allowable offer yet (needs ARV and repairs)."}
    pct = Decimal(str(a["offer_range_pct"]))
    low = _r500(mao * (1 - pct / 100))
    top = _r500(mao)
    fee = _d(fee) or Decimal(0)
    return {"available": True,
            "offer_low": _f(low), "offer_high": _f(top), "mao": _f(mao),
            "assignment_fee": _f(fee), "buyer_price_at_mao": _f(top + fee),
            "steps": ["Recommended offer range: MAO $%s less %s%% = $%s, up to MAO $%s (both rounded down "
                      "to $500)." % (format(int(mao), ","), format(pct.normalize(), "f"), format(int(low), ","),
                                     format(int(top), ",")),
                      "Price to the end buyer at the top of the range: $%s + assignment fee $%s = $%s." % (
                          format(int(top), ","), format(int(fee), ","), format(int(top + fee), ","))]}


def flip_scenario(arv, repairs, purchase, a: Dict[str, Any]) -> Dict[str, Any]:
    """The end buyer's fix-and-flip at `purchase` (what they pay us)."""
    arv, repairs, purchase = _d(arv), _d(repairs), _d(purchase)
    missing = [n for n, v in (("ARV", arv), ("repairs", repairs), ("purchase price", purchase)) if v is None]
    if missing:
        return {"available": False, "why": "Needs %s." % ", ".join(missing)}
    D = lambda k: Decimal(str(a[k]))  # noqa: E731
    buy_closing = purchase * D("flip_buy_closing_pct") / 100
    months = D("flip_holding_months")
    tax = arv * D("flip_tax_rate_pct") / 100 / 12 * months
    ins = D("flip_insurance_monthly") * months
    util = D("flip_utilities_monthly") * months
    holding = tax + ins + util
    loan = (purchase + repairs) * D("flip_loan_pct") / 100
    interest = loan * D("flip_interest_rate_pct") / 100 / 12 * months
    points = loan * D("flip_points_pct") / 100
    financing = interest + points
    selling = arv * D("flip_selling_pct") / 100
    total_cost = purchase + repairs + buy_closing + holding + financing + selling
    profit = arv - total_cost
    cash_in = purchase + repairs + buy_closing + holding + financing - loan
    roi = (profit / cash_in * 100) if cash_in > 0 else None
    margin = profit / arv * 100 if arv else None
    fmt = lambda v: "$" + format(int(round(v)), ",")  # noqa: E731
    return {"available": True, "purchase": _f(purchase), "repairs": _f(repairs), "buy_closing": _f(buy_closing),
            "holding": _f(holding), "financing": _f(financing), "selling": _f(selling),
            "total_cost": _f(total_cost), "net_profit": _f(profit), "cash_invested": _f(cash_in),
            "roi_pct": _f(roi) if roi is not None else None, "profit_margin_pct": _f(margin),
            "steps": [
                "Buy-side closing: %s x %s%% = %s" % (fmt(purchase), a["flip_buy_closing_pct"], fmt(buy_closing)),
                "Holding %s months: tax %s + insurance %s + utilities %s = %s" % (
                    a["flip_holding_months"], fmt(tax), fmt(ins), fmt(util), fmt(holding)),
                ("Financing: %s loan, interest %s + points %s = %s" % (fmt(loan), fmt(interest), fmt(points),
                                                                         fmt(financing)))
                if loan else "Financing: all cash, $0",
                "Selling: ARV %s x %s%% = %s" % (fmt(arv), a["flip_selling_pct"], fmt(selling)),
                "Net profit: ARV %s - (purchase %s + repairs %s + closing %s + holding %s + financing %s + "
                "selling %s) = %s" % (fmt(arv), fmt(purchase), fmt(repairs), fmt(buy_closing), fmt(holding),
                                      fmt(financing), fmt(selling), fmt(profit)),
                ("ROI: profit %s / cash invested %s = %s%%" % (fmt(profit), fmt(cash_in), round(roi, 1)))
                if roi is not None else "ROI: no cash invested to divide by",
            ]}


def rental_scenario(value, repairs, purchase, a: Dict[str, Any]) -> Dict[str, Any]:
    """Buy and hold at `purchase` + repairs. Rent is NEVER assumed."""
    value, repairs, purchase = _d(value), _d(repairs), _d(purchase)
    rent = _d(a.get("rent_monthly"))
    missing = [n for n, v in (("monthly rent", rent), ("repairs", repairs), ("purchase price", purchase)) if v is None]
    if missing:
        return {"available": False, "why": "Needs %s." % ", ".join(missing)}
    D = lambda k: Decimal(str(a[k]))  # noqa: E731
    basis = purchase + repairs
    gross = rent * 12
    vacancy = gross * D("rent_vacancy_pct") / 100
    opex_pct = (D("rent_management_pct") + D("rent_maintenance_pct") + D("rent_capex_pct")) / 100
    opex_rent = gross * opex_pct
    tax = (value or basis) * D("rent_tax_rate_pct") / 100
    ins = D("rent_insurance_annual")
    noi = gross - vacancy - opex_rent - tax - ins
    loan = purchase * (1 - D("rent_down_pct") / 100)
    pay = _monthly_payment(loan, D("rent_interest_rate_pct"), int(a["rent_term_years"]))
    debt = pay * 12
    cash_flow = noi - debt
    closing = purchase * D("rent_buy_closing_pct") / 100
    cash_in = purchase - loan + repairs + closing
    cap = noi / basis * 100 if basis else None
    coc = cash_flow / cash_in * 100 if cash_in > 0 else None
    fmt = lambda v: "$" + format(int(round(v)), ",")  # noqa: E731
    return {"available": True, "rent_monthly": _f(rent), "gross_rent_annual": _f(gross), "vacancy": _f(vacancy),
            "operating_expenses": _f(opex_rent + tax + ins), "noi_annual": _f(noi),
            "loan": _f(loan), "payment_monthly": _f(pay), "cash_flow_annual": _f(cash_flow),
            "cash_flow_monthly": _f(cash_flow / 12), "cash_invested": _f(cash_in),
            "cap_rate_pct": _f(cap), "cash_on_cash_pct": _f(coc),
            "steps": [
                "Gross rent: %s x 12 = %s; vacancy %s%% = %s" % (fmt(rent), fmt(gross), a["rent_vacancy_pct"],
                                                                 fmt(vacancy)),
                "Operating: management/maintenance/reserves %s%% of rent = %s, tax %s, insurance %s" % (
                    float(opex_pct * 100), fmt(opex_rent), fmt(tax), fmt(ins)),
                "Net operating income: %s" % fmt(noi),
                "Mortgage: %s at %s%% for %s years = %s/month" % (fmt(loan), a["rent_interest_rate_pct"],
                                                                  a["rent_term_years"], fmt(pay)),
                "Cash flow: NOI %s - debt %s = %s/year (%s/month)" % (fmt(noi), fmt(debt), fmt(cash_flow),
                                                                       fmt(cash_flow / 12)),
                ("Cap rate: NOI / (purchase + repairs %s) = %s%%" % (fmt(basis), round(cap, 2)))
                if cap is not None else "Cap rate: no basis",
                ("Cash-on-cash: %s / cash in %s = %s%%" % (fmt(cash_flow), fmt(cash_in), round(coc, 2)))
                if coc is not None else "Cash-on-cash: no cash invested",
            ]}


# ── the analysis ────────────────────────────────────────────────────────────

def _comps_view(db, org_id: str, deal, arv_detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from app.models.wholesale_models import WholesaleComp
    rows = (db.query(WholesaleComp).filter(WholesaleComp.organization_id == org_id,
                                           WholesaleComp.deal_id == deal.id)
            .order_by(WholesaleComp.created_at.asc()).all())
    used = {c.get("comp_id"): c for c in (arv_detail or {}).get("comps_used") or []}
    excl = {c.get("comp_id"): c for c in (arv_detail or {}).get("comps_excluded") or []}
    today = date.today()
    out = []
    for c in rows:
        sd = c.sale_date
        age = ((today.year - sd.year) * 12 + (today.month - sd.month)) if sd else None
        price = _d(c.sale_price)
        sq = c.square_feet or None
        e = excl.get(c.id) or {}
        reasons = [x.get("label") or x.get("reason") or str(x) for x in (e.get("excluded") or [])] \
            if isinstance(e.get("excluded"), list) else []
        out.append({
            "id": c.id, "address": c.street_address, "sale_price": _f(price),
            "sale_date": sd.isoformat() if sd else None, "age_months": age,
            "stale": age is not None and age > STALE_COMP_MONTHS,
            "square_feet": sq, "price_per_sqft": _f(price / sq) if price and sq else None,
            "beds": float(c.bedrooms) if c.bedrooms is not None else None,
            "baths": float(c.bathrooms) if c.bathrooms is not None else None,
            "distance_miles": float(c.distance_miles) if c.distance_miles is not None else None,
            "latitude": float(c.latitude) if getattr(c, "latitude", None) is not None else None,
            "longitude": float(c.longitude) if getattr(c, "longitude", None) is not None else None,
            "included": bool(c.included), "counted_in_arv": c.id in used,
            "why_not_counted": reasons or ([c.exclusion_reason] if c.exclusion_reason else []),
            "source": c.source, "source_reference": c.source_reference,
            "verification": c.verification_state, "sale_type": c.sale_type,
        })
    return {"count": len(out), "counted": sum(1 for x in out if x["counted_in_arv"]),
            "stale": sum(1 for x in out if x["stale"]), "items": out}


def _profile(db, org_id: str, deal, prop) -> Dict[str, Any]:
    g = lambda o, k: getattr(o, k, None) if o is not None else None  # noqa: E731
    out = {
        "address": " ".join(x for x in (g(prop, "street_address"), g(prop, "unit")) if x) or None,
        "city": g(prop, "city"), "state": g(prop, "state"), "zip": g(prop, "zip_code"), "county": g(prop, "county"),
        "parcel": g(prop, "parcel_apn"), "property_type": g(prop, "property_type"),
        "beds": float(g(prop, "bedrooms")) if g(prop, "bedrooms") is not None else None,
        "baths": float(g(prop, "bathrooms")) if g(prop, "bathrooms") is not None else None,
        "square_feet": g(prop, "square_feet"), "lot_size_sqft": g(prop, "lot_size_sqft"),
        "year_built": g(prop, "year_built"), "owner": g(prop, "owner_name"),
        "ownership_type": g(prop, "ownership_type"), "occupancy": g(prop, "occupancy_status"),
        "estimated_value": _f(_d(g(prop, "estimated_value"))), "estimated_value_source": g(prop, "estimated_value_source"),
        "mortgage_balance": _f(_d(g(prop, "mortgage_balance"))), "mortgage_source": g(prop, "mortgage_source"),
        "liens_note": g(prop, "liens_note"),
        "tax_value": None, "tax_value_source": None, "distress": [], "foreclosure": None,
        "sources": [], "legal_description": None,
    }
    # The acquisition record this deal came from (EvoSense): tax value, distress
    # signals, foreclosure notice, legal description - each with its source.
    try:
        from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
        from app.services.evosense import cohort as CO
        from app.services.evosense import fc_match as FM
        from app.services.evosense import valuation as VAL
        evo = (db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                                 EvoSenseProperty.promoted_deal_id == deal.id).first())
        if evo is not None:
            out["evosense_property_id"] = evo.id
            ap = (VAL.view(evo) or {}).get("appraisal")
            if ap and ap.get("value"):
                out["tax_value"] = ap.get("value")
                out["tax_value_source"] = ap.get("label") or ap.get("source")
            sigs = (db.query(EvoSenseSignal).filter(EvoSenseSignal.property_id == evo.id,
                                                    EvoSenseSignal.active.is_(True)).all())
            out["distress"] = sorted({s.signal_type for s in sigs})
            fc = CO.foreclosure_status(sigs)
            if fc.get("status") and fc.get("status") != "NO NOTICE FOUND":
                out["foreclosure"] = {k: fc.get(k) for k in ("status", "sale_date", "document", "document_url",
                                                              "foreclosure_type", "reposted_for")}
            legal = (FM.identity(db, evo).get("legal") or {}).get("raw")
            out["legal_description"] = " ".join(str(legal).split()) if legal else None
            out["sources"] = sorted({s.source for s in sigs if s.source})
            out["last_verified"] = evo.last_observed_at.isoformat() + "Z" if evo.last_observed_at else None
    except Exception:  # noqa: BLE001 - the profile is assembled from what is there
        pass
    return out


def analyze(db, org_id: str, deal, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.models.wholesale_models import WholesaleProperty
    from app.services import mao_gate
    from app.services import wholesale_analysis as WA
    from app.services import wholesale_repairs as REP
    from app.services import wholesale_service as svc
    a = assumptions(overrides)
    settings = svc.resolve_settings(db, org_id, commit=False)
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id,
                                              WholesaleProperty.organization_id == org_id).first()
    try:
        arv_detail = json.loads(deal.arv_detail) if getattr(deal, "arv_detail", None) else None
    except (TypeError, ValueError):
        arv_detail = None
    arv = _d(deal.arv)
    arv_low = _d((arv_detail or {}).get("low")) or (arv if arv is not None else None)
    arv_high = _d((arv_detail or {}).get("high")) or (arv if arv is not None else None)
    rep = REP.view(db, org_id, deal)
    rep_base = _d(rep.get("amount"))
    rep_low = _d(rep.get("low")) if rep.get("low") is not None else rep_base
    rep_high = _d(rep.get("high")) if rep.get("high") is not None else rep_base
    summary = WA.deal_summary(deal, settings)
    gate = mao_gate.evaluate(deal, settings)
    mao = _d(summary.get("max_allowable_offer")) if gate.get("status") != "NOT_CALCULATED" else None
    fee = _d(summary.get("wholesale_fee"))

    def calc(arv_v, rep_v):
        if arv_v is None or rep_v is None:
            return None
        c = WA.calculate_offer(arv_v, rep_v, summary.get("investor_percentage"), fee,
                               getattr(settings, "transaction_cost_percent", 0),
                               getattr(settings, "transaction_cost_flat", 0))
        return _d(c.get("mao"))

    cases = {}
    for name, av, rv in (("worst", arv_low, rep_high), ("base", arv, rep_base), ("best", arv_high, rep_low)):
        m = mao if name == "base" else (calc(av, rv) if mao is not None else None)
        purchase = (m + (fee or 0)) if m is not None else None
        cases[name] = {"arv": _f(av), "repairs": _f(rv), "mao": _f(m),
                       "wholesale": wholesale_scenario(av, rv, m, fee, a),
                       "flip": flip_scenario(av, rv, purchase, a),
                       "rental": rental_scenario(av, rv, purchase, a)}

    missing: List[str] = []
    if arv is None:
        missing.append("ARV: %s" % ((arv_detail or {}).get("label") or "not established - add closed comparable "
                                                                       "sales (with a source) or enter an ARV"))
    if rep["status"] in ("UNKNOWN", "SELLER_REPORTED"):
        missing.append("Repairs: %s - enter an itemized or inspection estimate" % rep["label"])
    if a.get("rent_monthly") is None:
        missing.append("Monthly rent (for the rental scenario)")
    for r in gate.get("reasons") or []:
        if r not in " ".join(missing):
            missing.append(r)

    conf_arv = (getattr(deal, "arv_confidence", None) or ("insufficient" if arv is None else "low"))
    rep_conf = {"VERIFIED": "high", "INSPECTION_ESTIMATE": "high", "MANUAL_ESTIMATE": "medium",
                "SYSTEM_ESTIMATE": "low"}.get(rep["status"], "insufficient")
    order = ["insufficient", "low", "medium", "high"]
    confidence = min(conf_arv if conf_arv in order else "low", rep_conf, key=order.index)

    comps = _comps_view(db, org_id, deal, arv_detail)
    base = cases["base"]["wholesale"]
    offer = {"mao": _f(mao), "range_low": base.get("offer_low"), "range_high": base.get("offer_high"),
             "confidence": confidence, "gate": gate.get("label"), "can_request_approval": mao is not None,
             "assumptions": {"investor_percentage": summary.get("investor_percentage"),
                             "wholesale_fee": summary.get("wholesale_fee"),
                             "transaction_costs": summary.get("transaction_costs")},
             "steps": summary.get("steps")}
    return {
        "version": VERSION, "calculated_at": datetime.utcnow().isoformat() + "Z", "deal_id": deal.id,
        "profile": _profile(db, org_id, deal, prop),
        "comps": comps,
        "arv": {"value": _f(arv), "low": _f(arv_low), "high": _f(arv_high), "source": deal.arv_source,
                "method": (arv_detail or {}).get("method") or deal.arv_method,
                "confidence": conf_arv, "label": (arv_detail or {}).get("label"),
                "limitations": (arv_detail or {}).get("limitations") or [],
                "note": "Tax values, AVM estimates and list prices are reference only - never the ARV."},
        "repairs": rep, "offer": offer, "scenarios": cases,
        "assumptions": a, "assumption_labels": ASSUMPTION_LABELS,
        "assumptions_note": "Starting assumptions - edit them for this deal. They are not market data.",
        "confidence": confidence, "missing": missing,
        "workflow": workflow(db, org_id, deal, arv, rep, mao),
    }


def workflow(db, org_id: str, deal, arv, rep, mao) -> List[Dict[str, Any]]:
    """The six steps on the deal page: done / needs attention / not started,
    and what to do next - read off the deal's own records."""
    from app.models.wholesale_models import (WholesaleApproval, WholesaleBuyerMatch,
                                             WholesaleBuyerOutreach)
    approvals = db.query(WholesaleApproval).filter(WholesaleApproval.deal_id == deal.id,
                                                   WholesaleApproval.organization_id == org_id).all()
    offer_ok = any(x.kind == "offer" and x.status == "approved" for x in approvals)
    offer_wait = any(x.kind == "offer" and x.status == "pending" for x in approvals)
    matches = db.query(WholesaleBuyerMatch).filter(WholesaleBuyerMatch.deal_id == deal.id,
                                                   WholesaleBuyerMatch.organization_id == org_id).count()
    outreach = db.query(WholesaleBuyerOutreach).filter(WholesaleBuyerOutreach.deal_id == deal.id,
                                                       WholesaleBuyerOutreach.organization_id == org_id).count()
    published = bool(getattr(deal, "buyer_room_published", False))
    closed = deal.stage in ("closed",)
    st = lambda done, attention=False: "done" if done else ("attention" if attention else "todo")  # noqa: E731
    return [
        {"key": "analyze", "label": "Analyze deal", "tab": "underwriting",
         "state": st(arv is not None, arv is None),
         "next": None if arv is not None else "Add closed comparable sales (or enter an ARV)."},
        {"key": "repairs", "label": "Estimate repairs", "tab": "underwriting",
         "state": st(rep["status"] not in ("UNKNOWN", "SELLER_REPORTED"), rep["status"] == "SELLER_REPORTED"),
         "next": None if rep["status"] not in ("UNKNOWN", "SELLER_REPORTED") else "Enter an itemized estimate."},
        {"key": "offer", "label": "Prepare offer", "tab": "offer",
         "state": st(offer_ok, offer_wait or (mao is not None and not offer_ok)),
         "next": None if offer_ok else ("Waiting for the offer approval." if offer_wait else
                                        "Request the offer approval." if mao is not None else
                                        "Needs ARV and repairs first.")},
        {"key": "buyers", "label": "Find buyers", "tab": "buyers", "state": st(matches > 0),
         "next": None if matches else "Match buyers to this deal."},
        {"key": "package", "label": "Create deal package", "tab": "sharing", "state": st(published),
         "next": None if published else "Prepare the buyer page and PDF; publish after approval."},
        {"key": "disposition", "label": "Manage disposition", "tab": "closing",
         "state": st(closed, outreach > 0 and not closed),
         "next": None if closed else ("Track buyer responses and offers." if outreach else "Not started.")},
    ]


def history(db, org_id: str, deal_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    from app.models.wholesale_models import WholesaleEvent
    rows = (db.query(WholesaleEvent).filter(WholesaleEvent.organization_id == org_id,
                                            WholesaleEvent.deal_id == deal_id,
                                            WholesaleEvent.action == "underwriting.run")
            .order_by(WholesaleEvent.created_at.desc()).limit(limit).all())
    out = []
    for e in rows:
        try:
            d = json.loads(e.details) if isinstance(e.details, str) else (e.details or {})
        except (TypeError, ValueError):
            d = {}
        out.append({"at": e.created_at.isoformat() + "Z" if e.created_at else None, "summary": e.summary,
                    "by": e.actor_user_id, **{k: d.get(k) for k in ("arv", "repairs", "mao", "offer_low",
                                                                       "offer_high", "confidence",
                                                                       "changed_assumptions")}})
    return out


def record_run(db, org_id: str, deal, result: Dict[str, Any], user) -> None:
    from app.services import wholesale_service as svc
    changed = {k: v for k, v in result["assumptions"].items() if DEFAULTS.get(k) != v}
    o = result["offer"]
    svc.log_event(db, org_id, "underwriting.run", actor_type="user", actor_user_id=getattr(user, "id", None),
                  deal_id=deal.id, summary="Deal analyzed: ARV %s, repairs %s, MAO %s (%s confidence)" % (
                      _money(result["arv"]["value"]), _money(result["repairs"].get("amount")), _money(o["mao"]),
                      result["confidence"]),
                  details={"arv": result["arv"]["value"], "repairs": result["repairs"].get("amount"),
                           "mao": o["mao"], "offer_low": o["range_low"], "offer_high": o["range_high"],
                           "confidence": result["confidence"], "changed_assumptions": changed,
                           "version": VERSION})


def _money(v) -> str:
    return ("$" + format(int(v), ",")) if v is not None else "not set"


# ── itemized repairs ────────────────────────────────────────────────────────

QUALITY = {"rental": Decimal("0.85"), "standard": Decimal("1.0"), "premium": Decimal("1.3")}


def itemized(items: List[Dict[str, Any]], contingency_pct=15, quality: str = "standard") -> Dict[str, Any]:
    """Itemized repair estimate from lines a person entered (quantity x unit
    cost they chose). Low = the lines as entered; base = + contingency; high =
    + twice the contingency. `quality` scales unit costs (rental 0.85 /
    standard 1.0 / premium 1.3) - stated, editable, never hidden."""
    if not items:
        raise ValueError("Add at least one repair line.")
    q = QUALITY.get((quality or "standard").lower())
    if q is None:
        raise ValueError("Quality must be rental, standard or premium.")
    cont = _d(contingency_pct)
    if cont is None or cont < 0 or cont > 100:
        raise ValueError("Contingency must be between 0 and 100%.")
    lines, total = [], Decimal(0)
    for i, it in enumerate(items, 1):
        qty, cost = _d(it.get("quantity")), _d(it.get("unit_cost"))
        name = (it.get("item") or "").strip()
        if not name:
            raise ValueError("Line %s needs a description." % i)
        if qty is None or qty <= 0 or cost is None or cost < 0:
            raise ValueError("Line %s (%s) needs a quantity above 0 and a unit cost." % (i, name))
        amount = (qty * cost * q).quantize(Decimal("0.01"))
        total += amount
        lines.append({"category": (it.get("category") or "General").strip()[:60], "item": name[:120],
                      "room": (it.get("room") or "").strip()[:60] or None,
                      "quantity": float(qty), "unit": (it.get("unit") or "each").strip()[:20],
                      "unit_cost": float(cost), "amount": float(amount),
                      "note": (it.get("note") or "").strip()[:300] or None})
    base = total * (1 + cont / 100)
    high = total * (1 + 2 * cont / 100)
    by_cat: Dict[str, float] = {}
    for ln in lines:
        by_cat[ln["category"]] = round(by_cat.get(ln["category"], 0) + ln["amount"], 2)
    return {"lines": lines, "subtotal": float(total), "contingency_pct": float(cont), "quality": quality,
            "quality_factor": float(q), "low": float(total), "base": float(base.quantize(Decimal("0.01"))),
            "high": float(high.quantize(Decimal("0.01"))), "by_category": by_cat,
            "basis": "%s line%s priced by the person entering them, %s quality (x%s); base adds %s%% contingency, "
                     "high adds %s%%" % (len(lines), "" if len(lines) == 1 else "s", quality, q, cont, cont * 2)}
