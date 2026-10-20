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


def record_handoff(db: Session, *, lead, actor_user_id: Optional[str], supplier: str,
                   note: str, follow_up_days: int = 2) -> Dict[str, Any]:
    """Record that an advisor handed this customer to a supplier, and open a
    follow-up task to confirm the outcome."""
    from app.models.work_models import LeadTask
    if supplier not in SUPPLIER_KEYS:
        raise ValueError("Unknown supplier.")
    sup = next(s for s in catalog() if s["key"] == supplier)
    now = datetime.utcnow()
    try:
        cf = json.loads(lead.custom_fields or "{}")
        if not isinstance(cf, dict):
            cf = {}
    except (ValueError, TypeError):
        cf = {}
    entry = {"supplier": supplier, "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "by": actor_user_id, "note": (note or "").strip()[:500]}
    cf["supplier_handoffs"] = (cf.get("supplier_handoffs") or []) + [entry]
    lead.custom_fields = json.dumps(cf)
    task = LeadTask(organization_id=lead.organization_id, lead_id=lead.id,
                    title="Confirm outcome with %s" % sup["name"],
                    details="Handed off %s. Confirm with: %s." % (now.strftime("%b %d"), sup["confirm_with"]),
                    due_at=now + timedelta(days=max(0, follow_up_days)), status="open",
                    assigned_to_id=lead.assigned_to_id or actor_user_id,
                    created_by_id=actor_user_id, source="supplier_handoff")
    db.add(task)
    db.commit()
    return {"handoff": entry, "task_id": task.id, "supplier": sup}
