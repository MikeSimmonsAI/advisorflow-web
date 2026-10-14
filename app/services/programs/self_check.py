"""SELF-CHECK - is the program doing what the plan says?

Every check compares the PLAN (the schedule, the sending window, the daily
batch) with what ACTUALLY happened (messages written, notices received, jobs
run). It exists because the owner found, by looking, two texts hours apart
that the plan spaced days apart, and bounce notices the app had not counted.
The program now looks for those itself, every 15 minutes, and says so.

Each check returns {key, ok, title, detail, people}. `run()` never raises.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

TOO_CLOSE = timedelta(hours=20)
OVERDUE = timedelta(minutes=15)
FIRST_LATE = timedelta(minutes=25)
STALE_JOB = timedelta(minutes=15)
_NDR = re.compile(r"(?i)^(undeliverable|undelivered|delivery status notification|mail delivery|"
                  r"returned mail|delivery (has )?failed|failure notice|message not delivered)")


def _name(lead) -> str:
    return " ".join(x for x in (lead.first_name, lead.last_name) if x).title() or "Family"


def _check(key, title, problems: List[str], ok_text: str, bad_text: str) -> Dict:
    return {"key": key, "ok": not problems, "title": title,
            "detail": ok_text if not problems else bad_text % len(problems), "people": problems[:12]}


def texts_too_close(db: Session, org_id: str, now: datetime) -> Dict:
    from app.models.models import Lead, Message
    since = now - timedelta(hours=24)
    rows = (db.query(Message.lead_id, Message.sent_at).join(Lead, Lead.id == Message.lead_id)
            .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True),
                    Message.sent_at >= since - TOO_CLOSE).order_by(Message.sent_at).all())
    by: Dict[str, list] = {}
    for lid, at in rows:
        by.setdefault(lid, []).append(at)
    bad = []
    for lid, ts in by.items():
        for a, b in zip(ts, ts[1:]):
            if b >= since and b - a < TOO_CLOSE:
                bad.append(lid)
                break
    names = [_name(l) for l in db.query(Lead).filter(Lead.id.in_(bad)).all()] if bad else []
    return _check("texts_too_close", "Texts spaced as planned", names,
                  "No one got two texts less than 20 hours apart in the last day.",
                  "%d people got two texts less than 20 hours apart in the last day.")


def texts_overdue(db: Session, org_id: str, now: datetime) -> Dict:
    from app.models.models import CadenceState, Lead
    from app.services import contact_hours
    rows = (db.query(CadenceState, Lead).join(Lead, Lead.id == CadenceState.lead_id)
            .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True), CadenceState.status == "active",
                    CadenceState.next_touch_due_at < now - OVERDUE).all())
    bad = []
    for s, l in rows:
        c = contact_hours.check(l, now)
        if c["permitted"]:
            bad.append(_name(l))
        elif c["code"] in (contact_hours.ZONE_UNKNOWN, contact_hours.ZONE_UNRESOLVABLE):
            # Not "waiting for the window" - it can NEVER go until fixed.
            bad.append("%s (no time zone for this number)" % _name(l))
    return _check("texts_overdue", "Texts going out on time", bad,
                  "Every text that is due and allowed right now has gone out.",
                  "%d texts are overdue: due, and either allowed right now or blocked for good.")


def texts_never_started(db: Session, org_id: str, now: datetime) -> Dict:
    """An approved person on an active text sequence for a day with NOT ONE
    text attempted. Whatever the reason, the plan is not running for them.
    (Oct 14: 15 families at "0 of 6 texts" for two days, nothing flagged.)"""
    from app.models.models import CadenceState, Lead, Message
    rows = (db.query(CadenceState, Lead).join(Lead, Lead.id == CadenceState.lead_id)
            .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True), CadenceState.status == "active",
                    CadenceState.cadence_started_at < now - timedelta(hours=24)).all())
    ids = [l.id for _s, l in rows]
    tried = {i for (i,) in db.query(Message.lead_id).filter(Message.lead_id.in_(ids or ["-"])).distinct().all()}
    bad = [_name(l) for _s, l in rows if l.id not in tried and l.status not in ("dnc", "booked")]
    return _check("texts_never_started", "Every approved person's texts started", bad,
                  "Everyone approved more than a day ago has had their first text attempted.",
                  "%d people were approved over a day ago and not one text has been attempted.")


def first_messages_late(db: Session, org_id: str, now: datetime) -> Dict:
    from app.models.models import EmailMessage, Lead, Message
    from app.models.program_models import ProgramSourceRecord
    from app.services import contact_hours
    recs = (db.query(ProgramSourceRecord)
            .filter(ProgramSourceRecord.organization_id == org_id, ProgramSourceRecord.lead_id.isnot(None),
                    ProgramSourceRecord.approved_to_send_at.isnot(None),
                    ProgramSourceRecord.scheduled_start_at.isnot(None),
                    ProgramSourceRecord.scheduled_start_at < now - FIRST_LATE,
                    ProgramSourceRecord.scheduled_start_at > now - timedelta(hours=24)).all())
    ids = list({r.lead_id for r in recs})
    if not ids:
        return _check("first_messages_late", "First messages on schedule", [],
                      "Everyone scheduled for a first message got one.", "")
    got = {i for (i,) in db.query(Message.lead_id).filter(Message.lead_id.in_(ids)).distinct().all()}
    got |= {i for (i,) in db.query(EmailMessage.lead_id).filter(EmailMessage.lead_id.in_(ids)).distinct().all()}
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_(ids)).all()}
    bad = [_name(leads[i]) for i in ids if i not in got and i in leads and not leads[i].is_test
           and leads[i].status not in ("dnc", "booked") and contact_hours.is_permitted(leads[i], now)]
    return _check("first_messages_late", "First messages on schedule", bad,
                  "Everyone scheduled for a first message got one.",
                  "%d people are past their start time and have not been texted or emailed.")


def bounces_unrecorded(db: Session, org_id: str, now: datetime) -> Dict:
    from app.models.inbound_mailbox_models import InboundMailboxMessage
    rows = (db.query(InboundMailboxMessage)
            .filter(InboundMailboxMessage.received_at >= now - timedelta(days=7),
                    InboundMailboxMessage.outcome != "bounce").all())
    bad = [(r.subject or "")[:60] for r in rows if _NDR.match((r.subject or "").strip())]
    return _check("bounces_unrecorded", "Every bounce recorded", bad,
                  "Every bounce notice in the support mailbox is recorded in the app.",
                  "%d bounce notices in the support mailbox are not recorded yet.")


def replies_unrecorded(db: Session, org_id: str, now: datetime) -> Dict:
    """A reply to one of OUR emails (same subject) that the mailbox reader
    could not put on a contact. Nothing a family writes back may sit unseen."""
    from app.models.inbound_mailbox_models import InboundMailboxMessage
    from app.models.models import EmailMessage, Lead
    from app.services.inbound_mailbox_service import _norm_subject
    rows = (db.query(InboundMailboxMessage)
            .filter(InboundMailboxMessage.received_at >= now - timedelta(days=7),
                    InboundMailboxMessage.outcome.in_(("no_lead", "ambiguous", "unmatched"))).all())
    rows = [r for r in rows if re.match(r"(?i)^\s*(re|aw|fw|fwd)\s*:", r.subject or "")]
    if not rows:
        return _check("replies_unrecorded", "Every email reply recorded", [],
                      "Every reply to our emails is on a contact.", "")
    ours = {_norm_subject(s) for (s,) in db.query(EmailMessage.subject).join(Lead, Lead.id == EmailMessage.lead_id)
            .filter(Lead.organization_id == org_id, EmailMessage.sent_at >= now - timedelta(days=45)).distinct().all()}
    bad = ["%s (%s)" % (r.from_address or "unknown sender", (r.subject or "")[:50])
           for r in rows if _norm_subject(r.subject) in ours]
    return _check("replies_unrecorded", "Every email reply recorded", bad,
                  "Every reply to our emails is on a contact.",
                  "%d replies to our emails are in the support mailbox but not on any contact.")


def jobs_running(db: Session, org_id: str, now: datetime) -> Dict:
    from app.models.inbound_mailbox_models import InboundMailbox
    from app.models.job_models import JobName, JobRun
    bad = []
    last = (db.query(JobRun.started_at).filter(JobRun.job_name == JobName.CADENCE_LOOP)
            .order_by(JobRun.started_at.desc()).first())
    if last is None or (last[0].replace(tzinfo=None) if last[0].tzinfo else last[0]) < now - STALE_JOB:
        bad.append("Text sender has not run in the last 15 minutes")
    for b in db.query(InboundMailbox).filter(InboundMailbox.is_active.is_(True)).all():
        if b.last_polled_at is None or b.last_polled_at < now - STALE_JOB:
            bad.append("Reply mailbox %s has not been read in the last 15 minutes" % b.address)
    return _check("jobs_running", "Senders and mailbox running", bad,
                  "The text sender and the reply mailbox reader are both running.",
                  "%d background jobs have stopped.")


def daily_batch_ran(db: Session, org_id: str, now: datetime) -> Dict:
    from app.models.program_models import OutreachProgram
    from app.services.programs import daily_batch
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org_id).first()
    bad = []
    if prog is not None and (prog.daily_batch_count or 0) > 0:
        local = daily_batch._local_now(now)
        start = daily_batch._start_time(prog)
        due = (str(local.isoweekday()) in (prog.daily_batch_days or "12345")
               and local.hour * 60 + local.minute >= start.hour * 60 + start.minute + 30)
        if due and prog.daily_batch_last_date != local.date().isoformat():
            bad.append("Today's batch of %d has not been approved" % prog.daily_batch_count)
    return _check("daily_batch_ran", "Daily batch went out", bad,
                  "The daily batch ran (or is off / not due yet).", "%d daily batch problem.")


CHECKS = [texts_too_close, texts_overdue, texts_never_started, first_messages_late, bounces_unrecorded, replies_unrecorded,
          jobs_running, daily_batch_ran]


def signature(c: Dict) -> str:
    import hashlib
    raw = "%s|%s|%s" % (c["key"], c.get("detail", ""), ",".join(sorted(c.get("people") or [])))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _acks(db: Session, org_id: str) -> Dict[str, str]:
    import json
    from app.models.program_models import OutreachProgram
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org_id).first()
    try:
        return json.loads(getattr(prog, "self_check_ack", None) or "{}") if prog else {}
    except ValueError:
        return {}


def acknowledge(db: Session, org_id: str, key: str, sig: str) -> None:
    import json
    from app.models.program_models import OutreachProgram
    prog = db.query(OutreachProgram).filter(OutreachProgram.organization_id == org_id).first()
    acks = _acks(db, org_id)
    acks[key] = sig
    prog.self_check_ack = json.dumps(acks)
    db.commit()


def run(db: Session, org_id: str, now: datetime = None) -> Dict:
    now = now or datetime.utcnow()
    out = []
    for fn in CHECKS:
        try:
            out.append(fn(db, org_id, now))
        except Exception as exc:  # noqa: BLE001 - a broken check is itself a finding
            db.rollback()
            log.exception("self-check %s failed", fn.__name__)
            out.append({"key": fn.__name__, "ok": False, "title": fn.__name__.replace("_", " ").title(),
                        "detail": "This check could not run: %s" % type(exc).__name__, "people": []})
    acks = _acks(db, org_id)
    for c in out:
        c["signature"] = signature(c)
        c["acknowledged"] = (not c["ok"]) and acks.get(c["key"]) == c["signature"]
    open_ = [c for c in out if not c["ok"] and not c["acknowledged"]]
    return {"checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": not open_,
            "problems": len(open_), "acknowledged": sum(1 for c in out if c["acknowledged"]), "checks": out}


# ── alerting: email the owner once per problem per day ──────────────────────
_alerted: Dict[str, str] = {}


def alert_all(db: Session, now: datetime = None) -> int:
    from app.models.program_models import OutreachProgram
    from app.services.programs.responses import _deliver_staff_alert
    now = now or datetime.utcnow()
    sent = 0
    for prog in db.query(OutreachProgram).filter(OutreachProgram.is_active.is_(True)).all():
        if not (prog.alert_email and prog.staff_sms_alerts_enabled):
            continue
        res = run(db, prog.organization_id, now)
        day = now.strftime("%Y-%m-%d")
        new = [c for c in res["checks"] if not c["ok"] and not c.get("acknowledged")
               and _alerted.get("%s:%s" % (prog.id, c["key"])) != day]
        if not new:
            continue
        lines = ["%s - %s%s" % (c["title"], c["detail"], (" (" + ", ".join(c["people"][:6]) + ")") if c["people"] else "")
                 for c in new]
        msg = "System self-check found %d problem(s): %s" % (len(new), " | ".join(lines))
        ok, _why = _deliver_staff_alert(db, prog, "email", prog.alert_email, msg, kind="self_check")
        if ok:
            for c in new:
                _alerted["%s:%s" % (prog.id, c["key"])] = day
            sent += 1
    return sent
