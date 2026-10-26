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
    from app.services.test_records import exclude_test_program_records
    for r in (exclude_test_program_records(
              db.query(ProgramSourceRecord)
              .filter(ProgramSourceRecord.organization_id == org_id,
                      ProgramSourceRecord.lead_id.isnot(None)))
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


MODES = ("text_email", "email_only", "call")
MODE_LABEL = {"text_email": "Text + Email", "email_only": "Email only", "call": "Call only"}


def textable(lead) -> bool:
    """A real number that is not a landline."""
    return bool(lead.phone) and _real_us_number(lead.phone) \
        and getattr(lead, "phone_line_type", None) not in ("landline", "invalid") \
        and lead.contact_channel != "email_only"


def reaches(lead, mode: Optional[str]) -> str:
    """How this person is reached, in plain words."""
    if mode == "call":
        return "Call only"
    t = textable(lead) and mode != "email_only"
    e = bool((lead.email or "").strip()) and lead.allow_email is not False
    if t and e:
        return "Text + Email"
    if t:
        return "Text only"
    if e:
        return "Email only"
    return "Call only" if lead.phone else "No way to reach"


def _last_sent(db: Session, lead_ids: List[str]) -> Dict[str, Dict[str, Optional[str]]]:
    from sqlalchemy import func
    from app.models.models import EmailMessage, Message
    out: Dict[str, Dict[str, Optional[str]]] = {i: {"text": None, "email": None} for i in lead_ids}
    if not lead_ids:
        return out
    from sqlalchemy import or_
    for lid, at in (db.query(Message.lead_id, func.max(Message.sent_at))
                    .filter(Message.lead_id.in_(lead_ids),
                            or_(Message.delivery_status.is_(None),
                                Message.delivery_status.notin_(("failed", "undelivered"))))
                    .group_by(Message.lead_id).all()):
        out[lid]["text"] = at.isoformat() + "Z" if at else None
    for lid, at in (db.query(EmailMessage.lead_id, func.max(EmailMessage.sent_at))
                    .filter(EmailMessage.lead_id.in_(lead_ids), EmailMessage.status != "failed")
                    .group_by(EmailMessage.lead_id).all()):
        out[lid]["email"] = at.isoformat() + "Z" if at else None
    return out


def reach_for_lead(db: Session, prog: OutreachProgram, lead_id: str) -> Optional[Dict]:
    """How one person on the program is reached, and when we last texted / emailed them."""
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == prog.organization_id).first()
    if lead is None:
        return None
    rows = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == prog.organization_id,
                    ProgramSourceRecord.lead_id == lead_id)
            .order_by(ProgramSourceRecord.row_number, ProgramSourceRecord.id).all())
    if not rows:
        return None
    m = _master(rows)
    mode = getattr(m, "send_mode", None) or "text_email"
    sent = _last_sent(db, [lead_id])[lead_id]
    return {"lead_id": lead_id, "mode": mode, "mode_label": MODE_LABEL.get(mode, mode),
            "reaches": reaches(lead, mode), "line_type": getattr(lead, "phone_line_type", None),
            "last_text_at": sent["text"], "last_email_at": sent["email"]}


