"""Location outreach programs: calls to a location number.

Hooks used by the platform telephony service, for program organizations only
(every function returns early and changes nothing for anyone else):

  greeting_text      the voicemail greeting for a location number
  on_voicemail_text  a transcribed voicemail -> the same reply handling as an
                     email or text (classify, pause, alert, suggested reply)
  on_missed_call     a call nobody answered and no voicemail was left ->
                     a follow-up item with a suggested text, never sent
  location_report    calls, answered, missed, voicemails per location

Live forwarding is the platform's own route on the number
(`default_inbound_route.ring_user_ids`): it rings program members at their
VERIFIED callback numbers. No personal number is stored or guessed here.

THE SCI TOLL-FREE LINE (Mike, 2026-10-08)
-----------------------------------------
Every call to the program's toll-free line goes STRAIGHT to voicemail: no
ringing, no forwarding, no AI receptionist (`voicemail_only_line`). The caller
is matched by phone number inside the program organization:

  one cemetery    -> that cemetery's custom greeting (LocationProfile
                     brand_settings.voicemail_greeting) or a default that
                     names it; the voicemail is saved to that contact and the
                     assigned representative is notified to call back
  ambiguous       -> the number belongs to contacts at DIFFERENT cemeteries:
                     neutral greeting, voicemail to the toll-free review queue
                     (no contact is guessed)
  unknown         -> neutral greeting, toll-free review queue
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _program_and_location(db: Session, org_id: Optional[str], location_id: Optional[str]):
    from app.models.program_models import LocationProfile
    from app.services.programs import identity
    prog = identity.program_for_org(db, org_id)
    if prog is None:
        return None, None
    prof = None
    if location_id:
        prof = (db.query(LocationProfile)
                .filter(LocationProfile.organization_id == org_id,
                        LocationProfile.location_id == location_id,
                        LocationProfile.is_review_bucket.is_(False)).first())
    return prog, prof


def location_for_number(db: Session, phone_number_id: Optional[str]) -> Optional[str]:
    if not phone_number_id:
        return None
    from app.models.telephony_models import PhoneNumber
    rec = db.query(PhoneNumber).filter(PhoneNumber.id == phone_number_id).first()
    return getattr(rec, "workspace_id", None) if rec else None


def _shared_line_pool(db: Session, phone_number_id: Optional[str]):
    """The pool (area-code pool or the toll-free line) a number belongs to, or None."""
    if not phone_number_id:
        return None
    from app.models.telephony_models import PhoneNumber
    from app.services.programs import regional_pools
    rec = db.query(PhoneNumber).filter(PhoneNumber.id == phone_number_id).first()
    return regional_pools.pool_for_phone_number(rec) if rec else None


def voicemail_only_line(db: Session, org_id: Optional[str], phone_number_id: Optional[str]) -> bool:
    """True for a program organization's toll-free line: voicemail only, always."""
    from app.services.programs import identity, regional_pools
    if not org_id or identity.program_for_org(db, org_id) is None:
        return False
    pool = _shared_line_pool(db, phone_number_id)
    return bool(pool) and regional_pools.is_toll_free_pool(pool["pool_id"])


KNOWN, AMBIGUOUS, UNRESOLVED, UNKNOWN = "known", "ambiguous", "unresolved", "unknown"


def caller_match(db: Session, org_id: str, from_raw: Optional[str]):
    """(lead, profile, status) for a caller, by phone number, inside one org.

    known       every contact with this number resolves to ONE cemetery
    ambiguous   contacts with this number resolve to different cemeteries
    unresolved  a contact matches but no cemetery resolves (person known)
    unknown     no contact has this number
    """
    from app.models.models import Lead
    from app.services import number_resolution as NR
    from app.services.programs import identity
    forms = NR.phone_forms(from_raw)
    if not forms or not org_id:
        return None, None, UNKNOWN
    leads = (db.query(Lead).filter(Lead.organization_id == org_id, Lead.phone.in_(forms))
             .order_by(Lead.updated_at.desc()).all())
    if not leads:
        return None, None, UNKNOWN
    resolved = [(lead, identity.location_profile_for_lead(db, lead)) for lead in leads]
    locs = {p.location_id for _, p in resolved if p is not None}
    if len(locs) > 1:
        return None, None, AMBIGUOUS
    if len(locs) == 1:
        if any(p is None for _, p in resolved):
            return None, None, AMBIGUOUS          # some rows unresolved: do not guess
        lead, prof = resolved[0]
        return lead, prof, KNOWN
    return leads[0], None, UNRESOLVED


