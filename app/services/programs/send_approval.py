"""Choose & Send: a person picks exactly who gets the program's outreach.

Mike's rule for SCI: every contact can be loaded, but NOTHING automated goes to
anyone until a manager ticks that contact and approves it. This module is that
rule:

  * list_contacts   every loaded contact (one row per person), with whether it
                    is approved and, for the page shown, anything blocking it.
  * preview         exactly what the first text says, per contact. Sends nothing.
  * approve         marks the ticked contacts approved, switches APPROVAL MODE on
                    for the program (from then on identity.send_refusal refuses
                    every automated send to a contact nobody approved), switches
                    on the ticked contacts' campaign families, and starts their
                    cadence. The cadence engine sends the first text at the next
                    permitted hour in the contact's own time zone; the email
                    runner sends the email touches (when its server switch is on).

Holds, reviews, DNC, opt-outs and every other send gate still apply: approving
a contact never overrides them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from app.models.models import CadenceState, Lead, User
from app.models.program_models import CampaignFamily, LocationProfile, OutreachProgram, ProgramSourceRecord
from app.services.programs import identity

MAX_PER_APPROVAL = 25

NOT_APPROVED_REFUSAL = (
    "NOT APPROVED: nobody has approved this contact to receive outreach yet. "
    "Pick it on Family Service Center > Choose & Send.")


def is_approved(recs: Iterable[ProgramSourceRecord]) -> bool:
    return any(getattr(r, "approved_to_send_at", None) for r in recs)


def _people(db: Session, org_id: str) -> Dict[str, List[ProgramSourceRecord]]:
    """lead_id -> its source records (a person may have several rows)."""
    out: Dict[str, List[ProgramSourceRecord]] = {}
    for r in (db.query(ProgramSourceRecord)
              .filter(ProgramSourceRecord.organization_id == org_id,
                      ProgramSourceRecord.lead_id.isnot(None))
              .order_by(ProgramSourceRecord.row_number, ProgramSourceRecord.id).all()):
        out.setdefault(r.lead_id, []).append(r)
    return out


def _master(rows: List[ProgramSourceRecord]) -> ProgramSourceRecord:
    return next((r for r in rows if r.contact_master_key and r.source_lead_id == r.contact_master_key), rows[0])


def _real_us_number(raw) -> bool:
    """10 digits (after a leading 1) whose area code and exchange can exist."""
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    if not (len(d) == 10 and d[0] not in "01" and d[3] not in "01"):
        return False
    try:   # assigned area codes / exchanges - the same rules carriers use
        import phonenumbers
        return phonenumbers.is_valid_number(phonenumbers.parse("+1" + d))
    except ImportError:
        return True


def blocker(db: Session, lead: Lead) -> Optional[str]:
    """Why this contact can't be approved right now, in plain words, or None."""
    from app.services import test_records
    if lead is None:
        return "not found"
    if (lead.status or "").lower() == "dnc" or not test_records.is_outreach_eligible(lead):
        return "Do not contact"
    if not (lead.phone or (lead.email or "").strip()):
        return "No phone or email"
    if lead.phone and not _real_us_number(lead.phone):
        return "Bad phone number"
    if getattr(lead, "phone_line_type", None) == "invalid":
        return "Bad phone number"
    if getattr(lead, "phone_line_type", None) == "landline" and not (lead.email or "").strip():
        return "Landline - call instead"
    from app.services import send_source as _ss
    r = identity.send_refusal(db, lead, _ss.MANUAL)
    if r:
        return r.split(":", 1)[0].capitalize()   # "ON HOLD: ..." -> "On hold"
    return None


def list_contacts(db: Session, prog: OutreachProgram, *, search: str = "", location_id: str = "",
                  family: str = "", show: str = "all", limit: int = 100, offset: int = 0) -> Dict:
    org_id = prog.organization_id
    people = _people(db, org_id)
    leads = {}
    ids = list(people)
    for i in range(0, len(ids), 500):
        for l in db.query(Lead).filter(Lead.organization_id == org_id, Lead.id.in_(ids[i:i + 500])).all():
            leads[l.id] = l
    enrolled = set()
    for i in range(0, len(ids), 500):
        enrolled.update(x for (x,) in db.query(CadenceState.lead_id)
                        .filter(CadenceState.lead_id.in_(ids[i:i + 500])).all())
    pname = {p.location_id: p.official_name for p in db.query(LocationProfile)
             .filter(LocationProfile.organization_id == org_id).all()}
    fname = {f.key: f.name for f in db.query(CampaignFamily).filter(CampaignFamily.organization_id == org_id).all()}
    term = (search or "").strip().lower()
    rows = []
    for lead_id, recs in people.items():
        lead = leads.get(lead_id)
        if lead is None:
            continue
        m = _master(recs)
        approved = is_approved(recs)
        if show == "approved" and not approved:
            continue
        if show == "not_approved" and approved:
            continue
        if location_id and m.location_id != location_id:
            continue
        if family and (m.campaign_family or "") != family:
            continue
        if term:
            hay = " ".join(str(x or "") for x in (lead.first_name, lead.last_name, lead.email, lead.phone,
                                                  m.source_lead_id, pname.get(m.location_id))).lower()
            if term not in hay:
                continue
        rows.append((lead, m, recs, approved))
    total = len(rows)
    page = rows[offset:offset + limit]
    items = []
    for lead, m, recs, approved in page:
        when = max((r.approved_to_send_at for r in recs if getattr(r, "approved_to_send_at", None)), default=None)
        items.append({
            "lead_id": lead.id, "source_lead_id": m.source_lead_id,
            "name": " ".join(x for x in (lead.first_name, lead.last_name) if x) or "(no name)",
            "location": pname.get(m.location_id), "location_id": m.location_id,
            "campaign": fname.get(m.campaign_family, m.campaign_family), "campaign_key": m.campaign_family,
            "phone": lead.phone, "email": lead.email,
            "line_type": getattr(lead, "phone_line_type", None),
            "approved": approved, "approved_at": when.isoformat() + "Z" if when else None,
            "in_campaign": lead.id in enrolled,
            "blocked": None if approved else blocker(db, lead),
        })
    return {"total": total, "items": items, "approval_mode": bool(getattr(prog, "require_send_approval", False))}


