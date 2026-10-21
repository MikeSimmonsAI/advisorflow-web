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
    """Uses spacing.violations - the SAME rule the sending side enforces - so a
    reply to a family who wrote back, a person's own text, an appointment
    confirmation, or a resend after a text the carrier refused (30007) is never
    reported as two texts too close (Oct 16: 13 false findings)."""
    from app.models.models import Lead
    from app.services.programs import spacing
    bad = spacing.violations(db, org_id, now - timedelta(hours=24))
    names = sorted(_name(l) for l in db.query(Lead).filter(Lead.id.in_(bad)).all()) if bad else []
    return _check("texts_too_close", "Texts spaced as planned", names,
                  "No one got two unsolicited texts less than 20 hours apart in the last day.",
                  "%d people got two unsolicited texts less than 20 hours apart in the last day.")


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
    # A shared mailbox contains NDR notices from multiple organizations.
    # An NDR with no resolved organization is not proof that SCI (or any
    # tenant) bounced an email. Keep unmatched notices in mailbox review;
    # this tenant's self-check must only report positively scoped records.
    rows = (db.query(InboundMailboxMessage)
            .filter(InboundMailboxMessage.organization_id == org_id,
                    InboundMailboxMessage.received_at >= now - timedelta(days=7),
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


# ── alerting: ONE notice per unchanged incident ──────────────────────────────
# An incident is a check key plus its signature (what is wrong and who it is
# about). The owner hears about it once. The same unchanged finding is not
# repeated - not after a deploy or restart (remembered in the database), not
# the next morning - until REPEAT_AFTER has passed. A materially different
# finding (new signature: new people, a different count) is a new incident
# and is sent at once. Acknowledged findings stay quiet until they change.
REPEAT_AFTER = timedelta(days=7)
_alerted: Dict[str, str] = {}


def _marker(c: Dict) -> str:
    return "[%s:%s]" % (c["key"], c["signature"])


def _already_alerted(db: Session, prog, c: Dict, now: datetime) -> bool:
    from app.models.program_models import ProgramAlert
    base = (db.query(ProgramAlert.id)
            .filter(ProgramAlert.organization_id == prog.organization_id, ProgramAlert.kind == "self_check"))
    people = [p for p in (c.get("people") or []) if p]
    if people:
        # The same incident seen through a rolling window: as days pass the
        # list SHRINKS (Oct 15 8:33 AM: yesterday's 8 people became 2, a new
        # signature, and the owner was texted again). Only someone the owner
        # has NOT been told about in the reminder window is news.
        told = " ".join(m or "" for (m,) in db.query(ProgramAlert.message).filter(
            ProgramAlert.organization_id == prog.organization_id, ProgramAlert.kind == "self_check",
            ProgramAlert.created_at >= now - REPEAT_AFTER, ProgramAlert.message.like("%%[%s%%" % c["key"])).all())
        if told and all(p in told for p in people):
            return True
    if base.filter(ProgramAlert.created_at >= now - REPEAT_AFTER,
                   ProgramAlert.message.like("%%%s%%" % _marker(c))).first() is not None:
        return True
    # Notices written before incident markers carried only "[key]": honour them
    # for the rest of that day so this deploy does not repeat tonight's notice.
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return base.filter(ProgramAlert.created_at >= since,
                       ProgramAlert.message.like("%%[%s]%%" % c["key"])).first() is not None


# Kept for callers/tests that ask the old question.
def _alerted_today(db: Session, prog, key: str, now: datetime) -> bool:
    from app.models.program_models import ProgramAlert
    since = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (db.query(ProgramAlert.id)
            .filter(ProgramAlert.organization_id == prog.organization_id, ProgramAlert.kind == "self_check",
                    ProgramAlert.created_at >= since, ProgramAlert.message.like("%%[%s%%" % key))
            .first()) is not None


def _lock(db: Session, prog) -> bool:
    """Two processes checking at the same moment must not both send: a
    transaction-scoped Postgres advisory lock per program (no-op elsewhere)."""
    try:
        if db.bind is None or db.bind.dialect.name != "postgresql":
            return True
        from sqlalchemy import text
        import zlib
        key = zlib.crc32(("self_check:" + prog.id).encode()) & 0x7FFFFFFF
        return bool(db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}).scalar())
    except Exception:                                    # noqa: BLE001
        db.rollback()
        return True


def alert_all(db: Session, now: datetime = None) -> int:
    from app.models.program_models import OutreachProgram
    from app.services.programs.responses import owner_system_alert
    now = now or datetime.utcnow()
    sent = 0
    for prog in db.query(OutreachProgram).filter(OutreachProgram.is_active.is_(True)).all():
        if not ((prog.alert_email or prog.alert_phone) and prog.staff_sms_alerts_enabled):
            continue
        res = run(db, prog.organization_id, now)
        if not _lock(db, prog):
            continue
        for c in res["checks"]:
            c.setdefault("signature", signature(c))
        new = [c for c in res["checks"] if not c["ok"] and not c.get("acknowledged")
               and _alerted.get("%s:%s" % (prog.id, c["key"])) != c["signature"]
               and not _already_alerted(db, prog, c, now)]
        if not new:
            db.commit()
            continue
        lines = ["%s - %s%s" % (c["title"], c["detail"], (" (" + ", ".join(c["people"][:6]) + ")") if c["people"] else "")
                 for c in new]
        msg = "System self-check found %d problem(s): %s %s" % (
            len(new), " | ".join(lines), " ".join(_marker(c) for c in new))
        # OWNER ONLY: internal problems never go to the team list.
        res = owner_system_alert(db, prog, "self_check", msg)
        db.commit()
        ok = any(v and v[0] for v in res.values())
        if ok:
            for c in new:
                _alerted["%s:%s" % (prog.id, c["key"])] = c["signature"]
            sent += 1
    return sent
