"""Promote ONE org contact to ONE lead — an explicit human action.

Universal Intake keeps WHO A PERSON IS in `org_contacts`. A contact becomes a
Lead during an import only when the import decided it was an opportunity
(`commit.activate_lead`). This module is the other door: a person looking at a
contact in the workspace clicks "Promote to lead".

WHAT PROMOTION DOES
    * creates exactly one Lead in the contact's organization;
    * links both ways: Lead.org_contact_id = contact.id, contact.lead_id = lead.id;
    * copies identity for display (name, channels, address) the way
      `activate_lead` does, so the Lead reads like an imported one;
    * respects the plan's lead ceiling by HOLDING (lead_capacity), never by
      dropping the person the operator just chose.

WHAT PROMOTION NEVER DOES
    * grant any consent or permission. `sms_consent`, `allow_sms`,
      `allow_email`, `allow_bulk_email`, `allow_voice` are never set to True.
      A DENIAL the contact already carries (unsubscribed / suppressed email,
      opted-out / DNC / suppressed SMS) is carried as False, because a new
      Lead that silently forgot an opt-out would be the worse defect;
    * enroll a cadence, create AutoSend items, start an AI conversation, or send
      anything. The Lead is created with status "new" and no message track;
    * change any other field on the contact.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError

from app.models.intake_models import OrgContact, SmsStatus

log = logging.getLogger(__name__)

# Lead.source for a lead a person promoted from a contact. Also the marker the
# import rollback reads so it never deletes a contact a person promoted.
PROMOTION_SOURCE = "contact_promotion"

_SMS_DENY = (SmsStatus.OPTED_OUT, SmsStatus.DNC, SmsStatus.SUPPRESSED)


_PERMISSION_FIELDS = ("allow_email", "allow_bulk_email", "allow_sms", "allow_voice")


class DoNotContact(Exception):
    """The contact is on do-not-contact; the import refuses it the same way
    (commit.may_activate_lead -> (False, "dnc"))."""


def source_denials(db, org_id: str, contact: OrgContact) -> dict:
    """{permission_field: False} for every DENIAL the record carries. Never True.

    Read from the contact's own statuses and from the most recent staged row
    that committed into this contact (same organization) - the only place the
    source's voice / bulk-email refusals are kept.
    """
    from app.models.import_models import ImportStagedRow
    out = {}
    row = (db.query(ImportStagedRow)
           .filter(ImportStagedRow.organization_id == org_id,
                   ImportStagedRow.committed_contact_id == contact.id)
           .order_by(ImportStagedRow.created_at.desc(), ImportStagedRow.id.desc())
           .first())
    if row is not None:
        for f, v in (("allow_email", row.consent_email),
                     ("allow_bulk_email", row.consent_bulk_email),
                     ("allow_sms", row.consent_sms),
                     ("allow_voice", row.consent_voice)):
            if v is False:
                out[f] = False
    from app.services.intake import commit as CM
    if contact.email_status in CM._DENY_EMAIL:
        out["allow_email"] = False
    if contact.sms_status in _SMS_DENY:
        out["allow_sms"] = False
    if contact.sms_status in (SmsStatus.DNC, SmsStatus.SUPPRESSED):
        out["allow_voice"] = False
        out["allow_bulk_email"] = False
    assert all(v is False for v in out.values())
    return out


class AlreadyPromoted(Exception):
    def __init__(self, lead_id: Optional[str]):
        super().__init__(lead_id)
        self.lead_id = lead_id


def existing_lead_id(db, org_id: str, contact: OrgContact) -> Optional[str]:
    """The lead this contact already is, in THIS organization, if any."""
    from app.models.models import Lead
    if contact.lead_id:
        hit = (db.query(Lead.id).filter(Lead.id == contact.lead_id,
                                        Lead.organization_id == org_id).scalar())
        if hit:
            return hit
    return (db.query(Lead.id).filter(Lead.organization_id == org_id,
                                     Lead.org_contact_id == contact.id)
            .order_by(Lead.created_at.asc()).limit(1).scalar())


def _j(s, d):
    try:
        return json.loads(s) if s else d
    except Exception:  # noqa: BLE001
        return d


def build_lead(db, org_id: str, contact: OrgContact, *, tier: str,
               assigned_to_id: Optional[str], actor_name: Optional[str],
               note: Optional[str] = None):
    """(Lead, held) for this contact; the lead is not yet added to the session.

    The plan-capacity decision lives HERE, beside the only line that builds the
    Lead (as in `commit.activate_lead`), so no caller can create one unchecked.
    Promotion follows the arrival rule: over the ceiling the lead is HELD
    (lead_capacity), never refused - the operator chose this person.

    Mirrors `commit.activate_lead`'s construction (same phone format, same
    bad-email flag, same custom-field carry-over) but reads the CONTACT, which
    is the canonical record here, rather than a staged import row.
    """
    from app.models.models import Lead
    from app.services.intake import commit as CM
    from app.services.intake import engine as ENG

    cd = None
    if contact.classification:
        try:
            cd = ENG.org_catalog(db, org_id).get(contact.classification)
        except Exception:  # noqa: BLE001 - a catalog miss only loses a label
            cd = None
    custom = {}
    if contact.company:
        custom["company"] = contact.company
    if contact.job_title:
        custom["job_title"] = contact.job_title
    if contact.classification:
        custom["classification"] = contact.classification
    custom["record_class"] = contact.record_class
    custom.update(_j(contact.custom_fields, {}) or {})
    custom.update(_j(contact.vertical_fields, {}) or {})

    email = contact.email or None
    manual_flag = manual_reason = None
    if contact.email_status in CM._BAD_EMAIL and email:
        manual_flag, manual_reason = "bad_email", f"contact: {contact.email_status}"
    phone = CM._lead_phone(contact.mobile_phone or contact.phone)
    phone_raw = (contact.mobile_phone_raw if contact.mobile_phone else contact.phone_raw)

    lead = Lead(
        organization_id=org_id,
        assigned_to_id=assigned_to_id,
        first_name=contact.first_name, last_name=contact.last_name,
        phone=phone, phone_raw=phone_raw, email=email,
        street_address=contact.street_address, city=contact.city, state=contact.state,
        zip_code=contact.zip_code,
        status="new", tier=tier,
        contact_channel="sms" if phone else "email_only",
        relationship_type=(cd.relationship_type if cd and getattr(cd, "relationship_type", None)
                           else "cold_lead"),
        source=PROMOTION_SOURCE,
        source_detail=contact.source or contact.source_detail,
        source_category=contact.classification,
        imported_by_name=actor_name,
        last_contact_date=contact.last_activity_at,
        manual_flag=manual_flag, manual_flag_reason=manual_reason,
        custom_fields=json.dumps(custom) if custom else None,
        notes=(note or None),
        org_contact_id=contact.id,
        # import_batch_id deliberately NOT stamped: capture.py reads
        # "lead.import_batch_id == batch.id" as "this batch created the lead".
        # Provenance stays on the contact and in the audit entry.
    )
    for f, v in source_denials(db, org_id, contact).items():
        setattr(lead, f, v)
    from app.models.models import Organization
    from app.services import lead_capacity
    org = db.query(Organization).filter(Organization.id == org_id).first()
    held = lead_capacity.hold_if_over_capacity(db, lead, org)
    return lead, held


def promote(db, ctx, contact: OrgContact, *, tier: str, assigned_to_id: Optional[str],
            note: Optional[str] = None) -> dict:
    """Create the lead. Raises AlreadyPromoted. Commits."""
    from app.routers.audit_log_router import log_action
    from app.services import master_contacts

    org_id = ctx.org_id
    prior = existing_lead_id(db, org_id, contact)
    if prior:
        raise AlreadyPromoted(prior)
    if contact.sms_status == SmsStatus.DNC:
        raise DoNotContact()

    lead, held = build_lead(db, org_id, contact, tier=tier, assigned_to_id=assigned_to_id,
                            actor_name=ctx.actor_name, note=note)
    db.add(lead)
    try:
        db.flush()
    except IntegrityError:
        # uq_leads_org_contact: another path linked a lead to this contact first.
        db.rollback()
        raise AlreadyPromoted(existing_lead_id(db, org_id, contact))

    # The link is claimed with a conditional UPDATE, so two simultaneous
    # promotions of one contact cannot both succeed.
    claimed = (db.query(OrgContact)
               .filter(OrgContact.id == contact.id, OrgContact.organization_id == org_id,
                       OrgContact.lead_id.is_(None))
               .update({OrgContact.lead_id: lead.id,
                        # keep updated_at as it was: the contact's own data did
                        # not change, only its link.
                        OrgContact.updated_at: contact.updated_at},
                       synchronize_session=False))
    if not claimed:
        db.rollback()
        db.expire_all()
        fresh = db.query(OrgContact).filter(OrgContact.id == contact.id,
                                            OrgContact.organization_id == org_id).first()
        raise AlreadyPromoted(existing_lead_id(db, org_id, fresh) if fresh else None)

    try:
        master_contacts.record_lead(db, lead, source=PROMOTION_SOURCE,
                                    source_detail=contact.source or None,
                                    ingestion_path="intake.promote")
    except Exception:  # noqa: BLE001 - best effort by construction
        log.exception("master contact retention failed for lead %s", lead.id)

    details = dict(ctx.audit_details()) if hasattr(ctx, "audit_details") else {}
    details.update({"lead_id": lead.id, "tier": tier, "held_over_capacity": bool(held),
                    "assigned_to_id": assigned_to_id,
                    "contact_import_batch_id": contact.import_batch_id})
    log_action(db, org_id, ctx.actor_id, "contact.promoted_to_lead", "org_contact",
               contact.id, details=details, note=note or None, commit=False)
    db.commit()
    return {"lead_id": lead.id, "contact_id": contact.id, "tier": tier,
            "held_over_capacity": bool(held)}