def blocker(db: Session, lead: Lead) -> Optional[str]:
    """Why this contact can't be approved right now, in plain words, or None."""
    from app.services import test_records
    if lead is None:
        return "not found"
    if (lead.status or "").lower() == "dnc" or not test_records.is_outreach_eligible(lead):
        return "Do not contact"
    if not (lead.phone or (lead.email or "").strip()):
        return "No phone or email"
    has_email = bool((lead.email or "").strip()) and lead.allow_email is not False
    if not textable(lead) and not has_email:
        if getattr(lead, "phone_line_type", None) == "landline":
            return "Landline - call instead"
        return "Bad phone number"
    from app.services import send_source as _ss
    r = identity.send_refusal(db, lead, _ss.MANUAL)
    if r:
        return r.split(":", 1)[0].capitalize()   # "ON HOLD: ..." -> "On hold"
    rec = identity.source_record_for_lead(db, lead)
    if rec is not None and not rec.campaign_family:
        return "No campaign"                     # #385: never worked under another campaign's words
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
    sent = _last_sent(db, [lead.id for lead, _m, _r, _a in page])
    for lead, m, recs, approved in page:
        when = max((r.approved_to_send_at for r in recs if getattr(r, "approved_to_send_at", None)), default=None)
        items.append({
            "lead_id": lead.id, "source_lead_id": m.source_lead_id,
            "name": " ".join(x for x in (lead.first_name, lead.last_name) if x) or "(no name)",
            "location": pname.get(m.location_id), "location_id": m.location_id,
            "campaign": fname.get(m.campaign_family, m.campaign_family), "campaign_key": m.campaign_family,
            "phone": lead.phone, "email": lead.email, "status": lead.status,
            "line_type": getattr(lead, "phone_line_type", None),
            "mode": getattr(m, "send_mode", None) or "text_email",
            "reaches": reaches(lead, getattr(m, "send_mode", None)),
            "last_text_at": sent[lead.id]["text"], "last_email_at": sent[lead.id]["email"],
            "approved": approved, "approved_at": when.isoformat() + "Z" if when else None,
            "in_campaign": lead.id in enrolled,
            "starts_at": (m.scheduled_start_at.isoformat() + "Z")
            if getattr(m, "scheduled_start_at", None) and m.scheduled_start_at > datetime.utcnow() else None,
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


def approve(db: Session, prog: OutreachProgram, user: User, lead_ids: List[str],
            mode: str = "text_email", start_at: Optional[datetime] = None,
            spacing_minutes: int = 0) -> Dict:
    """start_at (naive UTC) = slow drip: the first contact starts then, each
    next one spacing_minutes later. Nothing - text or email - goes out before
    a contact's start time."""
    from datetime import timedelta
    mode = mode if mode in MODES else "text_email"
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
        fam_key = _master(recs).campaign_family
        fam_row = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == prog.organization_id,
                                                   CampaignFamily.key == fam_key).first()) if fam_key else None
        from app.services.programs import campaigns as _camp
        if fam_row is None:
            # #385: a record with no campaign is never worked under another
            # campaign's words.
            skipped.append({"lead_id": lid, "reason": "No campaign"})
            continue
        if not fam_row.is_active and _camp.activation_blocker(fam_row):
            # #385: approving a family never switches on a campaign that has no
            # approved copy (Central Alabama stays off until it does).
            skipped.append({"lead_id": lid, "reason": "Campaign not approved yet"})
            continue
        starts = (start_at + timedelta(minutes=max(0, spacing_minutes) * len(approved))) if start_at else None
        for r in recs:
            if r.approved_to_send_at is None:
                r.approved_to_send_at, r.approved_to_send_by = now, user.id
            r.send_mode = mode
            r.scheduled_start_at = starts
        if not lead.assigned_to_id:
            sender = email_touches._sender(db, prog, lead)
            if sender is not None:
                lead.assigned_to_id = sender.id
        families.add(fam_key)
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
        if mode == "text_email" and textable(lead):
            state = start_cadence(db, lead)
            if state is not None:
                texting += 1
                starts = _master(identity.records_for_lead(db, lead)).scheduled_start_at
                if starts is not None and (state.current_touch_number or 0) == 0:
                    state.next_touch_due_at = starts
                    db.commit()
        else:
            email_only += 1
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.send_approved",
               target_type="outreach_program", target_id=prog.id,
               details={"approved": len(approved), "skipped": len(skipped),
                        "lead_ids": [l.id for l in approved], "campaigns_switched_on": switched_on})
    db.commit()
    return {"approved": len(approved), "texting": texting, "email_only": email_only, "mode": mode,
            "start_at": start_at.isoformat() + "Z" if start_at else None,
            "spacing_minutes": spacing_minutes if start_at else 0,
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
