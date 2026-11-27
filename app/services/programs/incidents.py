"""SYSTEM-CHECK INCIDENTS - one problem, one text, one report, one page (Nov 27).

WHY. Nov 27 12:12 AM CT: two Restland checks (texts_never_started and
first_messages_late) flagged the same family. The owner got a text that said
only "The system check found a problem. Please check your email." and both
the text and the email linked to the Responses inbox, which knew nothing about
it. This module turns self-check findings into incidents:

  * GROUPED. Checks that see the same outreach problem share a category
    (CATEGORY_OF). One OPEN incident per category per workspace; another check
    or a repeat run adds to its history and never sends again.
  * TEXT = BRIEF HEADS-UP. Program, the issue in plain words, how many
    families, whether the owner must act, the incident number and a link to
    the incident page. No family names, numbers or emails - a text is useful
    on its own when the email does not arrive.
  * EMAIL = FULL REPORT, from the system's own records: what happened, when,
    who, original and current times in Central, each family's text and email
    outcome (scheduled / attempted / accepted by the provider / delivered /
    failed - never more than the record says), the cause only when the send
    log records it (otherwise "Cause unconfirmed"), what the system already
    did, what it will do next and when, who follows up, whether the owner
    must act, and when the next notice comes.
  * DELIVERY PER CHANNEL. Every notice goes through alert_ledger.page with an
    incident dedupe key, so the text and the email are separate rows with
    their own provider status, a retry is the same row, and a second worker
    can never send twice.
  * RESOLUTION. When no check sees the problem any more AND the outcome is
    verified (every affected family's first message attempted with a recorded
    result, or the family is no longer in an active sequence), one short
    resolution text and email go out.

OWNER ONLY, like every system alert (responses.SYSTEM_KINDS): rows are kind
"self_check" and _deliver_staff_alert refuses any recipient but the owner.

No settings exist for staff-alert quiet hours or per-alert severity on a
program (checked Nov 27); none are invented here. `severity` is recorded on
the incident for the page; it does not change who is told or when.

Enabled for Restland only (enabled_for). SCI keeps its existing notice.
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

TZ = "America/Chicago"
UPDATE_EVERY = timedelta(minutes=60)       # growth: at most one update email an hour
FINAL_SMS = ("delivered", "undelivered", "failed")
ACCEPTED_SMS = ("queued", "accepted", "sending", "sent", "pending", "scheduled")
MAX_EMAIL_PEOPLE = 25

FIRST = "first_outreach_delayed"
CATEGORY_OF = {
    "texts_never_started": FIRST,
    "first_messages_late": FIRST,
    "texts_overdue": "texts_overdue",
    "texts_too_close": "texts_too_close",
    "bounces_unrecorded": "mailbox_records",
    "replies_unrecorded": "mailbox_records",
    "jobs_running": "jobs_stopped",
    "daily_batch_ran": "batch_approval_pending",
}
CATEGORIES = {
    FIRST: {"title": "First outreach delayed", "severity": "warning", "per_lead": True,
            "happened": "A family's first text or first email has not gone out at its planned time."},
    "texts_overdue": {"title": "Follow-up texts overdue", "severity": "warning", "per_lead": True,
                      "happened": "Texts that are due, and allowed right now, have not gone out."},
    "texts_too_close": {"title": "Texts sent too close together", "severity": "warning", "per_lead": True,
                        "happened": "A family got two scheduled texts less than 20 hours apart in the last day."},
    "mailbox_records": {"title": "Email bounce or reply not recorded", "severity": "warning", "per_lead": False,
                        "happened": "A bounce notice or a reply to one of our emails is in the support mailbox "
                                    "but not on any family."},
    "jobs_stopped": {"title": "Sender or mailbox reader stopped", "severity": "outage", "per_lead": False,
                     "happened": "A background job that sends texts or reads replies has not run in 15 minutes."},
    "batch_approval_pending": {"title": "Daily batch waiting for approval", "severity": "action_needed",
                               "per_lead": False,
                               "happened": "Today's batch has not been approved yet. This is waiting on an "
                                           "approval, not a system outage."},
    "check_error": {"title": "A system check could not run", "severity": "warning", "per_lead": False,
                    "happened": "One of the system checks failed to run, so that part of the system is unchecked."},
}


# ── who it is for ────────────────────────────────────────────────────────────

def enabled_for(db: Session, prog) -> bool:
    """Restland: the program on the text-slot schedule / Restland theme, and
    never SCI (SCI keeps its own notice and wording)."""
    try:
        from app.services.programs import program_profile
        p = program_profile.profile(db, prog)
        return bool(p) and not p.get("is_sci") and p.get("theme") == "restland"
    except Exception:                                    # noqa: BLE001
        log.exception("incident enablement check failed")
        return False


def category_of(key: str) -> str:
    return CATEGORY_OF.get(key, "check_error")


def _owner_targets(prog) -> List:
    return [("owner", "sms", prog.alert_phone), ("owner", "email", prog.alert_email)]


# ── time ─────────────────────────────────────────────────────────────────────

def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def ct(dt: Optional[datetime], with_day: bool = True) -> str:
    """Naive-UTC -> "Fri Nov 27, 2:26 PM CT"."""
    dt = _naive(dt)
    if dt is None:
        return "-"
    from zoneinfo import ZoneInfo
    loc = dt.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(TZ))
    t = loc.strftime("%-I:%M %p")
    return ("%s, %s CT" % (loc.strftime("%a %b %-d"), t)) if with_day else "%s CT" % t


def _duration(a: datetime, b: datetime) -> str:
    mins = int(abs((_naive(b) - _naive(a)).total_seconds()) // 60)
    h, m = divmod(mins, 60)
    return ("%d hours %d minutes" % (h, m)) if h else "%d minutes" % m


# ── the record ───────────────────────────────────────────────────────────────

def _j(raw, default):
    try:
        v = json.loads(raw) if raw else default
        return v if isinstance(v, type(default)) else default
    except (TypeError, ValueError):
        return default


def _prefix(db: Session, prog) -> str:
    from app.services.programs.alert_ledger import program_label
    letters = "".join(c for c in program_label(db, prog).upper() if c.isalpha())
    return (letters[:3] or "PRG")


def _number(db: Session, prog, now: datetime) -> str:
    from zoneinfo import ZoneInfo
    loc = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(TZ))
    return "%s-%s-%s" % (_prefix(db, prog), loc.strftime("%y%m%d-%H%M"), uuid.uuid4().hex[:4])


def _history_add(inc, entry: Dict) -> None:
    h = _j(inc.history, [])
    h.append(entry)
    inc.history = json.dumps(h[-200:])


def _check_entry(c: Dict, now: datetime) -> Dict:
    return {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "check", "key": c["key"], "title": c.get("title"),
            "detail": c.get("detail"), "signature": c.get("signature"), "count": len(c.get("lead_ids") or [])
            or len(c.get("people") or []), "acknowledged": bool(c.get("acknowledged"))}


def _last_signature(inc, key: str) -> Optional[str]:
    for e in reversed(_j(inc.history, [])):
        if e.get("type") == "check" and e.get("key") == key:
            return e.get("signature")
    return None


def link(inc) -> Optional[str]:
    """The incident page. Only the random incident id is in it - never a
    family's name, number or email."""
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    return "%s/program/incident/%s" % (base, inc.id) if base else None


