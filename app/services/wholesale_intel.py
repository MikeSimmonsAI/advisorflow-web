"""SHARED INTELLIGENCE: fact verification, the Wholesale / Investment switch and
the Deal Opportunity Report (#415, sections 16-19).

NOTHING NEW IS COLLECTED HERE. This module reads what the platform already
holds - EvoSense observations (one row per source per retrieval, with the
source's own payload, its date and ours), signals, owners, facts, the existing
preliminary economics (`evosense.economics`), the type-specific income / land
analysis (`evosense.type_details`), Wholesale deal analysis and the buyer
engine - and states, for each fact, how well it is supported.

VERIFICATION STATUSES (the same rules for properties, buyers and contacts)
    VERIFIED    an official record source (appraisal district, tax office,
                county clerk, code enforcement) states it, no official source
                disagrees, and the newest such record is inside its freshness window
    LIKELY      only a statewide layer or geocoder states it, or one official
                source is near the edge of its window
    UNVERIFIED  only a claim states it (an imported list, a person's note, a
                buyer's own words) - shown, never treated as fact
    CONFLICTED  two official sources disagree - a person decides; nothing is
                overwritten
    STALE       the newest official record is older than the window
    UNKNOWN     nobody states it - with the next appropriate lookup named

A weaker source never replaces a verified value: this module only READS, and
every value keeps its source, original value, source date and retrieval date.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

OFFICIAL = {"dcad", "tad", "collin_cad", "denton_gis", "johnson_cad", "hcad_roll", "tarrant_tax_roll", "dallas_trw",
            "travis_delinquent", "lgbs_tax_sales", "tx_clerk_foreclosure", "fw_code_violations",
            "fw_open_cases", "dallas_311", "austin_code"}
STATEWIDE = {"txgio_roll", "txgio_parcels", "census_geocoder", "texas_parcel_owner"}
CLAIM = {"csv_import", "manual", "propstream_csv", "list_import"}
FRESH_DAYS = {"owner": 400, "mailing": 400, "parcel": 800, "address": 800, "characteristics": 900,
              "transfer": 800, "tax": 60, "foreclosure": 30, "code": 120, "debt": 180, "contact": 180}

FIELDS: List[Tuple[str, str, str]] = [
    # (key, label, freshness group)
    ("parcel_apn", "Parcel / APN", "parcel"),
    ("street_address", "Property address", "address"),
    ("zip_code", "ZIP", "address"),
    ("county", "County", "address"),
    ("owner_name", "Owner of record", "owner"),
    ("owner_mailing", "Owner mailing address", "mailing"),
    ("property_type", "Property type", "characteristics"),
    ("square_feet", "Living area (sq ft)", "characteristics"),
    ("year_built", "Year built", "characteristics"),
    ("bedrooms", "Bedrooms", "characteristics"),
    ("bathrooms", "Bathrooms", "characteristics"),
    ("deed_transfer_date", "Last recorded transfer", "transfer"),
]
NEXT_LOOKUP = {
    "owner": "County appraisal record (free) - run Discovery for this county",
    "mailing": "County appraisal record (free); if absent, a paid skip trace needs your approval",
    "parcel": "County appraisal district search (free)",
    "address": "Census geocoder (free)",
    "characteristics": "County appraisal record (free) or a person's walkthrough",
    "transfer": "County appraisal record or county clerk deed index (free)",
    "tax": "County tax office account lookup (free, one at a time)",
    "foreclosure": "County clerk foreclosure postings (free; Dallas, Tarrant, Denton, Johnson are automatic)",
    "debt": "Mortgage data - ATTOM or PropStream (paid; not connected; needs your approval)",
    "contact": "Skip trace - Tracerfy (paid; needs your approval)",
}


def _norm(v) -> str:
    if v is None:
        return ""
    s = str(v).strip().upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    s = re.sub(r"\b(STREET|ST|AVENUE|AVE|DRIVE|DR|ROAD|RD|LANE|LN|COURT|CT|BOULEVARD|BLVD)\b", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _pl(raw) -> Dict[str, Any]:
    try:
        d = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _iso(v):
    return v.isoformat() + "Z" if isinstance(v, datetime) else (str(v) if v else None)


def _extract(key: str, p: Dict[str, Any]):
    if key == "owner_name":
        return (p.get("owner") or {}).get("name")
    if key == "owner_mailing":
        o = p.get("owner") or {}
        parts = [o.get("mailing_street"), o.get("mailing_city"), o.get("mailing_state"), o.get("mailing_zip")]
        return ", ".join(str(x) for x in parts if x) or None
    return p.get(key)


def _rank(provider: str, kind: str) -> str:
    if kind == "sandbox":
        return "sandbox"
    if provider in OFFICIAL:
        return "official"
    if provider in STATEWIDE:
        return "statewide"
    return "claim"


def status_for(group: str, sources: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    """Pure: one fact's status from its sources [{value, rank, source_date, retrieved_at, source}]."""
    now = now or datetime.utcnow()
    have = [s for s in sources if s.get("value") not in (None, "")]
    if not have:
        return {"status": "UNKNOWN", "why": "No source states it", "next": NEXT_LOOKUP.get(group)}
    off = [s for s in have if s["rank"] == "official"]
    window = FRESH_DAYS.get(group, 400)
    if off:
        latest_by_src: Dict[str, Dict[str, Any]] = {}
        for s in off:
            cur = latest_by_src.get(s["source"])
            if cur is None or (s.get("retrieved_at") or datetime.min) > (cur.get("retrieved_at") or datetime.min):
                latest_by_src[s["source"]] = s
        vals = {_norm(s["value"]) for s in latest_by_src.values()}
        if len(vals) > 1:
            return {"status": "CONFLICTED", "why": "Official sources disagree: %s" % "; ".join(
                "%s says %s" % (s["source"], s["value"]) for s in latest_by_src.values()),
                "next": "A person compares the records; nothing is overwritten"}
        newest = max((s.get("source_date") or s.get("retrieved_at") for s in off if (s.get("source_date") or s.get("retrieved_at"))),
                     default=None)
        age = (now - newest).days if newest else None
        if age is not None and age > window:
            return {"status": "STALE", "why": "Newest official record is %d days old (window %d)" % (age, window),
                    "next": NEXT_LOOKUP.get(group)}
        if age is not None and age > window * 0.8:
            return {"status": "LIKELY", "why": "Official record near the end of its %d-day window" % window,
                    "next": NEXT_LOOKUP.get(group)}
        return {"status": "VERIFIED", "why": "Stated by %s" % ", ".join(sorted(latest_by_src)), "next": None}
    if any(s["rank"] == "statewide" for s in have):
        return {"status": "LIKELY", "why": "Only a statewide layer states it", "next": NEXT_LOOKUP.get(group)}
    return {"status": "UNVERIFIED", "why": "Only a claim states it (list, note or buyer)", "next": NEXT_LOOKUP.get(group)}