def caller_for_line(db: Session, org_id: str, from_raw, lead, contact):
    """For the toll-free line: the contact a voicemail is saved to. An
    ambiguous number is saved to no contact (review queue)."""
    found, _prof, status = caller_match(db, org_id, from_raw)
    if status == AMBIGUOUS:
        return None, None
    if found is not None:
        return found, None
    return lead, contact


def location_greeting(prog, prof) -> str:
    """That cemetery's own greeting, or a default that names it."""
    from app.services.programs import identity
    custom = str(identity.brand_settings(prof).get("voicemail_greeting") or "").strip()
    if custom:
        return custom[:500]
    person = (prog.primary_contact_name or "").strip()
    who = person if person else "our planning team"
    return ("Thank you for calling %s. You've reached %s. Please leave your name, number and a "
            "short message after the tone, and we'll get back to you." % (prof.official_name, who))


def neutral_greeting(prog) -> str:
    person = (prog.primary_contact_name or "").strip()
    who = person if person else "our planning team"
    return ("Thank you for calling. You've reached the planning line for %s. Please leave your name, "
            "number and a short message after the tone, and we'll get back to you." % who)


def greeting_text(db: Session, org_id: str, phone_number_id: Optional[str],
                  from_raw: Optional[str] = None) -> Optional[str]:
    """The program's greeting, or None to keep the platform default.

    A location number names its location. A shared line (an area-code pool
    or the toll-free line) carries no location: the CALLER decides - one
    matched cemetery hears that cemetery's greeting; anyone else hears a
    general greeting that names no location, so nobody is told the wrong place."""
    prog, prof = _program_and_location(db, org_id, location_for_number(db, phone_number_id))
    if prog is None:
        return None
    if prof is not None:
        return location_greeting(prog, prof)
    if from_raw and _shared_line_pool(db, phone_number_id) is not None:
        _lead, matched, status = caller_match(db, org_id, from_raw)
        if status == KNOWN and matched is not None:
            return location_greeting(prog, matched)
    return neutral_greeting(prog)


def notify_voicemail(db: Session, vm, lead, assignee_id: Optional[str]) -> Optional[str]:
    """Program orgs: tell the contact's assigned representative (else the
    program's primary contact) there is a voicemail to call back. In-app only;
    nothing is sent to the caller. Returns the notified user id."""
    try:
        from app.models.models import Notification, NotificationType
        from app.services.programs import identity
        prog = identity.program_for_org(db, vm.organization_id)
        if prog is None:
            return None
        uids = [assignee_id or prog.primary_contact_user_id]
        if not uids[0]:
            # Nobody assigned and no program primary contact: the organization's
            # admins, so a voicemail never waits unseen.
            from app.services.programs.responses import _org_admin_users
            uids = [u.id for u in _org_admin_users(db, vm.organization_id)]
        if not uids:
            return None
        where = None
        if lead is not None:
            prof = identity.location_profile_for_lead(db, lead)
            where = prof.official_name if prof else None
        name = (("%s %s" % (getattr(lead, "first_name", "") or "", getattr(lead, "last_name", "") or ""))
                .strip().title() if lead is not None else "") or vm.from_e164 or "Unknown caller"
        no_callback = vm.caller_state in ("dnc", "suppressed")
        msg = ("Voicemail from %s%s on %s - %s" % (
            name, " (%s)" % where if where else "", vm.to_e164 or "the toll-free line",
            "review only, do not call back (Do Not Contact)" if no_callback else "please call back"))
        for uid in uids:
            db.add(Notification(user_id=uid, lead_id=getattr(lead, "id", None),
                                type=NotificationType.REPLY_RECEIVED, message=msg[:500],
                                link=("/leads/%s" % lead.id) if lead is not None else "/program?tab=responses"))
        return uids[0]
    except Exception:                                    # noqa: BLE001
        log.exception("program voicemail notification failed for %s", getattr(vm, "id", "?"))
        return None


