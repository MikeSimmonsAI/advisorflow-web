"""#449: Start / Resume Journey for a campaign that speaks only its own words
(Central Alabama).

WHY THIS EXISTS. Preparing Central Alabama marked 349 families Approved to
Send but started nothing. Switching the campaign on (Oct 31, 11:39 AM CT) let
the EMAIL runner find every approved family at once - it sent touch 1 to 50
of them in two batches - while no text journey existed for anyone, because
approval never created one and Choose & Send skips approved families.

Now:
  * approval alone sends nothing (identity.JOURNEY_NOT_STARTED_REFUSAL);
  * a person starts the journey here, for all eligible families or a first
    batch, spaced out; each family gets ONE start, ever (an atomic update on
    journey_started_at, so a double click or a retry after a timeout cannot
    start anyone twice);
  * a family keeps the exact place it reached: an email that already went is
    never repeated (the email schedule continues at its next touch), a text
    journey that exists is never restarted;
  * after a pause, starting again RESUMES: anyone whose next text or email
    fell due while the campaign was off is re-spaced from now instead of all
    going at once.

impact() is read-only. start() never sends anything itself - the existing
cadence and email runners do, at the scheduled times, through every existing
check (campaign on, contact hours, STOP / DNC / suppression, replies,
bookings, holds, spacing between texts and emails).
"""
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.models import BookingLink, CadenceState, Lead
from app.models.program_models import (CampaignFamily, LocationProfile, OutreachProgram, ProgramEmailTouch,
                                       ProgramResponse, ProgramSourceRecord)

DEFAULT_SPACING_MINUTES = 5
MAX_SPACING_MINUTES = 24 * 60
UNRESOLVED_EMAIL = ("unknown", "claimed")


def _people(db: Session, org_id: str, key: str) -> Dict[str, List[ProgramSourceRecord]]:
    out: Dict[str, List[ProgramSourceRecord]] = {}
    for r in (db.query(ProgramSourceRecord)
              .filter(ProgramSourceRecord.organization_id == org_id,
                      ProgramSourceRecord.campaign_family == key,
                      ProgramSourceRecord.lead_id.isnot(None))
              .order_by(ProgramSourceRecord.row_number, ProgramSourceRecord.id).all()):
        out.setdefault(r.lead_id, []).append(r)
    # Approved families only: a family excluded or held out of this campaign
    # (no approval on any of its rows) is never touched here.
    return {lid: rows for lid, rows in out.items() if any(x.approved_to_send_at for x in rows)}


def _email_usable(lead: Lead) -> bool:
    return bool((lead.email or "").strip()) and lead.allow_email is not False \
        and (lead.manual_flag or "") != "bad_email"


def _assess(db: Session, prog: OutreachProgram, lead: Lead, rows: List[ProgramSourceRecord],
            touches: Dict[int, ProgramEmailTouch], replied: bool, booked: bool,
            state: Optional[CadenceState]) -> Dict:
    from app.services import send_source as _ss
    from app.services import test_records as _tr
    from app.services.compliance_service import is_phone_suppressed
    from app.services.programs import identity, send_approval
    started = any(r.journey_started_at for r in rows)
    mode = next((r.send_mode for r in rows if r.send_mode), None) or "text_email"
    textable = send_approval.textable(lead) and mode not in ("email_only", "call") and lead.allow_sms is not False \
        and not (lead.phone and is_phone_suppressed(db, lead.organization_id, lead.phone))
    email_ok = _email_usable(lead) and mode != "call"
    # #449 (Oct 31, 4:32 PM): an address whose provider the program holds
    # (Outlook / Yahoo / AOL ...) gets no email. A family with no textable
    # phone and only such an address can't be reached - never "started".
    from app.services.programs.email_touches import provider_hold_reason
    email_hold = provider_hold_reason(prog, lead.email) if email_ok else None
    if email_hold:
        email_ok = False
    first_email = touches.get(1)
    out = {"lead_id": lead.id, "started": started, "textable": textable, "email_ok": email_ok,
           "email_1": first_email.status if first_email else None,
           "text_position": (state.current_touch_number or 0) if state else None,
           "text_status": state.status if state else None}
    if started:
        return dict(out, action="already_started", reason="Already started - keeps its place")
    if (lead.status or "").lower() == "dnc" or not _tr.is_outreach_eligible(lead):
        return dict(out, action="hold", reason="Do not contact")
    if any(r.on_hold for r in rows):
        return dict(out, action="hold", reason="On hold")
    if booked:
        return dict(out, action="hold", reason="Appointment booked")
    if replied:
        return dict(out, action="hold", reason="Replied - a person has it")
    review = identity.send_refusal(db, lead, _ss.MANUAL)     # holds, data/duplicate/location review
    if review:
        return dict(out, action="hold", reason=review.split(":")[0].title())
    if any(t.status in UNRESOLVED_EMAIL for t in touches.values()):
        return dict(out, action="hold", reason="An email's outcome is unknown - check it before starting")
    if not textable and email_hold:
        return dict(out, action="hold", reason="Email provider on hold and no phone that takes texts")
    if not textable and not email_ok:
        return dict(out, action="hold", reason="No usable phone or email - correct the record first")
    channel = "Text + email" if textable and email_ok else "Text only" if textable else "Email only"
    note = None
    if first_email is not None and first_email.status == "sent":
        note = "Email 1 already went - emails continue at Email 2"
    return dict(out, action="start", reason=channel, note=note)


