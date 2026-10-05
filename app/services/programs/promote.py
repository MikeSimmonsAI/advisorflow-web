"""Promote STAGED program records into live contacts and leads. Dry run by default.

ONE CONTACT MASTER -> ONE CONTACT + ONE LEAD. Source rows auto-linked under a
master (same person + same phone/email) share that contact and lead, and EVERY
source Lead ID is recorded against the contact (OrgContactSourceId) and keeps
its own ProgramSourceRecord - 551 IDs in, 551 IDs traceable, nothing deleted.

What a promoted lead does NOT get:
    consent      sms_consent False; allow_* left unset (NULL = never stated)
    a status     "new" - the customer's own status stays in custom_fields
    enrollment   no cadence, no campaign; nothing is sent
Records in Location / Data / Duplicate Review are promoted too (they are real
contacts) but the send gate refuses them until a person clears the review.

Not run in production tonight. This is tomorrow's apply step.
"""
import json
from collections import defaultdict
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.intake_models import OrgContact, OrgContactSourceId
from app.models.models import Lead, Organization, User
from app.models.program_models import OutreachProgram, ProgramSourceRecord
from app.services.programs.normalize import email_key, phone_key


def _lead_phone(raw: Optional[str]) -> Optional[str]:
    p = phone_key(raw)
    return "1" + p if p else None


def plan(db: Session, org: Organization) -> Dict:
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org.id)
            .order_by(ProgramSourceRecord.row_number).all())
    groups: Dict[str, List[ProgramSourceRecord]] = defaultdict(list)
    for r in recs:
        groups[r.contact_master_key or r.source_lead_id].append(r)
    todo = {k: v for k, v in groups.items() if not any(x.lead_id for x in v)}
    return {"records": len(recs), "masters": len(groups), "to_create": len(todo),
            "already_live": len(groups) - len(todo), "groups": todo}


def promote(db: Session, org: Organization, actor: Optional[User] = None, *,
            apply: bool = False, assign_to_id: Optional[str] = None) -> Dict:
    p = plan(db, org)
    summary = {k: v for k, v in p.items() if k != "groups"}
    if not apply:
        summary["applied"] = False
        return summary
    # PLAN CAPACITY: all or nothing. A promotion that stopped halfway would
    # leave some of one customer's contacts live and the rest staged, which is
    # worse than a clear refusal to raise the plan first.
    from app.services import plan_limits
    if p["to_create"]:
        plan_limits.require_capacity(db, org, plan_limits.LIMIT_LEADS, adding=p["to_create"], actor=actor)
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org.id).first()
    owner = assign_to_id or (prog.primary_contact_user_id if prog else None)
    created = 0
    for key, members in p["groups"].items():
        master = next((m for m in members if m.source_lead_id == key), members[0])
        custom = {
            "program_source_lead_ids": [m.source_lead_id for m in members],
            "program_source_status": master.source_status,
            "program_source_campaign": master.source_campaign,
            "program_source_location": master.source_location_name,
            "program_source_owner": master.source_owner,
            "program_source_manager": master.source_manager,
        }
        contact = OrgContact(
            organization_id=org.id, first_name=master.first_name, last_name=master.last_name,
            full_name=("%s %s" % (master.first_name or "", master.last_name or "")).strip() or None,
            email=email_key(master.email), email_raw=master.email,
            phone=("+1" + phone_key(master.phone)) if phone_key(master.phone) else None,
            phone_raw=master.phone, source="Program source file", source_system="program",
            source_record_id=master.source_lead_id, owner_name=master.source_owner,
            source_fields=json.dumps(json.loads(master.raw_json), ensure_ascii=False),
            custom_fields=json.dumps(custom),
        )
        db.add(contact)
        db.flush()
        for m in members:
            db.add(OrgContactSourceId(organization_id=org.id, org_contact_id=contact.id,
                                      source_system="program", source_record_id=m.source_lead_id,
                                      is_primary=(m is master)))
        phone = _lead_phone(master.phone)
        lead = Lead(
            organization_id=org.id, first_name=master.first_name, last_name=master.last_name,
            phone=phone, phone_raw=master.phone, email=email_key(master.email),
            status="new", tier=None, contact_channel="sms" if phone else "email_only",
            source="Program source file", source_detail=master.source_campaign,
            source_category=None, sms_consent=False,
            assigned_to_id=owner, custom_fields=json.dumps(custom), org_contact_id=contact.id,
        )
        db.add(lead)
        db.flush()
        contact.lead_id = lead.id
        for m in members:
            m.lead_id, m.org_contact_id = lead.id, contact.id
        created += 1
    db.commit()
    summary.update({"applied": True, "created": created})
    return summary