def wants_transcription(db: Session, org_id: str) -> bool:
    import os
    from app.services.programs import identity
    if (os.environ.get("PROGRAM_VOICEMAIL_TRANSCRIBE") or "on").strip().lower() in ("0", "off", "false", "no"):
        return False
    return identity.program_for_org(db, org_id) is not None


def on_voicemail_text(db: Session, vm) -> Optional[object]:
    """A transcribed voicemail is handled like any other reply. Never raises."""
    try:
        from app.models.models import Lead
        from app.services.programs import responses
        prog, prof = _program_and_location(db, vm.organization_id, location_for_number(db, vm.phone_number_id))
        if prog is None or not (vm.transcript or "").strip():
            return None
        lead = (db.query(Lead).filter(Lead.id == vm.lead_id, Lead.organization_id == vm.organization_id).first()
                if vm.lead_id else None)
        if lead is None:
            if prof is not None:
                responses.record_unmatched(
                    db, prof, alias=vm.to_e164 or "", sender=vm.from_e164, subject="Voicemail",
                    body=vm.transcript, received_at=vm.received_at,
                    mailbox_message_id="vm:%s" % (vm.recording_sid or vm.id),
                    reason="voicemail to a location number from a caller that matches no contact")
                db.commit()
            else:
                from app.models.telephony_models import PhoneNumber
                from app.services.programs import regional_pools
                rec = db.query(PhoneNumber).filter(PhoneNumber.id == vm.phone_number_id).first()
                pool = regional_pools.pool_for_phone_number(rec) if rec else None
                if pool is not None:             # shared regional number: review queue, no location guessed
                    responses.record_unmatched(
                        db, responses.RegionalReviewBucket(vm.organization_id, pool["pool_id"], pool["label"]),
                        alias=vm.to_e164 or "", sender=vm.from_e164, subject="Voicemail",
                        body=vm.transcript, received_at=vm.received_at,
                        mailbox_message_id="vm:%s" % (vm.recording_sid or vm.id),
                        reason="voicemail to regional pool %s from a caller that matches no contact" % pool["pool_id"])
                    db.commit()
            return None
        if vm.caller_state in ("dnc", "suppressed"):
            return None                                  # reviewed by a person; no outreach suggestions
        return responses.on_inbound(db, lead, vm.transcript, "voicemail",
                                    reply_to_alias=vm.to_e164,
                                    alias_location_id=prof.location_id if prof else None)
    except Exception:                                    # noqa: BLE001
        log.exception("program voicemail handling failed for %s", getattr(vm, "id", "?"))
        try:
            db.rollback()
        except Exception:                                # noqa: BLE001
            pass
        return None


def missed_call_draft(first_name: Optional[str], person: Optional[str], location: Optional[str]) -> str:
    name = (first_name or "").strip().title()
    hi = "Hi %s," % name if name else "Hi,"
    who = person or "I"
    where = " with %s" % location if location else ""
    return ("%s this is %s%s. Sorry I missed your call - how can I help? Just reply here and I'll get "
            "right back to you." % (hi, who, where)).strip()


