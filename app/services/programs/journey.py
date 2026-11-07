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
# #449 (Nov 1): the full campaign is released on its own, one family every
# few minutes, ONLY inside a daily window on business days. A start that would
# fall after the window rolls to the next business morning - never into the
# night, where the senders would hold it and then release a pile at once.
# Nov 7 (owner): SCI outreach runs SEVEN days a week, 9 AM - 6 PM Central
# (send_window). Saturday and Sunday are ordinary release days now.
RELEASE_TZ = "America/Chicago"
RELEASE_START = (9, 0)          # first new start of a day, local
RELEASE_END = (18, 0)           # no new start at or after this, local
RELEASE_WEEKDAYS_ONLY = False
# (Texts and emails are allowed 8:30 AM - 7:45 PM Eastern = 7:30 AM - 6:45 PM
# Central; the window sits inside that with room for a sender pass.)


def release_slots(first: datetime, n: int, spacing: timedelta, *, tz: Optional[str] = None,
                  day_start=None, day_end=None, weekdays_only: Optional[bool] = None) -> List[datetime]:
    """n start times (naive UTC) from `first`, `spacing` apart, every one
    inside [day_start, day_end) local on an allowed day; the next one after the
    window closes is the next allowed day's opening time."""
    from datetime import time as _t, timezone as _tzu
    from zoneinfo import ZoneInfo
    tz = tz or RELEASE_TZ
    day_start = day_start or RELEASE_START
    day_end = day_end or RELEASE_END
    weekdays_only = RELEASE_WEEKDAYS_ONLY if weekdays_only is None else weekdays_only
    z = ZoneInfo(tz)
    open_t, close_t = _t(*day_start), _t(*day_end)

    def ok_day(d) -> bool:
        return not weekdays_only or d.weekday() < 5

    def opening(d):                                      # local date -> aware local datetime
        return datetime.combine(d, open_t).replace(tzinfo=z)

    def first_ok(local):
        while True:
            if ok_day(local.date()) and open_t <= local.timetz().replace(tzinfo=None) < close_t:
                return local
            if ok_day(local.date()) and local.timetz().replace(tzinfo=None) < open_t:
                return opening(local.date())
            nxt = local.date() + timedelta(days=1)
            while not ok_day(nxt):
                nxt += timedelta(days=1)
            local = opening(nxt)

    out = []
    cur = first_ok(first.replace(tzinfo=_tzu.utc).astimezone(z))
    for i in range(max(0, n)):
        if i:
            # step in real (UTC) time so a clock change never shifts the gap
            cur = first_ok((cur.astimezone(_tzu.utc) + spacing).astimezone(z))
        out.append(cur.astimezone(_tzu.utc).replace(tzinfo=None))
    return out
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


def _return_round(db: Session, prog: OutreachProgram, lead: Lead) -> bool:
    """Is this lead in a later round of a rotating campaign (Issue #507)?"""
    from app.services.programs import rotation
    try:
        m = rotation.open_member(db, prog.organization_id, lead.id)
    except Exception:                                    # noqa: BLE001
        db.rollback()
        return False
    return m is not None and (m.round_number or 1) > 1