# ── what the system's own records say about one family ───────────────────────

def _sms_outcome(m) -> Dict:
    if m is None:
        return {"state": "none", "label": "No text attempted"}
    ds = (m.delivery_status or "").lower()
    ts = (m.twilio_status or "").lower()
    ss = (getattr(m, "send_state", None) or "").lower()
    err = getattr(m, "error_code", None)
    at = ct(m.sent_at)
    if ds == "delivered" or ts == "delivered":
        return {"state": "delivered", "label": "Delivered (carrier report), sent %s" % at}
    if ds in ("undelivered", "failed") or ts in ("undelivered", "failed") or ss in ("failed", "blocked"):
        return {"state": "failed", "label": "Failed%s, attempted %s" % ((" (error %s)" % err) if err else "", at)}
    if m.twilio_sid:
        return {"state": "accepted", "label": "Accepted by Twilio %s - no delivery report yet" % at}
    return {"state": "attempted", "label": "Attempted %s - not accepted by the provider" % at}


def _email_outcome(e) -> Dict:
    if e is None:
        return {"state": "none", "label": "No email sent"}
    st = (e.status or "").lower()
    at = ct(e.sent_at)
    if e.bounced_at or st == "bounced":
        return {"state": "failed", "label": "Failed (bounced), sent %s" % at}
    if st == "failed":
        return {"state": "failed", "label": "Failed, attempted %s" % at}
    if st == "delivered":
        return {"state": "delivered", "label": "Delivered (provider report), sent %s" % at}
    return {"state": "accepted", "label": "Accepted by the email provider %s - inbox delivery can't be verified" % at}


