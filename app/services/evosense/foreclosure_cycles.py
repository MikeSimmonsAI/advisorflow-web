"""MONTHLY FORECLOSURE CYCLES - the Foreclosure Command Center's engine.

Identity, kept apart on purpose:
    PROPERTY      EvoSenseProperty (one per real house; never duplicated here)
    OWNER         EvoSenseOwner / EvoSenseOwnership (unchanged)
    NOTICE        EvoSenseSignal - one per recorded document (clerk notice, a
                  person's recorded notice, an imported list's claim), each with
                  its OWN sale date, recording date, type and status
    AUCTION EVENT EvoSenseAuctionEvent - one property in one monthly cycle
    OPPORTUNITY   the existing opportunity view / Wholesale deal (unchanged)

A property is in a month's cycle when it has an active foreclosure notice or
claim whose sale date falls in that month, or when a person carried it there
(a postponement). Nothing here copies, merges or rewrites a notice: a later
notice never overwrites an earlier sale, and a property posted for two months
is in both, flagged "possible reschedule - verify" until a person records
what happened.

Auction day: Tex. Prop. Code 51.002(a) - first Tuesday of the month, or the
first Wednesday when that Tuesday is January 1 or July 4. That is the CYCLE'S
default; a notice's own printed sale date always wins. Tax sales are also held
on first Tuesdays (Tex. Tax Code 34.01) but are not mixed in here: this module
reads mortgage / trustee / HOA / judicial foreclosure notices only.

Outcomes are never assumed from the calendar: after the sale date the status
is "sale date passed - outcome not verified" until a person records an outcome
with evidence. Free, read-only: nothing here buys data or contacts anyone.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.services.evosense import common as C

MONTH_RX = re.compile(r"^(20\d\d)-(0[1-9]|1[0-2])$")
SALE_RULE = ("Texas trustee sales: first Tuesday of the month (Tex. Prop. Code 51.002(a)); first Wednesday "
             "when that Tuesday is January 1 or July 4. A notice's own sale date always wins.")

# plain-language status of an auction event
STATUS = {
    "notice_verified": "Notice verified - sale scheduled",
    "claimed": "Listed - notice not yet verified",
    "not_found": "No clerk notice found - verify",
    "conflict": "Notice needs review (dates disagree)",
    "postponed": "Postponed",
    "canceled": "Canceled",
    "sold_at_auction": "Sold at auction",
    "reverted_to_lender": "Reverted to lender",
    "outcome_unverified": "Sale date passed - outcome not verified",
    "reported_resolved": "Reported resolved - verify",
    "awaiting_notice": "Carried forward - waiting for a new notice",
    "no_longer_on_record": "No longer on the notice record - review",
}
# what a person may record (each but "outcome_unverified" and "notice_verified" needs evidence)
MANUAL = ("notice_verified", "postponed", "canceled", "sold_at_auction", "reverted_to_lender",
          "outcome_unverified")
DERIVED_FROM_COHORT = {"VERIFIED NOTICE": "notice_verified", "CLAIMED": "claimed", "NO NOTICE FOUND": "not_found",
                       "CONFLICT": "conflict", "POSTPONED": "postponed", "CANCELED": "canceled",
                       "RESOLVED": "reported_resolved", "SALE PASSED": "outcome_unverified",
                       "NO EVIDENCE": "claimed"}
VERIFIED = ("notice_verified", "postponed", "canceled", "sold_at_auction", "reverted_to_lender")
PENDING = ("claimed", "not_found", "awaiting_notice", "reported_resolved")
CLOSED = ("canceled", "sold_at_auction", "reverted_to_lender")


class Refused(ValueError):
    pass


# ── calendar ────────────────────────────────────────────────────────────────

def parse_month(month: Optional[str]) -> Tuple[int, int]:
    m = MONTH_RX.match((month or "").strip())
    if not m:
        raise Refused("Month must look like 2026-11.")
    return int(m.group(1)), int(m.group(2))


def month_of(d: date) -> str:
    return "%04d-%02d" % (d.year, d.month)


def shift(month: str, n: int) -> str:
    y, m = parse_month(month)
    k = y * 12 + (m - 1) + n
    return "%04d-%02d" % (k // 12, k % 12 + 1)


def trustee_sale_date(month: str) -> date:
    from app.services.evosense.sources.clerk_fc import first_tuesday
    y, m = parse_month(month)
    return first_tuesday(y, m)


def label(month: str) -> str:
    y, m = parse_month(month)
    return date(y, m, 1).strftime("%B %Y")


def _today() -> date:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Chicago")).date()


# ── notices -> months ───────────────────────────────────────────────────────

def _fc_signals(db, org_id: str, *, include_test: bool = False, property_ids=None):
    from app.models.evosense_models import EvoSenseProperty, EvoSenseSignal
    from app.services.evosense.cohort import FORECLOSURE_TYPES
    q = (db.query(EvoSenseSignal)
         .join(EvoSenseProperty, EvoSenseProperty.id == EvoSenseSignal.property_id)
         .filter(EvoSenseSignal.organization_id == org_id, EvoSenseSignal.active.is_(True),
                 EvoSenseSignal.signal_type.in_(FORECLOSURE_TYPES), EvoSenseProperty.archived_at.is_(None)))
    if not include_test:
        q = q.filter(EvoSenseProperty.is_test.is_(False))
    if property_ids is not None:
        q = q.filter(EvoSenseSignal.property_id.in_(list(property_ids) or ["-"]))
    return q.all()


def _sale_of(sig) -> Optional[date]:
    from app.services.evosense import scoring as SC
    return SC._sale_date((C.jload(sig.provenance, {}) or {}).get("sale_date"))


def months_by_property(sigs) -> Dict[str, Dict[str, list]]:
    """{property_id: {month: [signals whose sale date is in that month]}}"""
    out: Dict[str, Dict[str, list]] = {}
    for s in sigs:
        d = _sale_of(s)
        if d is None:
            continue
        out.setdefault(s.property_id, {}).setdefault(month_of(d), []).append(s)
    return out


def _derive(sigs, check=None, today=None) -> Tuple[str, Optional[date], List[Dict[str, Any]], Dict[str, Any]]:
    """Status of ONE month's notices for one property, through the same reader
    the Top 100 uses (cohort.foreclosure_status) - nothing parallel."""
    from app.services.evosense import cohort as CO
    fc = CO.foreclosure_status(sigs, today=today, check=check)
    status = DERIVED_FROM_COHORT.get(fc["status"], "claimed")
    docs = []
    for s in sigs:
        pv = C.jload(s.provenance, {}) or {}
        docs.append({"signal_id": s.id, "document": pv.get("document") or s.source_reference,
                     "url": pv.get("document_url") or pv.get("source_url"), "sale_date": pv.get("sale_date"),
                     "recorded": pv.get("recorded"), "type": pv.get("foreclosure_type"),
                     "status": (s.case_status or "scheduled").lower(), "source": s.source,
                     "claim": bool(pv.get("claim")) or s.source in ("csv_import",)})
    dates = [d for d in (_sale_of(s) for s in sigs) if d]
    return status, (min(dates) if dates else None), docs, fc


# ── cycles ──────────────────────────────────────────────────────────────────

def get_cycle(db, org_id: str, month: str):
    from app.models.evosense_models import EvoSenseAuctionCycle
    return (db.query(EvoSenseAuctionCycle).filter(EvoSenseAuctionCycle.organization_id == org_id,
                                                  EvoSenseAuctionCycle.month == month).first())


def ensure_cycle(db, org_id: str, month: str, *, user=None):
    """Create or return THE cycle for (workspace, month). Safe under a race:
    the unique key decides, the loser reads the winner's row."""
    from sqlalchemy.exc import IntegrityError
    from app.models.evosense_models import EvoSenseAuctionCycle
    parse_month(month)
    cyc = get_cycle(db, org_id, month)
    if cyc is not None:
        return cyc, False
    sd = trustee_sale_date(month)
    cyc = EvoSenseAuctionCycle(organization_id=org_id, month=month, sale_date=datetime(sd.year, sd.month, sd.day),
                               sale_rule=SALE_RULE, status="active", started_by_id=getattr(user, "id", None),
                               started_at=C.now())
    try:
        with db.begin_nested():
            db.add(cyc)
            db.flush()
    except IntegrityError:
        return get_cycle(db, org_id, month), False
    C.log_event(db, org_id, "auction_cycle.started", user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION,
                summary="Foreclosure month %s started (trustee sale day %s)" % (label(month), sd.isoformat()),
                details={"month": month, "sale_date": sd.isoformat(), "cycle_id": cyc.id})
    return cyc, True


