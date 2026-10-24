"""Cash buyer intelligence: who is a real buyer, and who to send THIS deal to first.

Three things, each from evidence that exists - never from what a buyer typed:

1. VERIFICATION TIER (per buyer)
     VERIFIED ACTIVE BUYER  evidence they BUY (closed with us, made an offer on one
                            of our deals, or a deed to them in the county records
                            inside the window) AND a contact that WORKS (they replied
                            to us, proof of funds on file and not expired, or a
                            person on the team checked their cash).
     POTENTIAL BUYER        one of the two, not both.
     UNVERIFIED CONTACT     neither. A typed "past deals" count is shown, never counted.

2. WHAT THEY HAVE SHOWN US (learning from real actions)
   ZIP codes, property types and prices of the deals they made offers on, were
   chosen for or closed - and the ones they passed on. Read off outreach rows
   and deals; no row, no lesson.

3. PRIORITY FOR THIS DEAL
   The buy-box fit score (wholesale_matching, unchanged) plus two bounded,
   itemized adjustments: the tier (+10 / 0 / -10) and what their history says
   about THIS deal (-15 .. +15). Every point has a sentence. The fit score is
   still shown on its own; the priority only orders the list.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

VERIFIED = "VERIFIED_ACTIVE_BUYER"
POTENTIAL = "POTENTIAL_BUYER"
UNVERIFIED = "UNVERIFIED_CONTACT"
TIER_LABELS = {VERIFIED: "Verified active buyer", POTENTIAL: "Potential buyer",
               UNVERIFIED: "Unverified contact"}
TIER_POINTS = {VERIFIED: 10, POTENTIAL: 0, UNVERIFIED: -10}
HISTORY_CAP = 15

_DEEDS = re.compile(r"(\d+)\s+deed\(s\)\s+to them in the last\s+(\d+)\s+months", re.I)


def county_evidence(buyer: Any) -> Optional[Dict[str, Any]]:
    """What the county roll import recorded about this buyer, if it was one."""
    src = (getattr(buyer, "source", None) or "")
    if not src.startswith("county_roll:"):
        return None
    m = _DEEDS.search(getattr(buyer, "source_detail", None) or "")
    deeds, months = (int(m.group(1)), int(m.group(2))) if m else (None, None)
    return {"county": src.split(":", 1)[1].title(), "recent_deeds": deeds, "window_months": months,
            "detail": getattr(buyer, "source_detail", None)}


def _pof_current(buyer: Any, today: Optional[date] = None) -> bool:
    if not getattr(buyer, "proof_of_funds_on_file", False):
        return False
    exp = getattr(buyer, "proof_of_funds_expires", None)
    if exp is None:
        return True
    if isinstance(exp, datetime):
        exp = exp.date()
    return exp >= (today or date.today())


def tier(buyer: Any, activity: Optional[Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    """{tier, label, buys: [...], contact: [...], missing: [...]} - every line is evidence."""
    a = activity or {}
    buys: List[str] = []
    contact: List[str] = []
    closed = int(a.get("deals_closed") or 0)
    offers = int(a.get("offers_made") or 0)
    chosen = int(a.get("selected_count") or 0)
    if closed:
        buys.append("Closed %d deal%s with you" % (closed, "" if closed == 1 else "s"))
    if offers:
        buys.append("Made %d offer%s on your deals" % (offers, "" if offers == 1 else "s"))
    if chosen and not closed:
        buys.append("Chosen as buyer on %d deal%s" % (chosen, "" if chosen == 1 else "s"))
    ce = county_evidence(buyer)
    if ce and ce["recent_deeds"]:
        buys.append("%s County records: %d deed%s to them in the last %d months"
                    % (ce["county"], ce["recent_deeds"], "" if ce["recent_deeds"] == 1 else "s",
                       ce["window_months"]))
    responded = int(a.get("responded") or 0)
    if responded:
        contact.append("Replied to %d deal sheet%s" % (responded, "" if responded == 1 else "s"))
    if _pof_current(buyer, today):
        exp = getattr(buyer, "proof_of_funds_expires", None)
        contact.append("Proof of funds on file%s" % (" (good until %s)" % exp if exp else ""))
    if getattr(buyer, "cash_verified", False):
        contact.append("Cash checked by your team")
    if buys and contact:
        key = VERIFIED
    elif buys or contact:
        key = POTENTIAL
    else:
        key = UNVERIFIED
    missing = []
    if not buys:
        missing.append("No buying evidence yet (an offer, a closing, or a recent deed in county records)")
    if not contact:
        missing.append("Contact not confirmed (no reply yet, no current proof of funds)")
    if not (getattr(buyer, "phone", None) or getattr(buyer, "email", None)):
        missing.append("No phone or email on file")
    claimed = int(getattr(buyer, "past_deals_count", 0) or 0)
    return {"tier": key, "label": TIER_LABELS[key], "buys": buys, "contact": contact, "missing": missing,
            "claimed_past_deals": claimed,
            "claimed_note": ("Says %d past deal%s - not counted as evidence." % (claimed, "" if claimed == 1 else "s"))
            if claimed else None}


# ── what their actions show ─────────────────────────────────────────────────

def revealed(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """`rows`: one per deal the buyer acted on - {zip, property_type, price, action}
    where action is offer / chosen / closed / passed. Pure."""
    took = [r for r in rows if r.get("action") in ("offer", "chosen", "closed")]
    passed = [r for r in rows if r.get("action") == "passed"]
    zips: Dict[str, int] = {}
    types: Dict[str, int] = {}
    prices: List[float] = []
    for r in took:
        if r.get("zip"):
            zips[r["zip"]] = zips.get(r["zip"], 0) + 1
        if r.get("property_type"):
            types[r["property_type"]] = types.get(r["property_type"], 0) + 1
        if r.get("price"):
            prices.append(float(r["price"]))
    passed_zips: Dict[str, int] = {}
    for r in passed:
        if r.get("zip"):
            passed_zips[r["zip"]] = passed_zips.get(r["zip"], 0) + 1
    return {"deals_acted_on": len(took), "deals_passed": len(passed), "zips": zips, "property_types": types,
            "price_low": min(prices) if prices else None, "price_high": max(prices) if prices else None,
            "passed_zips": passed_zips}


def history_fit(rev: Dict[str, Any], zip_code: Optional[str], property_type: Optional[str],
                price: Optional[float]) -> Dict[str, Any]:
    """Bounded points for THIS deal from what the buyer has done. Each line explains itself."""
    pts = 0
    lines: List[Dict[str, Any]] = []
    if not rev or not (rev.get("deals_acted_on") or rev.get("deals_passed")):
        return {"points": 0, "lines": [{"points": 0, "why": "No offers, picks or passes from this buyer on other deals yet - nothing learned."}]}
    n = rev["zips"].get(zip_code or "", 0)
    if n:
        p = min(8, 4 * n)
        pts += p
        lines.append({"points": p, "why": "Offered on, was chosen for, or bought %d other deal%s in ZIP %s" % (n, "" if n == 1 else "s", zip_code)})
    t = rev["property_types"].get(property_type or "", 0)
    if t:
        p = min(4, 2 * t)
        pts += p
        lines.append({"points": p, "why": "Acted on %d other %s deal%s" % (t, (property_type or "").replace("_", " "), "" if t == 1 else "s")})
    lo, hi = rev.get("price_low"), rev.get("price_high")
    if price and lo is not None:
        if lo * 0.8 <= price <= hi * 1.2:
            pts += 5
            lines.append({"points": 5, "why": "Price is within the range of their past offers ($%s - $%s)"
                          % (format(int(lo), ","), format(int(hi), ","))})
        elif price > hi * 1.5:
            pts -= 5
            lines.append({"points": -5, "why": "Price is well above anything they offered before (top $%s)" % format(int(hi), ",")})
    pz = rev["passed_zips"].get(zip_code or "", 0)
    if pz:
        p = -min(10, 5 * pz)
        pts += p
        lines.append({"points": p, "why": "Passed on %d deal%s in ZIP %s" % (pz, "" if pz == 1 else "s", zip_code)})
    pts = max(-HISTORY_CAP, min(HISTORY_CAP, pts))
    if not lines:
        lines.append({"points": 0, "why": "Their past deals do not tell us anything about this one."})
    return {"points": pts, "lines": lines}


def priority(fit_score: int, disqualified: bool, t: Dict[str, Any], h: Dict[str, Any]) -> Dict[str, Any]:
    tp = TIER_POINTS[t["tier"]]
    total = int(fit_score or 0) + tp + int(h["points"])
    explain = [{"points": int(fit_score or 0), "why": "Buy-box fit"},
               {"points": tp, "why": t["label"]}] + h["lines"]
    return {"score": max(0, min(125, total)) if not disqualified else 0, "explain": explain}


# ── DB glue ─────────────────────────────────────────────────────────────────

def history_rows(db, org_id: str, buyer_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """One row per (buyer, deal) action, read from outreach and deals."""
    from app.models.wholesale_models import WholesaleBuyerOutreach, WholesaleDeal, WholesaleProperty
    from app.services.wholesale_analysis import money
    out: Dict[str, List[Dict[str, Any]]] = {b: [] for b in buyer_ids}
    if not buyer_ids:
        return out
    outreach = (db.query(WholesaleBuyerOutreach)
                .filter(WholesaleBuyerOutreach.organization_id == org_id,
                        WholesaleBuyerOutreach.buyer_id.in_(buyer_ids)).all())
    closed = (db.query(WholesaleDeal)
              .filter(WholesaleDeal.organization_id == org_id,
                      WholesaleDeal.assigned_buyer_id.in_(buyer_ids),
                      WholesaleDeal.closed_at.isnot(None)).all())
    deal_ids = {r.deal_id for r in outreach} | {d.id for d in closed}
    deals = {d.id: d for d in db.query(WholesaleDeal).filter(WholesaleDeal.id.in_(deal_ids)).all()} if deal_ids else {}
    pids = {d.property_id for d in deals.values()}
    props = {p.id: p for p in db.query(WholesaleProperty).filter(WholesaleProperty.id.in_(pids)).all()} if pids else {}

    def row(deal, action, price):
        p = props.get(deal.property_id)
        return {"deal_id": deal.id, "zip": getattr(p, "zip_code", None),
                "property_type": getattr(p, "property_type", None), "price": price, "action": action}

    seen = set()
    for d in closed:
        price = money(getattr(d, "buyer_price", None)) or money(getattr(d, "contract_price", None))
        out[d.assigned_buyer_id].append(row(d, "closed", float(price) if price else None))
        seen.add((d.assigned_buyer_id, d.id))
    for r in outreach:
        d = deals.get(r.deal_id)
        if d is None or (r.buyer_id, d.id) in seen:
            continue
        amt = money(r.offer_amount)
        if amt is not None:
            action = "offer"
        elif bool(getattr(r, "is_selected", False)):
            action = "chosen"
        elif r.status in ("passed", "rejected"):
            action = "passed"
        else:
            continue
        out[r.buyer_id].append(row(d, action, float(amt) if amt is not None else None))
        seen.add((r.buyer_id, d.id))
    return out


def for_deal(db, org_id: str, deal: Any) -> Dict[str, Any]:
    """Every eligible buyer for this deal: fit, tier, history, priority - ranked."""
    from app.models.wholesale_models import WholesaleBuyer, WholesaleProperty
    from app.routers.wholesale_buyers_router import _buyer_activity
    from app.services import wholesale_matching as matching
    from app.services.wholesale_analysis import money
    prop = db.query(WholesaleProperty).filter(WholesaleProperty.id == deal.property_id).first()
    buyers = [b for b in db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id,
                                                         WholesaleBuyer.is_active.is_(True)).all()
              if bool(b.is_test) == bool(deal.is_test)]
    results = matching.match_deal_to_buyers(deal, prop, buyers)
    ids = [r["buyer"].id for r in results]
    activity = _buyer_activity(db, org_id, ids)
    hist = history_rows(db, org_id, ids)
    asking = money(getattr(deal, "buyer_room_asking_price", None)) or money(getattr(deal, "buyer_price", None))
    price = asking or money(getattr(deal, "max_allowable_offer", None))
    price = float(price) if price is not None else None
    items = []
    counts = {VERIFIED: 0, POTENTIAL: 0, UNVERIFIED: 0}
    for r in results:
        b = r["buyer"]
        t = tier(b, activity.get(b.id))
        counts[t["tier"]] += 1
        # learn from OTHER deals only - this deal's own offers must not vote for themselves
        rev = revealed([x for x in (hist.get(b.id) or []) if x.get("deal_id") != deal.id])
        h = history_fit(rev, getattr(prop, "zip_code", None), getattr(prop, "property_type", None), price)
        pr = priority(r["score"], r["disqualified"], t, h)
        items.append({
            "buyer_id": b.id, "name": b.company_name or b.contact_name or "(unnamed buyer)",
            "has_phone": bool(b.phone), "has_email": bool(b.email),
            "fit_score": r["score"], "fit_factors": r["factors"],
            "disqualified": r["disqualified"], "disqualified_reason": r["disqualified_reason"],
            "tier": t, "learned": rev, "history_fit": h, "priority": pr["score"], "explain": pr["explain"],
            "source": b.source,
        })
    items.sort(key=lambda x: (not x["disqualified"], x["priority"], x["fit_score"]), reverse=True)
    return {"deal_id": deal.id, "price_used": price,
            "price_note": "Asking price to buyers" if asking else
                          ("MAO (no asking price set yet)" if price else "No price yet - price fit not used"),
            "counts": counts, "buyers": items,
            "rules": {"tiers": TIER_LABELS, "tier_points": TIER_POINTS, "history_cap": HISTORY_CAP}}