def _reason_text(reason: str, at: Optional[datetime]) -> str:
    """A recorded reason code in plain words. The day is named only when the
    record proves which kind of day it was."""
    r = reason or ""
    if r.startswith("quiet hours: Nothing is sent today"):
        try:
            from app.services import no_contact_days
            day = no_contact_days.local_day(at)
            if no_contact_days.quiet_on(day):
                return "Held - %s was a no-contact day" % day.strftime("%a %b %-d")
            return "Held - %s was a closed day" % day.strftime("%a %b %-d")
        except Exception:                                # noqa: BLE001
            return "Held - closed or no-contact day"
    if r.startswith("quiet hours:"):
        return "Held - outside contact hours"
    if r.startswith("slot: queued for the follow-up slot at"):
        return "Placed in the next free follow-up slot (%s)" % r.split(" at ", 1)[1]
    if r.startswith("slot: slot") and "missed" in r:
        return "Its minute was missed; " + r[len("slot: "):]
    if r.startswith("slot: waiting for its slot"):
        return "Waiting for its reserved slot"
    return r[:160]


def lead_facts(db: Session, org_id: str, lead_id: str, now: datetime) -> Optional[Dict]:
    from app.models.models import CadenceState, CadenceTouchLog, EmailMessage, Lead, Message
    from app.models.program_models import ProgramSourceRecord
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
    if lead is None:
        return None
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead_id).first()
    psr = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org_id,
                                                ProgramSourceRecord.lead_id == lead_id)
           .order_by(ProgramSourceRecord.created_at.desc()).first())
    m = db.query(Message).filter(Message.lead_id == lead_id).order_by(Message.sent_at.desc()).first()
    e = db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id).order_by(EmailMessage.sent_at.desc()).first()
    logs = (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id == lead_id)
            .order_by(CadenceTouchLog.created_at.desc()).limit(8).all())
    logs = list(reversed(logs))
    original = _naive((psr.scheduled_start_at if psr and psr.scheduled_start_at else None)
                      or (st.cadence_started_at if st else None))
    status = str(getattr(st.status, "value", st.status)) if st else None
    active = status == "active" and (lead.status or "") not in ("dnc", "booked")
    current = _naive(st.next_touch_due_at) if (st and active) else None
    reserved = False
    try:
        from app.services.programs import outreach_slots
        r = outreach_slots.reservation(db, st.id, 1) if st else None
        reserved = bool(r is not None and current is not None and _naive(r.slot_at) == current)
    except Exception:                                    # noqa: BLE001 - table absent on an old database
        reserved = False
    sms = _sms_outcome(m)
    if sms["state"] == "none":
        if current is not None and current > now:
            sms = {"state": "scheduled", "label": "Scheduled for %s%s - not attempted yet" % (
                ct(current), " (slot reserved)" if reserved else "")}
        elif current is not None:
            sms = {"state": "none", "label": "Not attempted - was due %s" % ct(current)}
        elif not active:
            sms = {"state": "stopped", "label": "Not attempted - sequence is %s" % (
                (lead.status if lead.status in ("dnc", "booked") else status) or "not active")}
    reasons = [{"at": ct(l.created_at), "outcome": l.outcome, "channel": l.channel,
                "touch": l.touch_number, "code": (l.reason or "")[:300],
                "plain": _reason_text(l.reason or "", _naive(l.created_at))} for l in logs]
    return {"lead_id": lead_id, "name": " ".join(x for x in (lead.first_name, lead.last_name) if x).title()
            or "Family", "original": original, "current": current, "reserved": reserved, "active": active,
            "sequence_status": status, "sms": sms, "email": _email_outcome(e), "reasons": reasons,
            "_logs": logs}


