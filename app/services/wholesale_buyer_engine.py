"""BUYER INTELLIGENCE ENGINE - segments, three separate measurements, an honest
enrichment meter, and the one buyer list every Wholesale screen reads.

Built ON the existing buyer network (wholesale_buyers / wholesale_buy_boxes,
wholesale_buyer_intel tiers, wholesale_matching, the disposition desk). There is
no second buyer table: the evidence rows in wholesale_buyer_engine_models all
hang off `wholesale_buyers`.

THREE MEASUREMENTS, NEVER BLENDED
    Buyer match score   (wholesale_matching v2) how well ONE property fits what
                        the buyer stated or showed they buy. Per property.
    Buyer verification  evidence about WHO they are, WHAT they bought, whether
                        the CONTACT works and whether the BUY BOX is confirmed.
                        Separate dimensions with their own dates - no single enum.
    Buyer readiness     whether they can move NOW: stated buying status, funding
                        evidence (stated is not documented), closing record.

A buyer who owns ten properties is not automatically an active buyer, a 95
match is not money, and a registered agent is not a decision-maker - so each
fact is its own line with its own source and date, and stale facts expire.

ENRICHMENT tracks RESEARCH COMPLETENESS (five stages x 20%), not buyer quality
or the chance of a purchase. A failed or unavailable lookup earns nothing.

PURE FUNCTIONS for the rules (plain objects in, dicts out) so they are testable
without a database; the DB glue at the bottom loads everything in bulk, scoped
to one organization.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence

VERSION = "buyer_intel/v1"

# ── how long a fact stays good ─────────────────────────────────────────────
CONTACT_FRESH_DAYS = 180          # a verified phone/email older than this needs re-checking
BUYBOX_FRESH_DAYS = 365           # confirmed criteria older than this need re-confirming
STATED_BUYING_FRESH_DAYS = 90     # "I'm buying right now" goes stale fast
RECENT_PURCHASE_DAYS = 730        # a recorded acquisition in the last 24 months counts as recent
ACTIVE_PURCHASE_DAYS = 365        # ...and in the last 12 months as evidence of current activity

# ── taxonomy (stable keys; labels are presentation only) ────────────────────
SEGMENTS = [("all", "All buyers"), ("single_family", "Single-family"), ("multifamily", "Multifamily"),
            ("land", "Land"), ("commercial", "Commercial"), ("industrial", "Industrial"),
            ("mixed_use", "Mixed-use"), ("portfolio", "Portfolio"), ("other", "Other")]
SEGMENT_KEYS = [k for k, _ in SEGMENTS]
UNIT_BANDS = [("u5_19", "5–19 units", 5, 19), ("u20_49", "20–49 units", 20, 49),
              ("u50_99", "50–99 units", 50, 99), ("u100_199", "100–199 units", 100, 199),
              ("u200_299", "200–299 units", 200, 299), ("u300p", "300+ units", 300, None)]
SUBSEGMENTS: Dict[str, List[tuple]] = {
    "single_family": [("flip", "Flip / Renovate"), ("buy_hold", "Buy and Hold"),
                      ("rental_portfolio", "Rental Portfolio"), ("luxury", "Luxury"),
                      ("entry_level", "Entry-Level"), ("other", "Other")],
    "multifamily": [(k, label) for k, label, _lo, _hi in UNIT_BANDS],
    "land": [("residential_lots", "Residential Lots"), ("acreage", "Acreage"), ("infill", "Infill"),
             ("development", "Development"), ("agricultural", "Agricultural"),
             ("commercial_land", "Commercial Land")],
    "commercial": [("retail", "Retail"), ("office", "Office"), ("hospitality", "Hospitality"),
                   ("self_storage", "Self-Storage"), ("medical", "Medical"), ("mixed_use", "Mixed-Use"),
                   ("other", "Other")],
}
# price thresholds behind the two price-based single-family groups (shown in the UI)
LUXURY_MIN_PRICE = 750_000
ENTRY_MAX_PRICE = 250_000

# every property-type word the platform uses -> (segment, sub-segment or None)
TYPE_SEGMENT: Dict[str, tuple] = {}
for _w in ("residential", "single_family", "sfr", "house", "condo", "condominium", "townhouse", "townhome",
           "mobile_home", "manufactured_home", "duplex", "triplex", "fourplex", "duplex_fourplex"):
    TYPE_SEGMENT[_w] = ("single_family", None)
for _w in ("multifamily", "multi_family", "apartments", "small_apartment", "large_apartment",
           "student_housing", "senior_housing"):
    TYPE_SEGMENT[_w] = ("multifamily", None)
TYPE_SEGMENT.update({
    "land": ("land", None), "lot": ("land", "residential_lots"), "lots": ("land", "residential_lots"),
    "residential_lot": ("land", "residential_lots"), "vacant_lot": ("land", "residential_lots"),
    "infill_lot": ("land", "infill"), "development_land": ("land", "development"),
    "undeveloped_acreage": ("land", "acreage"), "acreage": ("land", "acreage"),
    "commercial_land": ("land", "commercial_land"),
    "agricultural": ("land", "agricultural"), "farm": ("land", "agricultural"), "ranch": ("land", "agricultural"),
    "pasture": ("land", "agricultural"), "timberland": ("land", "agricultural"), "ag_acreage": ("land", "agricultural"),
    "ag_improvements": ("land", "agricultural"),
    "commercial": ("commercial", None), "retail": ("commercial", "retail"), "shopping_center": ("commercial", "retail"),
    "restaurant": ("commercial", "retail"), "office": ("commercial", "office"),
    "medical_office": ("commercial", "medical"), "medical": ("commercial", "medical"),
    "hospitality": ("commercial", "hospitality"), "hotel": ("commercial", "hospitality"),
    "self_storage": ("commercial", "self_storage"),
    "industrial": ("industrial", None), "warehouse": ("industrial", None), "manufacturing": ("industrial", None),
    "distribution": ("industrial", None), "flex_industrial": ("industrial", None),
    "mixed_use": ("mixed_use", None), "residential_commercial": ("mixed_use", None),
    "retail_with_apartments": ("mixed_use", None), "live_work": ("mixed_use", None),
    "mobile_home_park": ("other", None), "rv_park": ("other", None), "specialty": ("other", None),
    "other_investment": ("other", None), "other": ("other", None),
    "portfolio": ("portfolio", None),
})


def _jl(raw: Any, default=None):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return default


def _lst(raw: Any) -> List[str]:
    v = _jl(raw, [])
    return [str(x).strip().lower() for x in v if str(x).strip()] if isinstance(v, list) else []


def _num(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _d(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _days(v: Any, today: date) -> Optional[int]:
    d = _d(v)
    return (today - d).days if d else None


def _iso(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


# ── segments ───────────────────────────────────────────────────────────────

def box_segments(box: Any) -> Dict[str, set]:
    """{segment: {sub-segments}} one buy box asks for. An empty result means the
    box names no property type (it is not "everything")."""
    out: Dict[str, set] = {}

    def put(seg: str, sub: Optional[str] = None):
        out.setdefault(seg, set())
        if sub:
            out[seg].add(sub)

    crit = _jl(getattr(box, "criteria", None), {}) or {}
    if not isinstance(crit, dict):
        crit = {}
    for t in _lst(getattr(box, "property_types", None)):
        seg = TYPE_SEGMENT.get(t)
        if seg:
            put(*seg)
        else:
            put("other")
    strategies = set(_lst(getattr(box, "strategies", None)))
    if "portfolio" in strategies or crit.get("portfolio"):
        put("portfolio")
    if "single_family" in out:
        sf = out["single_family"]
        if "flip" in strategies:
            sf.add("flip")
        if strategies & {"rental", "brrrr", "buy_hold", "hold"}:
            sf.add("buy_hold")
        if crit.get("portfolio") or "portfolio" in strategies:
            sf.add("rental_portfolio")
        for p in _lst(crit.get("sf_profile")):
            if p in dict(SUBSEGMENTS["single_family"]):
                sf.add(p)
        lo, hi = _num(getattr(box, "min_price", None)), _num(getattr(box, "max_price", None))
        if lo is not None and lo >= LUXURY_MIN_PRICE:
            sf.add("luxury")
        if hi is not None and hi <= ENTRY_MAX_PRICE:
            sf.add("entry_level")
    if "land" in out:
        for p in _lst(crit.get("land_types")):
            if p in dict(SUBSEGMENTS["land"]):
                out["land"].add(p)
        if (_num(getattr(box, "min_acres", None)) or 0) >= 5:
            out["land"].add("acreage")
    if "commercial" in out:
        for p in _lst(crit.get("commercial_types")):
            if p in dict(SUBSEGMENTS["commercial"]):
                out["commercial"].add(p)
    lo_u, hi_u = _num(getattr(box, "min_units", None)), _num(getattr(box, "max_units", None))
    if lo_u is not None or hi_u is not None:
        out.setdefault("multifamily", set())
        lo_u = lo_u if lo_u is not None else 5
        hi_u = hi_u if hi_u is not None else float("inf")
        for key, _label, b_lo, b_hi in UNIT_BANDS:
            if lo_u <= (b_hi if b_hi is not None else float("inf")) and hi_u >= b_lo:
                out["multifamily"].add(key)
    return out


def segments(boxes: Sequence[Any], acquisitions: Sequence[Any] = ()) -> Dict[str, Dict[str, Any]]:
    """{segment: {subs: [...], basis: [stated|file|records|inferred]}} for one buyer.

    Two kinds of basis, always kept apart: what the buyer (or their file) says
    they buy, and what the public record shows they bought. A buyer can belong
    to several segments; that is still ONE buyer record."""
    out: Dict[str, Dict[str, Any]] = {}
    for b in boxes:
        if not getattr(b, "is_active", True):
            continue
        src = (getattr(b, "source", None) or "").lower()
        basis = ("records" if src == "county_records" else "file" if src == "file"
                 else "stated" if src in ("stated", "ai_conversation", "staff") or getattr(b, "confirmed_at", None)
                 else "file")
        for seg, subs in box_segments(b).items():
            e = out.setdefault(seg, {"subs": set(), "basis": set()})
            e["subs"] |= subs
            e["basis"].add(basis)
    for a in acquisitions:
        kind = (getattr(a, "property_kind", None) or "").lower()
        if getattr(a, "evidence_class", "") == "CLAIMED_PURCHASE":
            continue
        seg = TYPE_SEGMENT.get(kind)
        if seg:
            e = out.setdefault(seg[0], {"subs": set(), "basis": set()})
            if seg[1]:
                e["subs"].add(seg[1])
            e["basis"].add("records")
    if not out:
        out["other"] = {"subs": set(), "basis": {"none"}}
    return {k: {"subs": sorted(v["subs"]), "basis": sorted(v["basis"])} for k, v in out.items()}


# ── verification dimensions ────────────────────────────────────────────────

def purchase_history(acquisitions: Sequence[Any], buyer: Any, activity: Optional[Dict[str, Any]],
                     today: date) -> Dict[str, Any]:
    acq = [a for a in acquisitions if getattr(a, "evidence_class", None)]
    recorded = [a for a in acq if a.evidence_class == "RECORDED_TITLE_TRANSFER"]
    verified = [a for a in acq if a.evidence_class in ("VERIFIED_PURCHASE", "VERIFIED_CASH_PURCHASE")]
    reported = [a for a in acq if a.evidence_class == "REPORTED_INVESTOR_PURCHASE"]
    claimed = [a for a in acq if a.evidence_class == "CLAIMED_PURCHASE"]
    dated = [(_d(getattr(a, "transfer_date", None)), a) for a in acq if a.evidence_class != "CLAIMED_PURCHASE"]
    dated = [(d, a) for d, a in dated if d]
    last = max((d for d, _ in dated), default=None)
    recent = [a for d, a in dated if (today - d).days <= RECENT_PURCHASE_DAYS]
    last_12 = [a for d, a in dated if (today - d).days <= ACTIVE_PURCHASE_DAYS]
    closed_here = int((activity or {}).get("deals_closed") or 0)
    from app.services.wholesale_buyer_intel import county_evidence
    ce = county_evidence(buyer)
    count_only = bool(ce and ce.get("recent_deeds")) and not recorded
    lines: List[str] = []
    if closed_here:
        lines.append("Closed %d deal%s with you" % (closed_here, "" if closed_here == 1 else "s"))
    if verified:
        lines.append("%d verified purchase%s" % (len(verified), "" if len(verified) == 1 else "s"))
    if recorded:
        lines.append("%d recorded title transfer%s to them (%d in the last 24 months)"
                     % (len(recorded), "" if len(recorded) == 1 else "s",
                        sum(1 for a in recent if a.evidence_class == "RECORDED_TITLE_TRANSFER")))
    if count_only:
        lines.append("%s County records counted %d deed(s) to them in %d months - the individual "
                     "records are not attached yet" % (ce["county"], ce["recent_deeds"], ce["window_months"] or 24))
    if reported:
        lines.append("%d purchase%s reported by your team" % (len(reported), "" if len(reported) == 1 else "s"))
    if claimed:
        lines.append("%d purchase%s claimed by them or their file - not counted as evidence"
                     % (len(claimed), "" if len(claimed) == 1 else "s"))
    if verified or closed_here:
        status = "VERIFIED"
    elif recent:
        status = "RECORDED"
    elif dated:
        status = "STALE"
    elif count_only:
        status = "COUNT_ONLY"
    elif claimed or (getattr(buyer, "purchase_evidence", None) or getattr(buyer, "past_deals_count", 0)):
        status = "CLAIMED"
    else:
        status = "NONE"
    label = {"VERIFIED": "Purchase history verified", "RECORDED": "Recorded acquisitions (public record)",
             "STALE": "No acquisition in the last 24 months", "COUNT_ONLY": "Deed count only - records not attached",
             "CLAIMED": "Claimed only", "NONE": "No purchase evidence"}[status]
    return {"status": status, "label": label, "lines": lines, "recent": len(recent), "last_12_months": len(last_12),
            "last_transfer": _iso(last), "verified": len(verified), "recorded": len(recorded),
            "claimed": len(claimed), "closed_with_you": closed_here,
            "note": "A recorded title transfer shows they took title - Texas deeds state no price, so it is "
                    "never treated as a verified or cash purchase."}


def contact_status(buyer: Any, activity: Optional[Dict[str, Any]], today: date) -> Dict[str, Any]:
    has = bool(getattr(buyer, "phone", None) or getattr(buyer, "email", None))
    if getattr(buyer, "do_not_contact", False):
        return {"status": "DO_NOT_CONTACT", "label": "Do not contact",
                "why": getattr(buyer, "do_not_contact_reason", None) or "Opted out", "verified_at": None}
    verified_at = getattr(buyer, "contact_verified_at", None)
    replied_at = (activity or {}).get("last_response_at")
    if replied_at and (verified_at is None or replied_at > verified_at):
        verified_at, how = replied_at, "Replied to a deal sheet"
    else:
        how = getattr(buyer, "contact_verified_how", None) or "Checked by your team"
    bad = getattr(buyer, "contact_bad_at", None)
    if bad and (verified_at is None or bad >= verified_at):
        return {"status": "BAD", "label": "Contact failed - needs a new number or email",
                "why": "Marked wrong / bounced on %s" % _d(bad), "verified_at": None}
    if verified_at:
        age = _days(verified_at, today)
        if age is not None and age > CONTACT_FRESH_DAYS:
            return {"status": "STALE", "label": "Contact verification is stale",
                    "why": "Last confirmed %d days ago (%s) - re-check before relying on it" % (age, how),
                    "verified_at": _iso(verified_at)}
        return {"status": "VERIFIED", "label": "Contact verified", "why": how, "verified_at": _iso(verified_at)}
    if has:
        return {"status": "UNVERIFIED", "label": "Contact on file, not verified",
                "why": "A phone or email is on file but no exchange has confirmed it", "verified_at": None}
    return {"status": "NONE", "label": "No phone or email", "why": "Nothing to contact them on yet",
            "verified_at": None}


def buybox_status(boxes: Sequence[Any], today: date) -> Dict[str, Any]:
    active = [b for b in boxes if getattr(b, "is_active", True)]
    if not active:
        return {"status": "NONE", "label": "No buy box", "why": "We do not know what they buy"}
    confirmed = [b for b in active if getattr(b, "confirmed_at", None)]
    fresh = [b for b in confirmed if (_days(b.confirmed_at, today) or 0) <= BUYBOX_FRESH_DAYS]
    if fresh:
        return {"status": "CONFIRMED", "label": "Buy box confirmed",
                "why": "Confirmed %s%s" % (_d(max(b.confirmed_at for b in fresh)),
                                           " by %s" % fresh[0].confirmed_by if getattr(fresh[0], "confirmed_by", None) else "")}
    if confirmed:
        return {"status": "STALE", "label": "Buy box needs re-confirming",
                "why": "Last confirmed over %d days ago" % BUYBOX_FRESH_DAYS}
    srcs = {(getattr(b, "source", None) or "").lower() for b in active}
    if srcs & {"stated", "ai_conversation", "staff"}:
        return {"status": "STATED", "label": "Buy box stated, not confirmed",
                "why": "Taken from what they said - confirm it with them"}
    if srcs == {"county_records"}:
        return {"status": "INFERRED", "label": "Inferred from what they own",
                "why": "Built from their county holdings - they never stated it"}
    return {"status": "FILE", "label": "From your buyer file",
            "why": "What a list or a form said - not confirmed with them"}


def _pof_current(buyer: Any, today: date) -> bool:
    from app.services.wholesale_buyer_intel import _pof_current as cur
    return cur(buyer, today)


def readiness(buyer: Any, contact: Dict[str, Any], box: Dict[str, Any], hist: Dict[str, Any],
              activity: Optional[Dict[str, Any]], today: date) -> Dict[str, Any]:
    """Can they move on a deal now? Every input is listed; stated is never documented."""
    a = activity or {}
    have: List[str] = []
    missing: List[str] = []
    if contact["status"] == "DO_NOT_CONTACT":
        return {"level": "DO_NOT_CONTACT", "label": "Do not contact", "have": [], "missing": [],
                "funding": {"stated": None, "documented": False}}
    stated = (getattr(buyer, "stated_buying_status", None) or "").lower()
    stated_age = _days(getattr(buyer, "stated_buying_at", None), today)
    file_inactive = (getattr(buyer, "file_status", None) or "").lower() in ("inactive", "not buying", "paused")
    if not getattr(buyer, "is_active", True) or (stated == "not_buying" and (stated_age or 0) <= 180):
        why = "Marked inactive" if not getattr(buyer, "is_active", True) else "Said they are not buying"
        return {"level": "INACTIVE", "label": "Inactive", "have": [why], "missing": [],
                "funding": {"stated": getattr(buyer, "stated_funding", None), "documented": False}}
    buying_now = stated == "buying" and stated_age is not None and stated_age <= STATED_BUYING_FRESH_DAYS
    if buying_now:
        have.append("Said they are buying now (%d days ago)" % stated_age)
    elif stated == "buying":
        missing.append("Said they were buying over %d days ago - ask again" % STATED_BUYING_FRESH_DAYS)
    elif stated == "paused":
        missing.append("Said buying is paused")
    else:
        missing.append("Not yet asked whether they are buying now")
    pof = _pof_current(buyer, today)
    documented = bool(pof or getattr(buyer, "cash_verified", False))
    if pof:
        have.append("Proof of funds on file and current")
    elif getattr(buyer, "proof_of_funds_on_file", False):
        missing.append("Proof of funds expired - request a new one")
    if getattr(buyer, "cash_verified", False):
        have.append("Cash checked by your team")
    funding = getattr(buyer, "stated_funding", None) or getattr(buyer, "financing_preference", None)
    budget = (_num(getattr(buyer, "stated_budget_min", None)), _num(getattr(buyer, "stated_budget_max", None)))
    if funding:
        have.append("Says they buy with %s (stated, not documented)" % funding.replace("_", " "))
    if budget[0] or budget[1]:
        have.append("Stated budget %s - %s (their words, not financing capacity)"
                    % ("$%s" % format(int(budget[0]), ",") if budget[0] else "?",
                       "$%s" % format(int(budget[1]), ",") if budget[1] else "?"))
    if not documented:
        missing.append("No documented funding (proof of funds or a checked cash purchase)")
    close = getattr(buyer, "stated_close_days", None) or getattr(buyer, "typical_close_days", None)
    if close:
        have.append("Closes in about %s days (stated)" % close)
    closed = int(a.get("deals_closed") or 0)
    offers = int(a.get("offers_made") or 0)
    if closed:
        have.append("Closed %d deal%s with you" % (closed, "" if closed == 1 else "s"))
    elif offers:
        have.append("Made %d offer%s on your deals" % (offers, "" if offers == 1 else "s"))
    if hist.get("last_12_months"):
        have.append("%d recorded acquisition%s in the last 12 months"
                    % (hist["last_12_months"], "" if hist["last_12_months"] == 1 else "s"))
    contact_ok = contact["status"] == "VERIFIED"
    if not contact_ok:
        missing.append("Contact not verified")
    box_ok = box["status"] == "CONFIRMED"
    if not box_ok:
        missing.append("Buy box not confirmed with them")
    if contact_ok and box_ok and buying_now and documented:
        level, label = "DEAL_READY", "Deal-ready buyer"
    elif contact_ok and (buying_now or closed or offers or hist.get("last_12_months")):
        level, label = "ACTIVE", "Active buyer"
    elif buying_now or closed or offers or hist.get("last_12_months"):
        level, label = "SIGNS_OF_ACTIVITY", "Signs of activity - not confirmed"
    else:
        level, label = "UNKNOWN", "Readiness not assessed"
    if file_inactive and level in ("UNKNOWN", "SIGNS_OF_ACTIVITY"):
        missing.append("Your file lists them as %s" % getattr(buyer, "file_status"))
    return {"level": level, "label": label, "have": have, "missing": missing,
            "funding": {"stated": funding, "documented": documented,
                        "budget_stated": {"min": budget[0], "max": budget[1]}}}


def identity(buyer: Any, entities: Sequence[Any]) -> Dict[str, Any]:
    review = [e for e in entities if getattr(e, "status", None) == "needs_review"]
    confirmed = [e for e in entities if getattr(e, "status", None) == "confirmed"]
    if getattr(buyer, "linked_to_buyer_id", None):
        return {"status": "LINKED", "label": "Another LLC of an investor in your list",
                "entities": [], "review": []}
    st = (getattr(buyer, "identity_status", None) or "").lower()
    names = [e.legal_name for e in confirmed]
    if review or st == "needs_review":
        status, label = "NEEDS_REVIEW", "Possible related entity - needs a person's review"
    elif st == "confirmed" or getattr(buyer, "decision_maker_confirmed", False):
        status, label = "CONFIRMED", "Identity confirmed"
    elif (getattr(buyer, "source", None) or "").startswith("county_roll:") or confirmed:
        status, label = "PUBLIC_RECORD", "Legal name from public record"
    elif getattr(buyer, "company_name", None) or getattr(buyer, "contact_name", None):
        status, label = "NAMED", "Name from your list - not checked"
    else:
        status, label = "UNKNOWN", "No name"
    dm = getattr(buyer, "decision_maker_name", None)
    return {"status": status, "label": label, "entities": names,
            "review": [{"id": e.id, "name": e.legal_name, "reason": e.reason,
                        "linked_buyer_id": e.linked_buyer_id} for e in review],
            "decision_maker": {"name": dm, "confirmed": bool(getattr(buyer, "decision_maker_confirmed", False))},
            "note": "A registered agent or the name on a deed is not automatically the decision-maker."}


# ── enrichment meter ───────────────────────────────────────────────────────

STAGES = [("identity", "Buyer identity research"), ("purchases", "Verified purchase history"),
          ("contact", "Contact / decision-maker research"), ("buy_box", "Buy box research"),
          ("readiness", "Readiness / financing research")]
STAGE_POINTS = {"COMPLETE": 20, "PARTIAL": 10}
FREE_EVIDENCE_COUNTIES = ("dallas", "tarrant")


def enrichment(buyer: Any, ident: Dict[str, Any], hist: Dict[str, Any], contact: Dict[str, Any],
               box: Dict[str, Any], ready: Dict[str, Any], today: date) -> Dict[str, Any]:
    st: Dict[str, Dict[str, Any]] = {}
    # 1 identity
    if ident["status"] in ("CONFIRMED", "PUBLIC_RECORD"):
        st["identity"] = {"status": "COMPLETE", "why": ident["label"]}
    elif ident["status"] == "NEEDS_REVIEW":
        st["identity"] = {"status": "NEEDS_REVIEW", "why": ident["label"]}
    elif ident["status"] == "NAMED":
        st["identity"] = {"status": "PARTIAL", "why": "Name from your list; not matched to a public record"}
    else:
        st["identity"] = {"status": "NOT_STARTED", "why": "No name on file"}
    # 2 purchases
    ps = hist["status"]
    st["purchases"] = {"status": {"VERIFIED": "COMPLETE", "RECORDED": "COMPLETE", "STALE": "PARTIAL",
                                  "COUNT_ONLY": "PARTIAL", "CLAIMED": "PARTIAL",
                                  "NONE": "NOT_STARTED"}[ps], "why": hist["label"]}
    # 3 contact / decision-maker
    cs = contact["status"]
    dm = bool(getattr(buyer, "decision_maker_name", None))
    if cs == "DO_NOT_CONTACT":
        st["contact"] = {"status": "NOT_APPLICABLE", "why": "Do not contact"}
    elif cs == "VERIFIED" and dm:
        st["contact"] = {"status": "COMPLETE", "why": "Contact verified and decision-maker named"}
    elif cs in ("BAD", "STALE"):
        st["contact"] = {"status": "NEEDS_REVIEW", "why": contact["label"]}
    elif cs in ("VERIFIED", "UNVERIFIED"):
        st["contact"] = {"status": "PARTIAL",
                         "why": contact["label"] + ("" if dm else "; decision-maker not named")}
    else:
        st["contact"] = {"status": "NOT_STARTED", "why": "No phone or email yet"}
    # 4 buy box
    bs = box["status"]
    st["buy_box"] = {"status": {"CONFIRMED": "COMPLETE", "STALE": "NEEDS_REVIEW", "STATED": "PARTIAL",
                                "FILE": "PARTIAL", "INFERRED": "PARTIAL", "NONE": "NOT_STARTED"}[bs],
                     "why": box["label"]}
    # 5 readiness / financing
    rl = ready["level"]
    stated_now = any(h.startswith("Said they are buying now") for h in ready["have"])
    close_known = bool(getattr(buyer, "stated_close_days", None) or getattr(buyer, "typical_close_days", None))
    if rl == "DO_NOT_CONTACT":
        st["readiness"] = {"status": "NOT_APPLICABLE", "why": "Do not contact"}
    elif rl == "INACTIVE":
        st["readiness"] = {"status": "COMPLETE", "why": "Asked - inactive"}
    elif stated_now and ready["funding"]["documented"] and close_known:
        st["readiness"] = {"status": "COMPLETE", "why": "Buying status, documented funding and closing time known"}
    elif getattr(buyer, "proof_of_funds_on_file", False) and not ready["funding"]["documented"]:
        st["readiness"] = {"status": "NEEDS_REVIEW", "why": "Proof of funds expired"}
    elif stated_now or ready["funding"]["stated"] or ready["funding"]["documented"] or close_known \
            or getattr(buyer, "stated_buying_status", None):
        st["readiness"] = {"status": "PARTIAL", "why": "Some answers on file: " + "; ".join(ready["have"][:2])}
    else:
        st["readiness"] = {"status": "NOT_STARTED", "why": "Not asked about buying status or funding yet"}

    applicable = [k for k, _ in STAGES if st[k]["status"] != "NOT_APPLICABLE"]
    earned = sum(STAGE_POINTS.get(st[k]["status"], 0) for k in applicable)
    pct = int(round(100 * earned / (20 * len(applicable)))) if applicable else 0
    stages = [{"key": k, "label": label, "weight": 20, **st[k],
               "points": STAGE_POINTS.get(st[k]["status"], 0)} for k, label in STAGES]
    return {"percent": pct, "stages": stages, "next": next_action(buyer, st, hist, contact, box, ready),
            "note": "Research completeness only - not how good a buyer they are or how likely they are to buy."}


def next_action(buyer: Any, st: Dict[str, Dict[str, Any]], hist: Dict[str, Any], contact: Dict[str, Any],
                box: Dict[str, Any], ready: Dict[str, Any]) -> Dict[str, Any]:
    """The single next research step, and whether it can be done in one click
    from a FREE, authorized source. Paid lookups are never one-click."""
    if contact["status"] == "DO_NOT_CONTACT":
        return {"key": "none", "label": "Do not contact - research only from public records", "one_click": False}
    if st["identity"]["status"] == "NEEDS_REVIEW":
        return {"key": "identity_review", "label": "Review the possible related LLC", "one_click": True,
                "free": True}
    county = None
    src = getattr(buyer, "source", None) or ""
    if src.startswith("county_roll:"):
        county = src.split(":", 1)[1]
    if st["purchases"]["status"] in ("NOT_STARTED", "PARTIAL") and hist["status"] in ("COUNT_ONLY", "NONE", "STALE"):
        if county in FREE_EVIDENCE_COUNTIES:
            return {"key": "evidence_refresh", "label": "Attach the recorded deeds from %s County records (free)"
                    % county.title(), "one_click": True, "free": True, "county": county}
        if hist["status"] == "NONE":
            return {"key": "add_evidence", "label": "Add a purchase you know of (address and date) - no free "
                    "record source is connected for this buyer", "one_click": False}
    if st["contact"]["status"] == "NOT_STARTED":
        return {"key": "find_contact", "label": "Find a phone or email - paid lookups need your approval; the "
                "county mailing address is free for a letter", "one_click": False, "paid": True}
    if contact["status"] in ("BAD", "STALE"):
        return {"key": "recheck_contact", "label": "Re-check the contact details", "one_click": False}
    if contact["status"] == "UNVERIFIED":
        return {"key": "qualify", "label": "Confirm the contact works and who decides", "one_click": True}
    if st["buy_box"]["status"] != "COMPLETE":
        return {"key": "qualify", "label": "Confirm what they buy (types, areas, price, units)", "one_click": True}
    if st["readiness"]["status"] != "COMPLETE":
        return {"key": "qualify", "label": "Ask about funding, timing and request proof of funds", "one_click": True}
    if st["contact"]["status"] == "PARTIAL":
        return {"key": "decision_maker", "label": "Name the decision-maker", "one_click": False}
    return {"key": "match", "label": "Research complete - match them to deals", "one_click": True}


# ── the headline badges (separate facts shown together, not one enum) ───────

def badges(ident, hist, contact, box, ready) -> List[str]:
    out = ["DISCOVERED"]
    if hist["status"] == "VERIFIED" or hist["status"] == "RECORDED":
        out.append("PURCHASE_HISTORY_VERIFIED")
    if contact["status"] == "VERIFIED":
        out.append("CONTACT_VERIFIED")
    if box["status"] == "CONFIRMED":
        out.append("BUY_BOX_CONFIRMED")
    if ready["level"] in ("ACTIVE", "DEAL_READY"):
        out.append("ACTIVE_BUYER")
    if ready["level"] == "DEAL_READY":
        out.append("DEAL_READY_BUYER")
    if ready["level"] == "INACTIVE":
        out.append("INACTIVE")
    if contact["status"] == "DO_NOT_CONTACT":
        out.append("DO_NOT_CONTACT")
    return out


BADGE_LABELS = {"DISCOVERED": "Discovered", "PURCHASE_HISTORY_VERIFIED": "Purchase history verified",
                "CONTACT_VERIFIED": "Contact verified", "BUY_BOX_CONFIRMED": "Buy box confirmed",
                "ACTIVE_BUYER": "Active buyer", "DEAL_READY_BUYER": "Deal-ready buyer",
                "INACTIVE": "Inactive", "DO_NOT_CONTACT": "Do not contact"}
HEADLINE_ORDER = ["DO_NOT_CONTACT", "INACTIVE", "DEAL_READY_BUYER", "ACTIVE_BUYER", "BUY_BOX_CONFIRMED",
                  "CONTACT_VERIFIED", "PURCHASE_HISTORY_VERIFIED", "DISCOVERED"]


def headline(bs: List[str]) -> str:
    return next(b for b in HEADLINE_ORDER if b in bs)


def assess(buyer: Any, boxes: Sequence[Any], acquisitions: Sequence[Any], entities: Sequence[Any],
           activity: Optional[Dict[str, Any]], today: Optional[date] = None) -> Dict[str, Any]:
    """Everything the screens show about one buyer, from evidence. Pure."""
    today = today or date.today()
    ident = identity(buyer, entities)
    hist = purchase_history(acquisitions, buyer, activity, today)
    contact = contact_status(buyer, activity, today)
    box = buybox_status(boxes, today)
    ready = readiness(buyer, contact, box, hist, activity, today)
    enr = enrichment(buyer, ident, hist, contact, box, ready, today)
    bs = badges(ident, hist, contact, box, ready)
    return {"version": VERSION, "identity": ident, "purchases": hist, "contact": contact, "buy_box": box,
            "readiness": ready, "enrichment": enr, "badges": bs, "headline": headline(bs),
            "headline_label": BADGE_LABELS[headline(bs)],
            "segments": segments(boxes, acquisitions)}


# ── DB glue ────────────────────────────────────────────────────────────────

def _name(b) -> str:
    return b.company_name or b.contact_name or "(unnamed buyer)"


def load(db, org_id: str, *, include_test: bool = False, buyer_ids: Optional[Iterable[str]] = None):
    """Every buyer of one organization with its boxes, evidence, entities,
    conversations and activity - in a handful of queries."""
    from app.models.wholesale_buyer_engine_models import (WholesaleBuyerAcquisition, WholesaleBuyerConversation,
                                                          WholesaleBuyerEntity)
    from app.models.wholesale_models import WholesaleBuyBox, WholesaleBuyer, WholesaleBuyerMatch
    from app.routers.wholesale_buyers_router import _buyer_activity
    q = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id)
    if buyer_ids is not None:
        q = q.filter(WholesaleBuyer.id.in_(list(buyer_ids) or [""]))
    buyers = [b for b in q.all() if include_test or not b.is_test]
    ids = [b.id for b in buyers]

    def group(model, *extra):
        out: Dict[str, List[Any]] = {}
        if not ids:
            return out
        rows = db.query(model).filter(model.organization_id == org_id, model.buyer_id.in_(ids), *extra).all()
        for r in rows:
            out.setdefault(r.buyer_id, []).append(r)
        return out
    boxes = group(WholesaleBuyBox)
    acqs = group(WholesaleBuyerAcquisition)
    ents = group(WholesaleBuyerEntity)
    convs = group(WholesaleBuyerConversation)
    matches = group(WholesaleBuyerMatch, WholesaleBuyerMatch.disqualified.is_(False))
    activity = _buyer_activity(db, org_id, ids) if ids else {}
    return buyers, boxes, acqs, ents, convs, matches, activity


GOOD_MATCH = 60


def row(b, boxes, acqs, ents, convs, matches, activity, today: date) -> Dict[str, Any]:
    a = assess(b, boxes, acqs, ents, activity, today)
    terr = {"states": set(), "counties": set(), "cities": set(), "zips": set()}
    for x in boxes:
        if getattr(x, "is_active", True):
            for k in terr:
                terr[k] |= set(_lst(getattr(x, k, None)))
    for x in acqs:
        if x.county:
            terr["counties"].add(x.county.lower())
    last_conv = max(convs, key=lambda c: c.updated_at or c.created_at) if convs else None
    good = [m for m in matches if (m.score or 0) >= GOOD_MATCH]
    act = activity or {}
    last_touch = max([d for d in (getattr(b, "last_interaction_at", None), act.get("last_response_at"),
                                  act.get("last_contacted_at"),
                                  getattr(last_conv, "updated_at", None) if last_conv else None) if d],
                     default=None)
    return {
        "buyer_id": b.id, "name": _name(b), "entity_type": b.entity_type, "is_test": bool(b.is_test),
        "source": b.source, "segments": a["segments"],
        "geography": {"states": sorted(s.upper() for s in terr["states"]),
                      "counties": sorted(c.title() for c in terr["counties"]),
                      "cities": sorted(c.title() for c in terr["cities"])[:8], "zips": sorted(terr["zips"])[:12]},
        "recent_purchases": a["purchases"]["recent"], "purchases": a["purchases"],
        "badges": a["badges"], "headline": a["headline"], "headline_label": a["headline_label"],
        "identity": a["identity"], "contact": a["contact"], "buy_box": a["buy_box"], "readiness": a["readiness"],
        "enrichment": {"percent": a["enrichment"]["percent"], "next": a["enrichment"]["next"]},
        "ai": {"status": last_conv.status if last_conv else "none", "mode": last_conv.mode if last_conv else None,
               "qualification": getattr(b, "qualification_status", None)},
        "has_phone": bool(b.phone), "has_email": bool(b.email),
        "last_interaction": _iso(last_touch),
        "matches": {"good": len(good), "best": max((m.score or 0 for m in matches), default=None)},
        "price": {"min": min([_num(x.min_price) for x in boxes if _num(x.min_price) is not None], default=None),
                  "max": max([_num(x.max_price) for x in boxes if _num(x.max_price) is not None], default=None)},
        "strategies": sorted({s for x in boxes for s in _lst(getattr(x, "strategies", None))}),
        "units": {"min": min([x.min_units for x in boxes if x.min_units is not None], default=None),
                  "max": max([x.max_units for x in boxes if x.max_units is not None], default=None)},
        "acres": {"min": min([_num(x.min_acres) for x in boxes if x.min_acres is not None], default=None),
                  "max": max([_num(x.max_acres) for x in boxes if x.max_acres is not None], default=None)},
        "linked_to": b.linked_to_buyer_id, "do_not_contact": bool(b.do_not_contact),
        "funding": a["readiness"]["funding"],
    }


def _in_range(lo, hi, want_lo, want_hi) -> bool:
    """Does the buyer's [lo, hi] overlap the filter's [want_lo, want_hi]? Unknown passes nothing."""
    if lo is None and hi is None:
        return False
    lo = lo if lo is not None else 0
    hi = hi if hi is not None else float("inf")
    return (want_lo is None or hi >= want_lo) and (want_hi is None or lo <= want_hi)


def apply_filters(rows: List[Dict[str, Any]], f: Dict[str, Any], today: date) -> List[Dict[str, Any]]:
    """Every filter combines with every other (AND). An unknown value never
    passes a filter that asks for a specific value."""
    out = []
    seg = f.get("segment") or "all"
    sub = f.get("sub")
    q = (f.get("q") or "").strip().lower()
    for r in rows:
        if not f.get("show_linked") and r["linked_to"]:
            continue
        if seg != "all":
            s = r["segments"].get(seg)
            if not s:
                continue
            if sub and sub not in s["subs"]:
                continue
        g = r["geography"]
        if f.get("state") and f["state"].upper() not in g["states"]:
            continue
        if f.get("county") and f["county"].title() not in g["counties"]:
            continue
        if f.get("city") and f["city"].title() not in g["cities"]:
            continue
        if f.get("zip") and f["zip"][:5] not in g["zips"]:
            continue
        if (f.get("price_min") is not None or f.get("price_max") is not None) and not _in_range(
                r["price"]["min"], r["price"]["max"], f.get("price_min"), f.get("price_max")):
            continue
        if (f.get("units_min") is not None or f.get("units_max") is not None) and not _in_range(
                r["units"]["min"], r["units"]["max"], f.get("units_min"), f.get("units_max")):
            continue
        if (f.get("acres_min") is not None or f.get("acres_max") is not None) and not _in_range(
                r["acres"]["min"], r["acres"]["max"], f.get("acres_min"), f.get("acres_max")):
            continue
        if f.get("recent") == "12m" and not r["purchases"]["last_12_months"]:
            continue
        if f.get("recent") == "24m" and not r["purchases"]["recent"]:
            continue
        if f.get("strategy") and f["strategy"].lower() not in r["strategies"]:
            continue
        if f.get("verification") and f["verification"] not in r["badges"]:
            continue
        if f.get("readiness") and r["readiness"]["level"] != f["readiness"]:
            continue
        if f.get("ai"):
            if f["ai"] == "none" and r["ai"]["status"] != "none":
                continue
            if f["ai"] != "none" and r["ai"]["status"] != f["ai"]:
                continue
        if f.get("contactable") == "yes" and not ((r["has_phone"] or r["has_email"]) and not r["do_not_contact"]):
            continue
        if f.get("contactable") == "no" and (r["has_phone"] or r["has_email"]) and not r["do_not_contact"]:
            continue
        if f.get("interaction"):
            days = {"30d": 30, "90d": 90}.get(f["interaction"])
            li = _d(r["last_interaction"])
            if f["interaction"] == "never":
                if li:
                    continue
            elif days and (not li or (today - li).days > days):
                continue
        if f.get("funding") == "documented" and not r["funding"]["documented"]:
            continue
        if f.get("funding") == "stated" and not (r["funding"]["stated"] and not r["funding"]["documented"]):
            continue
        if f.get("funding") == "none" and (r["funding"]["stated"] or r["funding"]["documented"]):
            continue
        if f.get("match") == "matched" and not r["matches"]["good"]:
            continue
        if f.get("match") == "unmatched" and r["matches"]["good"]:
            continue
        if q and q not in r["name"].lower() and not any(q in z for z in r["geography"]["zips"]):
            continue
        out.append(r)
    return out


READY_ORDER = {"DEAL_READY": 0, "ACTIVE": 1, "SIGNS_OF_ACTIVITY": 2, "UNKNOWN": 3, "INACTIVE": 4, "DO_NOT_CONTACT": 5}


def listing(db, org_id: str, filters: Dict[str, Any], *, include_test: bool = False, limit: int = 100,
            offset: int = 0, today: Optional[date] = None) -> Dict[str, Any]:
    today = today or date.today()
    buyers, boxes, acqs, ents, convs, matches, activity = load(db, org_id, include_test=include_test)
    rows = [row(b, boxes.get(b.id, []), acqs.get(b.id, []), ents.get(b.id, []), convs.get(b.id, []),
                matches.get(b.id, []), activity.get(b.id), today) for b in buyers]
    seg_counts = {k: 0 for k in SEGMENT_KEYS}
    for r in rows:
        if r["linked_to"]:
            continue
        seg_counts["all"] += 1
        for s in r["segments"]:
            if s in seg_counts:
                seg_counts[s] += 1
    hits = apply_filters(rows, filters, today)
    hits.sort(key=lambda r: (READY_ORDER.get(r["readiness"]["level"], 9), -r["enrichment"]["percent"],
                             -r["recent_purchases"], r["name"].lower()))
    return {"total": len(hits), "buyers": hits[offset:offset + limit], "segment_counts": seg_counts,
            "version": VERSION, "as_of": today.isoformat()}


def summary(db, org_id: str, *, include_test: bool = False, today: Optional[date] = None) -> Dict[str, Any]:
    """The four cards - every number is a count of rows in this workspace."""
    from app.models.wholesale_models import WholesaleBuyerMatch, WholesaleDeal
    today = today or date.today()
    buyers, boxes, acqs, ents, convs, matches, activity = load(db, org_id, include_test=include_test)
    rows = [row(b, boxes.get(b.id, []), acqs.get(b.id, []), ents.get(b.id, []), convs.get(b.id, []),
                matches.get(b.id, []), activity.get(b.id), today) for b in buyers]
    rows = [r for r in rows if not r["linked_to"]]
    active = [r for r in rows if r["readiness"]["level"] in ("ACTIVE", "DEAL_READY")]
    to_qualify = [r for r in rows if r["readiness"]["level"] not in ("DEAL_READY", "INACTIVE", "DO_NOT_CONTACT")
                  and (r["ai"]["qualification"] or "") not in ("qualified", "declined")]
    dq = (db.query(WholesaleBuyerMatch.deal_id).join(WholesaleDeal, WholesaleDeal.id == WholesaleBuyerMatch.deal_id)
          .filter(WholesaleBuyerMatch.organization_id == org_id, WholesaleBuyerMatch.disqualified.is_(False),
                  WholesaleBuyerMatch.score >= GOOD_MATCH))
    if not include_test:
        dq = dq.filter(WholesaleDeal.is_test.is_(False))
    deals = {d for (d,) in dq.distinct().all()}
    return {"cards": [
        {"key": "discovered", "label": "Buyers discovered", "value": len(rows),
         "detail": "Investors in your buyer network (other LLCs of the same investor counted once)"},
        {"key": "verified_active", "label": "Verified active buyers", "value": len(active),
         "detail": "Contact verified AND recent buying evidence or a current statement"},
        {"key": "to_qualify", "label": "Buyers to qualify", "value": len(to_qualify),
         "detail": "Not yet deal-ready, not inactive, not opted out"},
        {"key": "deals_matched", "label": "Deals with buyer matches", "value": len(deals),
         "detail": "Deals with at least one buyer scoring %d+ and not disqualified" % GOOD_MATCH},
    ], "include_test": include_test, "as_of": today.isoformat(), "version": VERSION}


def detail(db, org_id: str, buyer_id: str, *, today: Optional[date] = None) -> Dict[str, Any]:
    from fastapi import HTTPException

    from app.models.wholesale_buyer_engine_models import EVIDENCE_LABELS
    from app.models.wholesale_models import WholesaleBuyer, WholesaleDeal, WholesaleProperty
    today = today or date.today()
    b = db.query(WholesaleBuyer).filter(WholesaleBuyer.id == buyer_id,
                                        WholesaleBuyer.organization_id == org_id).first()
    if b is None:
        raise HTTPException(status_code=404, detail="Buyer not found")
    linked = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id,
                                             WholesaleBuyer.linked_to_buyer_id == b.id).all()
    ids = [b.id] + [x.id for x in linked]
    _buyers, boxes, acqs, ents, convs, matches, activity = load(db, org_id, include_test=True, buyer_ids=ids)
    all_boxes = [x for i in ids for x in boxes.get(i, [])]
    all_acqs = [x for i in ids for x in acqs.get(i, [])]
    r = row(b, all_boxes, all_acqs, ents.get(b.id, []), convs.get(b.id, []), matches.get(b.id, []),
            activity.get(b.id), today)
    a = assess(b, all_boxes, all_acqs, ents.get(b.id, []), activity.get(b.id), today)
    deal_ids = [m.deal_id for m in matches.get(b.id, [])]
    deals = {d.id: d for d in db.query(WholesaleDeal).filter(WholesaleDeal.id.in_(deal_ids or [""])).all()}
    props = {p.id: p for p in db.query(WholesaleProperty).filter(
        WholesaleProperty.id.in_([d.property_id for d in deals.values()] or [""])).all()}
    from app.services.wholesale_matching import _list as mlist
    return {
        **r, "assessment": a,
        "overview": {"contact_name": b.contact_name, "company_name": b.company_name, "phone": b.phone,
                     "email": b.email, "preferred_channel": b.preferred_channel, "notes": b.notes,
                     "source_detail": b.source_detail, "created_at": _iso(b.created_at),
                     "decision_maker": b.decision_maker_name, "stated_capacity": b.stated_capacity},
        "entities": [{"id": e.id, "name": e.legal_name, "status": e.status, "reason": e.reason,
                      "linked_buyer_id": e.linked_buyer_id, "source": e.source} for e in ents.get(b.id, [])],
        "linked_buyers": [{"buyer_id": x.id, "name": _name(x)} for x in linked],
        "buy_boxes": [{"id": x.id, "label": x.label, "active": bool(x.is_active), "source": x.source,
                       "confirmed_at": _iso(x.confirmed_at), "confirmed_by": x.confirmed_by,
                       "property_types": mlist(x.property_types), "strategies": mlist(x.strategies),
                       "states": mlist(x.states), "counties": mlist(x.counties), "cities": mlist(x.cities),
                       "zips": mlist(x.zips), "min_price": _num(x.min_price), "max_price": _num(x.max_price),
                       "min_units": x.min_units, "max_units": x.max_units, "min_acres": _num(x.min_acres),
                       "max_acres": _num(x.max_acres), "rehab_tolerance": x.rehab_tolerance,
                       "criteria": _jl(x.criteria, {}), "segments": {k: sorted(v) for k, v in box_segments(x).items()}}
                      for x in all_boxes],
        "evidence": [{"id": x.id, "class": x.evidence_class, "class_label": EVIDENCE_LABELS.get(x.evidence_class),
                      "source": x.source, "reference": x.reference, "grantee": x.grantee_name,
                      "kind": x.property_kind, "address": ", ".join(v for v in (x.street, x.city, x.zip_code) if v),
                      "county": x.county, "date": _iso(x.transfer_date), "price": x.price,
                      "confidence": x.confidence, "note": x.note}
                     for x in sorted(all_acqs, key=lambda z: z.transfer_date or date.min, reverse=True)],
        "conversations": [conv_json(c) for c in sorted(convs.get(b.id, []), key=lambda c: c.created_at or datetime.min,
                                                        reverse=True)],
        "matched_deals": [{"deal_id": m.deal_id, "score": m.score, "computed_at": _iso(m.computed_at),
                           "address": getattr(props.get(getattr(deals.get(m.deal_id), "property_id", None)),
                                              "street_address", None),
                           "stage": getattr(deals.get(m.deal_id), "stage", None)}
                          for m in sorted(matches.get(b.id, []), key=lambda m: -(m.score or 0))],
        "activity": activity.get(b.id),
    }


def conv_json(c) -> Dict[str, Any]:
    return {"id": c.id, "mode": c.mode, "channel": c.channel, "status": c.status,
            "transcript": _jl(c.transcript, []), "answers": _jl(c.answers, {}), "summary": c.summary,
            "objections": _jl(c.objections, []), "commitments": _jl(c.commitments, []),
            "handoff_reason": c.handoff_reason, "handoff_exception_id": c.handoff_exception_id,
            "followup_exception_id": c.followup_exception_id, "is_test": bool(c.is_test),
            "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at)}


def taxonomy() -> Dict[str, Any]:
    return {"segments": [{"key": k, "label": label} for k, label in SEGMENTS],
            "subsegments": {k: [{"key": s[0], "label": s[1]} for s in v] for k, v in SUBSEGMENTS.items()},
            "unit_bands": [{"key": k, "label": label, "min": lo, "max": hi} for k, label, lo, hi in UNIT_BANDS],
            "price_rules": {"luxury_min_price": LUXURY_MIN_PRICE, "entry_max_price": ENTRY_MAX_PRICE},
            "badges": [{"key": k, "label": v} for k, v in BADGE_LABELS.items()],
            "readiness": [{"key": k, "label": label} for k, label in (
                ("DEAL_READY", "Deal-ready"), ("ACTIVE", "Active"), ("SIGNS_OF_ACTIVITY", "Signs of activity"),
                ("UNKNOWN", "Not assessed"), ("INACTIVE", "Inactive"), ("DO_NOT_CONTACT", "Do not contact"))],
            "stages": [{"key": k, "label": label} for k, label in STAGES],
            "version": VERSION}
