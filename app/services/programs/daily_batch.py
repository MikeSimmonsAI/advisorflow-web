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
On the listed days (Central; every day unless a manager chose fewer - Nov 7
owner: seven days a week), the first pass at or after the start time
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
# Nov 7 (owner): seven days a week. A program that never chose its days sends
# every day; a stored choice (e.g. "12345") is still honoured as written.
DEFAULT_DAYS = "1234567"


def days_of(prog: OutreachProgram) -> str:
    return prog.daily_batch_days or DEFAULT_DAYS


def fits_today(start_local: datetime, spacing_minutes: int) -> int:
    """How many starts, `spacing` apart from start + 2 min, fit before the
    6 PM Central close (send_window.CLOSE). A batch never runs past the close:
    anything after it would wait overnight and leave together at 9 AM."""
    from app.services.programs import send_window
    close = start_local.replace(hour=send_window.CLOSE.hour, minute=send_window.CLOSE.minute, second=0,
                                microsecond=0)
    first = start_local + timedelta(minutes=2)
    if first >= close:
        return 0
    return int((close - first).total_seconds() // 60 // max(1, spacing_minutes)) + 1


def _local_now(now: Optional[datetime] = None) -> datetime:
    from zoneinfo import ZoneInfo
    return (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(TZ))


def _start_time(prog: OutreachProgram) -> time:
    try:
        h, m = (prog.daily_batch_start or "09:00").split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return time(9, 0)


TEXT_AND_EMAIL, EMAIL_ONLY = "text_email", "email_only"


def _email_route_ok(prog, lead) -> Optional[str]:
    """None when this family's email may carry the journey, else why not."""
    from app.services.programs import email_touches
    addr = (getattr(lead, "email", None) or "").strip()
    if not addr:
        return "No email address"
    if getattr(lead, "allow_email", None) is False:
        return "Email opt-out of record"
    if (getattr(lead, "manual_flag", None) or "") == "bad_email":
        return "Invalid or bounced email"
    if email_touches.provider_hold_reason(prog, addr):
        return "Email provider hold"
    return None


def pool(db: Session, prog: OutreachProgram) -> Dict:
    """THE STANDING AUTHORIZATION'S POOL (owner, Nov 7): every real SCI family
    the daily batch may enroll right now, with the channel it will use, and
    every family left out with the exact reason. The owner switched the daily
    batch on once ("DAILY <n>"); that standing approval covers every family
    below - nobody is asked to approve them one by one.

    In the pool: never contacted (no text, no email), status new, in a
    campaign that is switched on, nothing blocking (STOP/DNC, holds, data /
    location / duplicate review - send_approval.blocker), not a campaign with
    its own started-journey workflow, and at least one usable channel:
      * a CELL phone with an SMS consent of record -> text + email journey;
      * otherwise a usable email (not opted out, not bounced, provider not on
        hold) -> the email-only journey. VoIP, landline and unknown phone
        types are never texted by the batch and never assumed to be cell.
    """
    from app.models.models import Lead as _Lead
    from app.services import sms_programs, wholesale_sms
    from app.services.programs import send_approval as sa
    from app.services.programs.campaigns import OWN_COPY_KEYS
    active = {k for (k,) in db.query(CampaignFamily.key).filter(
        CampaignFamily.organization_id == prog.organization_id, CampaignFamily.is_active.is_(True)).all()}
    rows = sa.list_contacts(db, prog, show="not_approved", limit=100000)["items"]
    sci = sms_programs.is_sci_org(db, prog.organization_id)
    gone = ok = None
    if sci:
        if sms_programs.auto_attest_at_send() and sms_programs.owner_holds_records():
            gone = sms_programs.opted_out_numbers(db, prog.organization_id)
        else:
            ok = sms_programs.consented_numbers(db, prog.organization_id)
    leads = {}
    ids = [r["lead_id"] for r in rows]
    for i in range(0, len(ids), 500):
        for l in db.query(_Lead).filter(_Lead.id.in_(ids[i:i + 500])).all():
            leads[l.id] = l
    entries, skipped = [], {}

    def skip(why):
        skipped[why] = skipped.get(why, 0) + 1
    for r in rows:
        if r["approved"]:
            continue
        if r["last_text_at"] or r["last_email_at"]:
            skip("Already contacted")
            continue
        if r["blocked"]:
            skip(r["blocked"])
            continue
        if r["campaign_key"] not in active:
            skip("Campaign switched off")
            continue
        if r["campaign_key"] in OWN_COPY_KEYS:
            skip("Own journey - started from its campaign")
            continue
        if r["status"] != "new":
            skip("Status is %s" % r["status"])
            continue
        lead = leads.get(r["lead_id"])
        e164 = wholesale_sms.normalize_e164(r.get("phone"))
        consent = True
        if sci:
            consent = (e164 not in gone) if gone is not None else (e164 in (ok or set()))
        if r["line_type"] == "mobile" and consent:
            entries.append({"lead_id": r["lead_id"], "location_id": r["location_id"], "mode": TEXT_AND_EMAIL,
                            "why": "cell phone with SMS consent"})
            continue
        no_email = _email_route_ok(prog, lead) if lead is not None else "No email address"
        if no_email is None:
            phone_why = ("cell phone without SMS consent of record" if r["line_type"] == "mobile"
                         else "%s phone" % (r["line_type"] or "unknown") if r.get("phone") else "no phone")
            entries.append({"lead_id": r["lead_id"], "location_id": r["location_id"], "mode": EMAIL_ONLY,
                            "why": "usable email; %s - email only" % phone_why})
            continue
        if r["line_type"] == "mobile":
            skip("No SMS consent of record and %s" % no_email.lower())
        elif r["line_type"] in ("voip", "landline"):
            skip("%s phone and %s" % (r["line_type"].title(), no_email.lower()))
        else:
            skip("No textable phone and %s" % no_email.lower())
    return {"entries": entries, "skipped": skipped,
            "text_email": sum(1 for e in entries if e["mode"] == TEXT_AND_EMAIL),
            "email_only": sum(1 for e in entries if e["mode"] == EMAIL_ONLY)}


def pick(db: Session, prog: OutreachProgram, n: int) -> List[str]:
    """Lead ids for the next n people, spread across locations."""
    return [e["lead_id"] for e in pick_entries(db, prog, n)]


def pick_entries(db: Session, prog: OutreachProgram, n: int) -> List[Dict]:
    by_loc: Dict[str, list] = {}
    for e in pool(db, prog)["entries"]:
        by_loc.setdefault(e["location_id"] or "", []).append(e)
    out: List[Dict] = []
    i = 0
    while len(out) < n:
        added = False
        for es in by_loc.values():
            if i < len(es):
                out.append(es[i])
                added = True
                if len(out) >= n:
                    break
        if not added:
            break
        i += 1
    return out


def ready_now(db: Session, prog: OutreachProgram) -> Optional[int]:
    """How many families the batch could pick right now (same rules as pick)."""
    p = ready_detail(db, prog)
    return None if p is None else p["total"]


def ready_detail(db: Session, prog: OutreachProgram) -> Optional[Dict]:
    try:
        p = pool(db, prog)
    except Exception:                                    # noqa: BLE001 - a count, never a blocker
        log.exception("daily batch ready count failed for %s", prog.id)
        return None
    return {"total": len(p["entries"]), "text_email": p["text_email"], "email_only": p["email_only"],
            "skipped": p["skipped"]}


def status(prog: OutreachProgram) -> Dict:
    return {"count": int(prog.daily_batch_count or 0), "spacing_minutes": int(prog.daily_batch_spacing or 8),
            "start": prog.daily_batch_start or "09:00", "days": days_of(prog),
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
    if str(local.isoweekday()) not in days_of(prog):
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
    spacing = max(1, min(240, int(prog.daily_batch_spacing or 8)))
    # Never more than fits before the 6 PM close (a late start shrinks it).
    n = min(n, fits_today(local, spacing))
    entries = pick_entries(db, prog, n) if n > 0 else []
    if not entries:
        # Nov 7: an empty day is recorded too, so the owner can see the batch
        # RAN and found nobody ready - not that it silently did nothing.
        from app.routers.audit_log_router import log_action
        detail = ready_detail(db, prog) or {}
        log_action(db, prog.organization_id, user.id, action="program.daily_batch",
                   target_type="outreach_program", target_id=prog.id,
                   details={"date": today, "picked": 0, "approved": 0, "fits": n,
                            "note": "nobody ready", "skipped": detail.get("skipped") or {}})
        db.commit()
        return {"approved": 0, "picked": 0}
    utc_now = (now or datetime.utcnow())
    start_at = utc_now + timedelta(minutes=2)
    # One drip for the day: texts-and-email families first, then email-only
    # families, `spacing` apart - same standing approval, same audit entry.
    text_ids = [e["lead_id"] for e in entries if e.get("mode", TEXT_AND_EMAIL) == TEXT_AND_EMAIL]
    email_ids = [e["lead_id"] for e in entries if e.get("mode") == EMAIL_ONLY]
    approved, texting, emailing = 0, 0, 0
    if text_ids:
        r = sa.approve(db, prog, user, text_ids, mode=TEXT_AND_EMAIL, start_at=start_at, spacing_minutes=spacing)
        approved += r.get("approved", 0)
        texting = r.get("approved", 0)
    if email_ids:
        r2 = sa.approve(db, prog, user, email_ids, mode=EMAIL_ONLY,
                        start_at=start_at + timedelta(minutes=spacing * len(text_ids)), spacing_minutes=spacing)
        approved += r2.get("approved", 0)
        emailing = r2.get("approved", 0)
    from app.routers.audit_log_router import log_action
    log_action(db, prog.organization_id, user.id, action="program.daily_batch",
               target_type="outreach_program", target_id=prog.id,
               details={"date": today, "picked": len(entries), "approved": approved,
                        "text_email": texting, "email_only": emailing,
                        "authority": "standing daily-batch approval",
                        "selected": [{"lead_id": e["lead_id"], "mode": e.get("mode", TEXT_AND_EMAIL),
                                      "why": e.get("why")} for e in entries]})
    db.commit()
    return {"approved": approved, "picked": len(entries), "date": today,
            **({"email_only": emailing} if email_ids else {})}


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
    days = days_of(prog)
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
