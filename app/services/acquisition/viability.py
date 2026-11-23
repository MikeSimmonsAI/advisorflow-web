"""Viability score, attack groups and the search log for Harvey (#452).

Every business in the master pool (lead_intel_prospects) gets a 0-100
VIABILITY score made of five parts, each with its reason in plain words:

    Fit            30  does this kind of business buy recurring cleaning?
    Reachable      25  verified email, business phone, a named decision maker
    Job size       20  how big the job is likely to be (from business type + size signals)
    Easy to get in 15  a direct sale to an owner/manager vs. a public bid
    Timing         10  a sign of change (new permit, just moved in...) - none yet

and an attack group:

    A 75-100  start outreach
    B 50-74   fill in the missing contact info, then score again
    C 25-49   park it; check again in 30 days
    D 0-24    drop it - reason recorded, nothing deleted

This is deterministic and explainable: no AI, no network, no guessing. It
never contacts anyone and never changes a record's stage, bucket or the
older qualification score - it writes only its own viability_* columns.

The SEARCH LOG is the discovery runs already recorded (acquisition_discovery_runs).
The attack queue walks business type x city in a fixed order, apartments
and short-term rental managers side by side first (owner decision
2026-11-22), so nothing is skipped or repeated.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.services.acquisition import common as C

GROUPS = (("A", 75), ("B", 50), ("C", 25), ("D", 0))
GROUP_ACTION = {"A": "Start outreach", "B": "Find the missing contact info, then score again",
                "C": "Parked - check again in 30 days", "D": "Dropped - reason recorded, nothing deleted"}
RECHECK_DAYS = 30

# Fit (max 30): how likely this kind of business buys RECURRING cleaning.
FIT = {"apartments": 30, "short_term_rental": 30, "medical_dental": 28, "property_management": 27,
       "office": 24, "hoa": 18, "industrial": 18, "retail": 14, "homeowners": 10, "restaurants": 6}
FIT_DEFAULT = 10
# Job size baseline (max 20) by business type; size signals add on top.
SIZE = {"property_management": 18, "apartments": 16, "short_term_rental": 16, "industrial": 14, "hoa": 14,
        "office": 12, "medical_dental": 12, "retail": 8, "restaurants": 6, "homeowners": 4}
SIZE_DEFAULT = 6

# Attack order: apartments and short-term rental managers side by side, then the rest.
FIRST_PAIR = ("apartments", "short_term_rental")
THEN = ("medical_dental", "office", "property_management", "industrial", "retail", "hoa")
# OpenStreetMap has no tag for short-term rental managers; only Google Places finds them.
NEEDS_PLACES = {"short_term_rental"}


def group_for(score: int) -> str:
    for g, floor in GROUPS:
        if score >= floor:
            return g
    return "D"


def _discovery(p) -> Dict[str, Any]:
    return (C.jload(getattr(p, "evidence_json", None), {}) or {}).get("discovery") or {}


def score_prospect(p) -> Dict[str, Any]:
    """Score ONE master-pool record. Pure: reads the record, writes nothing."""
    seg = (getattr(p, "industry", None) or "").strip().lower()
    label = C.SEGMENT_LABEL.get(seg, seg or "unknown business type")
    disc = _discovery(p)
    parts: Dict[str, Dict[str, Any]] = {}

    # Dropped outright: suppressed, invalid or a duplicate. Recorded, never deleted.
    stage = (getattr(p, "stage", None) or "").lower()
    if getattr(p, "suppressed_reason", None) or stage in ("invalid", "duplicate", "suppressed"):
        why = getattr(p, "suppressed_reason", None) or ("Marked %s" % stage)
        return {"score": 0, "group": "D", "action": GROUP_ACTION["D"], "parts": {},
                "reasons": ["Dropped: %s" % why]}

    # 1. Fit
    fit = FIT.get(seg, FIT_DEFAULT)
    parts["fit"] = {"points": fit, "max": 30, "why": "%s - %s" % (
        label, "buys recurring cleaning" if fit >= 24 else "occasional or low cleaning need" if fit < 15
        else "some recurring cleaning")}

    # 2. Reachable
    reach, rwhy = 0, []
    if getattr(p, "email", None) and getattr(p, "email_verified", None):
        reach += 12; rwhy.append("+12 verified email")
    elif getattr(p, "email", None):
        reach += 4; rwhy.append("+4 email found but not verified")
    if getattr(p, "phone_raw", None) or getattr(p, "phone_e164", None):
        reach += 8; rwhy.append("+8 business phone")
    if getattr(p, "contact_name", None):
        reach += 5; rwhy.append("+5 named contact%s" % (
            " (%s)" % p.contact_title if getattr(p, "contact_title", None) else ""))
    if disc.get("website_verified") and reach < 25:
        add = min(3, 25 - reach)
        reach += add; rwhy.append("+%d website confirmed as theirs" % add)
    reach = min(reach, 25)
    parts["reachable"] = {"points": reach, "max": 25, "why": ", ".join(rwhy) or "No way to reach them yet"}

    # 3. Job size
    size = SIZE.get(seg, SIZE_DEFAULT)
    swhy = ["%d estimated from business type" % size]
    reviews = getattr(p, "reviews_count", None) or 0
    if reviews >= 100:
        size += 4; swhy.append("+4 busy location (%d public reviews)" % reviews)
    elif reviews >= 25:
        size += 2; swhy.append("+2 established (%d public reviews)" % reviews)
    size = min(size, 20)
    parts["job_size"] = {"points": size, "max": 20, "why": ", ".join(swhy)}

    # 4. Easy to get in
    src = (getattr(p, "source", None) or "").lower()
    if "bid" in src or "sam.gov" in src or "government" in seg:
        ease, ewhy = 5, "Public bid - bonding, insurance and certification needed"
    else:
        ease, ewhy = 15, "Direct sale to the owner or manager"
    parts["easy_to_get_in"] = {"points": ease, "max": 15, "why": ewhy}

    # 5. Timing - only real signals count. The permit feed isn't built yet.
    timing, twhy = 0, "No sign of change on record yet"
    signal = disc.get("timing_signal")
    if signal:
        timing, twhy = 10, str(signal)[:200]
    parts["timing"] = {"points": timing, "max": 10, "why": twhy}

    total = sum(x["points"] for x in parts.values())
    g = group_for(total)
    reasons = ["%s %d/%d: %s" % (k.replace("_", " ").capitalize(), v["points"], v["max"], v["why"])
               for k, v in parts.items()]
    return {"score": total, "group": g, "action": GROUP_ACTION[g], "parts": parts, "reasons": reasons}


def store(p, result: Dict[str, Any], now: Optional[datetime] = None) -> None:
    p.viability_score = result["score"]
    p.viability_group = result["group"]
    p.viability_json = C.jdump({"parts": result["parts"], "reasons": result["reasons"]})
    p.viability_at = now or datetime.utcnow()


def rescore_pool(db: Session, only_unscored: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Score the whole master pool (or just the records never scored).
    Writes only the viability_* columns. Returns counts per group."""
    from app.models.lead_intel_models import LeadIntelProspect
    now = now or datetime.utcnow()
    q = db.query(LeadIntelProspect)
    if only_unscored:
        q = q.filter(LeadIntelProspect.viability_at.is_(None))
    n, moved = 0, 0
    for p in q.all():
        before = p.viability_group
        res = score_prospect(p)
        store(p, res, now)
        n += 1
        if before and before != res["group"]:
            moved += 1
    db.commit()
    return {"scored": n, "changed_group": moved, "groups": group_counts(db)}