def _context(db: Session, org_id: str, ids: List[str]):
    leads, touches, replied, booked, states = {}, {}, set(), set(), {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for l in db.query(Lead).filter(Lead.organization_id == org_id, Lead.id.in_(chunk)).all():
            leads[l.id] = l
        for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == org_id,
                                                    ProgramEmailTouch.lead_id.in_(chunk)).all():
            touches.setdefault(t.lead_id, {})[t.touch_number] = t
        replied.update(x for (x,) in db.query(ProgramResponse.lead_id).filter(
            ProgramResponse.organization_id == org_id, ProgramResponse.lead_id.in_(chunk)).all())
        booked.update(x for (x,) in db.query(BookingLink.lead_id).filter(
            BookingLink.lead_id.in_(chunk), BookingLink.status.in_(("booked", "confirmed", "completed"))).all())
        for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all():
            states[s.lead_id] = s
    return leads, touches, replied, booked, states


def assess_all(db: Session, prog: OutreachProgram, fam: CampaignFamily) -> List[Dict]:
    people = _people(db, prog.organization_id, fam.key)
    leads, touches, replied, booked, states = _context(db, prog.organization_id, list(people))
    names = {p.location_id: p.official_name for p in db.query(LocationProfile)
             .filter(LocationProfile.organization_id == prog.organization_id).all()}
    out = []
    for lid, rows in people.items():
        lead = leads.get(lid)
        if lead is None:
            continue
        a = _assess(db, prog, lead, rows, touches.get(lid, {}), lid in replied, lid in booked, states.get(lid))
        loc = next((r.location_id for r in rows if r.location_id), None)
        a.update(name=("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title(),
                 location=names.get(loc), location_id=loc)
        out.append(a)
    out.sort(key=lambda x: (x["location"] or "", x["name"]))
    return out


RESUME_GRACE = timedelta(minutes=15)


def _marks(fam) -> Dict:
    import json
    try:
        d = json.loads(getattr(fam, "touch_copy_json", None) or "{}")
        return d.get("journey_marks") or {}
    except ValueError:
        return {}


def mark(fam, key: str, at: Optional[datetime] = None) -> None:
    """Remember when the campaign was paused / when journeys were last
    started, on the campaign itself (microsecond precision)."""
    import json
    try:
        d = json.loads(getattr(fam, "touch_copy_json", None) or "{}")
    except ValueError:
        d = {}
    d.setdefault("journey_marks", {})[key] = (at or datetime.utcnow()).isoformat()
    fam.touch_copy_json = json.dumps(d)


def _paused_since_last_start(db: Session, prog: OutreachProgram, fam: CampaignFamily) -> bool:
    """Was the campaign switched off after the last Start / Resume?"""
    m = _marks(fam)
    return bool(m.get("paused_at") and m.get("started_at") and m["paused_at"] > m["started_at"])


def _overdue(db: Session, prog: OutreachProgram, fam: CampaignFamily, rows: List[Dict], now: datetime) -> List[str]:
    """Started families to re-space: after a pause, EVERY started family that
    is not finished (its texts were pushed back hours at a time while the
    campaign was off, and its emails fell due - left alone they would all go
    together when it comes back on); otherwise any whose next text is
    simply long past due."""
    ids = [r["lead_id"] for r in rows if r["action"] == "already_started"]
    if not ids:
        return []
    paused = _paused_since_last_start(db, prog, fam)
    late = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        states = {s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk)).all()}
        emails = {}
        for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == prog.organization_id,
                                                    ProgramEmailTouch.lead_id.in_(chunk)).all():
            emails[t.lead_id] = max(emails.get(t.lead_id, 0), t.touch_number if t.status == "sent" else 0)
        from app.services.programs import email_touches as _et
        for lid in chunk:
            s = states.get(lid)
            texting = s is not None and s.status == "active"
            emailing = emails.get(lid, 0) < _et.max_touches()
            if paused and (texting or emailing):
                late.append(lid)
            elif texting and s.next_touch_due_at and s.next_touch_due_at < now - RESUME_GRACE:
                late.append(lid)
    return late


def impact(db: Session, prog: OutreachProgram, fam: CampaignFamily, now: Optional[datetime] = None) -> Dict:
    """READ-ONLY: what Start / Resume would do right now."""
    now = now or datetime.utcnow()
    rows = assess_all(db, prog, fam)
    holds: Dict[str, int] = {}
    for r in rows:
        if r["action"] == "hold":
            holds[r["reason"]] = holds.get(r["reason"], 0) + 1
    start = [r for r in rows if r["action"] == "start"]
    return {
        "campaign_active": bool(fam.is_active),
        "approved": len(rows),
        "already_started": sum(1 for r in rows if r["action"] == "already_started"),
        "would_resume": len(_overdue(db, prog, fam, rows, now)),
        "will_start": len(start),
        "will_start_text": sum(1 for r in start if r["textable"]),
        "will_start_email_only": sum(1 for r in start if not r["textable"]),
        "email_1_already_sent": sum(1 for r in start if r["email_1"] == "sent"),
        "held": holds,
        "sends_now": 0,
        "families": rows,
    }