def on_missed_call(db: Session, row) -> Optional[object]:
    """A known caller rang a program number and nobody answered (no voicemail).

    Records ONE follow-up response per call (ACTIVE, intent missed_call) with
    a suggested text for a person to send. Nothing is sent automatically.
    Pauses the contact's automated sequence like any reply. Never raises."""
    try:
        from app.models.models import Lead
        from app.models.program_models import ProgramResponse
        from app.services.programs import identity, responses
        prog, prof = _program_and_location(db, row.organization_id, location_for_number(db, row.phone_number_id))
        if prog is None or not row.lead_id or row.caller_state in ("dnc", "suppressed"):
            return None
        key = "call:%s" % (row.call_sid or row.id)
        if db.query(ProgramResponse.id).filter(ProgramResponse.reply_id == key).first():
            return None
        lead = db.query(Lead).filter(Lead.id == row.lead_id, Lead.organization_id == row.organization_id).first()
        if lead is None:
            return None
        home = identity.location_profile_for_lead(db, lead) or prof
        rec = identity.source_record_for_lead(db, lead)
        now = datetime.utcnow()
        name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A contact"
        where = home.official_name if home else None
        resp = ProgramResponse(
            organization_id=row.organization_id, lead_id=lead.id, reply_id=key, channel="call",
            reply_to_alias=row.to_e164, location_id=home.location_id if home else None,
            campaign_family=rec.campaign_family if rec else None,
            source_lead_id=rec.source_lead_id if rec else None,
            response_class=responses.ACTIVE, urgency="ACTIVE", body_excerpt="Missed call - no voicemail left.",
            summary="%s%s called %s - missed, no voicemail." % (name, " (%s)" % where if where else "", row.to_e164),
            recommended_action="Call back or send the suggested text. Nothing has been sent.",
            received_at=now, handling_status="new")
        import json as _json
        resp.intents = _json.dumps(["missed_call"])
        resp.suggested_reply = missed_call_draft(lead.first_name, prog.primary_contact_name, where)
        resp.cadence_paused = responses._pause_cadence(db, lead)
        db.add(resp)
        db.flush()
        responses._alert(db, prog, resp, lead, responses.ACTIVE, "📞 %s" % resp.summary,
                         management=False, external=False)
        db.commit()
        return resp
    except Exception:                                    # noqa: BLE001
        log.exception("program missed-call handling failed for %s", getattr(row, "id", "?"))
        try:
            db.rollback()
        except Exception:                                # noqa: BLE001
            pass
        return None


def location_report(db: Session, org_id: str, since: Optional[datetime] = None) -> List[Dict]:
    """Per location: calls, answered, missed, voicemails, transcribed."""
    from app.models.program_models import LocationProfile
    from app.models.telephony_models import InboundCallLog, PhoneNumber, Voicemail
    nums = {n.id: n.workspace_id for n in db.query(PhoneNumber).filter(PhoneNumber.organization_id == org_id)}
    names = {p.location_id: p.official_name for p in db.query(LocationProfile)
             .filter(LocationProfile.organization_id == org_id)}
    q = db.query(InboundCallLog).filter(InboundCallLog.organization_id == org_id)
    vq = db.query(Voicemail).filter(Voicemail.organization_id == org_id)
    if since is not None:
        q = q.filter(InboundCallLog.created_at >= since)
        vq = vq.filter(Voicemail.received_at >= since)
    out: Dict[Optional[str], Dict] = {}

    def slot(num_id):
        loc = nums.get(num_id)
        return out.setdefault(loc, {"location_id": loc, "location": names.get(loc, "Not tied to a location"),
                                    "calls": 0, "answered": 0, "missed": 0, "voicemails": 0, "transcribed": 0})
    for c in q.all():
        s = slot(c.phone_number_id)
        s["calls"] += 1
        if c.status == "answered":
            s["answered"] += 1
        elif c.status in ("no_voicemail", "voicemail", "missed", "no-answer"):
            s["missed"] += 1
    for v in vq.all():
        s = slot(v.phone_number_id)
        s["voicemails"] += 1
        s["transcribed"] += int(v.transcript_status == "completed")
    return sorted(out.values(), key=lambda r: r["location"])