def assess_all(db: Session, prog: OutreachProgram, fam: CampaignFamily) -> List[Dict]:
    people = _people(db, prog.organization_id, fam.key)
    leads, touches, replied, booked, states = _context(db, prog.organization_id, list(people))
    from app.services.programs.campaigns import email_schedule as _own_days
    if _own_days(fam.key):
        # Issue #507: a later round sees only ITS OWN emails (touch base+n),
        # and only replies that are not automatic.
        from app.services.programs import rotation
        bases = rotation.email_bases(db, prog.organization_id, list(people))
        touches = {lid: {n - bases.get(lid, 0): t for n, t in ts.items()
                         if bases.get(lid, 0) < n <= bases.get(lid, 0) + 99}
                   for lid, ts in touches.items()}
        replied = rotation.genuine_repliers(db, prog.organization_id, list(people)) & set(replied)
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
    """Started families whose next TEXT was actually held back: it is past due,
    or the sender refused it because the campaign was off (it then re-checks
    every few hours, which would bunch everyone together when it comes back).

    #449 (Nov 1, 11:56 PM): an off/on toggle of 16 seconds used to count as a
    pause that re-spaced EVERY started family - and put them after 270 new
    starts, pushing Batch 2's first texts from Monday 9 AM to Wednesday
    evening. A family whose next text is still ahead and was never refused is
    left exactly where it is; emails are never re-spaced here (their sender
    paces itself)."""
    from app.models.models import CadenceTouchLog
    ids = [r["lead_id"] for r in rows if r["action"] == "already_started"]
    if not ids:
        return []
    paused_at = _marks(fam).get("paused_at")
    paused_at = datetime.fromisoformat(paused_at) if paused_at else None
    late = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        states = {s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(chunk),
                                                                     CadenceState.status == "active").all()}
        refused = set()
        if paused_at is not None and states:
            refused = {g.lead_id for g in db.query(CadenceTouchLog).filter(
                CadenceTouchLog.lead_id.in_(list(states)), CadenceTouchLog.created_at >= paused_at,
                CadenceTouchLog.reason.like("%CAMPAIGN OFF%")).all()}
        for lid in chunk:
            s = states.get(lid)
            if s is None:
                continue
            if lid in refused or (s.next_touch_due_at and s.next_touch_due_at < now - RESUME_GRACE):
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
    overdue = _overdue(db, prog, fam, rows, now)
    slots = release_slots(first, len(eligible) + len(overdue), spacing)
    slot = 0
    started, texting, email_only, raced = 0, 0, 0, 0
    # RESUME FIRST: anyone already started whose next text was held back is
    # re-spaced from now, ahead of new starts - same place, no burst, and
    # never later than a time it already had that is still ahead.
    resumed = 0
    for lid in overdue:
        st = db.query(CadenceState).filter(CadenceState.lead_id == lid).first()
        if st is None or st.status != "active":
            continue
        at = slots[slot]
        if st.next_touch_due_at and st.next_touch_due_at >= now and st.next_touch_due_at < at:
            continue                                    # its own time is sooner: keep it
        slot += 1
        st.next_touch_due_at = at
        resumed += 1
    db.commit()
    for r in eligible:
        at = slots[slot]
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
                elif state is not None and state.status != "active" and _return_round(db, prog, lead) \
                        and _may_enrol(lead):
                    # Issue #507: a RETURNING family's new round restarts its
                    # text journey from text 1 with a new start; the earlier
                    # round's attempts stay as history (cadence_service only
                    # counts attempts since cadence_started_at).
                    state.status, state.current_touch_number = "active", 0
                    state.cadence_started_at, state.next_touch_due_at = at, at
                    state.completed_at = None
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
    mark(fam, "started_at", datetime.utcnow())
    db.commit()
    from app.routers.audit_log_router import log_action
    last = slots[slot - 1] if slot else first
    first = slots[0] if slots else first
    log_action(db, prog.organization_id, user.id, action="program.journey_started",
               target_type="program_campaign_family", target_id=fam.id,
               details={"key": fam.key, "started": started, "texting": texting, "email_only": email_only,
                        "resumed": resumed, "spacing_minutes": int(spacing.total_seconds() // 60),
                        "first_at": first.isoformat() + "Z", "last_at": last.isoformat() + "Z",
                        "already_started_by_another_click": raced, "sent_now": 0})
    db.commit()
    from zoneinfo import ZoneInfo as _Z
    by_day: Dict[str, int] = {}
    for t in slots[:slot]:
        d = t.replace(tzinfo=_Z("UTC")).astimezone(_Z(RELEASE_TZ)).strftime("%a %b %d")
        by_day[d] = by_day.get(d, 0) + 1
    return {"by_day": by_day, "started": started, "texting": texting, "email_only": email_only, "resumed": resumed,
            "already_started_by_another_click": raced, "first_at": first.isoformat() + "Z",
            "last_at": last.isoformat() + "Z", "spacing_minutes": int(spacing.total_seconds() // 60),
            "sent_now": 0}


# ── #449 repair: the Nov 1 11:58 PM resume ──────────────────────────────────

REPAIR_KEY = "repair_resume_1101"


def repair_pushed_starts(db: Session, prog: OutreachProgram, fam: CampaignFamily,
                         now: Optional[datetime] = None) -> Dict:
    """Undo what the old Resume did on Nov 1 (16-second off/on, then START 270):
    it moved every already-started family to the END of the queue.

    * A family whose FIRST text never went and whose text is now set more than
      a day after its own start (Batch 2: due Mon 9-11 AM, moved to Wed/Thu)
      goes back to the FRONT; every family still waiting for its first
      message is re-spaced behind it, 5 minutes apart, inside the weekday
      window - so nothing doubles up and nothing is sent at once.
    * A family already texted gets back the next-text time its own schedule
      gives (never earlier than now), and its emails are no longer held
      behind the moved start time.
    Changes schedules only: nothing is sent, nothing in anyone's history is
    touched, nobody is restarted. Runs once (marked on the campaign)."""
    from app.models.models import CadenceTouchLog, Message
    from app.services import cadence_service as cs
    import json as _json
    now = now or datetime.utcnow()
    try:
        d = _json.loads(fam.touch_copy_json or "{}")
    except ValueError:
        d = {}
    if (d.get("repairs") or {}).get(REPAIR_KEY):
        return {"skipped": "already done"}
    recs: Dict[str, List[ProgramSourceRecord]] = {}
    for r in (db.query(ProgramSourceRecord)
              .filter(ProgramSourceRecord.organization_id == prog.organization_id,
                      ProgramSourceRecord.campaign_family == fam.key,
                      ProgramSourceRecord.journey_started_at.isnot(None)).all()):
        recs.setdefault(r.lead_id, []).append(r)
    ids = list(recs)
    states = {s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(ids or ["-"])).all()}
    texted = {x for (x,) in db.query(Message.lead_id).filter(Message.lead_id.in_(ids or ["-"])).distinct().all()}
    began = {lid: min(x.journey_started_at for x in v) for lid, v in recs.items()}
    emailed = set()
    for t in db.query(ProgramEmailTouch).filter(ProgramEmailTouch.lead_id.in_(ids or ["-"]),
                                                ProgramEmailTouch.status == "sent").all():
        if t.created_at and t.created_at >= began[t.lead_id]:
            emailed.add(t.lead_id)                       # an email of THIS journey went
    latest = max(began.values(), default=now)            # the last Start / Resume click

    def start_of(lid):
        v = [x.scheduled_start_at for x in recs[lid] if x.scheduled_start_at]
        return min(v) if v else None

    pushed, waiting = [], []
    for lid in ids:
        st = states.get(lid)
        first_pending = lid not in texted and lid not in emailed and (
            st is None or (st.status == "active" and (st.current_touch_number or 0) == 0))
        sched = start_of(lid)
        if not first_pending or sched is None or sched <= now:
            continue
        if began[lid] < latest - timedelta(minutes=1):
            pushed.append(lid)                           # started earlier, put behind the last batch
        else:
            waiting.append(lid)                          # the last batch, still waiting its turn
    if not pushed:
        d.setdefault("repairs", {})[REPAIR_KEY] = {"at": now.isoformat(), "pushed": 0}
        fam.touch_copy_json = _json.dumps(d)
        db.commit()
        return {"pushed": 0, "respaced": 0, "followups": 0}
    pushed.sort(key=lambda x: (began[x], start_of(x), x))
    waiting.sort(key=lambda x: (start_of(x), x))
    order = pushed + waiting
    slots = release_slots(max(now, now.replace(second=0, microsecond=0) + timedelta(minutes=5)),
                          len(order), timedelta(minutes=DEFAULT_SPACING_MINUTES))
    for lid, at in zip(order, slots):
        st = states.get(lid)
        if st is not None and st.status == "active" and (st.current_touch_number or 0) == 0:
            st.next_touch_due_at = at
            st.cadence_started_at = at
        for r in recs[lid]:
            r.scheduled_start_at = at
    # Families already texted: their own next-text time back, emails freed.
    schedule = cs._get_org_cadence_schedule(db, prog.organization_id)
    fixed = 0
    for lid in ids:
        st = states.get(lid)
        if st is None or st.status != "active" or (st.current_touch_number or 0) < 1 or lid not in texted:
            continue
        nxt = (st.current_touch_number or 0) + 1
        nd = next((t for t in schedule if t["touch_number"] == nxt), None)
        if nd is None or not st.cadence_started_at:
            continue
        base = st.cadence_started_at.replace(tzinfo=None)
        last = st.last_touch_sent_at.replace(tzinfo=None) if st.last_touch_sent_at else base
        natural = max(base + timedelta(days=nd["day_offset"]), last + cs._touch_gap(schedule, nxt))
        if st.next_touch_due_at and st.next_touch_due_at > natural + timedelta(minutes=30):
            st.next_touch_due_at = max(natural, now + timedelta(minutes=5 + fixed))
            fixed += 1
            for r in recs[lid]:
                if r.scheduled_start_at and r.scheduled_start_at > now:
                    r.scheduled_start_at = r.journey_started_at
    d.setdefault("repairs", {})[REPAIR_KEY] = {"at": now.isoformat(), "pushed": len(pushed),
                                               "respaced": len(order), "followups": fixed}
    fam.touch_copy_json = _json.dumps(d)
    db.commit()
    log_payload = {"pushed": len(pushed), "respaced": len(order), "followups": fixed,
                   "first_at": slots[0].isoformat() + "Z" if slots else None,
                   "last_at": slots[len(order) - 1].isoformat() + "Z" if order else None}
    import logging
    logging.getLogger(__name__).warning("journey repair %s: %s", REPAIR_KEY, log_payload)
    return log_payload


def repair_for_sci(db: Session) -> Optional[Dict]:
    from app.services import sms_programs
    from app.services.programs import campaigns as _camp
    from app.services.programs import identity
    org_id = sms_programs.sci_org_id(db)
    if not org_id:
        return None
    prog = identity.program_for_org(db, org_id)
    fam = (db.query(CampaignFamily).filter(CampaignFamily.organization_id == org_id,
                                           CampaignFamily.key == _camp.CENTRAL_ALABAMA_KEY).first())
    if prog is None or fam is None:
        return None
    return repair_pushed_starts(db, prog, fam)