def _cause(facts: List[Dict]) -> Dict:
    """Only what the send log records. One recorded sequence for all - or
    "Cause unconfirmed"."""
    seen = set()
    for f in facts:
        codes = [l.reason or "" for l in f["_logs"] if (l.touch_number or 0) <= 1]
        held = next((l for l in f["_logs"] if (l.reason or "").startswith("quiet hours: Nothing is sent today")), None)
        queued = next((l for l in f["_logs"] if (l.reason or "").startswith("slot: queued for the follow-up slot")),
                      None)
        missed = next((c for c in codes if c.startswith("slot: slot") and "missed" in c), None)
        if held is not None and queued is not None and queued.created_at >= held.created_at:
            seen.add("held_then_queued:" + _reason_text(held.reason, _naive(held.created_at)))
        elif held is not None:
            seen.add("held:" + _reason_text(held.reason, _naive(held.created_at)))
        elif missed:
            seen.add("missed")
        else:
            seen.add("unknown")
    if len(seen) != 1 or "unknown" in seen:
        return {"confirmed": False, "text": "Cause unconfirmed. The send log does not record one reason for "
                                            "every family listed."}
    one = seen.pop()
    if one.startswith("held_then_queued:"):
        day = one.split(":", 1)[1]
        return {"confirmed": True, "text": "From the send log: %s, so the first text was not sent. When sending "
                                           "reopened, the text was not put back ahead of the line - it was "
                                           "placed in the next free follow-up slot." % day.replace("Held - ", "")}
    if one.startswith("held:"):
        return {"confirmed": True, "text": "From the send log: %s." % one.split(":", 1)[1]}
    return {"confirmed": True, "text": "From the send log: the first text's minute was missed and it was moved "
                                       "to a later free minute."}


def _actions_taken(facts: List[Dict]) -> List[str]:
    out = []
    for f in facts[:MAX_EMAIL_PEOPLE]:
        for l in f["_logs"]:
            r = l.reason or ""
            if r.startswith("slot: queued") or (r.startswith("slot: slot") and "missed" in r):
                out.append("%s - the system re-queued %s's first text: %s" % (
                    ct(l.created_at), f["name"], _reason_text(r, _naive(l.created_at))))
    return out


# ── the summary every notice and the page are built from ─────────────────────

def summarize(db: Session, prog, inc, now: datetime, checks: Optional[List[Dict]] = None) -> Dict:
    cat = CATEGORIES.get(inc.category, CATEGORIES["check_error"])
    ids = _j(inc.lead_ids, [])
    facts = []
    if cat["per_lead"]:
        for lid in ids:
            f = lead_facts(db, prog.organization_id, lid, now)
            if f is not None:
                facts.append(f)
    out = {"category": inc.category, "title": cat["title"], "severity": cat["severity"], "happened": cat["happened"],
           "count": len(ids) if cat["per_lead"] else max(inc.affected_count or 0, 1), "facts": facts}
    latest = {}
    for e in _j(inc.history, []):
        if e.get("type") == "check":
            latest[e["key"]] = e
    out["checks"] = list(latest.values())
    if cat["per_lead"]:
        out["cause"] = _cause(facts) if facts else {"confirmed": False, "text": "Cause unconfirmed."}
        out["actions"] = _actions_taken(facts)
        waiting = [f for f in facts if f["sms"]["state"] == "scheduled"]
        stuck = [f for f in facts if f["sms"]["state"] == "none"]
        failed = [f for f in facts if f["sms"]["state"] == "failed" and f["email"]["state"] in ("none", "failed")]
        if inc.status == "resolved":
            out["act"] = (False, "No")
            out["next"] = "Nothing - this is resolved."
        elif stuck or failed:
            n = len(stuck) + len(failed)
            out["act"] = (True, "Yes - %d %s no send time on record or failed. Open the incident to review." % (
                n, "family has" if n == 1 else "families have"))
            out["next"] = ("%d %s scheduled on its own. The rest have no future send time on record, so the "
                           "system will not send to them without a change." % (
                               len(waiting), "is" if len(waiting) == 1 else "are"))
        elif waiting:
            first = min(f["current"] for f in waiting)
            out["act"] = (False, "No - the system will send on its own. Act only if you want it sooner.")
            out["next"] = ("The text sender will try the first text at %s in its reserved slot." % ct(first)
                           if len(waiting) == 1 else
                           "The text sender will try these first texts in their reserved slots, starting %s."
                           % ct(first))
            out["next_try"] = first
        else:
            out["act"] = (False, "No")
            out["next"] = "The system is waiting for the outcome to be recorded."
    else:
        out["cause"] = {"confirmed": False, "text": "Cause unconfirmed."}
        out["actions"] = []
        if inc.category == "batch_approval_pending":
            out["act"] = (inc.status != "resolved", "Yes - approve today's batch on the Send screen."
                          if inc.status != "resolved" else "No")
            out["next"] = ("Today's batch does not start until it is approved." if inc.status != "resolved"
                           else "Nothing - the batch was approved.")
        elif inc.status == "resolved":
            out["act"] = (False, "No")
            out["next"] = "Nothing - this is resolved."
        else:
            out["act"] = (True, "Yes - review it on the incident page.")
            out["next"] = "The checks run every 15 minutes; nothing is retried automatically for this."
    out["owner"] = "No one is assigned. System alerts go only to the program owner."
    out["next_update"] = ("One text and one email when it is resolved and the outcome is checked. Repeat checks "
                          "every 15 minutes add to this incident without sending again; if more families are "
                          "affected you get one email update (at most one an hour), no text.")
    return out