def _events(db, org_id: str, cycle_id: str):
    from app.models.evosense_models import EvoSenseAuctionEvent
    return (db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.organization_id == org_id,
                                                  EvoSenseAuctionEvent.cycle_id == cycle_id).all())


def _latest_earlier_event(db, org_id: str, property_ids: List[str], month: str) -> Dict[str, Any]:
    """{property_id: its most recent event in an EARLIER month}."""
    from app.models.evosense_models import EvoSenseAuctionCycle, EvoSenseAuctionEvent
    if not property_ids:
        return {}
    out: Dict[str, Any] = {}
    rows = (db.query(EvoSenseAuctionEvent, EvoSenseAuctionCycle.month)
            .join(EvoSenseAuctionCycle, EvoSenseAuctionCycle.id == EvoSenseAuctionEvent.cycle_id)
            .filter(EvoSenseAuctionEvent.organization_id == org_id,
                    EvoSenseAuctionEvent.property_id.in_(property_ids),
                    EvoSenseAuctionCycle.month < month)
            .order_by(EvoSenseAuctionCycle.month.asc()).all())
    for ev, _m in rows:
        out[ev.property_id] = ev
    return out


def sync(db, org_id: str, month: str, *, user=None, include_test: bool = False, today=None) -> Dict[str, Any]:
    """Bring a month's auction events in line with the notices on record.
    Idempotent: a second run changes nothing. Never touches a notice, and never
    replaces what a person recorded."""
    from app.models.evosense_models import EvoSenseAuctionEvent, EvoSenseProperty
    from app.services.evosense import cohort as CO
    cyc, created = ensure_cycle(db, org_id, month, user=user)
    by_prop = months_by_property(_fc_signals(db, org_id, include_test=include_test))
    in_month = {pid: ms[month] for pid, ms in by_prop.items() if month in ms}
    existing = {e.property_id: e for e in _events(db, org_id, cyc.id)}
    checks = CO.clerk_checks(db, org_id, list(in_month))
    earlier = _latest_earlier_event(db, org_id, [p for p in in_month if p not in existing], month)
    tests = {pid: t for pid, t in db.query(EvoSenseProperty.id, EvoSenseProperty.is_test)
             .filter(EvoSenseProperty.id.in_(list(in_month) or ["-"])).all()}
    counts = Counter()
    for pid, sigs in in_month.items():
        status, sale, docs, _fc = _derive(sigs, check=checks.get(pid), today=today)
        ev = existing.get(pid)
        if ev is None:
            prev = earlier.get(pid)
            ev = EvoSenseAuctionEvent(organization_id=org_id, cycle_id=cyc.id, property_id=pid,
                                      is_test=bool(tests.get(pid)),
                                      carried_from_event_id=getattr(prev, "id", None),
                                      carry_reason=("Also had a notice for an earlier month" if prev else None))
            db.add(ev)
            counts["added"] += 1
        changed = (ev.derived_status != status or C.jdump(docs) != ev.documents or not ev.on_record
                   or (ev.sale_date.date() if ev.sale_date else None) != sale)
        ev.derived_status = status
        ev.documents = C.jdump(docs)
        ev.sale_date = datetime(sale.year, sale.month, sale.day) if sale else None
        ev.on_record = True
        if changed:
            counts["updated"] += 1
    for pid, ev in existing.items():
        if pid in in_month or not ev.on_record:
            continue
        if ev.carried_from_event_id and ev.derived_status == "awaiting_notice":
            continue                       # carried by a person: it has no notice yet, by design
        ev.on_record = False
        ev.derived_status = "no_longer_on_record"
        counts["off_record"] += 1
    cyc.last_synced_at = C.now()
    summary = {"properties": len(in_month) + sum(1 for p, e in existing.items() if p not in in_month),
               "added": counts["added"], "updated": counts["updated"], "off_record": counts["off_record"],
               "at": cyc.last_synced_at.isoformat() + "Z"}
    cyc.sync_summary = C.jdump(summary)
    db.flush()
    if counts["added"] or counts["off_record"]:
        C.log_event(db, org_id, "auction_cycle.refreshed", user=user,
                    actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION,
                    summary="%s: %s new, %s changed, %s no longer on the notice record"
                    % (label(month), counts["added"], counts["updated"], counts["off_record"]),
                    details=dict(summary, month=month))
    return dict(summary, cycle_id=cyc.id, created=created)


