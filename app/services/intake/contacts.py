"""Operator corrections to a person, owned by Universal Intake.

Universal Intake owns WHO A PERSON IS (`org_contacts`). A module that lets an
operator correct a person's name, email or phone (Wholesale's seller editor
today, any other editor tomorrow) calls `apply_manual_edit` instead of writing
the fields itself, so that:

* the org contact is the record that changes, and the Lead mirrors it;
* every corrected field is added to `manually_edited_fields`, which
  `commit.update_contact` and the matched-lead blank-fill both honour: a later
  import of the old spelling can never overwrite (or refill) an operator's
  correction, whatever the batch's update policy says;
* the correction is audited as an org_contact change, with before/after.

Channels are stored in each record's own format: E.164 on the org contact,
the platform format (`dedup_service.normalize_phone`, "12145550123") on the
Lead, because every Lead reader (inbound SMS lookup, suppression, dedupe)
compares that format.

A Lead that predates Universal Intake has no org contact. Nothing is invented
for it here: the Lead is edited directly by the caller, and intake's blank-fill
still never overwrites a value the Lead already holds.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from app.models.intake_models import OrgContact

log = logging.getLogger(__name__)

EDITABLE = ("first_name", "last_name", "email", "phone")


def _j(s, d):
    try:
        return json.loads(s) if s else d
    except Exception:  # noqa: BLE001
        return d


def protected_fields(contact: Optional[OrgContact]) -> set:
    """Fields an operator corrected by hand on this contact."""
    return set(_j(getattr(contact, "manually_edited_fields", None), [])) if contact else set()


def contact_for_lead(db, org_id: str, lead) -> Optional[OrgContact]:
    """The org contact this Lead belongs to, in THIS organization only."""
    if lead is None:
        return None
    q = db.query(OrgContact).filter(OrgContact.organization_id == org_id,
                                    OrgContact.archived_at.is_(None))
    cid = getattr(lead, "org_contact_id", None)
    if cid:
        c = q.filter(OrgContact.id == cid).first()
        if c is not None:
            return c
    return q.filter(OrgContact.lead_id == lead.id).order_by(OrgContact.created_at.asc()).first()


def _full_name(first: Optional[str], last: Optional[str]) -> Optional[str]:
    return " ".join(p for p in (first, last) if p) or None


def apply_manual_edit(db, org_id: str, lead, changes: Dict[str, Any], *,
                      actor_id: Optional[str] = None, actor_name: Optional[str] = None,
                      source: str = "operator_edit") -> List[str]:
    """Write an operator's correction through the org contact.

    `changes` holds only the fields the operator sent, already validated:
    first_name / last_name / email as cleaned strings or None (cleared), and
    phone as E.164 or None. Returns the org-contact fields that changed (empty
    when the Lead has no org contact). The caller still mirrors the values
    onto the Lead - that is the Lead's own format and its own rules (consent
    does not travel to a new number), which stay with the caller.
    """
    contact = contact_for_lead(db, org_id, lead)
    if contact is None:
        return []
    edits = {k: v for k, v in changes.items() if k in EDITABLE}
    if not edits:
        return []
    before: Dict[str, Any] = {}
    changed: List[str] = []

    def put(field: str, value):
        cur = getattr(contact, field)
        if cur != value:
            before[field] = cur
            setattr(contact, field, value)
            changed.append(field)

    for f, v in edits.items():
        put(f, v)
        if f == "email":
            put("email_raw", v)
        elif f == "phone":
            put("phone_raw", v)
    if "first_name" in edits or "last_name" in edits:
        put("full_name", _full_name(contact.first_name, contact.last_name))

    marked = protected_fields(contact)
    # Mark the fields the operator SENT, changed or not: confirming a value is
    # a correction too, and an import must not later "fix" it.
    sent = set(edits)
    if sent & {"first_name", "last_name"}:
        sent.add("full_name")
    new_marks = sorted(marked | sent)
    if new_marks != sorted(marked):
        contact.manually_edited_fields = json.dumps(new_marks)
    if not changed:
        return []
    contact.updated_at = datetime.utcnow()

    if actor_id:
        try:
            from app.routers.audit_log_router import log_action
            log_action(db, org_id, actor_id, "intake.contact_edited", "org_contact",
                       contact.id, details={"fields": changed, "source": source,
                                            "lead_id": getattr(lead, "id", None),
                                            "actor_name": actor_name},
                       before=before, after={f: getattr(contact, f) for f in changed},
                       commit=False)
        except Exception:  # noqa: BLE001 - an audit failure is loud, not fatal
            log.exception("org contact edit audit failed: contact=%s", contact.id)
    return changed


def mark_protected(fill: Dict[str, Any], protected: Iterable[str]) -> Dict[str, Any]:
    """Drop the fields an operator corrected from an import's blank-fill map."""
    p = set(protected or ())
    aliases = {"phone_raw": "phone", "email_raw": "email"}
    return {k: v for k, v in fill.items() if k not in p and aliases.get(k) not in p}