# ── the words ────────────────────────────────────────────────────────────────

def _plural(n: int, one: str = "lead", many: str = "leads") -> str:
    return "%d %s" % (n, one if n == 1 else many)


def sms_text(db: Session, prog, inc, s: Dict, event: str) -> str:
    from app.services.programs.alert_ledger import program_label
    label = program_label(db, prog)
    url = link(inc)
    tail = (" " + url) if url else " Open EvoSys Pro > System check."
    if event == "resolved":
        core = "%s | Resolved: %s | %s | %s" % (label, s["title"], _plural(s["count"]), inc.resolution or "verified")
    elif inc.category == "batch_approval_pending":
        core = "%s | Action needed: today's batch is waiting for your approval (not an outage)" % label
    else:
        act, _words = s["act"]
        if act:
            how = "Action needed"
        elif s.get("next_try"):
            how = "No action needed - next try %s" % ct(s["next_try"])
        else:
            how = "No action needed"
        core = "%s | %s | %s | %s" % (label, s["title"], _plural(s["count"]), how)
    return ("%s | %s%s" % (core, inc.incident_number, tail))[:600]


def subject(db: Session, prog, inc, s: Dict, event: str) -> str:
    from app.services.programs.alert_ledger import program_label
    label = program_label(db, prog)
    head = {"resolved": "Resolved: %s" % s["title"], "update": "Update: %s" % s["title"]}.get(event, s["title"])
    return "%s | %s | %s | %s" % (label, head, _plural(s["count"]), inc.incident_number)