def display_status(ev, today=None) -> str:
    if ev.manual_status:
        return ev.manual_status
    st = ev.derived_status or "claimed"
    today = today or _today()
    if st in ("notice_verified", "claimed", "not_found", "conflict") and ev.sale_date \
            and ev.sale_date.date() < today:
        return "outcome_unverified"            # the calendar never decides what happened
    return st


# ── a person records an outcome ─────────────────────────────────────────────

def record_status(db, org_id: str, event_id: str, status: str, *, evidence: str = "", source: str = "",
                  user=None, new_month: Optional[str] = None) -> Dict[str, Any]:
    """What a PERSON says happened, with evidence. The notices are untouched;
    the previous value is kept in the audit row. A postponement with a new
    month links (or creates) that month's event for the SAME property."""
    from app.models.evosense_models import EvoSenseAuctionCycle, EvoSenseAuctionEvent
    if status not in MANUAL:
        raise Refused("Choose one of: %s." % ", ".join(STATUS[s] for s in MANUAL))
    ev = (db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.organization_id == org_id,
                                                EvoSenseAuctionEvent.id == event_id).first())
    if ev is None:
        raise LookupError("No such auction event in this workspace.")
    evidence = (evidence or "").strip()
    if status not in ("outcome_unverified", "notice_verified") and len(evidence) < 5:
        raise Refused("Say what shows this (a trustee's notice, the clerk record, a call with the trustee, the "
                      "sale results) - an outcome without evidence is not recorded.")
    cyc = db.query(EvoSenseAuctionCycle).filter(EvoSenseAuctionCycle.id == ev.cycle_id).first()
    before = {"manual_status": ev.manual_status, "manual_evidence": ev.manual_evidence,
              "derived_status": ev.derived_status}
    ev.manual_status = None if status == "outcome_unverified" else status
    ev.manual_evidence = evidence[:2000] or None
    ev.manual_source = (source or "").strip()[:200] or None
    ev.reviewed_by_id, ev.reviewed_at = getattr(user, "id", None), C.now()
    out = {"event_id": ev.id, "status": display_status(ev), "label": STATUS[display_status(ev)], "moved_to": None}
    if status == "postponed" and new_month:
        if new_month <= cyc.month:
            raise Refused("A postponement moves the sale to a LATER month.")
        target, _ = ensure_cycle(db, org_id, new_month, user=user)
        nxt = (db.query(EvoSenseAuctionEvent).filter(EvoSenseAuctionEvent.cycle_id == target.id,
                                                     EvoSenseAuctionEvent.property_id == ev.property_id).first())
        if nxt is None:
            nxt = EvoSenseAuctionEvent(organization_id=org_id, cycle_id=target.id, property_id=ev.property_id,
                                       derived_status="awaiting_notice", on_record=True, is_test=ev.is_test)
            db.add(nxt)
        nxt.carried_from_event_id = ev.id
        nxt.carry_reason = ("Postponed from %s to %s - recorded by %s: %s" % (
            label(cyc.month), label(new_month), getattr(user, "email", None) or "a person", evidence[:120]))[:250]
        db.flush()
        out["moved_to"] = {"month": new_month, "event_id": nxt.id}
    C.log_event(db, org_id, "auction.status_recorded", property_id=ev.property_id, user=user,
                actor_type=C.ACTOR_USER if user else C.ACTOR_AUTOMATION, is_test=bool(ev.is_test),
                summary="%s - %s: %s%s" % (label(cyc.month), STATUS[out["status"]], evidence[:160] or "no note",
                                           (" (moved to %s)" % label(new_month)) if out["moved_to"] else ""),
                details={"event_id": ev.id, "month": cyc.month, "before": before,
                         "after": {"manual_status": ev.manual_status, "evidence": ev.manual_evidence,
                                   "source": ev.manual_source}, "moved_to": out["moved_to"]})
    db.flush()
    return out


