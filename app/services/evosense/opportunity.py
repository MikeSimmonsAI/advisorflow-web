"""ONE opportunity view for every property, whatever list or source it came from.

It does not replace the existing scores; it reads them and puts them side by
side with the things they never covered, each kept separate:

    SELLER OPPORTUNITY  scoring.property_opportunity (unchanged, versioned)
    URGENCY             how soon something must happen (a sale date, a tax sale,
                        a promised follow-up, a seller's own timeline)
    ECONOMICS           what assignment spread the numbers SUPPORT - through the
                        one MAO formula (wholesale_analysis.calculate_offer) and
                        the workspace's own settings; never a guess
    EVIDENCE CONFIDENCE verified / partial / insufficient - separate from the score
    ENGAGEMENT          what the owner actually said (seller_intent), if anything

plus the categories the evidence supports, ONE next action that points into
the existing workflow, and the workflow stage the property is in.

PRIORITY is a rules-based blend with documented weights (WEIGHTS). It is NOT a
probability of contract or closing - there is no validated model for that.
A dimension that is UNKNOWN (no supportable economics, no conversation yet) is
left out and the remaining weights are re-normalised: unknown is never scored
as zero, and the property is marked "needs underwriting" instead.

Every function below `assess` is pure (records in, dict out); `refresh` loads
what they need for a whole batch in a fixed number of queries and caches the
result on the property only when it changed.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from app.services.evosense import common as C

VERSION = "opportunity/v3"
# v2 = v1 + a deadline-only hand-off is not a conversation; a test deal never
# changes a real property. The version is part of the cache key, so a new
# version re-assesses every property on the next backfill passes.
# v3 = v2 + MEASURES: auction urgency, deal potential and data confidence kept
# as three separate answers (HIGH / MEDIUM / LOW / UNKNOWN with a reason each),
# the score explanation from the engine's own factors, and the missing inputs.

# ── documented weights ──────────────────────────────────────────────────────
# Priority = weighted average of the KNOWN dimensions (0-100 each).
WEIGHTS = {"seller_opportunity": 45, "urgency": 25, "economics": 15, "engagement": 15}
WEIGHT_NOTES = {
    "seller_opportunity": "Property Opportunity score (distress evidence, equity, ownership; scoring.py)",
    "urgency": "Days to a verified sale / tax sale, a promised follow-up, the seller's own timeline",
    "economics": "Assignment spread the numbers support at a real price (asking, contract, buyer offer)",
    "engagement": "Seller intent from what the owner actually said (scoring.seller_intent)",
}

# urgency by days to a scheduled foreclosure sale (verified clerk notice)
SALE_URGENCY = ((7, 100), (14, 92), (30, 80), (45, 65), (90, 45))
SALE_URGENCY_FAR = 25
CLAIM_FACTOR = 0.6          # a sale date only an imported list claims
CONFLICT_FACTOR = 0.8       # dates disagree: still urgent, but flagged
NOT_FOUND_FACTOR = 0.4      # the list claims a date the clerk record does not show
ESCALATE_DAYS = 14          # a verified sale this close goes to a person

# economics score from a spread grounded on a REAL price
SPREAD_POINTS = ((40000, 100), (30000, 85), (20000, 70), (10000, 50), (5000, 30), (1, 15))

NEEDS_UNDERWRITING = "Potential fee not yet supportable"

CONF_VERIFIED, CONF_PARTIAL, CONF_INSUFFICIENT = "verified", "partial", "insufficient"

CATEGORY_LABELS = {
    "foreclosure_auction": "Foreclosure auction (verified notice)",
    "pre_foreclosure": "Pre-foreclosure",
    "tax_sale": "Tax-sale candidate",
    "tax_delinquent": "Tax delinquent",
    "vacant": "Vacant",
    "tired_landlord": "Tired landlord",
    "absentee": "Absentee owner",
    "inherited_probate": "Inherited / probate",
    "code_violation": "Code violation",
    "distressed_condition": "Distressed condition",
    "long_held": "Long-held",
    "high_equity": "High equity",
}
SIGNAL_CATEGORY = {
    "TAX_DELINQUENT": "tax_delinquent", "TAX_SUIT": "tax_delinquent", "TAX_SALE": "tax_sale",
    "VACANT": "vacant", "TIRED_LANDLORD": "tired_landlord",
    "ABSENTEE_OWNER": "absentee", "OUT_OF_STATE_OWNER": "absentee",
    "PROBATE": "inherited_probate", "ESTATE": "inherited_probate",
    "CODE_VIOLATION": "code_violation", "CODE_COMPLAINT": "code_violation",
    "DISTRESSED_CONDITION": "distressed_condition", "CDU_POOR": "distressed_condition",
    "LONG_OWNERSHIP": "long_held", "FREE_AND_CLEAR": "high_equity",
}
HIGH_EQUITY_PCT = 40

# the operator's workflow (directive order) mapped from the existing buckets
WORKFLOW = ("DISCOVERED", "VERIFIED", "CONTACT_DATA_READY", "OUTREACH_ELIGIBILITY_CHECK", "OUTREACH_QUEUED",
            "CONTACT_ATTEMPTED", "RESPONDED", "FOLLOW_UP", "NOT_INTERESTED", "HUMAN_HANDOFF", "APPOINTMENT",
            "ANALYSIS", "OFFER", "CONTRACT", "DISPOSITION", "CLOSED", "DO_NOT_CONTACT")
WORKFLOW_LABELS = {k: k.replace("_", " ").capitalize() for k in WORKFLOW}

# next actions: key -> (button label, where it lives)
ACTIONS = {
    "verify_owner": "Verify Owner",
    "resolve_owner": "Resolve Owner",
    "confirm_notice": "Confirm Notice",
    "review_auction_status": "Review Auction Status",
    "confirm_heirs": "Confirm Heirs / Probate",
    "find_contact": "Find Contact",
    "review_outreach_eligibility": "Review Outreach Eligibility",
    "start_conversation": "Start Permitted Conversation",
    "escalate_deadline": "Human Review - Sale Soon",
    "read_reply": "Read Reply",
    "call_seller": "Call Interested Seller",
    "follow_up": "Follow Up",
    "schedule": "Schedule Appointment",
    "obtain_comps": "Obtain Sold Comps",
    "estimate_repairs": "Request Photos / Estimate Repairs",
    "prepare_offer": "Prepare Offer",
    "request_approval": "Request Approval",
    "match_buyers": "Match Buyers",
    "manage_disposition": "Manage Disposition",
    "view_deal": "View Deal",
    "monitor": "Monitor",
    "do_not_contact": "Do Not Contact",
    "gather_evidence": "Gather Evidence",
}


def _f(points, label, source=None, value=None, weight=None, at=None) -> Dict[str, Any]:
    return {"points": points, "label": label, "source": source, "value": value, "weight": weight,
            "at": at}


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat() + "Z"
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def _money(v) -> Optional[Decimal]:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace("$", "").replace(",", ""))
    except Exception:  # noqa: BLE001
        return None


def _i(v) -> Optional[int]:
    return None if v is None else int(round(float(v)))


def _fmt(v) -> str:
    return "$" + format(int(v), ",") if v is not None else "-"


# ── URGENCY ─────────────────────────────────────────────────────────────────

def _sale_points(days: int) -> int:
    for limit, pts in SALE_URGENCY:
        if days <= limit:
            return pts
    return SALE_URGENCY_FAR


def urgency(fc: Dict[str, Any], tax: Dict[str, Any], *, followup_due: Optional[datetime] = None,
            seller_quick: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """How soon must someone act? The strongest single reason wins; every
    reason is listed. A claim nobody verified counts at a fraction."""
    now = now or C.now()
    parts: List[Dict[str, Any]] = []
    deadline = None
    flags: List[str] = []
    st = (fc or {}).get("status")
    days = (fc or {}).get("days")
    sale = (fc or {}).get("sale_date")
    src = (fc or {}).get("document") or (fc or {}).get("source")
    if st == "VERIFIED NOTICE" and days is not None and days >= 0:
        parts.append(_f(_sale_points(days), "Foreclosure sale %s - %d day%s away (verified notice %s)" % (
            sale, days, "" if days == 1 else "s", src or ""), "clerk notice", days, at=sale))
        deadline = sale
        if (fc.get("reposted_for") or []):
            flags.append("possible_postponement")
    elif st == "CONFLICT":
        flags.append("notice_conflict")
        if days is not None and days >= 0:
            parts.append(_f(int(_sale_points(days) * CONFLICT_FACTOR),
                            "Sale %s - dates disagree between the list and the clerk notice" % sale,
                            "clerk notice vs list", days, at=sale))
            deadline = sale
    elif st == "CLAIMED" and days is not None and days >= 0:
        flags.append("unconfirmed_notice")
        parts.append(_f(int(_sale_points(days) * CLAIM_FACTOR),
                        "Sale %s claimed by an imported list - not yet verified (%d days)" % (sale, days),
                        "imported list (claim)", days, at=sale))
        deadline = sale
    elif st == "NO NOTICE FOUND" and fc.get("claimed_sale_date"):
        flags.append("unconfirmed_notice")
        from app.services.evosense import scoring as SC
        d = SC._sale_date(fc["claimed_sale_date"])
        cd = (d - now.date()).days if d else None
        if cd is not None and cd >= 0:
            parts.append(_f(int(_sale_points(cd) * NOT_FOUND_FACTOR),
                            "List claims a %s sale; the clerk record shows no notice for it" % fc["claimed_sale_date"],
                            "imported list (claim)", cd, at=fc["claimed_sale_date"]))
            deadline = fc["claimed_sale_date"]
    elif st == "POSTPONED":
        flags.append("possible_postponement")
        parts.append(_f(30, "Sale reported postponed - a new date may be posted", "clerk notice"))
    elif st == "SALE PASSED":
        flags.append("sale_date_passed")
        parts.append(_f(10, "Sale date %s has passed - sold, cancelled or postponed is unknown" % sale, "clerk notice"))
    elif st in ("CANCELED", "RESOLVED"):
        flags.append("sale_cancelled_or_resolved")
    tst = (tax or {}).get("status")
    if tst == "TAX SALE":
        parts.append(_f(70, "Tax foreclosure sale on the calendar", tax.get("source")))
    elif tst == "TAX LAWSUIT":
        parts.append(_f(40, "Tax lawsuit filed", tax.get("source")))
    elif tst == "DELINQUENT":
        parts.append(_f(20, "Taxes delinquent", tax.get("source")))
    if followup_due is not None:
        late = followup_due <= now
        parts.append(_f(60 if late else 35, "Promised follow-up %s %s" % (
            "was due" if late else "due", followup_due.strftime("%b %d")), "follow-up", at=_iso(followup_due)))
    if seller_quick:
        parts.append(_f(55, "Seller said they want to sell quickly: “%s”" % seller_quick[:80],
                        "seller stated"))
    value = max([p["points"] for p in parts], default=0)
    parts.sort(key=lambda p: -p["points"])
    escalate = bool(st == "VERIFIED NOTICE" and days is not None and 0 <= days <= ESCALATE_DAYS)
    return {"value": value, "factors": parts, "deadline": deadline, "flags": flags, "escalate": escalate,
            "label": "none" if value == 0 else "critical" if value >= 85 else "high" if value >= 60
            else "medium" if value >= 30 else "low"}


# ── ECONOMICS ───────────────────────────────────────────────────────────────

def _spread_points(spread: Optional[Decimal]) -> Optional[int]:
    if spread is None:
        return None
    for limit, pts in SPREAD_POINTS:
        if spread >= limit:
            return pts
    return 0


def economics(*, arv: Optional[Dict[str, Any]], repairs: Optional[Dict[str, Any]], settings,
              repairs_accepted: bool = True, asking=None, contract_price=None, best_buyer_offer=None,
              realized_fee=None, closed: bool = False, deal_fee=None,
              foreclosure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Assignment-spread intelligence, kept honest.

        investor price = ARV x investor% - repairs - transaction costs
        MAO            = investor price - desired assignment fee      (calculate_offer)
        spread at P    = investor price - P

    `arv` = {value, low, high, verified: bool, source}; `repairs` = {base, low,
    high, status, source}. Without a VERIFIED ARV and an ACCEPTED repair figure
    there is no number - only what is needed. The five kinds of spread are kept
    apart and never added together; an estimate is never called revenue."""
    from app.services import wholesale_analysis as WA
    pct = _money(getattr(settings, "investor_percentage", None))
    fee = _money(deal_fee) if deal_fee is not None else _money(getattr(settings, "default_wholesale_fee", None))
    tc_pct = getattr(settings, "transaction_cost_percent", 0) or 0
    tc_flat = getattr(settings, "transaction_cost_flat", 0) or 0
    assumptions = {"investor_percentage": float(pct) if pct is not None else None,
                   "assignment_fee_target": float(fee) if fee is not None else None,
                   "transaction_cost_percent": float(tc_pct), "transaction_cost_flat": float(tc_flat),
                   "source": "Wholesale settings (this workspace) - a buy-box assumption, not a market rule"}
    needs: List[str] = []
    a = arv or {}
    r = repairs or {}
    arv_v = _money(a.get("value")) if a.get("verified") else None
    if arv_v is None:
        needs.append("Verified ARV from closed comparable sales (an appraisal district tax value or a modelled "
                     "estimate is never an ARV)")
    rep_v = _money(r.get("base"))
    if rep_v is None:
        needs.append("Repair estimate (photos, a walkthrough or an itemized estimate)")
    elif not repairs_accepted:
        needs.append("A repair estimate this workspace accepts (the only figure now is %s)"
                     % (r.get("status") or "a rough system estimate").replace("_", " ").lower())
    if pct is None:
        needs.append("Investor percentage in Wholesale settings")

    out: Dict[str, Any] = {"status": "not_supportable", "headline": NEEDS_UNDERWRITING, "needs": needs,
                           "assumptions": assumptions, "scenarios": None, "target": None, "sensitivity": None,
                           "spreads": {}, "score": None, "basis": None,
                           "arv": {"value": _i(arv_v), "verified": bool(arv_v is not None), "source": a.get("source"),
                                   "low": _i(_money(a.get("low"))) if arv_v is not None else None,
                                   "high": _i(_money(a.get("high"))) if arv_v is not None else None},
                           "repairs": {"base": _i(rep_v), "low": _i(_money(r.get("low"))),
                                       "high": _i(_money(r.get("high"))), "status": r.get("status"),
                                       "source": r.get("source"), "accepted": bool(repairs_accepted)},
                           "foreclosure": foreclosure_money(foreclosure)}

    realized = _money(realized_fee)
    if closed and realized is not None:
        out["spreads"]["realized"] = {"value": _i(realized), "label": "Closed - assignment fee collected",
                                      "kind": "realized"}

    if needs:
        # a recorded contract or buyer offer is still worth showing without an ARV
        cp, bo = _money(contract_price), _money(best_buyer_offer)
        if cp is not None and bo is not None:
            out["spreads"]["buyer_offered"] = {"value": _i(bo - cp), "kind": "buyer_offered",
                                               "label": "Best buyer offer %s - contract %s" % (_fmt(bo), _fmt(cp))}
        if out["spreads"]:
            out["status"] = "partial"
            best = out["spreads"].get("realized") or out["spreads"].get("buyer_offered")
            out["score"] = _spread_points(_money(best["value"]))
            out["headline"] = "%s: %s (ARV not verified)" % (best["label"], _fmt(best["value"]))
        return out

    def investor_price(av: Decimal, rv: Decimal) -> Optional[Decimal]:
        c = WA.calculate_offer(av, rv, pct, fee, tc_pct, tc_flat)
        if c.get("mao") is None:
            return None
        return Decimal(str(c["mao"])) + (fee or 0)

    arv_lo = _money(a.get("low")) or arv_v
    arv_hi = _money(a.get("high")) or arv_v
    rep_lo = _money(r.get("low")) if r.get("low") is not None else rep_v
    rep_hi = _money(r.get("high")) if r.get("high") is not None else rep_v
    sc = {}
    for name, av, rv in (("low", arv_lo, rep_hi), ("base", arv_v, rep_v), ("high", arv_hi, rep_lo)):
        ip = investor_price(av, rv)
        sc[name] = {"arv": _i(av), "repairs": _i(rv), "investor_price": _i(ip),
                    "max_acquisition": _i(ip - (fee or 0)) if ip is not None else None}
    out["scenarios"] = sc
    base_ip = _money(sc["base"]["investor_price"])
    out["target"] = {"max_acquisition": sc["base"]["max_acquisition"], "assignment_fee_target": _i(fee),
                     "investor_purchase_price": sc["base"]["investor_price"],
                     "label": "Pay at most %s to leave a %s assignment fee at an investor price of %s" % (
                         _fmt(sc["base"]["max_acquisition"]), _fmt(fee), _fmt(base_ip))}
    sens = []
    for d_arv in (-10, -5, 0, 5, 10):
        for d_rep in (20, 0, -20):
            av = arv_v * (1 + Decimal(d_arv) / 100)
            rv = rep_v * (1 + Decimal(d_rep) / 100)
            ip = investor_price(av, rv)
            sens.append({"arv_change_pct": d_arv, "repairs_change_pct": d_rep,
                         "max_acquisition": _i(ip - (fee or 0)) if ip is not None else None})
    out["sensitivity"] = sens

    def spread(price, kind, label):
        p = _money(price)
        if p is None or base_ip is None:
            return
        lo = _money(sc["low"]["investor_price"])
        hi = _money(sc["high"]["investor_price"])
        out["spreads"][kind] = {"value": _i(base_ip - p), "low": _i(lo - p) if lo is not None else None,
                                "high": _i(hi - p) if hi is not None else None, "kind": kind,
                                "label": label % _fmt(p)}
    spread(asking, "hypothetical", "At the seller's stated price %s (hypothetical)")
    spread(contract_price, "contracted", "At the contract price %s (estimated - no buyer price agreed)")
    cp, bo = _money(contract_price), _money(best_buyer_offer)
    if cp is not None and bo is not None:
        out["spreads"]["buyer_offered"] = {"value": _i(bo - cp), "kind": "buyer_offered",
                                           "label": "Best buyer offer %s - contract %s" % (_fmt(bo), _fmt(cp))}
    out["status"] = "supportable"
    order = ("realized", "buyer_offered", "contracted", "hypothetical")
    best = next((out["spreads"][k] for k in order if k in out["spreads"]), None)
    if best is not None:
        out["basis"] = best["kind"]
        out["score"] = _spread_points(_money(best["value"]))
        out["headline"] = "%s: %s" % (best["label"], _fmt(best["value"]))
        if "low" in best and best.get("low") is not None:
            out["headline"] += " (range %s to %s)" % (_fmt(best["low"]), _fmt(best["high"]))
    else:
        out["basis"] = "underwritten"
        out["headline"] = "Underwritten: %s" % out["target"]["label"]
        out["needs"] = ["The seller's price (asking or contract) to measure a spread against"]
    return out