def first_text(db: Session, lead: Lead) -> Optional[str]:
    text = identity.cadence_text(db, lead, 1)
    if not text:
        return None
    return identity.apply_sms_signoff(db, lead, text).rstrip() + " Reply STOP to opt out."


def preview(db: Session, prog: OutreachProgram, lead_ids: List[str]) -> Dict:
    from app.services.programs import email_touches
    out = []
    for lid in _clean(lead_ids):
        lead = db.query(Lead).filter(Lead.id == lid, Lead.organization_id == prog.organization_id).first()
        if lead is None or not identity.records_for_lead(db, lead):
            out.append({"lead_id": lid, "name": None, "blocked": "Not a contact in this program"})
            continue
        b = blocker(db, lead)
        prof = identity.location_profile_for_lead(db, lead)
        out.append({
            "lead_id": lid, "name": " ".join(x for x in (lead.first_name, lead.last_name) if x),
            "location": prof.official_name if prof else None,
            "text": first_text(db, lead) if lead.phone else None,
            "gets_text": bool(lead.phone) and lead.contact_channel != "email_only",
            "gets_email": bool((lead.email or "").strip()) and lead.allow_email is not False,
            "blocked": b,
        })
    return {"items": out, "email_sending_on": email_touches.sending_enabled(),
            "max_per_approval": MAX_PER_APPROVAL}


def _clean(ids: List[str]) -> List[str]:
    return list(dict.fromkeys(i for i in (ids or []) if i))


def approve(db: Session, prog: OutreachProgram, user: User, lead_ids: List[str]) -> Dict:
    from app.services.cadence_service import start_cadence
    from app.services.programs import email_touches
    ids = _clean(lead_ids)
    now = datetime.utcnow()
    # APPROVAL MODE ON FIRST: from this moment no automated send reaches a
    # contact nobody approved, so switching families on below cannot leak.
    prog.require_send_approval = True
    approved, skipped, families = [], [], set()
    for lid in ids:
        lead = db.query(Lead).filter(Lead.id == lid, Lead.organization_id == prog.organization_id).first()
        recs = identity.records_for_lead(db, lead) if lead is not None else []
        if lead is None or not recs:
            skipped.append({"lead_id": lid, "reason": "Not a contact in this program"})
            continue
        try:
            from app.services.programs import line_check
            line_check.check_lead(db, lead)
        except Exception:                               # never block an approval on the lookup
            pass
        b = blocker(db, lead)
        if b:
            skipped.append({"lead_id": lid, "reason": b})
            continue
        for r in recs:
            if r.approved_to_send_at is None:
                r.approved_to_send_at, r.approved_to_send_by = now, user.id
        if not lead.assigned_to_id:
            sender = email_touches._sender(db, prog, lead)
            if sender is not None:
                lead.assigned_to_id = sender.id
        families.add(_master(recs).campaign_family or "re_engagement")
        approved.append(lead)
    switched_on = []
    for f in (db.query(CampaignFamily).filter(CampaignFamily.organization_id == prog.organization_id,
                                              CampaignFamily.key.in_(families)).all() if families else []):
        if not f.is_active:
            f.is_active = True
            switched_on.append(f.name)
    db.commit()
    texting, email_only = 0, 0
    for lead in approved:
        if lead.phone and lead.contact_channel != "email_only" and getattr(lead, "phone_line_type", None) != "landline":
            if start_cadence(db, lead) is not None:
                texting += 1
        else:
            email_only += 1
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.send_approved",
               target_type="outreach_program", target_id=prog.id,
               details={"approved": len(approved), "skipped": len(skipped),
                        "lead_ids": [l.id for l in approved], "campaigns_switched_on": switched_on})
    db.commit()
    return {"approved": len(approved), "texting": texting, "email_only": email_only,
            "skipped": skipped, "campaigns_switched_on": switched_on,
            "email_sending_on": email_touches.sending_enabled()}


def unapprove(db: Session, prog: OutreachProgram, user: User, lead_ids: List[str]) -> Dict:
    """Take approval back: stops every automated send to these contacts and
    stops their cadence. A person can still reply by hand."""
    from app.services.cadence_service import stop_cadence_for_lead
    n = 0
    for lid in _clean(lead_ids):
        lead = db.query(Lead).filter(Lead.id == lid, Lead.organization_id == prog.organization_id).first()
        if lead is None:
            continue
        for r in identity.records_for_lead(db, lead):
            r.approved_to_send_at, r.approved_to_send_by = None, None
        stop_cadence_for_lead(db, lead.id, "cancelled")
        n += 1
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.send_unapproved",
               target_type="outreach_program", target_id=prog.id, details={"count": n})
    db.commit()
    return {"unapproved": n}