# ── start a new month ───────────────────────────────────────────────────────

def _carry_candidates(db, org_id: str, month: str) -> List[Any]:
    """Events a person marked POSTPONED in an earlier month, whose property has
    no event in `month` yet - the only thing carried forward automatically."""
    from app.models.evosense_models import EvoSenseAuctionCycle, EvoSenseAuctionEvent
    target = get_cycle(db, org_id, month)
    have = {e.property_id for e in _events(db, org_id, target.id)} if target else set()
    rows = (db.query(EvoSenseAuctionEvent)
            .join(EvoSenseAuctionCycle, EvoSenseAuctionCycle.id == EvoSenseAuctionEvent.cycle_id)
            .filter(EvoSenseAuctionEvent.organization_id == org_id, EvoSenseAuctionEvent.manual_status == "postponed",
                    EvoSenseAuctionCycle.month < month).all())
    latest: Dict[str, Any] = {}
    for e in rows:
        latest[e.property_id] = e
    later = {e.property_id for e in (db.query(EvoSenseAuctionEvent)
                                     .join(EvoSenseAuctionCycle,
                                           EvoSenseAuctionCycle.id == EvoSenseAuctionEvent.cycle_id)
                                     .filter(EvoSenseAuctionEvent.organization_id == org_id,
                                             EvoSenseAuctionEvent.property_id.in_(list(latest) or ["-"]),
                                             EvoSenseAuctionCycle.month >= month).all())}
    return [e for pid, e in latest.items() if pid not in have and pid not in later]


def next_month(db=None, org_id=None, today=None) -> str:
    """The next auction month whose trustee sale day has not passed."""
    today = today or _today()
    m = month_of(today)
    return m if trustee_sale_date(m) >= today else shift(m, 1)


def default_month(db, org_id: str, today=None) -> str:
    """The month to open: the earliest STARTED month whose sale day has not
    passed; otherwise the next auction month."""
    from app.models.evosense_models import EvoSenseAuctionCycle
    today = today or _today()
    for (m,) in (db.query(EvoSenseAuctionCycle.month).filter(EvoSenseAuctionCycle.organization_id == org_id)
                 .order_by(EvoSenseAuctionCycle.month.asc()).all()):
        if trustee_sale_date(m) >= today:
            return m
    return next_month(today=today)


def preview_start(db, org_id: str, month: str, *, include_test: bool = False) -> Dict[str, Any]:
    """What START NEW MONTH would do. Writes nothing."""
    parse_month(month)
    cyc = get_cycle(db, org_id, month)
    by_prop = months_by_property(_fc_signals(db, org_id, include_test=include_test))
    in_month = [pid for pid, ms in by_prop.items() if month in ms]
    have = {e.property_id for e in _events(db, org_id, cyc.id)} if cyc else set()
    earlier = _latest_earlier_event(db, org_id, in_month, month)
    carry = _carry_candidates(db, org_id, month)
    return {"month": month, "label": label(month), "sale_date": trustee_sale_date(month).isoformat(),
            "sale_rule": SALE_RULE, "cycle_exists": cyc is not None,
            "with_notices": len(in_month), "already_in_month": len(have & set(in_month)),
            "would_add": len([p for p in in_month if p not in have]),
            "also_in_earlier_month": len([p for p in in_month if p in earlier]),
            "postponed_to_carry": len(carry),
            "will_not": ["buy any data", "contact any homeowner", "create a contract", "copy or change a notice",
                         "create a second record for a property"]}


def start_month(db, org_id: str, month: str, *, user=None, include_test: bool = False) -> Dict[str, Any]:
    """Create (or return) the month's cycle, link every property with a notice
    for that month, and carry forward what a person marked postponed. Running it
    twice adds nothing the second time."""
    from app.models.evosense_models import EvoSenseAuctionEvent
    out = sync(db, org_id, month, user=user, include_test=include_test)
    cyc = get_cycle(db, org_id, month)
    carried = 0
    for prev in _carry_candidates(db, org_id, month):
        db.add(EvoSenseAuctionEvent(organization_id=org_id, cycle_id=cyc.id, property_id=prev.property_id,
                                    derived_status="awaiting_notice", on_record=True, is_test=prev.is_test,
                                    carried_from_event_id=prev.id,
                                    carry_reason=("Postponed earlier (%s) - no new sale date on record yet"
                                                  % (prev.manual_evidence or "recorded by a person"))[:250]))
        carried += 1
    db.flush()
    out["carried_forward"] = carried
    return out


# ── reading a month ─────────────────────────────────────────────────────────