def email_html(db: Session, prog, inc, s: Dict, event: str, now: datetime) -> str:
    import html as H
    esc = lambda v: H.escape(str(v if v is not None else ""))   # noqa: E731
    url = link(inc)
    P = '<p style="margin:0 0 10px">%s</p>'
    h = lambda t: '<h3 style="font-size:15px;margin:16px 0 6px">%s</h3>' % esc(t)   # noqa: E731
    body = ['<p style="font-size:16px;font-weight:700;margin:0 0 4px">%s</p>' % esc(subject(db, prog, inc, s, event)),
            P % ("Incident %s · %s · %s" % (esc(inc.incident_number), esc(s["severity"].replace("_", " ")),
                                            "Resolved" if inc.status == "resolved" else "Open"))]
    if url:
        body.append(P % ('<a href="%s">Open the incident in EvoSys Pro</a>' % esc(url)))
    if event == "resolved":
        body += [h("Resolved"), P % ("%s at %s." % (esc(inc.resolution or "Verified"), esc(ct(inc.resolved_at))))]
    elif event == "update":
        body += [h("Update"), P % ("More families are now affected: %s in total." % esc(s["count"]))]
    body += [h("What happened"), P % esc(s["happened"])]
    started = inc.started_at
    orig = [f["original"] for f in s["facts"] if f.get("original")]
    when = "First seen by the system check at %s." % ct(started)
    if orig:
        when += " The earliest planned first message was %s." % ct(min(orig))
    body += [h("When it started"), P % esc(when)]
    body += [h("Who or what is affected"),
             P % esc(_plural(s["count"], "family", "families") if s["facts"] else s["happened"])]
    if s["facts"]:
        rows = ['<table cellpadding="5" style="border-collapse:collapse;font-size:13px">',
                '<tr style="background:#f1f3f0;text-align:left"><th>Family</th><th>Planned first text</th>'
                '<th>Now set for</th><th>Text</th><th>Email</th></tr>']
        for f in s["facts"][:MAX_EMAIL_PEOPLE]:
            moved = ""
            if f.get("original") and f.get("current") and f["current"] > f["original"]:
                moved = "<br><small>moved %s later</small>" % esc(_duration(f["original"], f["current"]))
            rows.append("<tr><td>%s</td><td>%s</td><td>%s%s</td><td>%s</td><td>%s</td></tr>" % (
                esc(f["name"]), esc(ct(f["original"])), esc(ct(f["current"]) if f.get("current") else "-"), moved,
                esc(f["sms"]["label"]), esc(f["email"]["label"])))
        rows.append("</table>")
        if len(s["facts"]) > MAX_EMAIL_PEOPLE:
            rows.append(P % esc("%d more on the incident page." % (len(s["facts"]) - MAX_EMAIL_PEOPLE)))
        body += rows
        body.append('<p style="margin:6px 0 10px;font-size:12px;color:#5d6b7a">Scheduled = not attempted yet. '
                    'Accepted = the provider took it; arrival is only confirmed by a delivery report. Delivered = '
                    'the provider reported delivery. Failed = the provider or carrier refused it.</p>')
        codes = []
        for f in s["facts"][:5]:
            for r in f["reasons"]:
                codes.append("%s · %s · %s %s: %s" % (f["name"], r["at"], r["channel"] or "", r["outcome"] or "",
                                                      r["plain"]))
        if codes:
            body += [h("Recorded reasons (send log)"),
                     "<ul style=\"margin:0 0 10px;font-size:13px\">%s</ul>" % "".join(
                         "<li>%s</li>" % esc(c) for c in codes[:20])]
    body += [h("Cause"), P % esc(s["cause"]["text"])]
    acts = s.get("actions") or []
    body += [h("Actions already taken")]
    body.append(("<ul style=\"margin:0 0 10px\">%s</ul>" % "".join("<li>%s</li>" % esc(a) for a in acts))
                if acts else P % "None recorded.")
    body.append(P % "This alert has not retried, rescheduled or sent anything to a family.")
    body += [h("What the system will do next"), P % esc(s["next"]), h("Who follows up"), P % esc(s["owner"]),
             h("Do you need to act?"), P % esc(s["act"][1]), h("Next update"), P % esc(s["next_update"])]
    if s["checks"]:
        body += [h("Checks that saw it"),
                 "<ul style=\"margin:0 0 10px;font-size:13px\">%s</ul>" % "".join(
                     "<li>%s - %s</li>" % (esc(c.get("title")), esc(c.get("detail"))) for c in s["checks"])]
    if url:
        body.append(P % ('<a href="%s">Open the incident in EvoSys Pro</a>' % esc(url)))
    return "".join(body)


def notify(db: Session, prog, inc, event: str, now: datetime, *, channels=("sms", "email")) -> List:
    """One text and/or one email for this incident event - claimed in the
    alert ledger first, so a repeat run or a second worker sends nothing."""
    from app.services.programs import alert_ledger
    s = summarize(db, prog, inc, now)
    sms = sms_text(db, prog, inc, s, event)
    subj = subject(db, prog, inc, s, event)
    html = email_html(db, prog, inc, s, event, now)
    targets = [t for t in _owner_targets(prog) if t[1] in channels]
    key = "incident:%s:%s" % (inc.id, event if event != "update" else "update:%d" % s["count"])
    markers = " ".join("[%s:%s]" % (c["key"], c.get("signature")) for c in s["checks"] if c.get("signature"))
    rows = alert_ledger.page(db, prog, lead_id=None, key=key, kind="self_check", sms_text=sms, subject=subj,
                             html=html, coalesce=False, now=now, targets=targets,
                             audit_message="%s %s %s" % (inc.incident_number, subj, markers))
    _history_add(inc, {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "notice", "event": event,
                       "count": s["count"], "channels": [r.channel for r in rows],
                       "states": [r.send_state for r in rows]})
    return rows


