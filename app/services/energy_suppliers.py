"""WHAT EACH ATLANTIS SUPPLIER CAN ACTUALLY DO, AND THE MANUAL HANDOFF.

Every supplier entry states what is true today, from what the supplier told
Atlantis - not what an integration might one day do. Where no approved
connection exists, the advisor hands the customer off by hand and the
workspace records it, with a follow-up task, so nothing is lost between
"I sent them to the supplier" and "they are enrolled".

Nothing here sends anything to a customer or a supplier. A handoff record is
the advisor saying what they did.

Provenance: ported from PR #116 (dd73a038), which stays on HOLD. The supplier
descriptions repeat what #116 recorded from Atlantis's agreements; the owner
confirms them before launch. Chariot online enrollment is reported from the
CHARIOT_ALLOW_TRANSACTIONS switch, which stays OFF.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

ENV_COMPAREPOWER_URL = "COMPAREPOWER_AFFILIATE_URL"   # Atlantis's tracking link, set on the server


def _comparepower_link() -> Optional[str]:
    v = (os.environ.get(ENV_COMPAREPOWER_URL) or "").strip()
    return v if v.startswith("https://") else None


def catalog() -> List[Dict[str, Any]]:
    """The suppliers, in the words an advisor needs on the phone."""
    from app.services.providers import chariot
    cp = _comparepower_link()
    return [
        {"key": "chariot", "name": "Chariot Energy", "residential": True, "commercial": False,
         "online_enrollment": chariot.transactions_armed(),
         "how": ("Residential only. Enrollment through EvoSys once switched on; until then the "
                 "advisor enrolls with Chariot directly. Chariot emails the customer its documents; "
                 "there is no status lookup, so every enrollment is confirmed by a person."),
         "confirm_with": "The customer, or Chariot's weekly promo-code activity report"},
        {"key": "comparepower", "name": "ComparePower", "residential": True, "commercial": False,
         "online_enrollment": False, "link": cp, "link_configured": bool(cp),
         "how": ("Send the customer Atlantis's ComparePower link so Atlantis is credited; the "
                 "customer picks and enrolls on ComparePower." if cp else
                 "Atlantis's ComparePower tracking link is not set up in EvoSys yet. Use the link "
                 "from the ComparePower affiliate dashboard and record the handoff here."),
         "confirm_with": "ComparePower affiliate dashboard"},
        {"key": "gexa", "name": "Frontier / Gexa", "residential": True, "commercial": True,
         "online_enrollment": False,
         "how": ("Quote only. Atlantis's agreement is traditional brokerage, not digital "
                 "enrollment. The advisor completes enrollment with Gexa the way the agreement allows."),
         "confirm_with": "Gexa representative"},
        {"key": "commercial", "name": "Commercial procurement", "residential": False, "commercial": True,
         "online_enrollment": False,
         "how": "Advisor-led: usage, bills, sites and meters, then supplier quotes. Never the residential API.",
         "confirm_with": "Signed supplier contract"},
        {"key": "move_concierge", "name": "Move Concierge", "residential": True, "commercial": False,
         "online_enrollment": False,
         "how": "Open a move request in Move Concierge and work its checklist (power, internet, security, movers).",
         "confirm_with": "Each service provider on the move checklist"},
        {"key": "security", "name": "Home security (Revamped / ADT)", "residential": True,
         "commercial": False, "online_enrollment": False,
         "how": ("Partner onboarding not finished; their system is not connected. Only pass a "
                 "customer on with the customer's OK, the way the partner agreement allows."),
         "confirm_with": "Revamped Security"},
    ]


SUPPLIER_KEYS = tuple(s["key"] for s in catalog())


def residential_only_refusal(lead) -> Optional[str]:
    """Why a residential-only supplier (Chariot) must not take this customer,
    or None. Decided from what the lead records: the energy segment and the
    website request kind. Unknown is allowed - the advisor knows the customer."""
    try:
        cf = json.loads(lead.custom_fields or "{}")
        cf = cf if isinstance(cf, dict) else {}
    except (ValueError, TypeError):
        cf = {}
    if str(cf.get("segment") or "").strip().lower().startswith("commercial") \
            or cf.get("request_kind") == "commercial":
        return "Chariot is residential only; this customer is a business."
    if cf.get("request_kind") == "partner":
        return "This record is a partner referral, not the customer. Hand off the referred customer instead."
    return None


# ── HAND-OFF OUTCOMES ──────────────────────────────────────────────────────
# A hand-off is "we sent this customer to that supplier". What happened next
# is a separate fact with two possible sources: what Atlantis staff were told
# or believe (staff_report) and what the supplier itself confirmed
# (supplier_confirmation - a supplier email, portal, report or rep). The two
# are never merged: "confirmed" from a staff report is still not a supplier
# confirmation, and NOTHING here marks a customer enrolled. Enrollment stays
# the explicit Enroll action on Rate Requests.
OUTCOME_REFERRED = "referred"
OUTCOMES = ("awaiting_provider", "customer_action_pending", "confirmed",
            "not_completed", "cancelled", "unknown")
TERMINAL_OUTCOMES = ("confirmed", "not_completed", "cancelled")
SOURCES = ("staff_report", "supplier_confirmation")
TASK_SOURCE = "supplier_handoff"


def _load_cf(lead) -> Dict[str, Any]:
    try:
        cf = json.loads(lead.custom_fields or "{}")
        return cf if isinstance(cf, dict) else {}
    except (ValueError, TypeError):
        return {}


def _stamp(now) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _lock(db: Session, lead):
    """Row-lock the lead for the read-modify-write of its custom fields, so two
    simultaneous clicks cannot both see "no open hand-off" (Postgres; a no-op
    on SQLite, where writes are serialised anyway).

    Sessions run with autoflush off, and populate_existing() reloads the row,
    so pending edits are flushed first - otherwise they would be thrown away.
    Errors propagate: a failed lock must not silently become an unlocked write."""
    from app.models.models import Lead
    db.flush()
    locked = (db.query(Lead).filter(Lead.id == lead.id)
              .with_for_update().populate_existing().first())
    return locked or lead


def handoffs(lead) -> List[Dict[str, Any]]:
    """This lead's hand-offs, oldest first, with older entries normalised
    (entries written before outcomes existed have no id/status)."""
    out = []
    raw = _load_cf(lead).get("supplier_handoffs")
    for i, h in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(h, dict):
            continue
        h = dict(h)
        h.setdefault("id", "legacy-%d" % i)
        h.setdefault("status", OUTCOME_REFERRED)
        h.setdefault("history", [])
        out.append(h)
    return out


def is_open(h: Dict[str, Any]) -> bool:
    return h.get("status") not in TERMINAL_OUTCOMES


def record_handoff(db: Session, *, lead, actor_user_id: Optional[str], supplier: str,
                   note: str, follow_up_days: int = 2) -> Dict[str, Any]:
    """Record that an advisor handed this customer to a supplier, and open a
    follow-up task to confirm the outcome.

    ONE OPEN HAND-OFF PER SUPPLIER. A second request while an earlier hand-off
    to the same supplier is still open (double click, browser retry, a second
    advisor) returns that hand-off and its task with `duplicate: true` instead
    of writing another entry and another task. Once the earlier one has a
    final outcome a new hand-off is a new event and is recorded."""
    import uuid

    from app.models.work_models import LeadTask
    if supplier not in SUPPLIER_KEYS:
        raise ValueError("Unknown supplier.")
    sup = next(s for s in catalog() if s["key"] == supplier)
    lead = _lock(db, lead)
    now = datetime.utcnow()
    existing = [h for h in handoffs(lead) if h.get("supplier") == supplier and is_open(h)]
    if existing:
        h = existing[-1]
        task = (db.query(LeadTask).filter(LeadTask.lead_id == lead.id,
                                          LeadTask.source == TASK_SOURCE,
                                          LeadTask.status == "open",
                                          LeadTask.title == "Confirm outcome with %s" % sup["name"])
                .order_by(LeadTask.created_at.desc()).first())
        db.rollback()                                   # release the row lock; nothing written
        return {"handoff": h, "task_id": task.id if task else None, "supplier": sup,
                "duplicate": True}
    cf = _load_cf(lead)
    entry = {"id": uuid.uuid4().hex[:12], "supplier": supplier, "at": _stamp(now),
             "by": actor_user_id, "note": (note or "").strip()[:500],
             "status": OUTCOME_REFERRED, "history": []}
    prior = cf.get("supplier_handoffs")
    cf["supplier_handoffs"] = (prior if isinstance(prior, list) else []) + [entry]
    lead.custom_fields = json.dumps(cf)
    task = LeadTask(organization_id=lead.organization_id, lead_id=lead.id,
                    title="Confirm outcome with %s" % sup["name"],
                    details="Handed off %s. Confirm with: %s." % (now.strftime("%b %d"), sup["confirm_with"]),
                    due_at=now + timedelta(days=max(0, follow_up_days)), status="open",
                    assigned_to_id=lead.assigned_to_id or actor_user_id,
                    created_by_id=actor_user_id, source=TASK_SOURCE)
    db.add(task)
    db.commit()
    return {"handoff": entry, "task_id": task.id, "supplier": sup, "duplicate": False}


def record_outcome(db: Session, *, lead, actor_user_id: Optional[str], handoff_id: str,
                   outcome: str, source: str, note: str) -> Dict[str, Any]:
    """Record what happened to one hand-off. A final outcome (confirmed /
    not_completed / cancelled) closes its confirmation task; an interim one
    (awaiting_provider / customer_action_pending / unknown) keeps it open.
    Never changes the lead's stage and never marks enrollment."""
    from app.models.work_models import LeadTask
    if outcome not in OUTCOMES:
        raise ValueError("Unknown outcome.")
    if source not in SOURCES:
        raise ValueError("Say whether a person on our side reported this or the supplier confirmed it.")
    if not (note or "").strip():
        raise ValueError("Say how you know (who told you, or where the supplier confirmed it).")
    lead = _lock(db, lead)
    cf = _load_cf(lead)
    raw = cf.get("supplier_handoffs")
    raw = list(raw) if isinstance(raw, list) else []
    # Positions in the stored list (handoffs() skips non-dict junk, so its
    # indexes would not line up with `raw`).
    positions = [i for i, x in enumerate(raw) if isinstance(x, dict)]
    norm = handoffs(lead)
    k = next((j for j, h in enumerate(norm) if h["id"] == handoff_id), None)
    if k is None:
        db.rollback()
        raise LookupError("Hand-off not found on this customer.")
    idx = positions[k]
    h = norm[k]
    if not is_open(h):
        db.rollback()
        raise ValueError("This hand-off already has a final outcome (%s)." % h["status"])
    now = datetime.utcnow()
    h["history"] = list(h.get("history") or []) + [{
        "at": _stamp(now), "by": actor_user_id, "from": h.get("status"), "to": outcome,
        "source": source, "note": note.strip()[:500]}]
    h["status"] = outcome
    h["status_source"] = source
    raw[idx] = h
    cf["supplier_handoffs"] = raw
    lead.custom_fields = json.dumps(cf)
    closed = 0
    if outcome in TERMINAL_OUTCOMES:
        sup = next((s for s in catalog() if s["key"] == h.get("supplier")), None)
        title = "Confirm outcome with %s" % (sup["name"] if sup else h.get("supplier"))
        for t in (db.query(LeadTask).filter(LeadTask.lead_id == lead.id,
                                            LeadTask.source == TASK_SOURCE,
                                            LeadTask.status == "open",
                                            LeadTask.title == title).all()):
            t.status = "done"
            t.completed_at = now
            closed += 1
    db.commit()
    return {"handoff": h, "tasks_closed": closed,
            "enrolled": False,
            "next_step": ("If the supplier confirmed the enrollment, record it with Enroll on Rate Requests."
                          if outcome == "confirmed" and source == "supplier_confirmation" else None)}