def members(db, org_id: str, month: str) -> List[Any]:
    """The month's properties (for reuse: contact-lookup batches take
    source="cycle:YYYY-MM")."""
    from app.models.evosense_models import EvoSenseAuctionEvent, EvoSenseProperty
    cyc = get_cycle(db, org_id, month)
    if cyc is None:
        return []
    return (db.query(EvoSenseProperty)
            .join(EvoSenseAuctionEvent, EvoSenseAuctionEvent.property_id == EvoSenseProperty.id)
            .filter(EvoSenseAuctionEvent.cycle_id == cyc.id, EvoSenseProperty.organization_id == org_id,
                    EvoSenseProperty.archived_at.is_(None))
            .order_by(EvoSenseProperty.priority_score.desc().nullslast()).all())


_CACHE: Dict[Tuple[str, str], Tuple[float, Any, List[Dict[str, Any]]]] = {}
CACHE_SECONDS = 60


def _later_months(db, org_id: str, property_ids: List[str], month: str) -> Dict[str, List[str]]:
    from app.models.evosense_models import EvoSenseAuctionCycle, EvoSenseAuctionEvent
    out: Dict[str, List[str]] = {}
    for pid, m in (db.query(EvoSenseAuctionEvent.property_id, EvoSenseAuctionCycle.month)
                   .join(EvoSenseAuctionCycle, EvoSenseAuctionCycle.id == EvoSenseAuctionEvent.cycle_id)
                   .filter(EvoSenseAuctionEvent.organization_id == org_id,
                           EvoSenseAuctionEvent.property_id.in_(property_ids or ["-"]),
                           EvoSenseAuctionCycle.month > month).all()):
        out.setdefault(pid, []).append(m)
    return out


def _contact_state(r: Dict[str, Any], prop) -> str:
    c = r.get("contacts") or {}
    owner = (r.get("owner") or {}).get("status")
    if owner == "NO OWNER":
        return "missing_owner"
    if getattr(prop, "contactability", None) == "DO_NOT_CONTACT":
        return "restricted"
    if c.get("phones") or c.get("emails"):
        if (getattr(prop, "contactability", "") or "").startswith("CONTACTABLE"):
            return "outreach_eligible"
        return "contact_found"
    if owner != "VERIFIED":
        return "identity_review"
    return "lookup_ready"


CONTACT_LABELS = {"missing_owner": "Owner missing", "identity_review": "Verify owner first",
                  "lookup_ready": "Contact information missing", "lookup_pending": "Lookup running",
                  "contact_found": "Ready for approved calling", "outreach_eligible": "Outreach allowed",
                  "restricted": "Do not contact"}

ACQ_LABELS = {"not_contacted": "Not contacted", "attempted": "Attempted", "responded": "Responded",
              "interested": "Interested", "follow_up": "Follow-up", "appointment": "Appointment", "offer": "Offer",
              "contract": "Under contract", "closed": "Closed", "not_pursuing": "Not pursuing"}
_WF_TO_ACQ = {"CONTACT_ATTEMPTED": "attempted", "RESPONDED": "responded", "HUMAN_HANDOFF": "interested",
              "FOLLOW_UP": "follow_up", "APPOINTMENT": "appointment", "ANALYSIS": "interested", "OFFER": "offer",
              "CONTRACT": "contract", "DISPOSITION": "contract", "CLOSED": "closed",
              "NOT_INTERESTED": "not_pursuing", "DO_NOT_CONTACT": "not_pursuing"}


def _acq_state(r: Dict[str, Any]) -> str:
    wf = (((r.get("opportunity") or {}).get("workflow_state")) or {}).get("key")
    return _WF_TO_ACQ.get(wf or "", "not_contacted")


def _fin_state(r: Dict[str, Any]) -> Tuple[str, List[str]]:
    e = ((r.get("opportunity") or {}).get("economics")) or {}
    st = e.get("status") or "not_supportable"
    stage = {"supportable": "supported", "partial": "preliminary"}.get(st, "missing")
    wf = (((r.get("opportunity") or {}).get("workflow_state")) or {}).get("key")
    if wf in ("CONTRACT", "DISPOSITION"):
        stage = "contracted"
    if wf == "CLOSED":
        stage = "realized"
    return stage, list(e.get("needs") or [])


FIN_LABELS = {"missing": "Needs comparable sales and repairs", "preliminary": "Preliminary",
              "supported": "Supported", "contracted": "Contracted", "buyer_offered": "Buyer offered",
              "realized": "Realized"}