# ── the decision ─────────────────────────────────────────────────────────────

def _verify(db: Session, prog, inc, now: datetime) -> Optional[str]:
    """The outcome, when it can be stated from the records - else None."""
    cat = CATEGORIES.get(inc.category, CATEGORIES["check_error"])
    if not cat["per_lead"] or inc.category != FIRST:
        if inc.category == "texts_too_close":
            return "the check no longer finds it (it looks back 24 hours)"
        return "the check passed"
    facts = [f for f in (lead_facts(db, prog.organization_id, lid, now) for lid in _j(inc.lead_ids, [])) if f]
    if not facts:
        return "the families are no longer in this workspace"
    sent = delivered = failed = stopped = 0
    for f in facts:
        sm, em = f["sms"]["state"], f["email"]["state"]
        if sm in ("delivered",) or em == "delivered":
            delivered += 1
        elif sm == "failed" and em in ("none", "failed"):
            failed += 1
        elif sm in ("accepted", "attempted") or em == "accepted":
            sent += 1
        elif sm == "stopped":
            stopped += 1
        else:
            return None                                  # still scheduled / not attempted: not verified
    parts = []
    if delivered:
        parts.append("%d delivered" % delivered)
    if sent:
        parts.append("%d accepted by the provider" % sent)
    if failed:
        parts.append("%d failed" % failed)
    if stopped:
        parts.append("%d no longer in an active sequence" % stopped)
    return "first message: " + ", ".join(parts)


def process(db: Session, prog, checks: List[Dict], now: datetime,
            legacy_alerted: Optional[Callable[[Dict], bool]] = None) -> int:
    """Turn this run's findings into incidents. Returns how many notices
    (an opening or a resolution, text + email counted once) were raised."""
    from app.models.program_models import ProgramIncident
    raised = 0
    failing: Dict[str, List[Dict]] = {}
    for c in checks:
        if not c.get("ok"):
            failing.setdefault(category_of(c["key"]), []).append(c)
    open_ = {i.category: i for i in db.query(ProgramIncident).filter(
        ProgramIncident.organization_id == prog.organization_id, ProgramIncident.status == "open").all()}
    for cat, cs in failing.items():
        ids = sorted({i for c in cs for i in (c.get("lead_ids") or [])})
        count = len(ids) or max(len(c.get("people") or []) for c in cs) or 1
        inc = open_.get(cat)
        if inc is None:
            if all(c.get("acknowledged") for c in cs):
                continue                                 # "Got it": quiet until it changes
            inc = ProgramIncident(organization_id=prog.organization_id, incident_number=_number(db, prog, now),
                                  category=cat, status="open",
                                  severity=CATEGORIES.get(cat, CATEGORIES["check_error"])["severity"],
                                  title=CATEGORIES.get(cat, CATEGORIES["check_error"])["title"],
                                  check_keys=json.dumps(sorted({c["key"] for c in cs})), lead_ids=json.dumps(ids),
                                  affected_count=count, started_at=now, last_seen_at=now)
            db.add(inc)
            db.flush()
            for c in cs:
                _history_add(inc, _check_entry(c, now))
            if legacy_alerted is not None and all(legacy_alerted(c) for c in cs):
                # Already told by the notice this replaces (deploy day): no second page.
                _history_add(inc, {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "event",
                                   "event": "already_notified", "count": count,
                                   "note": "The owner was already alerted about this by the earlier "
                                           "system-check notice; no new text or email."})
            else:
                notify(db, prog, inc, "opened", now)
                raised += 1
            continue
        # The same open incident: record what changed, send nothing new...
        for c in cs:
            if _last_signature(inc, c["key"]) != c.get("signature"):
                _history_add(inc, _check_entry(c, now))
        keys = sorted(set(_j(inc.check_keys, [])) | {c["key"] for c in cs})
        old = set(_j(inc.lead_ids, []))
        inc.check_keys = json.dumps(keys)
        inc.lead_ids = json.dumps(sorted(old | set(ids)))
        inc.affected_count = max(len(old | set(ids)), count if not ids else 0)
        inc.last_seen_at = now
        # ...except one update EMAIL when more families are affected than the
        # owner was last told about (at most one an hour).
        told = max([e.get("count") or 0 for e in _j(inc.history, [])
                    if e.get("type") == "notice" or e.get("event") == "already_notified"] or [0])
        grew = told and len(old | set(ids)) > told
        if grew and now - _naive(inc.last_update_at or inc.started_at) >= UPDATE_EVERY:
            inc.last_update_at = now
            notify(db, prog, inc, "update", now, channels=("email",))
    # Open incidents no check sees any more: resolve once the outcome is verified.
    for cat, inc in open_.items():
        if cat in failing:
            continue
        outcome = _verify(db, prog, inc, now)
        if outcome is None:
            h = _j(inc.history, [])
            if not h or h[-1].get("event") != "monitoring":
                _history_add(inc, {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "event", "event": "monitoring",
                                   "note": "No check flags it now; waiting for every family's outcome to be "
                                           "recorded before calling it resolved."})
            continue
        inc.status, inc.resolved_at, inc.resolution = "resolved", now, outcome[:500]
        _history_add(inc, {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": "event", "event": "resolved",
                           "note": outcome})
        notify(db, prog, inc, "resolved", now)
        raised += 1
    db.flush()
    return raised