def group_counts(db: Session) -> Dict[str, int]:
    from sqlalchemy import func
    from app.models.lead_intel_models import LeadIntelProspect
    out = {g: 0 for g, _ in GROUPS}
    out["unscored"] = 0
    for g, n in (db.query(LeadIntelProspect.viability_group, func.count(LeadIntelProspect.id))
                 .group_by(LeadIntelProspect.viability_group).all()):
        out[g if g in out else "unscored"] += n
    return out


def due_for_recheck(db: Session, now: Optional[datetime] = None) -> int:
    """Group C records whose 30 days are up."""
    from app.models.lead_intel_models import LeadIntelProspect
    cutoff = (now or datetime.utcnow()) - timedelta(days=RECHECK_DAYS)
    return (db.query(LeadIntelProspect).filter(LeadIntelProspect.viability_group == "C",
                                                LeadIntelProspect.viability_at < cutoff).count())


def ranked(db: Session, group: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    from app.models.lead_intel_models import LeadIntelProspect
    q = db.query(LeadIntelProspect).filter(LeadIntelProspect.viability_at.isnot(None))
    if group:
        q = q.filter(LeadIntelProspect.viability_group == group.upper())
    rows = q.order_by(LeadIntelProspect.viability_score.desc(), LeadIntelProspect.name).limit(max(1, min(limit, 500))).all()
    return [{"id": p.id, "name": p.name, "segment": p.industry, "segment_label": C.SEGMENT_LABEL.get(p.industry or ""),
             "city": p.city, "state": p.state, "phone": p.phone_raw, "email": p.email,
             "email_verified": bool(p.email_verified), "contact_name": p.contact_name,
             "score": p.viability_score, "group": p.viability_group, "action": GROUP_ACTION.get(p.viability_group or ""),
             "reasons": (C.jload(p.viability_json, {}) or {}).get("reasons", []), "scored_at": C.iso(p.viability_at)}
            for p in rows]


# ── SEARCH LOG + ATTACK QUEUE ───────────────────────────────────────────────
def attack_order(segments: List[str], cities: List[str]) -> List[Tuple[str, str]]:
    """Business type x city, in attack order. The first pair (apartments and
    short-term rental managers) runs side by side city by city; then each
    other wanted type works through every city."""
    wanted = set(segments)
    pair = [s for s in FIRST_PAIR if s in wanted]
    rest = [s for s in THEN if s in wanted] + sorted(wanted - set(FIRST_PAIR) - set(THEN))
    out: List[Tuple[str, str]] = []
    for city in cities:
        for seg in pair:
            out.append((seg, city))
    for seg in rest:
        for city in cities:
            out.append((seg, city))
    return out


def _searched(db: Session, org_id: Optional[str], since: datetime) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(segment, city) -> latest finished run, from the discovery runs log.
    An OpenStreetMap run covers every segment it lists (comma-separated)."""
    from app.models.acquisition_models import AcquisitionDiscoveryRun as R
    q = db.query(R).filter(R.status == "done", R.created_at >= since)
    if org_id:
        q = q.filter(R.organization_id == org_id)
    done: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in q.order_by(R.created_at).all():
        for seg in (r.segment or "").split(","):
            seg = seg.strip()
            if seg:
                done[(seg, (r.city or "").strip().lower())] = {
                    "at": C.iso(r.created_at), "provider": r.provider, "found": r.found, "added": r.added}
    return done


def queue(db: Session, org_id: Optional[str], segments: List[str], cities: List[str],
          places_ready: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The attack queue with each search's status: done / next / waiting /
    needs_google_places. A search counts as done for RECHECK_DAYS days."""
    now = now or datetime.utcnow()
    done = _searched(db, org_id, now - timedelta(days=RECHECK_DAYS))
    rows, nxt = [], None
    for seg, city in attack_order(segments, cities):
        hit = done.get((seg, city.strip().lower()))
        if hit:
            st = "done"
        elif seg in NEEDS_PLACES and not places_ready:
            st = "needs_google_places"
        else:
            st = "waiting"
            if nxt is None:
                nxt = {"segment": seg, "city": city}
                st = "next"
        rows.append({"segment": seg, "segment_label": C.SEGMENT_LABEL.get(seg, seg), "city": city,
                     "status": st, "last_run": hit})
    counts = {k: sum(1 for r in rows if r["status"] == k) for k in ("done", "next", "waiting", "needs_google_places")}
    return {"next": nxt, "counts": counts, "total": len(rows), "rows": rows,
            "note": "A search is done for %d days, then comes round again." % RECHECK_DAYS}


def next_city(db: Session, org_id: str, cities: List[str], now: Optional[datetime] = None) -> Optional[str]:
    """The client's city searched least recently (never-searched first; a
    failed search counts as tried, so one bad city can't stall the rest), so
    the daily free discovery walks the whole service area instead of
    repeating the first city every day."""
    from app.models.acquisition_models import AcquisitionDiscoveryRun as R
    if not cities:
        return None
    last: Dict[str, datetime] = {}
    for city, at in (db.query(R.city, R.created_at).filter(R.organization_id == org_id, R.status.in_(("done", "failed"))).all()):
        k = (city or "").strip().lower()
        if at and (k not in last or at > last[k]):
            last[k] = at
    never = [c for c in cities if c.strip().lower() not in last]
    if never:
        return never[0]
    return min(cities, key=lambda c: last[c.strip().lower()])


def daily_report(db: Session, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The daily count: businesses found, verified contacts, each group,
    appointments booked - all from what is recorded, nothing estimated."""
    from app.models.acquisition_models import AcquisitionDiscoveryRun as R, AcquisitionOpportunity as O
    from app.models.lead_intel_models import LeadIntelProspect as P
    now = now or datetime.utcnow()
    since = now - timedelta(hours=24)
    runs = db.query(R).filter(R.created_at >= since).all()
    return {"window": "last 24 hours", "as_of": C.iso(now),
            "searches_run": len(runs), "searches_failed": sum(1 for r in runs if r.status == "failed"),
            "businesses_found": sum(r.found or 0 for r in runs), "businesses_added": sum(r.added or 0 for r in runs),
            "pool_total": db.query(P).count(),
            "verified_emails": db.query(P).filter(P.email_verified.is_(True)).count(),
            "with_phone": db.query(P).filter(P.phone_raw.isnot(None)).count(),
            "groups": group_counts(db), "group_c_due_recheck": due_for_recheck(db, now),
            "interested_24h": db.query(O).filter(O.interested_at >= since).count(),
            "booked_24h": db.query(O).filter(O.booked_at >= since).count()}