def rows(db, org_id: str, month: str) -> List[Dict[str, Any]]:
    """The month's working list: the Top 100's own row builder (ranking,
    notice status, owner, tax, contacts, opportunity) for the month's members,
    plus the auction event. Cached for a minute per (workspace, month)."""
    import time
    from app.services.evosense import cohort as CO
    cyc = get_cycle(db, org_id, month)
    if cyc is None:
        return []
    key = (org_id, month)
    hit = _CACHE.get(key)
    stamp = (cyc.last_synced_at, cyc.updated_at)
    if hit and hit[1] == stamp and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[2]
    props = members(db, org_id, month)
    evs = {e.property_id: e for e in _events(db, org_id, cyc.id)}
    later = _later_months(db, org_id, [p.id for p in props], month)
    earlier_notice: Dict[str, List[str]] = {}
    # a later NOTICE counts even before that month is started
    for pid, ms in months_by_property(_fc_signals(db, org_id, include_test=True,
                                                  property_ids=[p.id for p in props])).items():
        for m in ms:
            if m > month and m not in later.get(pid, []):
                later.setdefault(pid, []).append(m)
            if m < month and any(not ((C.jload(s.provenance, {}) or {}).get("claim") or s.source == "csv_import")
                                 for s in ms[m]):
                earlier_notice.setdefault(pid, []).append(m)
    out = CO.rows_for(db, org_id, props)
    by_id = {p.id: p for p in props}
    today = _today()
    for r in out:
        ev = evs.get(r["id"])
        p = by_id[r["id"]]
        st = display_status(ev, today) if ev else "claimed"
        cstate = _contact_state(r, p)
        fstate, needs = _fin_state(r)
        flags = []
        if later.get(r["id"]) and st not in CLOSED:
            flags.append("Also posted for %s - possible reschedule, verify"
                         % ", ".join(label(m) for m in sorted(later[r["id"]])))
        if earlier_notice.get(r["id"]) and st in ("claimed", "not_found"):
            # the list says this month, but the recorded notice is for an earlier sale
            flags.append("The clerk notice on record is for %s, not this month - notice needs review"
                         % ", ".join(label(m) for m in sorted(earlier_notice[r["id"]])))
        if ev and ev.carried_from_event_id:
            flags.append(ev.carry_reason or "Carried forward from an earlier month")
        if (r.get("owner") or {}).get("status") == "CONFLICT" or (r.get("owner") or {}).get("flags"):
            flags.append("Ownership needs review")
        r["auction"] = {"event_id": getattr(ev, "id", None), "status": st, "label": STATUS.get(st, st),
                        "sale_date": ev.sale_date.date().isoformat() if ev and ev.sale_date else
                        (r.get("foreclosure") or {}).get("sale_date"),
                        "documents": C.jload(getattr(ev, "documents", None), []) or [],
                        "manual": bool(getattr(ev, "manual_status", None)),
                        "evidence": getattr(ev, "manual_evidence", None),
                        "reviewed_at": ev.reviewed_at.isoformat() + "Z" if ev and ev.reviewed_at else None,
                        "carried": bool(getattr(ev, "carried_from_event_id", None)),
                        "later_months": later.get(r["id"], []), "flags": flags}
        r["contact_state"] = {"key": cstate, "label": CONTACT_LABELS[cstate]}
        r["acquisition"] = {"key": _acq_state(r), "label": ACQ_LABELS[_acq_state(r)],
                            "deal_id": p.promoted_deal_id}
        r["financial"] = {"key": fstate, "label": FIN_LABELS[fstate], "needs": needs}
        r["needs_attention"] = bool(flags) or st in ("conflict", "not_found", "outcome_unverified",
                                                     "no_longer_on_record", "reported_resolved") \
            or cstate in ("missing_owner", "identity_review")
    _CACHE[key] = (time.monotonic(), stamp, out)
    return out


SORTS = {
    "opportunity": lambda r: (r.get("rank") or 9999,),
    "auction_date": lambda r: ((r["auction"]["sale_date"] or "9999"), r.get("rank") or 9999),
    "urgent": lambda r: (-(((r.get("opportunity") or {}).get("urgency") or {}).get("value") or 0), r.get("rank") or 9999),
    "recently_verified": lambda r: (-(_ts((r.get("foreclosure") or {}).get("observed"))), r.get("rank") or 9999),
    "contact_ready": lambda r: (0 if r["contact_state"]["key"] in ("contact_found", "outreach_eligible") else 1,
                                r.get("rank") or 9999),
    "underwritten": lambda r: (0 if r["financial"]["key"] in ("supported", "contracted", "realized") else 1,
                               r.get("rank") or 9999),
}


def _ts(v) -> float:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").timestamp()
    except (TypeError, ValueError):
        return 0.0


