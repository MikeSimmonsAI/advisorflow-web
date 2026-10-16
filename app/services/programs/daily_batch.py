"""DAILY BATCH - the program approves the next N people by itself, every
sending day, once a manager has switched it on.

WHY
---
Approving people to send was a person ticking 25 and pressing Good to go, one
batch at a time. A day nobody did that was a day nothing new went out. The
owner wants a standing schedule: "send this many new people every day".

THE AUTHORITY IS THE MANAGER'S, GIVEN ONCE
------------------------------------------
Switching it on is an explicit act by a workspace manager in the app (the
endpoint requires a typed confirmation and records who did it and the audit
log). Every daily batch is approved IN THAT PERSON'S NAME through the same
send_approval.approve() a click uses, so every existing gate still applies:
blockers, test records, cell-phone-only texting, provider holds, quiet hours,
the sending window, STOP/DNC. Switching it off (count 0) stops the next batch;
people already approved keep their cadence like any approval.

WHO IS PICKED
-------------
The same people "Pick 25 for me" ticks: never approved, never texted or
emailed, status new, a confirmed cell phone, in a campaign that is switched
on, nothing blocking them - one per location in turn so a day is spread
across locations. For SCI, ALSO an SMS consent of record for the number
(Oct 16): a family the send gate would refuse is never picked.

WHEN
----
On the listed weekdays (Central), the first pass at or after the start time
approves the day's batch, first person a couple of minutes later, then one
every `spacing` minutes. Once per day: daily_batch_last_date guards it, and
the pass runs on the single-instance claimed program loop.
"""
import logging
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead, User
from app.models.program_models import CampaignFamily, OutreachProgram

log = logging.getLogger(__name__)
TZ = "America/Chicago"
MAX_PER_DAY = 200
# A day's batch is approved only before this Central time. Switched on (or a
# server back up) later than this, the batch waits for the next sending day's
# start - so the day's families are paced through business hours instead of
# all being held overnight and then going out together when sending opens.
LATEST_START = time(15, 0)


def _local_now(now: Optional[datetime] = None) -> datetime:
    from zoneinfo import ZoneInfo
    return (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ))


def _start_time(prog: OutreachProgram) -> time:
    try:
        h, m = (prog.daily_batch_start or "09:00").split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return time(9, 0)


def pick(db: Session, prog: OutreachProgram, n: int) -> List[str]:
    """Lead ids for the next n people, spread across locations."""
    from app.services.programs import send_approval as sa
    active = {k for (k,) in db.query(CampaignFamily.key).filter(
        CampaignFamily.organization_id == prog.organization_id, CampaignFamily.is_active.is_(True)).all()}
    rows = sa.list_contacts(db, prog, show="not_approved", limit=100000)["items"]
    pool = [r for r in rows if not r["approved"] and not r["blocked"] and r["line_type"] == "mobile"
            and r["campaign_key"] in active and not r["last_text_at"] and not r["last_email_at"]
            and r["status"] == "new"]
    # TEXTABLE MEANS CONSENT OF RECORD (Oct 16). The batch texts, so it only
    # picks families whose number already has a usable SCI SMS consent; it
    # never fills its quota with families the send gate would refuse.
    from app.services import sms_programs, wholesale_sms
    if sms_programs.is_sci_org(db, prog.organization_id):
        ok = sms_programs.consented_numbers(db, prog.organization_id)
        pool = [r for r in pool if wholesale_sms.normalize_e164(r.get("phone")) in ok]
    by_loc: Dict[str, list] = {}
    for r in pool:
        by_loc.setdefault(r["location_id"] or "", []).append(r["lead_id"])
    out: List[str] = []
    i = 0
    while len(out) < n:
        added = False
        for ids in by_loc.values():
            if i < len(ids):
                out.append(ids[i])
                added = True
                if len(out) >= n:
                    break
        if not added:
            break
        i += 1
    return out


def status(prog: OutreachProgram) -> Dict:
    return {"count": int(prog.daily_batch_count or 0), "spacing_minutes": int(prog.daily_batch_spacing or 8),
            "start": prog.daily_batch_start or "09:00", "days": prog.daily_batch_days or "12345",
            "on": bool(prog.daily_batch_count), "set_by": prog.daily_batch_by,
            "last_date": prog.daily_batch_last_date}


