"""Commercial energy opportunities - an advisor-led sale, not a residential enrollment.

ONE RECORD, NOT A SECOND PIPELINE. A commercial opportunity is the same Lead
the website (or an advisor) created, with segment Commercial. Its sales detail
lives in custom_fields["commercial"]; its coarse status stays the Rate
Requests tier, which this module only ever moves FORWARD:

    discovery, information_requested           -> new_inquiry   (New)
    usage_received, quote_requested,
    quote_received                             -> rate_review   (In Review)
    proposal_presented, decision_pending,
    contract_submitted, supplier_confirmed     -> proposal_sent (Options Sent)
    won / lost / deferred                      -> tier untouched

WON IS NOT AUTOMATIC. "won" is accepted only after "supplier_confirmed" (the
supplier confirmed the signed contract), and even then nothing here sets
contract_signed or enrolled_at - that stays the explicit Enroll action.
A recorded supplier hand-off never moves a stage.

LOST / DEFERRED keep the history. Lost needs a reason (also written to the
lead's pipeline_lost_* columns); deferred needs a revisit date and opens one
internal task for that date.

SEVERAL LOCATIONS, ONE CUSTOMER. custom_fields["commercial"]["locations"] is
a list of service locations (address, utility, meter / ESI ID, current
supplier, contract end, annual kWh, quote status) under the one business
record. A location is matched on address + meter so the same site is not
added twice. The opportunity's contract end is the EARLIEST location's.

Internal reminders only (source "commercial"): one open task per kind; moving
to a later stage closes reminders that no longer apply. Nothing is sent to the
customer or to a supplier, no quote is requested from anyone, and no price,
rate or commission is stored or computed. `expected_annual_kwh` is the
customer's usage, not money.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

STAGES = ("discovery", "information_requested", "usage_received", "quote_requested",
          "quote_received", "proposal_presented", "decision_pending", "contract_submitted",
          "supplier_confirmed", "won", "lost", "deferred")
OPEN_STAGES = STAGES[:9]
TIER_FOR = {
    "discovery": "new_inquiry", "information_requested": "new_inquiry",
    "usage_received": "rate_review", "quote_requested": "rate_review", "quote_received": "rate_review",
    "proposal_presented": "proposal_sent", "decision_pending": "proposal_sent",
    "contract_submitted": "proposal_sent", "supplier_confirmed": "proposal_sent",
}
TIER_ORDER = ("web_lead", "", "new_inquiry", "rate_review", "proposal_sent", "contract_signed")
TASK_SOURCE = "commercial"
# stage -> (task title, days until due); one open task per title
REMINDERS = {
    "information_requested": ("Get usage / bills from the business", 2),
    "quote_requested": ("Follow up on supplier quote", 3),
    "proposal_presented": ("Follow up on proposal with the decision-maker", 3),
    "contract_submitted": ("Confirm contract with the supplier", 3),
}
LOCATION_FIELDS = {"address": 300, "utility": 80, "meter_id": 60, "current_supplier": 120,
                   "contract_end_date": 10, "annual_kwh": 20, "quote_status": 30, "note": 300}
QUOTE_STATUSES = ("not_requested", "requested", "received", "declined")


class NotCommercial(ValueError):
    pass


def _cf(lead) -> Dict[str, Any]:
    try:
        d = json.loads(lead.custom_fields or "{}")
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _stamp(now: datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _date(v, field) -> Optional[str]:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(str(v)[:10]).isoformat()
    except ValueError:
        raise ValueError("%s must be a date (YYYY-MM-DD)." % field)


def _require_commercial(cf):
    from app.services.energy_qualification import segment_of
    if segment_of(cf) != "commercial":
        raise NotCommercial("This is not a commercial opportunity (segment is not Commercial).")


def view(lead) -> Dict[str, Any]:
    cf = _cf(lead)
    _require_commercial(cf)
    c = cf.get("commercial") if isinstance(cf.get("commercial"), dict) else {}
    locs = [l for l in (c.get("locations") or []) if isinstance(l, dict)]
    ends = sorted(l["contract_end_date"] for l in locs if l.get("contract_end_date"))
    return {
        "lead_id": lead.id, "company": cf.get("company"),
        "stage": c.get("stage") or "discovery", "stages": list(STAGES),
        "history": c.get("history") or [],
        "expected_decision_date": c.get("expected_decision_date"),
        "expected_annual_kwh": c.get("expected_annual_kwh"),
        "lost_reason": c.get("lost_reason"), "revisit_on": c.get("revisit_on"),
        "locations": locs, "location_count": len(locs),
        "earliest_contract_end": ends[0] if ends else cf.get("contract_end_date"),
        "quotes": {s: sum(1 for l in locs if (l.get("quote_status") or "not_requested") == s)
                   for s in QUOTE_STATUSES},
        "tier": lead.tier,
    }


def _open_tasks(db, lead, title=None):
    from app.models.work_models import LeadTask
    q = db.query(LeadTask).filter(LeadTask.lead_id == lead.id, LeadTask.source == TASK_SOURCE,
                                  LeadTask.status == "open")
    if title:
        q = q.filter(LeadTask.title == title)
    return q.all()


def set_stage(db, lead, *, stage: str, actor_id: Optional[str], note: str = "",
              reason: str = "", revisit_on=None, expected_decision_date=None,
              expected_annual_kwh=None) -> Dict[str, Any]:
    from app.models.work_models import LeadTask
    if stage not in STAGES:
        raise ValueError("Unknown stage.")
    cf = _cf(lead)
    _require_commercial(cf)
    c = cf.get("commercial") if isinstance(cf.get("commercial"), dict) else {}
    current = c.get("stage") or "discovery"
    if current in ("won", "lost") and stage != current:
        raise ValueError("This opportunity is closed (%s). Reopen it by recording a new request." % current)
    if stage == "won" and current != "supplier_confirmed":
        raise ValueError("Won only after the supplier confirmed the contract (stage supplier_confirmed).")
    if stage == "lost" and not (reason or "").strip():
        raise ValueError("Say why it was lost.")
    revisit = _date(revisit_on, "revisit_on")
    if stage == "deferred" and not revisit:
        raise ValueError("A deferred opportunity needs a revisit date.")
    now = datetime.utcnow()
    c["history"] = list(c.get("history") or []) + [{
        "at": _stamp(now), "by": actor_id, "from": current, "to": stage,
        "note": (note or "").strip()[:500] or None, "reason": (reason or "").strip()[:300] or None}]
    c["stage"] = stage
    if expected_decision_date not in (None, ""):
        c["expected_decision_date"] = _date(expected_decision_date, "expected_decision_date")
    if expected_annual_kwh not in (None, ""):
        c["expected_annual_kwh"] = str(expected_annual_kwh).strip()[:20]
    if stage == "lost":
        c["lost_reason"] = reason.strip()[:300]
        lead.pipeline_lost_at = now
        lead.pipeline_lost_reason = c["lost_reason"]
    if stage == "deferred":
        c["revisit_on"] = revisit
    cf["commercial"] = c
    lead.custom_fields = json.dumps(cf)

    tier = TIER_FOR.get(stage)
    if tier and (lead.tier or "") in TIER_ORDER and \
            TIER_ORDER.index(tier) > TIER_ORDER.index(lead.tier or ""):
        lead.tier = tier                                   # forward only, never contract_signed

    keep = REMINDERS.get(stage, (None,))[0]
    closed = 0
    for t in _open_tasks(db, lead):
        if t.title != keep:
            t.status = "done" if stage not in ("lost", "deferred") else "cancelled"
            t.completed_at = now
            closed += 1
    created = None
    owner = lead.assigned_to_id or actor_id
    if keep and not _open_tasks(db, lead, keep):
        days = REMINDERS[stage][1]
        t = LeadTask(organization_id=lead.organization_id, lead_id=lead.id, title=keep,
                     details="Commercial stage: %s. Internal reminder - nothing was sent." % stage,
                     due_at=now + timedelta(days=days), status="open", assigned_to_id=owner,
                     created_by_id=actor_id, source=TASK_SOURCE)
        db.add(t)
        db.flush()
        created = t.id
    if stage == "deferred":
        t = LeadTask(organization_id=lead.organization_id, lead_id=lead.id,
                     title="Revisit deferred commercial opportunity",
                     details="Deferred: %s" % ((note or reason or "").strip()[:300] or "no note"),
                     due_at=datetime.fromisoformat(revisit + "T15:00:00"), status="open",
                     assigned_to_id=owner, created_by_id=actor_id, source=TASK_SOURCE)
        db.add(t)
        db.flush()
        created = t.id
    out = view(lead)
    out.update({"tasks_closed": closed, "task_created": created, "enrolled": False})
    return out


def _clean_location(data: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in (data or {}).items():
        if k not in LOCATION_FIELDS:
            raise ValueError("Unknown location field: %s" % k)
        v = "" if v is None else str(v).strip()[:LOCATION_FIELDS[k]]
        if k == "contract_end_date" and v:
            v = _date(v, "contract_end_date")
        if k == "quote_status" and v and v not in QUOTE_STATUSES:
            raise ValueError("quote_status must be one of: %s" % ", ".join(QUOTE_STATUSES))
        out[k] = v
    return out


def _same_site(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    norm = lambda s: " ".join(str(s or "").lower().replace(",", " ").split())  # noqa: E731
    if a.get("meter_id") and b.get("meter_id"):
        return norm(a["meter_id"]) == norm(b["meter_id"])
    return bool(a.get("address")) and norm(a.get("address")) == norm(b.get("address"))


def add_location(lead, data: Dict[str, Any]) -> Dict[str, Any]:
    cf = _cf(lead)
    _require_commercial(cf)
    loc = _clean_location(data)
    if not loc.get("address"):
        raise ValueError("A service location needs an address.")
    c = cf.get("commercial") if isinstance(cf.get("commercial"), dict) else {}
    locs = [l for l in (c.get("locations") or []) if isinstance(l, dict)]
    dup = next((l for l in locs if _same_site(l, loc)), None)
    if dup is not None:
        # Same site again: keep what is recorded, but fill any blanks the new
        # entry supplies (e.g. the meter id arrives after the address).
        filled = [k for k, v in loc.items() if v not in (None, "") and dup.get(k) in (None, "")]
        for k in filled:
            dup[k] = loc[k]
        if filled:
            c["locations"] = locs
            cf["commercial"] = c
            lead.custom_fields = json.dumps(cf)
        return {"location": dup, "duplicate": True, "filled": filled, **view(lead)}
    loc["id"] = uuid.uuid4().hex[:10]
    loc.setdefault("quote_status", "not_requested")
    locs.append(loc)
    c["locations"] = locs
    cf["commercial"] = c
    lead.custom_fields = json.dumps(cf)
    return {"location": loc, "duplicate": False, **view(lead)}


def update_location(lead, loc_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cf = _cf(lead)
    _require_commercial(cf)
    c = cf.get("commercial") if isinstance(cf.get("commercial"), dict) else {}
    locs = [l for l in (c.get("locations") or []) if isinstance(l, dict)]
    loc = next((l for l in locs if l.get("id") == loc_id), None)
    if loc is None:
        raise LookupError("Location not found on this opportunity.")
    for k, v in _clean_location(data).items():
        if v == "":
            loc.pop(k, None)
        else:
            loc[k] = v
    c["locations"] = locs
    cf["commercial"] = c
    lead.custom_fields = json.dumps(cf)
    return {"location": loc, **view(lead)}