def filter_rows(rs: List[Dict[str, Any]], f: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = rs
    if f.get("county"):
        out = [r for r in out if (r.get("county") or "").lower() == f["county"].lower()]
    if f.get("q"):
        q = f["q"].lower()
        out = [r for r in out if q in ("%s %s %s" % (r.get("street_address"), r.get("city"),
                                                     r.get("zip_code"))).lower()]
    if f.get("status"):
        want = set(f["status"].split(","))
        out = [r for r in out if r["auction"]["status"] in want]
    if f.get("verified") in ("yes", "no"):
        out = [r for r in out if (r["auction"]["status"] in VERIFIED) == (f["verified"] == "yes")]
    if f.get("owner") in ("identified", "missing"):
        out = [r for r in out if ((r.get("owner") or {}).get("status") != "NO OWNER") == (f["owner"] == "identified")]
    if f.get("contact"):
        want = set(f["contact"].split(","))
        out = [r for r in out if r["contact_state"]["key"] in want]
    if f.get("acquisition"):
        want = set(f["acquisition"].split(","))
        out = [r for r in out if r["acquisition"]["key"] in want]
    if f.get("financial"):
        want = set(f["financial"].split(","))
        out = [r for r in out if r["financial"]["key"] in want]
    if f.get("min_score"):
        out = [r for r in out if (r.get("score") or 0) >= int(f["min_score"])]
    if f.get("ptype"):
        want = set(f["ptype"].split(","))
        out = [r for r in out if ((r.get("property_class") or {}).get("category") or "unknown") in want]
    if f.get("psubtype"):
        out = [r for r in out if (r.get("property_class") or {}).get("subtype") == f["psubtype"]]
    if f.get("type"):
        out = [r for r in out if f["type"].lower() in ((r.get("foreclosure") or {}).get("foreclosure_type") or "").lower()]
    if f.get("attention") == "yes":
        out = [r for r in out if r["needs_attention"]]
    if f.get("carried") == "yes":
        out = [r for r in out if r["auction"]["carried"]]
    if f.get("deals") == "yes":
        out = [r for r in out if r["acquisition"]["key"] not in ("not_contacted", "attempted", "not_pursuing")
               or r["financial"]["key"] != "missing"]
    out = sorted(out, key=SORTS.get(f.get("sort") or "opportunity", SORTS["opportunity"]))
    return out


def summary(db, org_id: str, month: str) -> Dict[str, Any]:
    """The five cards and the overview, from the month's real rows."""
    cyc = get_cycle(db, org_id, month)
    rs = rows(db, org_id, month) if cyc else []
    st = Counter(r["auction"]["status"] for r in rs)
    cs = Counter(r["contact_state"]["key"] for r in rs)
    acq = Counter(r["acquisition"]["key"] for r in rs)
    fin = Counter(r["financial"]["key"] for r in rs)
    counties = Counter(r.get("county") or "Unknown" for r in rs)
    ptypes = Counter(((r.get("property_class") or {}).get("category") or "unknown") for r in rs)
    owner_flags = sum(1 for r in rs if (r.get("owner") or {}).get("status") == "CONFLICT"
                      or (r.get("owner") or {}).get("flags"))
    carried = sum(1 for r in rs if r["auction"]["carried"])
    conflicts = sum(1 for r in rs if r["auction"]["status"] == "conflict"
                    or any("notice needs review" in f for f in r["auction"]["flags"]))
    dates = Counter(r["auction"]["sale_date"] for r in rs if r["auction"]["sale_date"])
    urgent = [r for r in rs if r["needs_attention"]][:10]
    return {
        "month": month, "label": label(month), "exists": cyc is not None,
        "sale_date": trustee_sale_date(month).isoformat(), "sale_rule": SALE_RULE,
        "sale_dates_on_notices": dict(sorted(dates.items())),
        "last_refreshed": cyc.last_synced_at.isoformat() + "Z" if cyc and cyc.last_synced_at else None,
        "cards": {
            "found": {"total": len(rs), "new": len(rs) - carried, "carried": carried},
            "verified": {"verified": sum(st[s] for s in VERIFIED), "pending": sum(st[s] for s in PENDING),
                         "conflict": conflicts, "outcome_unverified": st["outcome_unverified"]},
            "contact": {"ready": cs["contact_found"] + cs["outreach_eligible"], "eligible": cs["outreach_eligible"],
                        "awaiting": cs["lookup_ready"],
                        "note": "A phone number is not permission to text."},
            "attention": {"missing_owners": cs["missing_owner"], "conflicts": conflicts,
                          "postponed": st["postponed"], "ownership": owner_flags,
                          "urgent": sum(1 for r in rs if r["needs_attention"])},
            "deals": {"active": sum(acq[k] for k in ("responded", "interested", "follow_up", "appointment",
                                                     "offer", "contract")),
                      "supported": fin["supported"], "offers": acq["offer"], "contracts": acq["contract"]},
        },
        "counties": dict(counties.most_common()),
        "property_types": dict(ptypes.most_common()),
        "statuses": {k: {"count": v, "label": STATUS.get(k, k)} for k, v in st.items()},
        "contact_states": {k: {"count": v, "label": CONTACT_LABELS[k]} for k, v in cs.items()},
        "acquisition": {k: {"count": v, "label": ACQ_LABELS[k]} for k, v in acq.items()},
        "financial": {k: {"count": v, "label": FIN_LABELS[k]} for k, v in fin.items()},
        "urgent": [{"id": r["id"], "address": r["street_address"], "city": r.get("city"),
                    "next_action": ((r.get("opportunity") or {}).get("next_action") or {}).get("label")
                    or r.get("next_action"), "flags": r["auction"]["flags"], "status": r["auction"]["label"]}
                   for r in urgent],
    }


def history(db, org_id: str) -> List[Dict[str, Any]]:
    """Every month this workspace has worked, newest first, with what happened."""
    from sqlalchemy import func
    from app.models.evosense_models import EvoSenseAuctionCycle, EvoSenseAuctionEvent, EvoSenseProperty
    cycles = (db.query(EvoSenseAuctionCycle).filter(EvoSenseAuctionCycle.organization_id == org_id)
              .order_by(EvoSenseAuctionCycle.month.desc()).all())
    out = []
    for c in cycles:
        evs = _events(db, org_id, c.id)
        ids = [e.property_id for e in evs]
        st = Counter(display_status(e) for e in evs)
        deals = (db.query(func.count(EvoSenseProperty.id))
                 .filter(EvoSenseProperty.id.in_(ids or ["-"]), EvoSenseProperty.promoted_deal_id.isnot(None))
                 .scalar() or 0)
        contacted = (db.query(func.count(func.distinct(EvoSenseProperty.id)))
                     .filter(EvoSenseProperty.id.in_(ids or ["-"]),
                             EvoSenseProperty.workflow_state.in_(("CONTACT_ATTEMPTED", "RESPONDED", "HUMAN_HANDOFF",
                                                                  "FOLLOW_UP", "APPOINTMENT", "NOT_INTERESTED")))
                     .scalar() or 0)
        out.append({"month": c.month, "label": label(c.month),
                    "sale_date": c.sale_date.date().isoformat() if c.sale_date else None,
                    "properties": len(evs), "verified": sum(st[s] for s in VERIFIED),
                    "carried_in": sum(1 for e in evs if e.carried_from_event_id),
                    "contacted": contacted, "deals": deals,
                    "outcomes": {STATUS[k]: v for k, v in st.items() if k in CLOSED + ("postponed",
                                                                                        "outcome_unverified")},
                    "last_refreshed": c.last_synced_at.isoformat() + "Z" if c.last_synced_at else None})
    return out


def sources(db, org_id: str) -> List[Dict[str, Any]]:
    """Each county's foreclosure-notice source as it really stands. A county
    the clerk reader cannot read says so - never 'checked'."""
    from app.models.evosense_models import EvoSenseEvent
    from app.services.evosense import cohort as CO
    from app.services.evosense import providers as PV
    from app.services.evosense.sources.clerk_fc import COUNTIES
    p = PV.PROVIDERS.get(CO.CLERK_SOURCE)
    cfg = PV.config(db, org_id, CO.CLERK_SOURCE)
    canon = PV.canonical(p, cfg, blocked=PV.platform_blocked(db, p.key, cfg)) if p else {}
    last = {}
    for ev in (db.query(EvoSenseEvent).filter(EvoSenseEvent.organization_id == org_id,
                                              EvoSenseEvent.action == CO.FC_CHECK_EVENT)
               .order_by(EvoSenseEvent.created_at.desc()).limit(2000).all()):
        d = C.jload(ev.details, {}) or {}
        cty = (d.get("county") or "").title()
        if cty and cty not in last:
            last[cty] = {"at": ev.created_at.isoformat() + "Z", "matched": d.get("matched")}
    out = []
    for cty in sorted(set(c.title() for c in COUNTIES)):
        l = last.get(cty)
        if canon.get("state") in ("FAILED", "BLOCKED", "DEGRADED", "RATE LIMITED"):
            state = "Source unavailable"
        elif not cfg.enabled:
            state = "Source switched off"
        elif l is None:
            state = "Not checked yet"
        else:
            state = "Checked"
        out.append({"county": cty, "source": "%s County Clerk (public notice search)" % cty, "state": state,
                    "why": canon.get("why"), "last_checked": (l or {}).get("at"),
                    "manual_action": None})
    for cty in ("Collin", "Ellis", "Kaufman", "Rockwall"):
        if cty in [o["county"] for o in out]:
            continue
        out.append({"county": cty, "source": "No automatic clerk reader", "state": "Manual entry",
                    "why": "Record each notice by hand on the property (with its document number), or import a list.",
                    "last_checked": None, "manual_action": "Record notices by hand or import a list"})
    jc = PV.PROVIDERS.get("johnson_cad")
    if jc is not None:
        jcfg = PV.config(db, org_id, "johnson_cad")
        jstate = PV.canonical(jc, jcfg, blocked=PV.platform_blocked(db, jc.key, jcfg))
        for o in out:
            if o["county"] == "Johnson":
                o["manual_action"] = ("Johnson County appraisal file must be uploaded (Sources) for owner and "
                                      "lot details - %s" % (jstate.get("state") or "").lower())
    return out


# ── automation (the EvoSense scheduler pass) ───────────────────────────────
AUTO_REFRESH_MINUTES = 60
COMPLETE_AFTER_DAYS = 30


def auto(db, org_id: Optional[str] = None, *, today=None) -> Dict[str, int]:
    """Safe, free, idempotent, every scheduler pass:
      - PREPARE the next auction month when notices for it exist and nobody
        has started it yet (same as a person pressing Start month; a second
        pass finds it started);
      - REFRESH each active month from the notices on record, at most hourly;
      - mark a month COMPLETED a month after its sale day (it stays readable).
    Reads only this platform's own records: no outside call, no purchase, no
    message, no contract. One workspace failing never stops the others."""
    from app.models.evosense_models import EvoSenseAuctionCycle, EvoSenseControl
    today = today or _today()
    counts = Counter()
    orgs = [org_id] if org_id else [o for (o,) in db.query(EvoSenseControl.organization_id).all()]
    nxt = next_month(today=today)
    for o in orgs:
        try:
            if get_cycle(db, o, nxt) is None and any(nxt in ms for ms in months_by_property(_fc_signals(db, o)).values()):
                start_month(db, o, nxt)
                counts["prepared"] += 1
            stale = C.now() - timedelta(minutes=AUTO_REFRESH_MINUTES)
            for cyc in (db.query(EvoSenseAuctionCycle).filter(EvoSenseAuctionCycle.organization_id == o,
                                                              EvoSenseAuctionCycle.status == "active").all()):
                if cyc.sale_date and cyc.sale_date.date() < today - timedelta(days=COMPLETE_AFTER_DAYS):
                    cyc.status = "completed"
                    counts["completed"] += 1
                    continue
                if cyc.last_synced_at is None or cyc.last_synced_at < stale:
                    sync(db, o, cyc.month, today=today)
                    counts["refreshed"] += 1
            db.commit()
        except Exception:  # noqa: BLE001 - one workspace never stops the others
            db.rollback()
            counts["failed"] += 1
            C.log.exception("foreclosure month automation failed for %s", o)
    return dict(counts)