def _may_enrol(lead: Lead) -> bool:
    """cadence_service.start_cadence's own enrolment rules, unchanged."""
    from app.services import test_records
    from app.services.lead_capacity import is_held
    if not test_records.is_outreach_eligible(lead):
        return False
    if lead.status in ("dnc", "needs_tier_review", "replied", "booked", "hot") or lead.is_duplicate:
        return False
    if is_held(lead) or lead.contact_channel == "email_only":
        return False
    return True


def start(db: Session, prog: OutreachProgram, fam: CampaignFamily, user, *, count: Optional[int] = None,
          spacing_minutes: int = DEFAULT_SPACING_MINUTES, start_at: Optional[datetime] = None,
          now: Optional[datetime] = None) -> Dict:
    """Start (or resume) the journey. Sends nothing itself; schedules only."""
    now = now or datetime.utcnow()
    if not fam.is_active:
        raise ValueError("Switch the campaign on first. Switching it on sends nothing by itself.")
    spacing = timedelta(minutes=max(0, min(MAX_SPACING_MINUTES, int(spacing_minutes or 0))))
    first = max(start_at or now, now)
    rows = assess_all(db, prog, fam)
    eligible = [r for r in rows if r["action"] == "start"]
    if count is not None:
        eligible = eligible[:max(0, int(count))]
    slot = 0
    started, texting, email_only, raced = 0, 0, 0, 0
    for r in eligible:
        at = first + spacing * slot
        res = db.execute(update(ProgramSourceRecord)
                         .where(ProgramSourceRecord.organization_id == prog.organization_id,
                                ProgramSourceRecord.lead_id == r["lead_id"],
                                ProgramSourceRecord.campaign_family == fam.key,
                                ProgramSourceRecord.journey_started_at.is_(None))
                         .values(journey_started_at=now, journey_started_by=user.id, scheduled_start_at=at)
                         .execution_options(synchronize_session=False))
        if not res.rowcount:
            db.rollback()
            raced += 1                                  # started by another click / pass
            continue
        lead = db.query(Lead).filter(Lead.id == r["lead_id"]).first()
        texts = False
        try:
            if r["textable"] and lead is not None:
                # The text journey is written in the SAME transaction as the
                # start, already at its scheduled time: no moment exists where
                # the family is "started" without it, or due "now" by mistake.
                state = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
                if state is None and _may_enrol(lead):
                    db.add(CadenceState(lead_id=lead.id, status="active", current_touch_number=0,
                                        cadence_started_at=at, next_touch_due_at=at))
                    texts = True
                elif state is not None and state.status == "active":
                    texts = True                        # an existing text journey keeps its place
            db.commit()
        except Exception:                               # noqa: BLE001 - nothing half-started
            db.rollback()
            raise
        slot += 1
        started += 1
        if texts:
            texting += 1
        else:
            email_only += 1
    # RESUME: anyone already started whose next text or email fell due while
    # the campaign was off is re-spaced from now - same place, no burst.
    resumed = 0
    for lid in _overdue(db, prog, fam, rows, now):
        at = first + spacing * slot
        slot += 1
        st = db.query(CadenceState).filter(CadenceState.lead_id == lid).first()
        if st is not None and st.status == "active":
            st.next_touch_due_at = at
        for rec in db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == prog.organization_id,
                                                        ProgramSourceRecord.lead_id == lid,
                                                        ProgramSourceRecord.campaign_family == fam.key).all():
            if rec.scheduled_start_at is None or rec.scheduled_start_at < at:
                rec.scheduled_start_at = at
        resumed += 1
    mark(fam, "started_at", datetime.utcnow())
    db.commit()
    from app.routers.audit_log_router import log_action
    last = first + spacing * max(0, slot - 1)
    log_action(db, prog.organization_id, user.id, action="program.journey_started",
               target_type="program_campaign_family", target_id=fam.id,
               details={"key": fam.key, "started": started, "texting": texting, "email_only": email_only,
                        "resumed": resumed, "spacing_minutes": int(spacing.total_seconds() // 60),
                        "first_at": first.isoformat() + "Z", "last_at": last.isoformat() + "Z",
                        "already_started_by_another_click": raced, "sent_now": 0})
    db.commit()
    return {"started": started, "texting": texting, "email_only": email_only, "resumed": resumed,
            "already_started_by_another_click": raced, "first_at": first.isoformat() + "Z",
            "last_at": last.isoformat() + "Z", "spacing_minutes": int(spacing.total_seconds() // 60),
            "sent_now": 0}