def run_for(db: Session, prog: OutreachProgram, now: Optional[datetime] = None) -> Optional[Dict]:
    """Approve today's batch if it is due. None when nothing was due."""
    from zoneinfo import ZoneInfo
    from app.services.programs import send_approval as sa
    n = min(int(prog.daily_batch_count or 0), MAX_PER_DAY)
    if n <= 0 or not prog.daily_batch_by:
        return None
    local = _local_now(now)
    today = local.date().isoformat()
    if prog.daily_batch_last_date == today:
        return None
    if str(local.isoweekday()) not in (prog.daily_batch_days or "12345"):
        return None
    start_t = _start_time(prog)
    if local.time() < start_t:
        return None
    if local.time() >= max(LATEST_START, start_t):
        return None
    user = db.query(User).filter(User.id == prog.daily_batch_by, User.is_active.is_(True)).first()
    if user is None:
        log.warning("daily batch for %s skipped: the person who switched it on is not active", prog.id)
        return None
    # Claim the day first, so a failure below never sends a second batch.
    prog.daily_batch_last_date = today
    db.commit()
    ids = pick(db, prog, n)
    if not ids:
        return {"approved": 0, "picked": 0}
    utc_now = (now or datetime.utcnow())
    start_at = utc_now + timedelta(minutes=2)
    spacing = max(1, min(240, int(prog.daily_batch_spacing or 8)))
    r = sa.approve(db, prog, user, ids, mode="text_email", start_at=start_at, spacing_minutes=spacing)
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.daily_batch",
               target_type="outreach_program", target_id=prog.id,
               details={"date": today, "picked": len(ids), "approved": r.get("approved")})
    db.commit()
    return {"approved": r.get("approved", 0), "picked": len(ids), "date": today}


def run(db: Session, now: Optional[datetime] = None) -> Dict:
    out = {}
    for prog in db.query(OutreachProgram).filter(OutreachProgram.daily_batch_count > 0).all():
        try:
            res = run_for(db, prog, now)
            if res is not None:
                out[prog.organization_id] = res
        except Exception:  # noqa: BLE001 - one program must not stop another
            db.rollback()
            log.exception("daily batch failed for program %s", prog.id)
    return out


def next_sending_start(prog: OutreachProgram, now: Optional[datetime] = None) -> datetime:
    """Naive UTC: the next sending day's batch start (today's if still ahead)."""
    from zoneinfo import ZoneInfo
    local = _local_now(now)
    start_t = _start_time(prog)
    days = prog.daily_batch_days or "12345"
    d = local.date()
    for i in range(0, 8):
        day = d + timedelta(days=i)
        if str(day.isoweekday()) not in days:
            continue
        at = datetime.combine(day, start_t, ZoneInfo(TZ))
        if at > local:
            return at.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return (local + timedelta(days=1)).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def repace(db: Session, prog: OutreachProgram, user: User, approved_since: datetime,
           now: Optional[datetime] = None) -> Dict:
    """Move the start times of families the daily batch approved since
    `approved_since` who have NOT been contacted yet (no text, no email, first
    touch not sent) to the next sending day: first at start + 2 min, then one
    every `spacing` minutes. That day's batch is counted as done, so the day
    gets these families, not twice as many. Only times change: approval,
    consent, STOP/DNC and every send-time check are untouched."""
    from zoneinfo import ZoneInfo
    from app.models.models import CadenceState, EmailMessage, Message
    from app.models.program_models import ProgramSourceRecord as R
    first = next_sending_start(prog, now) + timedelta(minutes=2)
    spacing = max(1, min(240, int(prog.daily_batch_spacing or 8)))
    recs = (db.query(R).filter(R.organization_id == prog.organization_id, R.lead_id.isnot(None),
                               R.approved_to_send_at.isnot(None), R.approved_to_send_at >= approved_since)
            .order_by(R.scheduled_start_at, R.approved_to_send_at).all())
    by_lead: Dict[str, list] = {}
    for r in recs:
        by_lead.setdefault(r.lead_id, []).append(r)
    moved, kept = [], []
    for lid, rs in by_lead.items():
        st = db.query(CadenceState).filter(CadenceState.lead_id == lid).first()
        texted = db.query(Message.id).filter(Message.lead_id == lid).first() is not None
        emailed = db.query(EmailMessage.id).filter(EmailMessage.lead_id == lid,
                                                   EmailMessage.status == "sent").first() is not None
        if texted or emailed or (st is not None and (st.current_touch_number or 0) > 0):
            kept.append({"lead_id": lid, "reason": "already contacted"})
            continue
        at = first + timedelta(minutes=spacing * len(moved))
        for r in rs:
            r.scheduled_start_at = at
        if st is not None and st.status == "active":
            st.next_touch_due_at = at
        moved.append({"lead_id": lid, "starts_at": at.isoformat() + "Z"})
    day = first.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ)).date().isoformat()
    if moved:
        prog.daily_batch_last_date = max(prog.daily_batch_last_date or "", day)
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.daily_batch_repace",
               target_type="outreach_program", target_id=prog.id,
               details={"moved": len(moved), "kept": len(kept), "first": first.isoformat(), "day": day})
    db.commit()
    return {"moved": len(moved), "kept": kept, "first_start": first.isoformat() + "Z",
            "last_start": moved[-1]["starts_at"] if moved else None, "counts_as_batch_for": day,
            "items": moved}