def property_verification(db, org_id: str, evo_id: str) -> Dict[str, Any]:
    from app.models.evosense_models import EvoSenseObservation, EvoSenseProperty
    from app.services.evosense import evaluate as EV
    prop = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == evo_id,
                                             EvoSenseProperty.organization_id == org_id).first()
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    obs = (db.query(EvoSenseObservation).filter(EvoSenseObservation.organization_id == org_id,
                                                EvoSenseObservation.property_id == prop.id)
           .order_by(EvoSenseObservation.observed_at.desc()).limit(400).all())
    from app.services.evosense import contacts as CT
    owner = CT.primary_owner(db, prop)
    facts = []
    for key, label, group in FIELDS:
        srcs = []
        for o in obs:
            if bool(o.is_test) != bool(prop.is_test):
                continue
            v = _extract(key, _pl(o.payload))
            if v in (None, ""):
                continue
            srcs.append({"source": o.provider_key, "rank": _rank(o.provider_key, o.connector_kind), "value": v,
                         "source_date": o.source_updated_at, "retrieved_at": o.last_seen_at or o.observed_at,
                         "reference": o.source_reference})
        st = status_for(group, [s for s in srcs if s["rank"] != "sandbox"] or srcs)
        if key == "owner_name":
            on_record = getattr(owner, "display_name", None)
        elif key == "owner_mailing":
            on_record = ", ".join(str(x) for x in (getattr(owner, "mailing_street", None),
                                                   getattr(owner, "mailing_city", None),
                                                   getattr(owner, "mailing_state", None),
                                                   getattr(owner, "mailing_zip", None)) if x) or None
        else:
            on_record = getattr(prop, key, None)
        facts.append({"key": key, "label": label, "status": st["status"], "why": st["why"], "next": st["next"],
                      "value_on_record": on_record,
                      "sources": [{"source": s["source"], "rank": s["rank"], "value": s["value"],
                                   "source_date": _iso(s["source_date"]), "retrieved_at": _iso(s["retrieved_at"]),
                                   "reference": s["reference"]} for s in srcs[:8]]})
    # distress, tax and foreclosure come from SIGNALS (each already carries evidence)
    stacked = EV.stacked_signals(db, prop)
    sig_rows = []
    for s in stacked:
        ev = (s.get("evidence") or [{}])[0] if s.get("evidence") else {}
        sig_rows.append({"type": s.get("signal_type"), "freshness": s.get("freshness"),
                         "sources": s.get("sources"), "observed_at": _iso(s.get("observed_at")),
                         "status": ("STALE" if s.get("freshness") == "stale"
                                    else "VERIFIED" if set(s.get("record_sources") or s.get("sources") or []) & OFFICIAL
                                    else "UNVERIFIED"),
                         "claim": bool((ev.get("provenance") or {}).get("claim"))})
    missing = [f["label"] for f in facts if f["status"] == "UNKNOWN"]
    debt = {"value": prop.mortgage_balance, "source": prop.mortgage_source,
            "status": "UNKNOWN" if prop.mortgage_balance is None else (
                "UNVERIFIED" if (prop.mortgage_source or "").startswith(("csv", "import", "manual")) else "LIKELY"),
            "next": NEXT_LOOKUP["debt"] if prop.mortgage_balance is None else None}
    counts: Dict[str, int] = {}
    for f in facts:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    return {"property_id": prop.id, "facts": facts, "signals": sig_rows, "debt": debt, "counts": counts,
            "missing": missing, "rules": "Official records outrank statewide layers, which outrank claims. A weaker "
                                         "source never replaces a verified value; conflicts go to a person."}


