"""Deleting an org Contact (org_contacts) safely - the Contacts screen's Delete.

Same rules as a lead delete (app/services/lead_deletion.py), applied to a
contact:
  * FK references are handled from the schema: nullable ones are DETACHED
    (a wholesale buyer / funding partner outlives the contact), NOT NULL ones
    are contact-owned (its alternate source ids) and go with it;
  * plain-string references that carry no FK (leads.org_contact_id, inbound
    call logs, voicemails) are detached explicitly;
  * a contact linked to a LEAD is not blocked: the lead is its own record and
    stays; it just stops pointing at the deleted contact;
  * an opt-out survives the delete: a contact whose SMS status is DNC /
    opted out / suppressed has its numbers written to the suppression list
    first, so re-importing the number can never make it contactable again;
  * import history is left alone: a later rollback of the contact's batch sees
    the contact "already gone" and keeps going;
  * one audit entry per contact, in the same transaction.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.intake_models import OrgContact
from app.models.models import Base
from app.services.lead_deletion import _clear

_OPTED_OUT = {"dnc", "opted_out", "suppressed"}

# (table, column) holding an org_contacts id WITHOUT a foreign key.
_SOFT_REFS = (("leads", "org_contact_id"), ("inbound_call_logs", "org_contact_id"),
              ("voicemails", "org_contact_id"))


def _preserve_opt_out(db: Session, c: OrgContact) -> List[str]:
    if (c.sms_status or "") not in _OPTED_OUT:
        return []
    from app.services import compliance_service
    kept = []
    for phone in {p for p in (c.phone, c.mobile_phone) if p}:
        try:
            entry = compliance_service.add_suppression_entry(
                db, c.organization_id, phone,
                reason="Preserved when the contact was deleted (it was DNC / opted out).")
            kept.append(entry.phone)
        except ValueError:
            continue  # not a usable US number - nothing a suppression could match
    return kept


def delete_contact(db: Session, contact: OrgContact, actor_user_id: Optional[str],
                   commit: bool = True) -> Dict:
    cid, org_id, lead_id = contact.id, contact.organization_id, contact.lead_id
    suppressed = _preserve_opt_out(db, contact)
    counts: Dict[str, int] = {}
    table = Base.metadata.tables["org_contacts"]
    _clear(db, table, [cid], 0, counts, {"org_contacts"})
    for tname, col in _SOFT_REFS:
        t = Base.metadata.tables.get(tname)
        if t is None or col not in t.c:
            continue
        where = t.c[col] == cid
        if "organization_id" in t.c:
            where = where & (t.c.organization_id == org_id)
        res = db.execute(t.update().where(where).values({col: None}))
        if res.rowcount:
            counts["detached:%s.%s" % (tname, col)] = res.rowcount

    from app.routers.audit_log_router import log_action
    log_action(db, org_id, actor_user_id, action="contact.delete",
               target_type="org_contact", target_id=cid,
               details={"name": contact.full_name or " ".join(
                            p for p in (contact.first_name, contact.last_name) if p) or None,
                        "import_batch_id": contact.import_batch_id,
                        "lead_kept": lead_id, "related": counts,
                        "suppression_preserved": suppressed},
               commit=False)
    db.expunge(contact)
    db.execute(table.delete().where(table.c.id == cid))
    if commit:
        db.commit()
    return {"deleted": True, "id": cid, "lead_kept": lead_id,
            "suppression_preserved": bool(suppressed)}