# ── the page ─────────────────────────────────────────────────────────────────

def _mask(to: Optional[str]) -> Optional[str]:
    v = (to or "").strip()
    if "@" in v:
        a, _, d = v.partition("@")
        return "%s•••@%s" % (a[:2], d)
    d = "".join(c for c in v if c.isdigit())
    return ("•••-%s" % d[-4:]) if len(d) >= 4 else None


def view(db: Session, prog, inc, now: Optional[datetime] = None) -> Dict:
    from app.models.program_models import ProgramAlert
    now = now or datetime.utcnow()
    s = summarize(db, prog, inc, now)
    rows = (db.query(ProgramAlert).filter(ProgramAlert.organization_id == inc.organization_id,
                                          ProgramAlert.dedupe_key.like("incident:%s:%%" % inc.id))
            .order_by(ProgramAlert.created_at).all())
    notices = [{"event": (r.dedupe_key or "").split(":", 2)[-1], "channel": r.channel, "to": _mask(r.recipient),
                "state": r.send_state, "provider_status": r.provider_status, "delivered": bool(r.delivered),
                "reason": r.reason, "attempts": r.attempts, "at": ct(r.created_at)} for r in rows]
    history = []
    for e in _j(inc.history, []):
        try:
            at = datetime.strptime(e.get("at"), "%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            at = None
        history.append(dict(e, at_ct=ct(at)))
    next_action = None
    if inc.category == "batch_approval_pending" and inc.status == "open":
        next_action = {"label": "Open Send to approve today's batch", "to": "/program?tab=send"}
    elif s["facts"]:
        next_action = {"label": "Open the family's record", "to": "/program?person=%s" % s["facts"][0]["lead_id"]}
    elif inc.category in ("jobs_stopped", "mailbox_records", "check_error"):
        next_action = {"label": "Open Health", "to": "/program?tab=health"}
    people = [{"lead_id": f["lead_id"], "name": f["name"], "original": ct(f["original"]) if f["original"] else None,
               "current": ct(f["current"]) if f["current"] else None, "reserved": f["reserved"],
               "sms": f["sms"], "email": f["email"], "reasons": f["reasons"],
               "record": "/program?person=%s" % f["lead_id"]} for f in s["facts"]]
    return {"id": inc.id, "number": inc.incident_number, "organization_id": inc.organization_id,
            "category": inc.category, "title": s["title"], "severity": s["severity"], "status": inc.status,
            "happened": s["happened"], "started": ct(inc.started_at), "last_seen": ct(inc.last_seen_at),
            "resolved": ct(inc.resolved_at) if inc.resolved_at else None, "resolution": inc.resolution,
            "affected": s["count"], "cause": s["cause"], "actions": s["actions"], "next": s["next"],
            "act": {"needed": bool(s["act"][0]), "words": s["act"][1]}, "owner": s["owner"],
            "next_update": s["next_update"], "people": people, "checks": s["checks"], "history": history,
            "notices": notices, "next_action": next_action,
            "controls_note": "No retry or reschedule control exists for a first text yet; the system sends it "
                             "in its reserved slot. Nothing on this page sends to a family."}