def buyer_verification(db, org_id: str, buyer_id: str) -> Dict[str, Any]:
    from app.services import wholesale_buyer_engine as ENG
    d = ENG.detail(db, org_id, buyer_id)
    a = d["assessment"]
    m = {"VERIFIED": "VERIFIED", "RECORDED": "VERIFIED", "STALE": "STALE", "COUNT_ONLY": "LIKELY",
         "CLAIMED": "UNVERIFIED", "NONE": "UNKNOWN"}
    c = {"VERIFIED": "VERIFIED", "STALE": "STALE", "UNVERIFIED": "UNVERIFIED", "NONE": "UNKNOWN", "BAD": "CONFLICTED",
         "DO_NOT_CONTACT": "VERIFIED"}
    i = {"CONFIRMED": "VERIFIED", "PUBLIC_RECORD": "VERIFIED", "NAMED": "UNVERIFIED", "NEEDS_REVIEW": "CONFLICTED",
         "LINKED": "VERIFIED", "UNKNOWN": "UNKNOWN"}
    bb = {"CONFIRMED": "VERIFIED", "STALE": "STALE", "STATED": "UNVERIFIED", "FILE": "UNVERIFIED",
          "INFERRED": "LIKELY", "NONE": "UNKNOWN"}
    return {"buyer_id": buyer_id, "facts": [
        {"key": "identity", "label": "Legal identity", "status": i[a["identity"]["status"]], "why": a["identity"]["label"]},
        {"key": "purchases", "label": "Purchase history", "status": m[a["purchases"]["status"]],
         "why": a["purchases"]["label"]},
        {"key": "contact", "label": "Contact", "status": c[a["contact"]["status"]], "why": a["contact"]["why"]},
        {"key": "buy_box", "label": "Buy box", "status": bb[a["buy_box"]["status"]], "why": a["buy_box"]["label"]},
        {"key": "funding", "label": "Funding", "status": "VERIFIED" if a["readiness"]["funding"]["documented"]
         else ("UNVERIFIED" if a["readiness"]["funding"]["stated"] else "UNKNOWN"),
         "why": "Proof of funds accepted by a person" if a["readiness"]["funding"]["documented"]
         else "Stated only - a budget is not financing" if a["readiness"]["funding"]["stated"] else "Not stated"},
    ], "next": a["enrichment"]["next"]}