def foreclosure_money(fc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The foreclosure numbers that are NOT interchangeable, each labelled with
    what is actually on file. Nothing is inferred: an original mortgage amount
    is not a payoff, and no lien balance or opening bid is invented."""
    if not fc:
        return None
    unknown = "unknown - not on file"
    return {"property_value": fc.get("appraisal") and {"value": fc["appraisal"],
                                                        "label": "Appraisal district tax value (reference, not ARV)"},
            "estimated_equity": ({"pct": fc.get("equity_pct"), "basis": fc.get("equity_basis")}
                                 if fc.get("equity_pct") is not None else unknown),
            "mortgage_balance": ({"value": fc.get("list_unpaid_balance"),
                                  "label": "Claimed by the imported list - unverified"}
                                 if fc.get("list_unpaid_balance") is not None else unknown),
            "payoff": unknown, "arrears": unknown, "liens": unknown, "opening_bid": unknown,
            "taxes": fc.get("tax_status") or unknown}


# ── EVIDENCE CONFIDENCE ─────────────────────────────────────────────────────

def evidence_confidence(*, owner: Dict[str, Any], verified_distress: List[str], claimed_only: List[str],
                        arv_verified: bool, has_value: bool, conflicts: List[str]) -> Dict[str, Any]:
    f: List[Dict[str, Any]] = []
    ost = owner.get("status")
    f.append(_f(None, {"VERIFIED": "Owner confirmed on an official county record",
                       "UNVERIFIED": "Owner not yet confirmed on an official record",
                       "CONFLICT": "Sources disagree about the current owner",
                       "NO OWNER": "No owner of record found"}.get(ost, "Owner unknown"),
                ", ".join(owner.get("sources") or []) or None))
    if verified_distress:
        f.append(_f(None, "Distress verified on a public record: %s" % ", ".join(verified_distress)))
    if claimed_only:
        f.append(_f(None, "Claimed only (not yet verified): %s" % ", ".join(claimed_only)))
    f.append(_f(None, "Verified ARV from closed sales" if arv_verified else
                "Value: appraisal district tax value only" if has_value else "No value evidence"))
    for c in conflicts:
        f.append(_f(None, "Conflict: %s" % c))
    if ost in ("NO OWNER",) or (not verified_distress and not claimed_only and not has_value):
        label = CONF_INSUFFICIENT
    elif ost == "VERIFIED" and verified_distress and not conflicts and (arv_verified or has_value):
        label = CONF_VERIFIED
    else:
        label = CONF_PARTIAL
    return {"label": label, "factors": f}


# ── ENGAGEMENT ──────────────────────────────────────────────────────────────

def engagement(seller_intent: Optional[int], eng_status: Optional[str], outcome: Optional[str]) -> Dict[str, Any]:
    if seller_intent is None and eng_status in (None, "pending", "blocked"):
        return {"value": None, "label": "no conversation yet", "status": eng_status}
    v = seller_intent
    if v is None and eng_status == "responded":
        v = 50
    if v is None:
        v = 0 if eng_status == "stopped" else None
    label = {None: "awaiting reply"}.get(v) if v is None else (
        "interested" if v >= 60 else "some interest" if v >= 30 else "not interested" if v == 0 else "low")
    if outcome:
        label += " (%s)" % outcome.replace("_", " ").lower()
    return {"value": v, "label": label, "status": eng_status}


# ── PRIORITY ────────────────────────────────────────────────────────────────

def priority(dims: Dict[str, Optional[int]], *, cap: Optional[int] = None,
             cap_reason: Optional[str] = None) -> Dict[str, Any]:
    known = {k: v for k, v in dims.items() if v is not None and k in WEIGHTS}
    if dims.get("seller_opportunity") is None:
        return {"value": None, "label": "needs evidence", "parts": [],
                "why": "No Property Opportunity score yet (insufficient evidence) - not ranked"}
    total_w = sum(WEIGHTS[k] for k in known)
    parts = []
    acc = 0.0
    for k in WEIGHTS:
        v = dims.get(k)
        if v is None:
            parts.append({"dimension": k, "value": None, "weight": WEIGHTS[k], "effective_weight": 0,
                          "contribution": None, "note": "unknown - left out, not scored as zero"})
            continue
        ew = WEIGHTS[k] / total_w
        acc += v * ew
        parts.append({"dimension": k, "value": v, "weight": WEIGHTS[k], "effective_weight": round(ew * 100, 1),
                      "contribution": round(v * ew, 1), "note": WEIGHT_NOTES[k]})
    value = int(round(acc))
    if cap is not None and value > cap:
        value = cap
        parts.append({"dimension": "cap", "value": cap, "note": cap_reason})
    return {"value": value, "parts": parts,
            "label": "top" if value >= 70 else "high" if value >= 55 else "medium" if value >= 35 else "low",
            "why": "; ".join("%s %s x %s%%" % (p["dimension"].replace("_", " "), p["value"], p["effective_weight"])
                             for p in parts if p.get("contribution") is not None)}


# ── CATEGORIES ──────────────────────────────────────────────────────────────

def categories(signal_types: Iterable[str], fc: Dict[str, Any], tax: Dict[str, Any], owner_flags: Iterable[str],
               equity_pct: Optional[int]) -> List[str]:
    out = set()
    for s in signal_types:
        if s in SIGNAL_CATEGORY:
            out.add(SIGNAL_CATEGORY[s])
    st = (fc or {}).get("status")
    if st == "VERIFIED NOTICE" and (fc.get("days") or -1) >= 0:
        out.add("foreclosure_auction")
        out.add("pre_foreclosure")
    elif st in ("CLAIMED", "CONFLICT", "POSTPONED", "NO NOTICE FOUND") and (
            st != "NO NOTICE FOUND" or fc.get("claimed_sale_date")):
        out.add("pre_foreclosure")
    if (tax or {}).get("status") == "TAX SALE":
        out.add("tax_sale")
    if (tax or {}).get("status") in ("TAX SALE", "TAX LAWSUIT", "DELINQUENT"):
        out.add("tax_delinquent")
    if set(owner_flags or ()) & {"ESTATE_INDICATED", "LIFE_ESTATE", "HEIRS"}:
        out.add("inherited_probate")
    if equity_pct is not None and equity_pct >= HIGH_EQUITY_PCT:
        out.add("high_equity")
    return sorted(out, key=list(CATEGORY_LABELS).index)


# ── NEXT ACTION + WORKFLOW STATE ────────────────────────────────────────────

def _act(key: str, why: str, link: Optional[str] = None) -> Dict[str, Any]:
    return {"key": key, "label": ACTIONS[key], "why": why, "link": link}


def workflow_state(*, status: Optional[str], owner_status: str, contactability: Optional[str],
                   eng_status: Optional[str], outcome: Optional[str], has_handoff: bool,
                   deal: Optional[Dict[str, Any]]) -> str:
    if deal is not None:
        stage = (deal.get("stage") or "").lower()
        if stage in ("closed",):
            return "CLOSED"
        if deal.get("contract_price") is not None or "contract" in stage:
            return "DISPOSITION" if deal.get("buyer_activity") else "CONTRACT"
        if deal.get("offer_made"):
            return "OFFER"
        if "appointment" in stage or deal.get("appointment"):
            return "APPOINTMENT"
        return "ANALYSIS"
    if status == C.S_SUPPRESSED or outcome == C.O_DNC:
        return "DO_NOT_CONTACT"
    if has_handoff:                       # (a deadline-only escalation is not a conversation)
        return "HUMAN_HANDOFF"
    if outcome == C.O_APPOINTMENT:
        return "APPOINTMENT"
    if outcome in (C.O_HARD_NO, C.O_ALREADY_SOLD, C.O_NOT_OWNER, C.O_WRONG_PERSON, C.O_LISTED):
        return "NOT_INTERESTED"
    if eng_status == "nurture" or outcome in (C.O_NOT_NOW, C.O_CALL_LATER, C.O_FAMILY, C.O_MAYBE):
        return "FOLLOW_UP"
    if eng_status == "responded":
        return "RESPONDED"
    if eng_status == "active":
        return "CONTACT_ATTEMPTED"
    if status == C.S_READY:
        return "OUTREACH_QUEUED"
    if contactability in ("CONTACTABLE_SMS", "CONTACTABLE_EMAIL", "CONTACTABLE_OTHER", "CONTACT_DATA_FOUND",
                          "REVIEW_REQUIRED"):
        return "OUTREACH_ELIGIBILITY_CHECK" if contactability != "CONTACT_DATA_FOUND" else "CONTACT_DATA_READY"
    if owner_status == "VERIFIED":
        return "VERIFIED"
    return "DISCOVERED"


def next_action(*, prop_id: str, wstate: str, owner: Dict[str, Any], fc: Dict[str, Any], urg: Dict[str, Any],
                contacts: Dict[str, Any], contactability: Optional[str], status: Optional[str],
                existing_next: Optional[str], econ: Dict[str, Any], deal: Optional[Dict[str, Any]],
                followup_due: Optional[datetime], opportunity: Optional[int], now: datetime) -> Dict[str, Any]:
    plink = "/wholesale/evosense/property/%s" % prop_id
    if deal is not None:
        dlink = "/wholesale/deals/%s" % deal["id"]
        if wstate == "CLOSED":
            return _act("view_deal", "Closed", dlink)
        if wstate in ("CONTRACT",):
            return _act("match_buyers", "Under contract - find and match buyers", dlink + "?tab=buyers")
        if wstate == "DISPOSITION":
            return _act("manage_disposition", "Buyers are engaged - track offers", dlink + "?tab=closing")
        if not econ["arv"]["verified"]:
            return _act("obtain_comps", "No verified ARV - add closed comparable sales", dlink + "?tab=underwriting")
        if econ["repairs"]["base"] is None or not econ["repairs"]["accepted"]:
            return _act("estimate_repairs", "Repairs not estimated well enough for an offer",
                        dlink + "?tab=underwriting")
        if deal.get("offer_pending"):
            return _act("request_approval", "Offer waiting for approval", dlink + "?tab=offer")
        return _act("prepare_offer", "ARV and repairs are in - prepare the offer", dlink + "?tab=offer")
    if wstate == "DO_NOT_CONTACT":
        return _act("do_not_contact", "The owner asked not to be contacted - honoured everywhere", plink)
    if wstate == "HUMAN_HANDOFF":
        return _act("call_seller", existing_next or "A person needs to take this conversation", plink)
    if wstate == "RESPONDED":
        return _act("read_reply", "The owner replied", plink)
    if wstate == "APPOINTMENT":
        return _act("schedule", "The owner asked for an appointment", plink)
    ost = owner.get("status")
    if ost == "CONFLICT":
        return _act("resolve_owner", "Sources disagree about who owns it", plink)
    if ost != "VERIFIED":
        return _act("verify_owner", "Owner not confirmed on an official county record", plink)
    fst = (fc or {}).get("status")
    if fst == "CONFLICT":
        return _act("confirm_notice", "The list and the clerk notice disagree on the sale date", plink)
    if fst in ("CLAIMED",) or (fst == "NO NOTICE FOUND" and fc.get("claimed_sale_date")):
        return _act("confirm_notice", "Sale date only claimed by a list - confirm the clerk notice", plink)
    if fst in ("SALE PASSED", "POSTPONED"):
        return _act("review_auction_status", "Find out whether the sale happened, moved or was cancelled", plink)
    keys = {f.get("key") for f in owner.get("flags") or []}
    if keys & {"ESTATE_INDICATED", "LIFE_ESTATE"}:
        return _act("confirm_heirs", "Owner record suggests an estate - confirm who can sell", plink)
    if followup_due is not None and followup_due <= now + timedelta(days=1):
        return _act("follow_up", "Follow-up due %s" % followup_due.strftime("%b %d"), plink)
    if wstate == "FOLLOW_UP":
        return _act("follow_up", existing_next or "Owner asked to be contacted later", plink)
    if urg.get("escalate") and wstate in ("VERIFIED", "CONTACT_DATA_READY", "OUTREACH_ELIGIBILITY_CHECK",
                                         "DISCOVERED"):
        no_contact = not (contacts.get("phones") or contacts.get("emails"))
        return _act("escalate_deadline", "Verified sale in %s days - a person decides the approach now%s"
                    % ((fc or {}).get("days"), "; no phone or email for the owner yet (Find Contact)"
                       if no_contact else ""), plink)
    if wstate == "OUTREACH_QUEUED":
        return _act("start_conversation", "Contact is permitted on a channel", plink)
    if wstate == "CONTACT_ATTEMPTED":
        return _act("follow_up", "Waiting for a reply", plink)
    if not (contacts.get("phones") or contacts.get("emails")):
        if (opportunity or 0) < 35 and not urg.get("value"):
            return _act("monitor", "Low opportunity and no deadline - keep watching", plink)
        return _act("find_contact", "No phone or email for the owner yet", plink)
    if contactability != "CONTACTABLE_SMS" and contactability != "CONTACTABLE_EMAIL":
        return _act("review_outreach_eligibility",
                    "Contact data on file, but no channel is permitted yet (consent / do-not-call / validation)",
                    plink)
    return _act("start_conversation", existing_next or "Ready to contact", plink)


# ── ASSESS: one property, everything above ──────────────────────────────────

def assess(*, prop, po: Dict[str, Any], fc: Dict[str, Any], tax: Dict[str, Any], owner: Dict[str, Any],
           contacts: Dict[str, Any], signal_types: List[str], verified_distress: List[str], claimed_only: List[str],
           settings, arv: Dict[str, Any], repairs: Dict[str, Any], repairs_accepted: bool,
           asking=None, seller_quick: Optional[str] = None, eng: Optional[Dict[str, Any]] = None,
           has_handoff: bool = False, followup_due: Optional[datetime] = None,
           deal: Optional[Dict[str, Any]] = None, list_claims: Optional[Dict[str, Any]] = None,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or C.now()
    eng = eng or {}
    urg = urgency(fc, tax, followup_due=followup_due, seller_quick=seller_quick, now=now)
    fmoney = {"appraisal": getattr(prop, "appraisal_value", None), "equity_pct": prop.equity_pct,
              "equity_basis": prop.equity_basis,
              "list_unpaid_balance": (list_claims or {}).get("list_unpaid_balance"),
              "tax_status": (tax or {}).get("status")} if (fc or {}).get("status") not in (None, "NO EVIDENCE") \
        or (tax or {}).get("status") not in (None, "NOT CHECKED", "CURRENT") else None
    econ = economics(arv=arv, repairs=repairs, settings=settings, repairs_accepted=repairs_accepted,
                     asking=asking, contract_price=(deal or {}).get("contract_price"),
                     best_buyer_offer=(deal or {}).get("best_buyer_offer"),
                     realized_fee=(deal or {}).get("realized_fee"), closed=bool((deal or {}).get("closed")),
                     deal_fee=(deal or {}).get("fee"), foreclosure=fmoney)
    conflicts = []
    if owner.get("status") == "CONFLICT":
        conflicts.append("current owner")
    if (fc or {}).get("status") == "CONFLICT":
        conflicts.append("foreclosure sale date")
    if getattr(prop, "has_conflicts", False):
        conflicts.append("property facts")
    conf = evidence_confidence(owner=owner, verified_distress=verified_distress, claimed_only=claimed_only,
                               arv_verified=econ["arv"]["verified"],
                               has_value=econ["arv"]["verified"] or getattr(prop, "appraisal_value", None) is not None,
                               conflicts=conflicts)
    en = engagement(getattr(prop, "seller_intent", None), eng.get("status"), eng.get("outcome"))
    wstate = workflow_state(status=prop.status, owner_status=owner.get("status") or "", contactability=getattr(
        prop, "contactability", None), eng_status=eng.get("status"), outcome=eng.get("outcome"),
        has_handoff=has_handoff, deal=deal)
    cap, cap_reason = None, None
    if wstate == "DO_NOT_CONTACT":
        cap, cap_reason = 15, "Owner asked not to be contacted"
    elif wstate == "NOT_INTERESTED":
        cap, cap_reason = 25, "Owner said no / not the owner / already sold"
    elif (fc or {}).get("status") in ("CANCELED", "RESOLVED") and not verified_distress:
        cap, cap_reason = 30, "Foreclosure cancelled or resolved, no other verified distress"
    pr = priority({"seller_opportunity": po.get("value"), "urgency": urg["value"],
                   "economics": econ["score"], "engagement": en["value"]}, cap=cap, cap_reason=cap_reason)
    cats = categories(signal_types, fc, tax, [f.get("key") for f in owner.get("flags") or []], prop.equity_pct)
    nxt = next_action(prop_id=prop.id, wstate=wstate, owner=owner, fc=fc, urg=urg, contacts=contacts,
                      contactability=getattr(prop, "contactability", None), status=prop.status,
                      existing_next=prop.next_action, econ=econ, deal=deal, followup_due=followup_due,
                      opportunity=po.get("value"), now=now)
    flags = list(urg["flags"])
    if owner.get("status") in ("NO OWNER", "UNVERIFIED", "CONFLICT"):
        flags.append("missing_ownership" if owner.get("status") != "CONFLICT" else "owner_conflict")
    if not (contacts.get("phones") or contacts.get("emails")):
        flags.append("missing_contacts")
    if not econ["arv"]["verified"]:
        flags.append("missing_valuation")
    if fmoney is not None:
        flags += ["unknown_liens", "unknown_payoff"]
    earned = [f for f in po.get("factors") or [] if (f.get("points") or 0) > 0][:4]
    meas = measures(prop=prop, po=po, fc=fc, econ=econ, conf=conf, contacts=contacts, owner=owner, nxt=nxt, now=now)
    return {
        "version": VERSION, "computed_at": _iso(now), "measures": meas,
        "priority": pr,
        "seller_opportunity": {"value": po.get("value"), "label": po.get("label"), "version": po.get("version"),
                               "top_factors": [{"points": f["points"], "label": f["label"]} for f in earned]},
        "urgency": urg, "economics": econ, "confidence": conf, "engagement": en,
        "categories": [{"key": c, "label": CATEGORY_LABELS[c]} for c in cats],
        "next_action": nxt, "workflow_state": {"key": wstate, "label": WORKFLOW_LABELS[wstate]},
        "flags": sorted(set(flags)),
        "contacts": contacts, "owner_status": owner.get("status"),
        "foreclosure": {"status": (fc or {}).get("status"), "sale_date": (fc or {}).get("sale_date"),
                        "days": (fc or {}).get("days"), "document": (fc or {}).get("document")},
        "tax": {"status": (tax or {}).get("status")},
        "note": "Priority is a rules-based blend with documented weights - not a probability of contract or "
                "closing. Unknown dimensions are left out, never scored as zero.",
    }


# ── MEASURES: three separate answers, never one number ─────────────────────
# AUCTION URGENCY  how soon a foreclosure sale is scheduled, and how sure we are
# DEAL POTENTIAL   what the SUPPORTED numbers say the deal is worth - unknown when
#                  there are no numbers (unknown is not "bad", and never "good")
# DATA CONFIDENCE  how much verified evidence the assessment rests on
URGENT_DAYS = ESCALATE_DAYS          # a verified sale this close is HIGH urgency, whatever the numbers
SOON_DAYS = 30
NON_RESIDENTIAL = ("multifamily", "commercial", "industrial", "land", "agricultural", "mixed_use", "specialty")
_COHORT_TO_EVENT = {"VERIFIED NOTICE": "notice_verified", "CLAIMED": "claimed", "NO NOTICE FOUND": "not_found",
                    "CONFLICT": "conflict", "POSTPONED": "postponed", "CANCELED": "canceled",
                    "RESOLVED": "reported_resolved", "SALE PASSED": "outcome_unverified"}


def _day(v) -> Optional[date]:
    if not v:
        return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def auction_urgency(status: Optional[str], sale_date=None, *, today: Optional[date] = None) -> Dict[str, Any]:
    """`status`: an auction-event status (foreclosure_cycles.STATUS keys) or a
    notice status from the Top 100 view ("VERIFIED NOTICE" ...). Only a VERIFIED
    clerk notice is ever HIGH; an imported date is never treated as one; a sale
    date that passed is never called completed."""
    today = today or C.now().date()
    st = _COHORT_TO_EVENT.get(status or "", status or "")
    d = _day(sale_date)
    days = (d - today).days if d else None
    when = d.isoformat() if d else "date not read"

    def out(level, label, reason, urgent=False):
        return {"level": level, "label": label, "reason": reason, "days": days,
                "sale_date": d.isoformat() if d else None, "urgent": urgent, "status": st or None}
    if st == "notice_verified":
        if days is None:
            return out("MEDIUM", "MEDIUM", "Verified clerk notice; the sale date could not be read", False)
        if days < 0:
            return out("PASSED", "SALE DATE PASSED - OUTCOME NOT VERIFIED",
                       "The %s sale date has passed; sold, canceled or postponed is not yet verified" % when)
        lvl = "HIGH" if days <= URGENT_DAYS else "MEDIUM" if days <= SOON_DAYS else "LOW"
        return out(lvl, lvl, "Verified clerk notice: sale %s, %d day%s away" % (when, days, "" if days == 1 else "s"),
                   lvl == "HIGH")
    if st == "conflict":
        soon = days is not None and 0 <= days <= SOON_DAYS
        return out("VERIFY", "URGENT - VERIFY NOTICE" if soon else "VERIFY NOTICE",
                   "The clerk notice and the list disagree about the sale (%s); check the notice before acting" % when,
                   soon)
    if st == "claimed":
        return out("UNVERIFIED", "UNVERIFIED DATE - VERIFY THE NOTICE",
                   "Only an imported list says %s; no clerk notice has confirmed it yet" % when)
    if st == "not_found":
        return out("UNVERIFIED", "NO CLERK NOTICE FOUND - VERIFY",
                   "The list claims a sale (%s) but the clerk records read show no notice for it" % when)
    if st == "postponed":
        return out("POSTPONED", "POSTPONED - NOT SCHEDULED FOR %s" % when.upper(),
                   "Recorded as postponed; watch for a new notice")
    if st == "canceled":
        return out("CANCELED", "CANCELED", "Sale recorded as canceled")
    if st in ("sold_at_auction", "reverted_to_lender"):
        return out("COMPLETED", "AUCTION HELD - %s" % st.replace("_", " ").upper(), "Outcome recorded with evidence")
    if st == "outcome_unverified":
        return out("PASSED", "SALE DATE PASSED - OUTCOME NOT VERIFIED",
                   "The %s sale date has passed; what happened is not yet verified" % when)
    if st == "reported_resolved":
        return out("VERIFY", "REPORTED RESOLVED - VERIFY", "Reported resolved; not yet verified")
    if st == "awaiting_notice":
        return out("UNVERIFIED", "WAITING FOR A NEW NOTICE", "Carried forward; no new notice yet")
    if st == "no_longer_on_record":
        return out("VERIFY", "OFF THE NOTICE RECORD - REVIEW", "The notice is no longer on the clerk record")
    return out("NONE", "NO SALE ON RECORD", "No foreclosure sale on record")


def deal_potential(econ: Dict[str, Any], po: Dict[str, Any], category: Optional[str]) -> Dict[str, Any]:
    """From SUPPORTED numbers only. The opportunity score's distress indicators
    are shown beside it as indicators - they are not a profit estimate."""
    v = po.get("value")
    indicators = {"score": v, "reading": None if v is None else "strong" if v >= 65 else
                  "moderate" if v >= 45 else "limited",
                  "note": "Seller-distress and equity indicators from the opportunity score - not a profit estimate"}
    score, needs = econ.get("score"), list(econ.get("needs") or [])
    if score is not None:
        lvl = "HIGH" if score >= 70 else "MEDIUM" if score >= 40 else "LOW"
        return {"level": lvl, "label": lvl, "reason": econ.get("headline"), "assessed": True,
                "needs": needs, "indicators": indicators}
    if category in NON_RESIDENTIAL:
        from app.services.evosense import property_class as PCL
        kind = PCL.CATEGORY_LABELS.get(category, category)
        return {"level": "INSUFFICIENT", "label": "INSUFFICIENTLY ASSESSED",
                "reason": "No validated %s model: the house-style analysis is not used. Enter the %s numbers "
                          "(Property type & details) to assess it." % (kind.lower(), kind.lower()),
                "assessed": False, "needs": needs, "indicators": indicators}
    if econ.get("status") == "supportable":
        return {"level": "UNKNOWN", "label": "MAX OFFER KNOWN - NEEDS THE SELLER'S PRICE",
                "reason": econ.get("headline"), "assessed": False, "needs": needs, "indicators": indicators}
    return {"level": "UNKNOWN", "label": "POTENTIAL UNKNOWN - RESEARCH REQUIRED",
            "reason": "No supportable profit estimate yet - unknown, not bad. Needs: %s"
                      % ("; ".join(n.split(" (")[0] for n in needs) or "underwriting"),
            "assessed": False, "needs": needs, "indicators": indicators}


def data_confidence_measure(conf: Dict[str, Any]) -> Dict[str, Any]:
    lvl = {CONF_VERIFIED: "HIGH", CONF_PARTIAL: "MEDIUM", CONF_INSUFFICIENT: "LOW"}.get(conf.get("label"), "LOW")
    reasons = [f["label"] for f in conf.get("factors") or []][:3]
    return {"level": lvl, "label": lvl, "reason": "; ".join(reasons)}


_GROUPS = (("PRE_FORECLOSURE", "Verified foreclosure"), ("_SALE", "Auction proximity"),
           ("ABSENTEE_OWNER", "Absentee owner"), ("OUT_OF_STATE_OWNER", "Absentee owner"),
           ("_EQUITY", "Equity"), ("LONG_OWNERSHIP", "Ownership length"), ("_SOURCES", "Independent sources"))


def _group_of(f: Dict[str, Any]) -> str:
    ev = f.get("evidence") or {}
    label = f.get("label") or ""
    if (f.get("points") or 0) < 0:
        return "Adjustments"
    if ev.get("days") is not None or label.startswith("Foreclosure sale scheduled"):
        return "Auction proximity"
    if label.startswith("Estimated equity"):
        return "Equity"
    if label.startswith("Evidence from"):
        return "Independent sources"
    sig = ev.get("signal")
    if sig == "PRE_FORECLOSURE":
        return "Verified foreclosure" if "clerk / trustee notice" in label else "Foreclosure (claimed)"
    for key, name in _GROUPS:
        if sig == key:
            return name
    return "Other supported indicators"


def explain(po: Dict[str, Any]) -> Dict[str, Any]:
    """WHY THIS SCORE - the engine's own factors, grouped. Points are exactly
    the engine's; nothing is added here."""
    order = ["Verified foreclosure", "Foreclosure (claimed)", "Auction proximity", "Absentee owner", "Equity",
             "Ownership length", "Independent sources", "Other supported indicators", "Adjustments"]
    groups: Dict[str, Dict[str, Any]] = {}
    not_scored = []
    raw = 0
    for f in po.get("factors") or []:
        pts = int(f.get("points") or 0)
        if pts == 0:
            not_scored.append(f.get("label"))
            continue
        raw += pts
        g = groups.setdefault(_group_of(f), {"points": 0, "items": []})
        g["points"] += pts
        g["items"].append({"points": pts, "label": f.get("label")})
    lines = [{"group": k, **groups[k]} for k in order if k in groups]
    total = po.get("value")
    note = None
    if total is not None and raw != total:
        note = ("The factors add to %d; the score is %d (kept between 0 and 100, or capped by the strategy)"
                % (raw, total))
    return {"score": total, "label": po.get("label"), "version": po.get("version"), "lines": lines,
            "not_scored": [x for x in not_scored if x][:8], "factor_total": raw, "note": note,
            "scale": "0-100. 80+ hot, 65-79 high, 45-64 medium, under 45 low. A distress-and-evidence score - "
                     "not a profit estimate."}


def missing_inputs(*, econ: Dict[str, Any], contacts: Dict[str, Any], owner: Dict[str, Any], fc: Dict[str, Any],
                   category: Optional[str]) -> List[str]:
    out = []
    if not (econ.get("arv") or {}).get("verified"):
        out.append("Missing verified comparable sales")
    if (econ.get("repairs") or {}).get("base") is None:
        out.append("Missing repair estimate")
    if (fc or {}).get("status") not in (None, "NO EVIDENCE"):
        out.append("Missing payoff or arrears")
    if not ((contacts or {}).get("phones") or (contacts or {}).get("emails")):
        out.append("Owner contact unavailable")
    if (owner or {}).get("status") in ("NO OWNER", "UNVERIFIED", "CONFLICT"):
        out.append("Ownership requires review")
    if not category or category == "unknown":
        out.append("Property type needs review")
    return out


def measures(*, prop, po: Dict[str, Any], fc: Dict[str, Any], econ: Dict[str, Any], conf: Dict[str, Any],
             contacts: Dict[str, Any], owner: Dict[str, Any], nxt: Dict[str, Any],
             now: Optional[datetime] = None) -> Dict[str, Any]:
    cat = getattr(prop, "property_category", None)
    au = auction_urgency((fc or {}).get("status"), (fc or {}).get("sale_date") or (fc or {}).get("claimed_sale_date"),
                         today=(now or C.now()).date())
    return {"auction_urgency": au, "deal_potential": deal_potential(econ, po, cat),
            "data_confidence": data_confidence_measure(conf),
            "next_best_action": {"label": (nxt or {}).get("label"), "why": (nxt or {}).get("why"),
                                 "key": (nxt or {}).get("key"), "link": (nxt or {}).get("link")},
            "explanation": explain(po),
            "missing": missing_inputs(econ=econ, contacts=contacts, owner=owner, fc=fc, category=cat)}


# ── batch loading + cache ───────────────────────────────────────────────────

def _deal_view(db, org_id: str, deal_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Promoted deals: the economics inputs and stage, in a fixed number of queries.
    Repairs go through wholesale_repairs.view per deal (bounded: only promoted
    properties, a small set)."""
    if not deal_ids:
        return {}
    from app.models.wholesale_models import (WholesaleApproval, WholesaleBuyerOutreach, WholesaleDeal,
                                             WholesaleOffer)
    from app.services import mao_gate
    from app.services import wholesale_repairs as REP
    from app.services import wholesale_service as svc
    settings = svc.resolve_settings(db, org_id, commit=False)
    deals = db.query(WholesaleDeal).filter(WholesaleDeal.organization_id == org_id,
                                           WholesaleDeal.id.in_(deal_ids)).all()
    offers: Dict[str, Decimal] = {}
    activity: Dict[str, int] = {}
    for did, amt in (db.query(WholesaleBuyerOutreach.deal_id, WholesaleBuyerOutreach.offer_amount)
                     .filter(WholesaleBuyerOutreach.organization_id == org_id,
                             WholesaleBuyerOutreach.deal_id.in_(deal_ids)).all()):
        activity[did] = activity.get(did, 0) + 1
        if amt is not None and (did not in offers or amt > offers[did]):
            offers[did] = amt
    made = {did for (did,) in db.query(WholesaleOffer.deal_id).filter(
        WholesaleOffer.organization_id == org_id, WholesaleOffer.deal_id.in_(deal_ids),
        WholesaleOffer.status.in_(("presented", "countered", "accepted"))).all()}
    pending = {did for (did,) in db.query(WholesaleApproval.deal_id).filter(
        WholesaleApproval.organization_id == org_id, WholesaleApproval.deal_id.in_(deal_ids),
        WholesaleApproval.kind == "offer", WholesaleApproval.status == "pending").all()}
    out = {}
    for d in deals:
        try:
            rep = REP.view(db, org_id, d)
        except Exception:  # noqa: BLE001
            rep = {"amount": d.repair_estimate, "status": d.repair_status}
        try:
            detail = json.loads(d.arv_detail) if d.arv_detail else {}
        except (TypeError, ValueError):
            detail = {}
        gate = mao_gate.evaluate(d, settings)
        arv_ok = any(c.get("code") == "ARV" and c.get("ok") for c in gate.get("checks") or [])
        out[d.id] = {"id": d.id, "stage": d.stage, "contract_price": d.contract_price,
                     "best_buyer_offer": offers.get(d.id), "buyer_activity": activity.get(d.id, 0),
                     "realized_fee": d.wholesale_fee_collected if d.wholesale_fee_collected is not None
                     else (d.assignment_fee if d.stage == "closed" else None),
                     "closed": d.stage == "closed", "fee": d.desired_wholesale_fee,
                     "offer_made": d.id in made, "offer_pending": d.id in pending,
                     "arv": {"value": d.arv, "low": (detail or {}).get("low"), "high": (detail or {}).get("high"),
                             # the MAO gate's own ARV check: engine at the workspace's minimum
                             # confidence, or entered by a person when the policy allows it
                             "verified": arv_ok, "source": d.arv_source},
                     "repairs": {"base": rep.get("amount"), "low": rep.get("low"), "high": rep.get("high"),
                                 "status": rep.get("status"), "source": rep.get("label")},
                     "repairs_accepted": (rep.get("status") or "UNKNOWN") in
                     mao_gate.policy_for(settings)["accepted_repair_statuses"],
                     "is_test": bool(d.is_test)}
    return out


def build(db, org_id: str, props: List[Any], *, now: Optional[datetime] = None,
          pre: Optional[Dict[str, Any]] = None, checks: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """Assess a batch: about two dozen queries whatever its size. A caller that
    already loaded the list's records (cohort.rows) passes them in."""
    from collections import defaultdict
    from app.models.evosense_models import (EvoSenseEngagement, EvoSenseFact, EvoSenseFollowUp,
                                            EvoSenseHandoff)
    from app.models.wholesale_models import WholesaleComp
    from app.services import mao_gate
    from app.services import wholesale_service as svc
    from app.services.evosense import cohort as CO
    from app.services.evosense import economics as EC
    from app.services.evosense import scoring as SC
    from app.services.evosense import signals as SIG
    from app.services.evosense import valuation as VAL
    now = now or C.now()
    if not props:
        return {}
    ids = [p.id for p in props]
    pre = pre if pre is not None else CO._prefetch(db, org_id, props)
    checks = checks if checks is not None else CO.clerk_checks(db, org_id, ids)
    settings = svc.resolve_settings(db, org_id, commit=False)
    accepted = mao_gate.policy_for(settings)["accepted_repair_statuses"]
    facts: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for f in (db.query(EvoSenseFact).filter(EvoSenseFact.organization_id == org_id,
                                            EvoSenseFact.property_id.in_(ids),
                                            EvoSenseFact.superseded.is_(False),
                                            EvoSenseFact.fact_type.in_(("asking_price", "condition",
                                                                        "wants_quick_close", "timeline")))
              .order_by(EvoSenseFact.created_at.asc()).all()):
        if f.truth_state in (C.T_SELLER_STATED, C.T_HUMAN):
            facts[f.property_id][f.fact_type] = f
    engs: Dict[str, Any] = {}
    for e in (db.query(EvoSenseEngagement).filter(EvoSenseEngagement.organization_id == org_id,
                                                  EvoSenseEngagement.property_id.in_(ids))
              .order_by(EvoSenseEngagement.updated_at.asc()).all()):
        engs[e.property_id] = e
    # an open hand-off is a conversation for a person - unless all it says is
    # "a verified sale is close" (our own deadline escalation), which is not a
    # conversation and must not read as one
    handoffs = set()
    for pid, reasons in db.query(EvoSenseHandoff.property_id, EvoSenseHandoff.reasons).filter(
            EvoSenseHandoff.organization_id == org_id, EvoSenseHandoff.property_id.in_(ids),
            EvoSenseHandoff.status.in_(("open", "acknowledged"))).all():
        codes = {r.get("code") for r in (C.jload(reasons, []) or [])}
        if codes - {DEADLINE_REASON}:
            handoffs.add(pid)
    fus: Dict[str, datetime] = {}
    for pid, due in (db.query(EvoSenseFollowUp.property_id, EvoSenseFollowUp.due_at)
                     .filter(EvoSenseFollowUp.organization_id == org_id, EvoSenseFollowUp.property_id.in_(ids),
                             EvoSenseFollowUp.status == "due").all()):
        if pid not in fus or due < fus[pid]:
            fus[pid] = due
    with_comps = {pid for (pid,) in db.query(WholesaleComp.evosense_property_id.distinct()).filter(
        WholesaleComp.organization_id == org_id, WholesaleComp.evosense_property_id.in_(ids)).all()}
    deals = _deal_view(db, org_id, sorted({p.promoted_deal_id for p in props if p.promoted_deal_id}))
    out = {}
    for p in props:
        all_sigs = pre["signals"].get(p.id, [])
        sigs = [x for x in all_sigs if x.active]
        owner = CO._owner_status_pre(pre, p)
        tax = CO._tax_status_pre(pre, p, sigs)
        fc = CO.foreclosure_status(sigs, today=now.date(), check=checks.get(p.id))
        contacts = CO._contacts_pre(pre, owner.get("owner_id"))
        owners = CO._owners_of(pre, p)
        sid = p.best_strategy_id or p.first_strategy_id
        stacked = SIG.stack(all_sigs, now)
        po = SC.property_opportunity(p, stacked, pre["strategies"].get(sid) if sid else None,
                                     weights=pre["weights"], owner=owners[0] if owners else None,
                                     options=pre["options"])
        current = [s for s in stacked if s["freshness"] != SIG.STALE and s["signal_type"] not in SIG.NEVER_SCORED
                   and (SIG.CATALOG.get(s["signal_type"], {}).get("points", 0) > 0)]
        verified, claimed = [], []
        for s in current:
            lbl = SIG.CATALOG.get(s["signal_type"], {}).get("label", s["signal_type"])
            srcs = set(s.get("record_sources") or s.get("sources") or [])
            if srcs and srcs <= set(SC.CLAIM_SOURCES):
                claimed.append(lbl)
            elif s["signal_type"] == "PRE_FORECLOSURE" and fc.get("status") != "VERIFIED NOTICE":
                claimed.append(lbl)
            else:
                verified.append(lbl)
        f = facts.get(p.id, {})
        deal = deals.get(p.promoted_deal_id) if p.promoted_deal_id else None
        if deal is not None and deal.get("is_test") and not p.is_test:
            deal = None                    # a TEST deal never changes a real property's money or stage
        if deal is not None:
            arv_in, rep_in, rep_ok = deal["arv"], deal["repairs"], deal["repairs_accepted"]
        else:
            a = VAL.arv(db, p) if p.id in with_comps else {"value": None}
            rank = {"low": 1, "medium": 2, "high": 3}
            arv_ok = a.get("value") is not None and rank.get(a.get("confidence") or "", 0) >= rank.get(
                mao_gate.policy_for(settings)["min_arv_confidence"], 2)
            arv_in = {"value": a.get("value"), "verified": arv_ok,
                      "source": a.get("method") or a.get("label")}
            cond = f.get("condition")
            rep_base = (EC.REPAIR_PER_SQFT[cond.value] * int(p.square_feet)
                        if cond is not None and p.square_feet and cond.value in EC.REPAIR_PER_SQFT else None)
            rep_in = {"base": rep_base, "status": "SYSTEM_ESTIMATE" if rep_base is not None else None,
                      "source": "seller-stated condition x $/sq ft" if rep_base is not None else None}
            rep_ok = rep_base is not None and "SYSTEM_ESTIMATE" in accepted
        e = engs.get(p.id)
        asking = f.get("asking_price")
        quick = f.get("wants_quick_close") or f.get("timeline")
        out[p.id] = assess(prop=p, po=po, fc=dict(fc, county=p.county), tax=tax, owner=owner, contacts=contacts,
                           signal_types=[x.signal_type for x in sigs], verified_distress=verified,
                           claimed_only=claimed, settings=settings, arv=arv_in, repairs=rep_in,
                           repairs_accepted=rep_ok, asking=asking.value if asking is not None else None,
                           seller_quick=(quick.quote or quick.value) if quick is not None and (
                               quick.fact_type == "wants_quick_close" or str(quick.value or "").lower()
                               in ("asap", "30_days", "immediately")) else None,
                           eng={"status": e.status, "outcome": e.last_outcome} if e is not None else None,
                           has_handoff=p.id in handoffs, followup_due=fus.get(p.id), deal=deal,
                           list_claims=CO._list_claims_pre(pre, p), now=now)
    return out


def _hash(res: Dict[str, Any]) -> str:
    body = {k: v for k, v in res.items() if k != "computed_at"}
    return VERSION + ":" + hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:20]


def cache(prop, res: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Store the summary columns and the detail. True when anything changed
    (an unchanged result only moves `opportunity_at`)."""
    prop.opportunity_at = now or C.now()
    h = _hash(res)
    if getattr(prop, "opportunity_hash", None) == h:
        return False
    econ = res["economics"]
    best = next((econ["spreads"][k] for k in ("realized", "buyer_offered", "contracted", "hypothetical")
                 if k in (econ.get("spreads") or {})), None)
    prop.opportunity_hash = h
    prop.priority_score = res["priority"]["value"]
    prop.urgency_score = res["urgency"]["value"]
    prop.economics_status = econ["status"]
    prop.spread_base = best["value"] if best else None
    prop.evidence_confidence = res["confidence"]["label"]
    prop.opportunity_categories = ("," + ",".join(c["key"] for c in res["categories"]) + ",") \
        if res["categories"] else None
    prop.next_best_action = res["next_action"]["key"]
    prop.workflow_state = res["workflow_state"]["key"]
    dl = res["urgency"].get("deadline")
    from app.services.evosense import scoring as SC
    prop.sale_deadline = SC._sale_date(dl) if dl else None
    prop.opportunity_detail = json.dumps(res, default=str)
    return True


def refresh(db, org_id: str, props: List[Any], *, now: Optional[datetime] = None) -> Dict[str, int]:
    """Assess and cache a batch. Idempotent: an unchanged result writes nothing
    but the timestamp. A property whose records trip the assessment is skipped
    (and logged), never allowed to stop the batch."""
    now = now or C.now()
    stats = {"assessed": 0, "changed": 0, "failed": 0}
    try:
        results = build(db, org_id, props, now=now)
    except Exception:  # noqa: BLE001 - fall back to one at a time so one bad record is isolated
        db.rollback()
        results = {}
        for p in props:
            try:
                results.update(build(db, org_id, [p], now=now))
            except Exception:  # noqa: BLE001
                db.rollback()
                stats["failed"] += 1
                C.log.exception("opportunity: property %s could not be assessed", p.id)
    for p in props:
        res = results.get(p.id)
        if res is None:
            continue
        stats["assessed"] += 1
        if cache(p, res, now):
            stats["changed"] += 1
    try:
        stats["escalated"] = escalate(db, props, results)
    except Exception:  # noqa: BLE001 - the view is cached even if a hand-off cannot be opened
        C.log.exception("opportunity: deadline escalation failed")
    return stats


ESCALATE_STATES = ("DISCOVERED", "VERIFIED", "CONTACT_DATA_READY", "OUTREACH_ELIGIBILITY_CHECK", "OUTREACH_QUEUED")
DEADLINE_REASON = "AUCTION_DEADLINE"


def escalate(db, props: List[Any], results: Dict[str, Dict[str, Any]]) -> int:
    """A verified foreclosure sale within ESCALATE_DAYS, and no conversation
    under way: put it in front of a person (the existing NEEDS YOU hand-off).
    Once per sale date - a person who resolved it is not asked again."""
    from app.models.evosense_models import EvoSenseHandoff
    from app.services.evosense import evaluate as EV
    from app.services.evosense import handoff as HO
    due = [p for p in props if (results.get(p.id) or {}).get("urgency", {}).get("escalate")
           and results[p.id]["workflow_state"]["key"] in ESCALATE_STATES and not p.promoted_deal_id]
    if not due:
        return 0
    seen = {}
    for h in (db.query(EvoSenseHandoff).filter(EvoSenseHandoff.property_id.in_([p.id for p in due])).all()):
        for r in C.jload(h.reasons, []) or []:
            if r.get("code") == DEADLINE_REASON:
                seen.setdefault(h.property_id, set()).add(r.get("sale_date"))
    n = 0
    for p in due:
        res = results[p.id]
        sale = res["urgency"].get("deadline")
        if sale in seen.get(p.id, set()):
            continue
        h = HO.open_handoff(db, p, [DEADLINE_REASON], priority=90,
                            next_action="Sale %s - decide how to reach the owner (%s)" % (
                                sale, res["next_action"]["label"]))
        reasons = C.jload(h.reasons, []) or []
        for r in reasons:
            if r.get("code") == DEADLINE_REASON:
                r["sale_date"] = sale
        h.reasons = C.jdump(reasons)
        C.log_event(db, p.organization_id, "opportunity.escalated", property_id=p.id, is_test=p.is_test,
                    actor_type=C.ACTOR_AUTOMATION,
                    summary="Verified foreclosure sale %s within %s days - handed to a person" % (sale, ESCALATE_DAYS))
        EV.refresh_status(db, p)
        n += 1
    return n


STALE_AFTER = timedelta(hours=20)       # urgency moves with the calendar: re-assess daily
BACKFILL_BATCH = 500


def backfill(db, *, org_id: Optional[str] = None, limit: int = BACKFILL_BATCH, now: Optional[datetime] = None) -> int:
    """One bounded pass: properties never assessed, re-scored since, or not
    assessed today. Grouped by organization; committed per batch."""
    from sqlalchemy import or_
    from app.models.evosense_models import EvoSenseProperty
    now = now or C.now()
    q = db.query(EvoSenseProperty).filter(EvoSenseProperty.archived_at.is_(None),
                                          or_(EvoSenseProperty.opportunity_at.is_(None),
                                              EvoSenseProperty.opportunity_at < now - STALE_AFTER,
                                              EvoSenseProperty.opportunity_at < EvoSenseProperty.last_evaluated_at,
                                              EvoSenseProperty.opportunity_hash.is_(None),
                                              ~EvoSenseProperty.opportunity_hash.like(VERSION + ":%")))
    if org_id:
        q = q.filter(EvoSenseProperty.organization_id == org_id)
    rows = (q.with_entities(EvoSenseProperty.id, EvoSenseProperty.organization_id)
            .order_by(EvoSenseProperty.opportunity_at.asc().nullsfirst()).limit(limit).all())
    by_org: Dict[str, List[str]] = {}
    for pid, oid in rows:
        by_org.setdefault(oid, []).append(pid)
    n = 0
    for oid, group in by_org.items():
        for i in range(0, len(group), 100):
            # loaded per chunk: a commit expires loaded rows, and an expired row
            # reloads one query at a time
            chunk = db.query(EvoSenseProperty).filter(EvoSenseProperty.id.in_(group[i:i + 100])).all()
            try:
                refresh(db, oid, chunk, now=now)
                db.commit()
                n += len(chunk)
            except Exception:  # noqa: BLE001
                db.rollback()
                C.log.exception("opportunity backfill failed for %s properties in %s", len(chunk), oid)
    return n


def detail(prop) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(prop.opportunity_detail) if getattr(prop, "opportunity_detail", None) else None
    except (TypeError, ValueError):
        return None


def compact(prop) -> Optional[Dict[str, Any]]:
    """What a list row shows."""
    d = detail(prop)
    if d is None:
        return None
    econ = d["economics"]
    return {"priority": d["priority"]["value"], "priority_label": d["priority"]["label"],
            "priority_why": d["priority"].get("why"),
            "urgency": d["urgency"]["value"], "urgency_label": d["urgency"]["label"],
            "deadline": d["urgency"].get("deadline"),
            "economics_status": econ["status"], "economics_headline": econ["headline"],
            "economics_needs": econ.get("needs") or [],
            "confidence": d["confidence"]["label"], "engagement": d["engagement"]["label"],
            "categories": d["categories"], "next_action": d["next_action"],
            "workflow_state": d["workflow_state"], "flags": d.get("flags") or [],
            "top_factors": d["seller_opportunity"].get("top_factors") or [],
            "measures": {k: v for k, v in (d.get("measures") or {}).items() if k != "explanation"} or None}