# ── underwriting: one inventory, three views ───────────────────────────────

def _subject(db, org_id: str, kind: str, sid: str):
    from app.models.evosense_models import EvoSenseProperty
    from app.models.wholesale_models import WholesaleDeal
    evo = deal = None
    if kind == "evosense":
        evo = db.query(EvoSenseProperty).filter(EvoSenseProperty.id == sid,
                                                EvoSenseProperty.organization_id == org_id).first()
        if evo is None:
            raise HTTPException(status_code=404, detail="Property not found")
        if evo.promoted_deal_id:
            deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == evo.promoted_deal_id).first()
    elif kind == "deal":
        deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == sid, WholesaleDeal.organization_id == org_id).first()
        if deal is None:
            raise HTTPException(status_code=404, detail="Deal not found")
        evo = db.query(EvoSenseProperty).filter(EvoSenseProperty.organization_id == org_id,
                                                EvoSenseProperty.promoted_deal_id == deal.id).first()
    else:
        raise HTTPException(status_code=400, detail="subject_type must be evosense or deal")
    return evo, deal


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def wholesale_view(db, evo, deal) -> Dict[str, Any]:
    from app.services.evosense import economics as ECO
    out: Dict[str, Any] = {"strategy": "wholesale", "lines": []}
    if evo is not None:
        pre = ECO.preliminary(db, evo)
        out["preliminary"] = pre
        out["lines"] += [{"label": l["label"], "value": l["value"], "basis": l["truth_label"]} for l in pre["lines"]]
    if deal is not None:
        contract, buyer = _f(deal.contract_price), _f(deal.buyer_price) or _f(deal.buyer_room_asking_price)
        cost = sum(x for x in (_f(deal.transaction_costs), _f(deal.other_costs)) if x)
        arv, rep = _f(deal.arv), _f(deal.repair_estimate)
        out["lines"] += [
            {"label": "ARV on the deal", "value": arv,
             "basis": (deal.arv_source or deal.arv_method or "deal record") if arv else "UNKNOWN - no ARV entered"},
            {"label": "Repair estimate on the deal", "value": rep,
             "basis": (deal.repair_estimate_source or "deal record") if rep else "UNKNOWN - no repair estimate"},
            {"label": "Acquisition (contract) price", "value": contract, "basis": "deal record" if contract else "NOT SET"},
            {"label": "Investor resale price", "value": buyer, "basis": "deal record" if buyer else "NOT SET"},
            {"label": "Transaction costs on record", "value": cost or None, "basis": "deal record" if cost else "NOT SET"},
            {"label": "Estimated assignment spread",
             "value": (buyer - contract - (cost or 0)) if (buyer is not None and contract is not None) else None,
             "basis": "investor price - contract price - recorded costs" if (buyer and contract)
             else "UNKNOWN - needs both prices"}]
    out["time_sensitivity"] = {"work_priority": getattr(evo, "work_priority", None)} if evo is not None else None
    out["note"] = "Unknown stays unknown: no spread without both prices; no MAO without a verified ARV and repairs."
    return out


def investment_view(db, evo, deal, assumptions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.services.evosense import type_details as TD
    a = assumptions or {}
    out: Dict[str, Any] = {"strategy": "investment", "assumptions": a, "lines": [], "scenarios": []}
    if evo is None:
        out["analysis"] = None
        out["note"] = "Investment analysis reads the property's type facts; this deal has no linked discovery record."
        return out
    v = TD.view(db, evo)
    an = v["analysis"]
    out["analysis"] = an
    out["lines"] = [{"label": l["label"], "value": l["value"], "basis": l["how"]} for l in an["lines"]]
    noi = an["values"].get("NOI")
    price = _f(a.get("purchase_price")) or (_f(deal.contract_price) if deal is not None else None)
    rate, ltv, years = _f(a.get("interest_rate")), _f(a.get("ltv")), _f(a.get("amortization_years"))
    if noi is not None and price:
        out["lines"].append({"label": "Cap rate at price", "value": round(100 * noi / price, 2),
                             "basis": "NOI / purchase price"})
    if noi is not None and price and rate and ltv and years:
        loan = price * ltv / 100.0
        r, n = rate / 100.0 / 12, int(years * 12)
        pay = loan * r / (1 - (1 + r) ** -n) if r else loan / n
        ds = pay * 12
        out["scenarios"].append({"label": "Scenario (your assumptions)", "loan": round(loan), "annual_debt_service": round(ds),
                                 "cash_flow_before_tax": round(noi - ds), "dscr": round(noi / ds, 2) if ds else None,
                                 "cash_in": round(price - loan), "cash_on_cash": round(100 * (noi - ds) / (price - loan), 2)
                                 if price > loan else None,
                                 "assumptions": {"rate": rate, "ltv": ltv, "years": years, "price": price}})
    elif noi is None:
        out["missing"] = an.get("missing")
    target = _f(a.get("target_cap_rate"))
    if noi is not None and target:
        out["lines"].append({"label": "Maximum supportable price at target cap", "value": round(noi / (target / 100.0)),
                             "basis": "NOI / target cap rate (your assumption)"})
    out["note"] = ("Built only from facts on record with their sources; debt service and cash flow are SCENARIOS from "
                   "the assumptions you enter, never presented as verified.")
    return out


def underwriting(db, org_id: str, kind: str, sid: str, strategy: str = "both",
                 assumptions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if strategy not in ("wholesale", "investment", "both"):
        raise HTTPException(status_code=400, detail="strategy must be wholesale, investment or both")
    evo, deal = _subject(db, org_id, kind, sid)
    out = {"subject": {"type": kind, "id": sid, "evosense_id": getattr(evo, "id", None),
                       "deal_id": getattr(deal, "id", None)}, "strategy": strategy}
    if strategy in ("wholesale", "both"):
        out["wholesale"] = wholesale_view(db, evo, deal)
    if strategy in ("investment", "both"):
        out["investment"] = investment_view(db, evo, deal, assumptions)
    return out


# ── the Deal Opportunity Report ────────────────────────────────────────────

def report(db, org_id: str, kind: str, sid: str, strategy: str = "both",
           assumptions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    from app.services import wholesale_buyer_match as BM
    evo, deal = _subject(db, org_id, kind, sid)
    uw = underwriting(db, org_id, kind, sid, strategy, assumptions)
    summary = {}
    if evo is not None:
        summary = {"address": ", ".join(x for x in (evo.street_address, evo.city, evo.state, evo.zip_code) if x),
                   "county": evo.county, "parcel": evo.parcel_apn,
                   "category": evo.property_category or evo.property_type, "subtype": evo.property_subtype,
                   "square_feet": evo.square_feet, "year_built": evo.year_built, "bedrooms": _f(evo.bedrooms),
                   "bathrooms": _f(evo.bathrooms), "work_priority": evo.work_priority,
                   "investment_evidence_score": evo.opportunity_score}
    elif deal is not None:
        from app.models.wholesale_models import WholesaleProperty
        p = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
        summary = {"address": ", ".join(x for x in (p.street_address, p.city, p.state, p.zip_code) if x) if p else None,
                   "county": getattr(p, "county", None), "category": getattr(p, "property_category", None)}
    ver = property_verification(db, org_id, evo.id) if evo is not None else None
    try:
        matches = BM.match(db, org_id, "deal" if deal is not None else "evosense", deal.id if deal is not None else evo.id)
        buyers = [{"name": b["name"], "match": b["match_score"], "verification": b["verification"]["headline"],
                   "readiness": b["readiness"]["label"]} for b in matches["buyers"] if not b["disqualified"]][:10]
    except HTTPException:
        buyers = []
    risks = []
    if ver:
        risks += ["%s: %s" % (f["label"], f["status"].lower()) for f in ver["facts"]
                  if f["status"] in ("CONFLICTED", "STALE", "UNKNOWN")]
        if ver["debt"]["status"] == "UNKNOWN":
            risks.append("Debt / payoff: unknown")
    w = uw.get("wholesale") or {}
    if w.get("preliminary") and w["preliminary"].get("mao") is None:
        risks.append("No MAO: %s" % (w["preliminary"].get("warnings") or ["inputs missing"])[0])
    i = uw.get("investment") or {}
    if i.get("analysis") and i["analysis"].get("missing"):
        risks.append("Investment analysis missing: %s" % ", ".join(i["analysis"]["missing"][:5]))
    nxt = "Verify the missing facts listed above" if risks else "Review buyer matches and prepare the deal package"
    if ver and any(f["next"] for f in ver["facts"]):
        nxt = next(f["next"] for f in ver["facts"] if f["next"])
    return {"title": "Deal Opportunity Report", "generated_at": _iso(datetime.utcnow()), "strategy": strategy,
            "property_summary": summary, "distress_signals": (ver or {}).get("signals", []),
            "evidence": (ver or {}).get("facts", []),
            "ownership": [f for f in (ver or {}).get("facts", []) if f["key"] in ("owner_name", "owner_mailing")],
            "financial": {"debt": (ver or {}).get("debt")},
            "wholesale_analysis": uw.get("wholesale"), "investment_analysis": uw.get("investment"),
            "assumptions": assumptions or {}, "risks_and_missing": risks, "buyer_matches": buyers,
            "next_action": nxt, "audience": "internal",
            "notice": "INTERNAL. Contains research and analysis not approved for investors. Share opportunities "
                      "through a Deal Room listing, which carries only the approved investor content."}


def investor_report(db, org_id: str, deal_id: str) -> Dict[str, Any]:
    """The investor-safe version: ONLY what the approved deal package publishes
    (the same whitelist the Buyer Room and Deal Room use), plus the investment
    lines built from those published numbers. No owner, seller, contact,
    research or internal notes - checked by the publication leak alarm."""
    from app.models.wholesale_models import WholesaleDeal
    from app.services import wholesale_publication as PUB
    deal = db.query(WholesaleDeal).filter(WholesaleDeal.id == deal_id, WholesaleDeal.organization_id == org_id).first()
    if deal is None:
        raise HTTPException(status_code=404, detail="Deal not found")
    if not deal.buyer_room_published:
        raise HTTPException(status_code=409, detail="Publish an approved deal package before sharing a report "
                                                    "with investors.")
    pay = PUB.buyer_room_payload(db, deal)
    rep = {"title": "Deal Opportunity Report", "generated_at": _iso(datetime.utcnow()), "audience": "investor",
           "opportunity": pay,
           "notice": "Prepared from the approved deal package. Figures are the offered terms and the "
                     "estimates shown; verify everything independently. This report is not an offer, a contract "
                     "or a right to purchase."}
    leaks = PUB.leaked_keys(rep, PUB.FORBIDDEN_TO_BUYER)
    if leaks:
        raise HTTPException(status_code=500, detail="Report withheld: restricted fields detected")
    return rep


def report_html(rep: Dict[str, Any]) -> str:
    import html as H

    def esc(v):
        return H.escape("" if v is None else (format(int(v), ",") if isinstance(v, float) and v.is_integer() and abs(v) > 999
                                               else str(v)))

    def rows(lines):
        return "".join("<tr><td>%s</td><td>%s</td><td class=m>%s</td></tr>" % (
            esc(l.get("label")), esc(l.get("value")) if l.get("value") is not None else "<i>unknown</i>",
            esc(l.get("basis") or "")) for l in lines or [])
    s = rep["property_summary"]
    parts = ["<h1>Deal Opportunity Report</h1><p class=m>%s &middot; generated %s UTC &middot; %s</p>" % (
        esc(s.get("address")), esc(rep["generated_at"][:16].replace("T", " ")), esc(rep["notice"])),
        "<h2>Property summary</h2><table>%s</table>" % "".join(
            "<tr><td>%s</td><td>%s</td></tr>" % (esc(k.replace("_", " ").capitalize()), esc(v)) for k, v in s.items()),
        "<h2>Distress signals</h2><table>%s</table>" % ("".join(
            "<tr><td>%s</td><td>%s</td><td class=m>%s %s</td></tr>" % (esc(x["type"]), esc(x["status"]),
                                                                   esc(", ".join(x.get("sources") or [])),
                                                                   esc(x.get("observed_at") or ""))
            for x in rep["distress_signals"]) or "<tr><td>None on record</td></tr>"),
        "<h2>Evidence and source dates</h2><table>%s</table>" % "".join(
            "<tr><td>%s</td><td>%s</td><td class=m>%s</td></tr>" % (
                esc(f["label"]), esc(f["status"]), esc("; ".join("%s: %s (%s)" % (x["source"], x["value"],
                                                                                    (x["retrieved_at"] or "")[:10])
                                                               for x in f["sources"][:3]) or f.get("next") or ""))
            for f in rep["evidence"])]
    if rep.get("wholesale_analysis"):
        parts.append("<h2>Wholesale analysis</h2><table>%s</table><p class=m>%s</p>" % (
            rows(rep["wholesale_analysis"]["lines"]), esc(rep["wholesale_analysis"]["note"])))
    if rep.get("investment_analysis"):
        ia = rep["investment_analysis"]
        parts.append("<h2>Investment analysis</h2><table>%s</table>%s<p class=m>%s</p>" % (
            rows(ia.get("lines")), "".join("<p>%s: debt service $%s/yr, cash flow $%s/yr, DSCR %s</p>" % (
                esc(x["label"]), esc(x["annual_debt_service"]), esc(x["cash_flow_before_tax"]), esc(x["dscr"]))
                for x in ia.get("scenarios") or []), esc(ia.get("note"))))
    parts.append("<h2>Risks and missing information</h2><ul>%s</ul>" % (
        "".join("<li>%s</li>" % esc(r) for r in rep["risks_and_missing"]) or "<li>None identified</li>"))
    parts.append("<h2>Buyer matches</h2><table>%s</table>" % ("".join(
        "<tr><td>%s</td><td>%s</td><td class=m>%s &middot; %s</td></tr>" % (
            esc(b["name"]), esc(b["match"]), esc(b["verification"]), esc(b["readiness"])) for b in rep["buyer_matches"])
        or "<tr><td>No eligible buyers yet</td></tr>"))
    parts.append("<h2>Next action</h2><p>%s</p>" % esc(rep["next_action"]))
    css = ("body{font-family:Inter,Arial,sans-serif;color:#0f172a;max-width:900px;margin:24px auto;padding:0 16px}"
           "h1{font-family:Georgia,serif;margin:0}h2{font-size:15px;text-transform:uppercase;letter-spacing:.06em;"
           "color:#13294b;border-bottom:1px solid #e5e7eb;padding-bottom:4px;margin-top:22px}"
           "table{width:100%;border-collapse:collapse;font-size:13px}td{padding:5px 6px;border-bottom:1px solid #f1f5f9;"
           "vertical-align:top}.m{color:#64748b;font-size:12px}@media print{body{margin:0}}")
    return "<!doctype html><html><head><meta charset=utf-8><title>Deal Opportunity Report</title><style>%s</style>" \
           "</head><body>%s</body></html>" % (css, "".join(parts))


# ── provider readiness (God Mode / Providers & Controls already manages them) ─

def provider_readiness() -> List[Dict[str, Any]]:
    import os

    from app.services.evosense.providers import PROVIDERS
    want = [("attom", "ATTOM", ("ATTOM_API_KEY",)), ("attom_comps", "ATTOM comps", ("ATTOM_API_KEY",)),
            ("propstream", "PropStream", ()), ("tracerfy", "Tracerfy skip trace", ("TRACERFY_API_TOKEN",)),
            ("reonomy", "Reonomy", ("REONOMY_API_KEY",)), ("trepp", "Trepp", ("TREPP_API_KEY",))]
    out = []
    for key, label, env in want:
        p = PROVIDERS.get(key)
        configured = bool(env) and all(os.environ.get(e) for e in env)
        if key == "propstream":
            state = "IMPORT ONLY - PropStream exports are imported as CSV (no licensed API connected)"
        elif p is None:
            state = "NOT BUILT - confirm the vendor, account and licensed API rights first"
        elif configured:
            state = "KEY PRESENT - enable and budget it on Providers & Controls"
        else:
            state = "NOT CONNECTED - no key; nothing is called"
        out.append({"key": key, "label": label, "adapter": p is not None or key == "propstream",
                    "configured": configured, "state": state, "paid": True,
                    "env": list(env), "managed_at": "/wholesale/evosense/controls"})
    return out


def investor_report_html(rep: Dict[str, Any]) -> str:
    import html as H
    o = rep["opportunity"]
    p = o.get("property") or {}

    def esc(v):
        return H.escape("" if v is None else str(v))

    def money(v):
        return "$" + format(int(v), ",") if isinstance(v, (int, float)) else "Not stated"
    addr = ", ".join(x for x in (p.get("line1"), p.get("city"), p.get("state"), p.get("zip_code")) if x)
    facts = [("Asking price", money(o.get("asking_price")))]
    if "arv" in o:
        facts.append(("Estimated after-repair value", money(o.get("arv"))))
    if "estimated_repairs" in o:
        facts.append(("Estimated repairs", money(o.get("estimated_repairs"))))
    for k in ("property_type", "bedrooms", "bathrooms", "square_feet", "year_built", "lot_size_sqft", "occupancy_status"):
        if p.get(k) not in (None, ""):
            facts.append((k.replace("_", " ").capitalize(), p.get(k)))
    cl = o.get("closing") or {}
    for k, lbl in (("target_close", "Target close"), ("closing_date", "Closing date"), ("title_company", "Title company")):
        if cl.get(k):
            facts.append((lbl, cl[k]))
    css = ("body{font-family:Inter,Arial,sans-serif;color:#0f172a;max-width:820px;margin:24px auto;padding:0 16px}"
           "h1{font-family:Georgia,serif;margin:0 0 4px}h2{font-size:14px;text-transform:uppercase;letter-spacing:.06em;"
           "color:#13294b;margin-top:22px}table{width:100%;border-collapse:collapse;font-size:14px}"
           "td{padding:6px;border-bottom:1px solid #eef2f7}.m{color:#64748b;font-size:12px}")
    body = ["<h1>%s</h1><p class=m>Deal Opportunity Report &middot; %s</p>" % (esc(addr or "Opportunity"),
                                                                               esc(rep["generated_at"][:10])),
            "<h2>Summary</h2><p>%s</p>" % esc(o.get("summary") or "No summary provided."),
            "<h2>Key facts</h2><table>%s</table>" % "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(a), esc(b))
                                                          for a, b in facts)]
    if o.get("condition_summary"):
        body.append("<h2>Condition</h2><p>%s</p>" % esc(o["condition_summary"]))
    if o.get("disclosure"):
        d = o["disclosure"]
        body.append("<h2>Disclosure</h2><p class=m>%s</p>" % esc(d.get("text") if isinstance(d, dict) else d))
    body.append("<p class=m>%s</p>" % esc(rep["notice"]))
    return "<!doctype html><html><head><meta charset=utf-8><title>Deal Opportunity Report</title><style>%s</style>" \
           "</head><body>%s</body></html>" % (css, "".join(body))
