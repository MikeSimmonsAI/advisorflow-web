"""SCI LIVE PERFORMANCE - what EvoSys Pro is actually accomplishing for SCI.

ONE LEDGER, EVERY NUMBER. Each request builds one ledger of real recorded
events for the SCI workspace (outreach attempts from the cadence log, replies,
AI / staff messages, appointment offers, confirmed calendar bookings,
opt-outs) and every statistic is `len(rows(metric, filters))` over that
ledger. The drill-down for a metric returns exactly those rows, so a card's
count and the list behind it can never disagree.

What never enters a result:
  * test / synthetic / demo families and the owner's live-test contact;
  * forecasts - projections are returned in their own block, flagged
    `is_projection`, and never added to a recorded total;
  * opt-outs - counted only as opt-outs; a family who opted out is not an
    appointment result;
  * auto-replies ("away from my desk") - not counted as genuine replies.

Touch numbers come from the cadence log (touch 1 = the first outreach a
family received). A reply, offer or booking is attributed to the latest touch
the family received before it happened; if none, it is "before outreach".

Dates are Central Time days (America/Chicago), the way SCI works.
Read-only: nothing here sends, enrolls, approves or changes a record.
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

CT = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")

# Attempt outcomes in the cadence log that mean a send was really tried.
ATTEMPT_OUTCOMES = ("sent", "failed", "provider_failed")
EMAIL_ATTEMPTS = ("sent", "failed", "unknown")          # program_email_touches: really tried
DELIVERED = ("delivered", "read", "opened", "clicked")
FAILED = ("failed", "undelivered", "bounced", "provider_failed")
NOT_GENUINE_REPLY = ("auto_reply", "opt_out")
BOOKED = ("booked", "confirmed", "completed")

ERR = {"30007": "Carrier filtered (link)", "30003": "Not reached - phone off or no signal, will try again", "30005": "Not reached - number not active right now, will try again",
       "30006": "Landline or unreachable carrier", "30008": "Unknown carrier error", "21610": "Opted out (STOP)",
       "30004": "Blocked by recipient", "21614": "Not a mobile number", "21211": "Invalid number"}

PRESETS = ("today", "24h", "7d", "30d", "all", "custom")


# ── time ────────────────────────────────────────────────────────────────────

def _booked_ct(b):
    """A booked time is stored as the advisor's LOCAL wall time (see
    app/services/booking_time.py), never UTC: shown in Central as booked."""
    from app.services import booking_time
    return datetime.fromisoformat(booking_time.wire_obj(b)).astimezone(CT)

def _utc_naive(local_dt: datetime) -> datetime:
    return local_dt.astimezone(UTC).replace(tzinfo=None)


def ct_day(at: Optional[datetime]) -> Optional[date]:
    if at is None:
        return None
    return at.replace(tzinfo=UTC).astimezone(CT).date()


def window(preset: str = "today", start: Optional[str] = None, end: Optional[str] = None,
           now: Optional[datetime] = None) -> Dict:
    """{"start": utc-naive or None, "end": utc-naive (exclusive), "label", ...}
    Custom: start / end are YYYY-MM-DD Central dates, both inclusive."""
    now = now or datetime.utcnow()
    today = ct_day(now)
    p = preset if preset in PRESETS else "today"
    if p == "custom":
        try:
            d0 = date.fromisoformat(start) if start else today
            d1 = date.fromisoformat(end) if end else today
        except ValueError:
            d0 = d1 = today
        if d1 < d0:
            d0, d1 = d1, d0
        d1 = min(d1, today)
        d0 = min(d0, d1)
        label = d0.strftime("%b %-d") + ("" if d0 == d1 else " – " + d1.strftime("%b %-d, %Y"))
    elif p == "all":
        d0, d1, label = None, today, "All time"
    elif p == "24h":
        s24 = now - timedelta(hours=24)
        return {"preset": p, "start": s24, "end": now + timedelta(seconds=1),
                "start_date": ct_day(s24).isoformat(), "end_date": today.isoformat(), "label": "Last 24 hours"}
    else:
        days = {"today": 1, "7d": 7, "30d": 30}[p]
        d0, d1 = today - timedelta(days=days - 1), today
        label = {"today": "Today", "7d": "Last 7 days", "30d": "Last 30 days"}[p]
    s = _utc_naive(datetime.combine(d0, datetime.min.time(), CT)) if d0 else None
    e = _utc_naive(datetime.combine(d1 + timedelta(days=1), datetime.min.time(), CT))
    return {"preset": p, "start": s, "end": e, "start_date": d0.isoformat() if d0 else None,
            "end_date": d1.isoformat(), "label": label}


def _in(at: Optional[datetime], w: Dict) -> bool:
    if at is None:
        return False
    if w.get("start") is not None and at < w["start"]:
        return False
    return at < w["end"]


def _booked_wire(b) -> Optional[str]:
    """A BookingLink's booked time for the browser, with its zone's offset."""
    from app.services import booking_time
    return booking_time.wire_obj(b)


def _iso(at: Optional[datetime]) -> Optional[str]:
    return at.isoformat() + "Z" if at else None


def touch_bucket(n: Optional[int]) -> str:
    if not n:
        return "none"
    return str(n) if n < 4 else "4+"


# ── the ledger ──────────────────────────────────────────────────────────────
class Ledger:
    """Every recorded event for the real SCI families, loaded once."""

    def __init__(self, db: Session, org_id: str):
        from app.models.models import (BookingLink, CadenceState, CadenceTouchLog, EmailMessage, Lead,
                                       Message)
        from app.models.program_models import (LocationProfile, ProgramConversation, ProgramResponse,
                                               ProgramSourceRecord)
        from app.services import send_source as ss
        from app.services.sci_staging_demo import DEMO_SOURCE
        import time as _t
        self.org = org_id
        self.built_at = datetime.utcnow()
        self.timing: Dict[str, int] = {}
        _t0 = [_t.perf_counter()]

        def lap(name):
            now_ = _t.perf_counter()
            self.timing[name] = int((now_ - _t0[0]) * 1000)
            _t0[0] = now_
        self.locs = {p.location_id: p.official_name for p in db.query(LocationProfile)
                     .filter(LocationProfile.organization_id == org_id,
                             LocationProfile.is_review_bucket.isnot(True)).all()}
        # Only the columns this screen needs: an import row also carries the
        # whole original spreadsheet line, which made this the slowest step.
        R_ = ProgramSourceRecord
        recs = (db.query(R_.lead_id, R_.location_id, R_.campaign_family, R_.created_at, R_.approved_to_send_at,
                         R_.on_hold, R_.hold_reason, R_.scheduled_start_at)
                .filter(R_.organization_id == org_id, R_.lead_id.isnot(None),
                        ~R_.source_lead_id.like("TEST-BOOKING-%"))
                .order_by(R_.created_at).all())
        rec_by = {}
        for r in recs:
            rec_by.setdefault(r.lead_id, r)
        leads = (db.query(Lead.id, Lead.first_name, Lead.last_name, Lead.phone, Lead.email, Lead.status,
                          Lead.phone_line_type, Lead.source)
                 .filter(Lead.organization_id == org_id, Lead.is_test.isnot(True)).all())
        self.families: Dict[str, Dict] = {}
        for l in leads:
            r = rec_by.get(l.id)
            if r is None or l.source == DEMO_SOURCE:
                continue
            self.families[l.id] = {
                "lead_id": l.id,
                "name": ("%s %s" % (l.first_name or "", l.last_name or "")).strip().title() or "Family",
                "location_id": r.location_id, "location": self.locs.get(r.location_id),
                "phone": l.phone, "email": l.email, "dnc": (l.status or "") == "dnc",
                "line_type": l.phone_line_type,
                "campaign": (r.campaign_family or "").replace("_", " ").title() or None,
                "imported_at": r.created_at, "approved_at": r.approved_to_send_at,
                "on_hold": bool(r.on_hold), "hold_reason": r.hold_reason,
                "campaign_key": r.campaign_family, "starts_at": r.scheduled_start_at,
            }
        from app.models.program_models import CampaignFamily
        self.active_campaigns = {k for (k,) in db.query(CampaignFamily.key).filter(
            CampaignFamily.organization_id == org_id, CampaignFamily.is_active.is_(True)).all()}
        ids = list(self.families) or ["-"]
        lap("families")

        self.states = {s.lead_id: s for s in db.query(CadenceState).filter(CadenceState.lead_id.in_(ids)).all()}
        lap("states")

        # outreach attempts (the cadence log)
        logs = (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id.in_(ids),
                                                 CadenceTouchLog.outcome.in_(ATTEMPT_OUTCOMES))
                .order_by(CadenceTouchLog.attempted_at).all())
        msg_ids = [l.message_id for l in logs if l.message_id]
        em_ids = [l.email_message_id for l in logs if l.email_message_id]
        msgs = {m.id: m for m in db.query(Message).filter(Message.id.in_(msg_ids or ["-"])).all()}
        ems = {e.id: e for e in db.query(EmailMessage).filter(EmailMessage.id.in_(em_ids or ["-"])).all()}
        self.attempts: List[Dict] = []
        for l in logs:
            at = l.attempted_at or l.created_at
            ch = "email" if (l.channel or "sms") == "email" else "sms"
            body, err = l.body_preview, None
            if ch == "sms":
                m = msgs.get(l.message_id)
                st = ((m.delivery_status or m.twilio_status) if m else None) or ""
                code = (m.error_code if m else None) or l.provider_error_code
                if l.outcome != "sent" or st in FAILED:
                    result = "failed"
                    err = ERR.get(str(code or ""), None) or (m.error_message if m else None) \
                        or l.provider_error_message or l.reason or ("Error %s" % code if code else "Not sent")
                elif st in DELIVERED:
                    result = "delivered"
                else:
                    result = "pending"
                if m is not None and m.body:
                    body = m.body
            else:
                e = ems.get(l.email_message_id)
                st = (e.status if e else "") or ""
                if l.outcome != "sent" or st in FAILED:
                    result, err = "failed", (getattr(e, "error", None) if e else None) or l.reason or "Not sent"
                elif e is not None and e.opened_at:
                    result = "opened"
                else:
                    result = "delivered" if st in ("sent", "delivered") else "pending"
                if e is not None:
                    body = (e.subject or "") + (" — " if e.subject and body else "") + (body or "")
            f = self.families.get(l.lead_id) or {}
            self.attempts.append({
                "kind": "attempt", "id": l.id, "lead_id": l.lead_id, "touch": l.touch_number or 0,
                "channel": ch, "at": at, "result": result, "error": err, "text": (body or "")[:400],
                "location_id": f.get("location_id"),
            })
        # outreach EMAILS are recorded in their own table (program_email_touches)
        from app.models.program_models import ProgramEmailTouch
        etouch = (db.query(ProgramEmailTouch).filter(ProgramEmailTouch.organization_id == org_id,
                                                     ProgramEmailTouch.lead_id.in_(ids),
                                                     ProgramEmailTouch.status.in_(EMAIL_ATTEMPTS)).all())
        ems2 = {e.id: e for e in db.query(EmailMessage).filter(
            EmailMessage.id.in_([t.email_message_id for t in etouch if t.email_message_id] or ["-"])).all()}
        for t in etouch:
            e = ems2.get(t.email_message_id)
            st = (e.status if e else "") or ""
            if t.status == "failed" or st in FAILED:
                result, err = "failed", (getattr(e, "error", None) if e else None) or t.reason or "Not sent"
            elif t.status == "unknown":
                result, err = "pending", None
            elif e is not None and e.opened_at:
                result, err = "opened", None
            else:
                result, err = "delivered", None
            f = self.families.get(t.lead_id) or {}
            self.attempts.append({
                "kind": "attempt", "id": t.id, "lead_id": t.lead_id, "touch": t.touch_number or 0,
                "channel": "email", "at": t.attempted_at or t.created_at, "result": result, "error": err,
                "text": ((e.subject or "") if e is not None else "Email touch %s" % t.touch_number)[:400]
                        or "Email touch %s" % t.touch_number,
                "location_id": f.get("location_id"), "email_message_id": t.email_message_id,
            })
        self.attempts.sort(key=lambda a: a["at"] or datetime.min)
        # SMS refused by the consent gate: never handed to a provider, so never
        # an attempt or a failure - counted on their own.
        self.blocked = []
        for l in (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id.in_(ids),
                                                   CadenceTouchLog.outcome == "blocked").all()):
            why = l.reason or ""
            if not any(c in why for c in ("NO_SMS_CONSENT", "OPTED_OUT", "CONSENT_WORDING_UNREGISTERED",
                                          "SUPPRESSED", "INVALID_NUMBER")):
                continue
            f = self.families.get(l.lead_id) or {}
            self.blocked.append({"kind": "attempt", "id": l.id, "lead_id": l.lead_id, "touch": l.touch_number or 0,
                                 "channel": "sms", "at": l.attempted_at or l.created_at, "result": "blocked",
                                 "error": "No SMS consent of record" if "NO_SMS_CONSENT" in why else why[:120],
                                 "text": l.body_preview or "", "location_id": f.get("location_id")})
        try:
            from app.services import sms_programs, wholesale_sms
            ok = sms_programs.consented_numbers(db, org_id)
            auto = sms_programs.auto_attest_at_send() and sms_programs.owner_holds_records()
            gone = sms_programs.opted_out_numbers(db, org_id) if auto else set()
            for fam in self.families.values():
                e = wholesale_sms.normalize_e164(fam["phone"])
                # With the owner's consent filed at first send, every number
                # that has not opted out is textable.
                fam["sms_consent"] = (e in ok) or (auto and bool(e) and e not in gone and not fam["dnc"])
        except Exception:                                 # noqa: BLE001
            for fam in self.families.values():
                fam["sms_consent"] = False
        self.attempts_by_lead: Dict[str, List[Dict]] = defaultdict(list)
        lap("attempts")
        for a in self.attempts:
            self.attempts_by_lead[a["lead_id"]].append(a)

        def touch_before(lid, at):
            best = 0
            for a in self.attempts_by_lead.get(lid, ()):
                if a["at"] and at and a["at"] <= at:
                    best = a["touch"]
            return best

        # replies (genuine), opt-outs
        resp = (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org_id,
                                                 ProgramResponse.lead_id.in_(ids))
                .order_by(ProgramResponse.received_at).all())
        self.replies, self.opt_outs = [], []
        for r in resp:
            f = self.families[r.lead_id]
            row = {"kind": "reply", "id": r.id, "lead_id": r.lead_id, "at": r.received_at,
                   "channel": "email" if r.channel == "email" else ("voicemail" if r.channel == "voicemail" else "sms"),
                   "text": (r.body_excerpt or r.summary or "")[:400], "class": r.response_class,
                   "result": r.handling_status, "touch": touch_before(r.lead_id, r.received_at),
                   "location_id": f["location_id"]}
            if r.response_class == "opt_out":
                self.opt_outs.append(dict(row, kind="opt_out"))
            elif r.response_class not in NOT_GENUINE_REPLY:
                self.replies.append(row)
        self.opted_out_ids = {o["lead_id"] for o in self.opt_outs} | {i for i, f in self.families.items() if f["dnc"]}
        lap("replies")

        # AI and staff messages in conversations (not outreach touches)
        self.ai_msgs, self.staff_msgs = [], []
        for m in (db.query(Message).filter(Message.lead_id.in_(ids),
                                           Message.send_source.in_((ss.SCI_SETTER, ss.MANUAL))).all()):
            st = m.delivery_status or m.twilio_status or ""
            row = {"kind": "message", "id": m.id, "lead_id": m.lead_id, "at": m.sent_at, "channel": "sms",
                   "text": (m.body or "")[:400],
                   "result": "failed" if st in FAILED else ("delivered" if st in DELIVERED else "pending"),
                   "error": ERR.get(str(m.error_code or ""), m.error_message) if st in FAILED else None,
                   "touch": touch_before(m.lead_id, m.sent_at),
                   "location_id": self.families[m.lead_id]["location_id"]}
            (self.ai_msgs if m.send_source == ss.SCI_SETTER else self.staff_msgs).append(row)
        from app.services.programs import email_reply as _er
        for e in (db.query(EmailMessage).filter(EmailMessage.lead_id.in_(ids),
                                                EmailMessage.send_source.in_((ss.SCI_SETTER, ss.MANUAL))).all()):
            row = {"kind": "message", "id": e.id, "lead_id": e.lead_id, "at": e.sent_at, "channel": "email",
                   "text": ((e.subject or "") + " — " + _er.plain_text(e.body_html or ""))[:400],
                   "result": "failed" if (e.status or "") in FAILED else ("opened" if e.opened_at else "delivered"),
                   "error": getattr(e, "error", None), "touch": touch_before(e.lead_id, e.sent_at),
                   "location_id": self.families[e.lead_id]["location_id"]}
            (self.ai_msgs if e.send_source == ss.SCI_SETTER else self.staff_msgs).append(row)

        # appointment offers (two real calendar times offered)
        lap("messages")
        from app.services.programs import appointments as A
        self.convs = {}
        for c in (db.query(ProgramConversation).filter(ProgramConversation.lead_id.in_(ids),
                                                       ProgramConversation.is_test.isnot(True))
                  .order_by(ProgramConversation.created_at).all()):
            self.convs[c.lead_id] = c
        self.offers = []
        for lid, c in self.convs.items():
            if c.offered_at is None:
                continue
            try:
                times = [A.describe(s) for s in A.offered(c)]
            except Exception:                             # noqa: BLE001
                times = []
            self.offers.append({"kind": "offer", "id": c.id, "lead_id": lid, "at": c.offered_at, "channel": None,
                                "text": "Offered " + (" or ".join(times) if times else "visit times"),
                                "result": c.state, "touch": touch_before(lid, c.offered_at),
                                "location_id": self.families[lid]["location_id"]})

        # confirmed appointments: on the calendar, real family, not opted out
        lap("offers")
        self.bookings = []
        for b in (db.query(BookingLink).filter(BookingLink.lead_id.in_(ids), BookingLink.status.in_(BOOKED),
                                               BookingLink.booked_time.isnot(None),
                                               BookingLink.calendar_event_id.isnot(None)).all()):
            if b.lead_id in self.opted_out_ids:
                continue
            at = b.confirmed_at or b.created_at
            self.bookings.append({"kind": "appointment", "id": b.id, "lead_id": b.lead_id, "at": at,
                                  "channel": None,
                                  # booked_time is the advisor's LOCAL wall time (booking_time.py):
                                  # sent with its real offset, never stamped as UTC
                                  "appt_at": _booked_wire(b),
                                  "text": "%s · %s" % (b.appt_label or "Visit",
                                                       _booked_ct(b)
                                                       .strftime("%a %b %-d, %-I:%M %p CT")),
                                  "result": b.status, "touch": touch_before(b.lead_id, at),
                                  "location_id": self.families[b.lead_id]["location_id"]})

        lap("bookings")
        ever_texted = {i for (i,) in db.query(Message.lead_id).filter(Message.lead_id.in_(ids)).distinct()}
        ever_emailed = {i for (i,) in db.query(EmailMessage.lead_id).filter(EmailMessage.lead_id.in_(ids),
                                                                            EmailMessage.status == "sent").distinct()}
        self.contacted_ever = ever_texted | ever_emailed | set(self.attempts_by_lead)
        lap("contacted")


_CACHE: Dict[str, "Ledger"] = {}
_LOCK = threading.Lock()
CACHE_SECONDS = 30


def ledger(db: Session, org_id: str, fresh: bool = False) -> "Ledger":
    """The ledger, rebuilt at most every 30 seconds per workspace so the home
    screen, its four ranges and every drill-down read the SAME snapshot (the
    counts then match by construction, even while new replies arrive)."""
    def stale(x):
        return x is None or (datetime.utcnow() - x.built_at).total_seconds() > CACHE_SECONDS

    L = _CACHE.get(org_id)
    if not fresh and not stale(L):
        return L
    # ONE builder at a time: requests that arrive while the snapshot is being
    # rebuilt wait for it instead of each rebuilding it (a burst of screens
    # opening at once must never tie up every database connection).
    with _LOCK:
        L = _CACHE.get(org_id)
        if fresh or stale(L):
            L = Ledger(db, org_id)
            _CACHE[org_id] = L
    return L


# ── metrics ─────────────────────────────────────────────────────────────────
METRICS = {
    # key: (label, row kind, help)
    "families_contacted": ("Families contacted", "family", "Unique families who received at least one outreach attempt."),
    "new_families": ("New families contacted", "family", "Families whose FIRST outreach touch happened in this period."),
    "followup_families": ("Existing families followed up", "family", "Families who received a 2nd or later touch in this period."),
    "attempts": ("Outreach attempts", "attempt", "Every text and email the outreach schedule tried to send."),
    "delivered": ("Delivered", "attempt", "Attempts the carrier or mail server confirmed delivered (emails: sent or opened)."),
    "failed": ("Failed", "attempt", "Attempts that were refused, undelivered or bounced - with the reason."),
    "pending": ("Awaiting delivery result", "attempt", "Sent, but the carrier has not reported delivery yet."),
    "families_delivered": ("Families reached", "family", "Unique families with at least one delivered attempt."),
    "replies": ("Replies", "reply", "Genuine family replies (auto-replies and opt-outs excluded)."),
    "responses_total": ("All meaningful responses", "reply",
                        "Every real family response including STOP/opt-out, but excluding automated replies."),
    "engaged_families": ("Families engaged", "family",
                         "Unique families who responded, including an opt-out, counted once per period."),
    "repliers": ("Families who replied", "family", "Unique families with a genuine reply."),
    "ai_messages": ("AI replies sent", "message", "Messages the SCI AI sent while talking with a family."),
    "staff_messages": ("Staff replies sent", "message", "Messages a person sent from SCI Live."),
    "offers": ("Appointment times offered", "offer", "Families offered two real calendar times."),
    "appointments": ("Appointments confirmed", "appointment",
                     "Confirmed on the Google Calendar. Never includes tests, forecasts or opt-outs."),
    "opt_outs": ("Opt-outs", "opt_out", "Families who asked not to be contacted (STOP)."),
    "loaded": ("Families loaded", "family", "Every real SCI family imported (snapshot, not date-filtered)."),
    "not_contacted": ("Not yet contacted", "family", "Loaded families who have never received a text or email (snapshot)."),
    "in_outreach": ("In active outreach", "family", "Families whose follow-up schedule is running now (snapshot)."),
    "consent_blocked": ("SMS blocked - no consent", "attempt",
                        "Texts the SCI consent gate refused before any provider call (no SMS consent of record, "
                        "opted out, suppressed). Never counted as attempts or failures."),
    "scheduled_sms_ready": ("Queued - SMS consent on file", "family",
                            "Queued new families whose number has a usable SCI SMS consent of record."),
    "scheduled_no_consent": ("Queued - no SMS consent", "family",
                             "Queued new families with NO SMS consent of record: the gate will block their texts "
                             "(emails still go)."),
    "in_followup": ("Active follow-up cadence", "family",
                    "Families already contacted whose follow-up schedule is still running (snapshot)."),
    "approved": ("Approved for outreach", "family", "Families approved to receive outreach (by a person or the daily batch)."),
    "scheduled_new": ("New families scheduled", "family",
                      "Approved and not contacted yet - their first text is scheduled (next time shown)."),
    "awaiting_approval": ("Ready, not yet approved", "family",
                          "Never contacted, cell phone, active campaign, nothing on hold - the daily batch picks from these."),
    "excluded": ("Can't be texted automatically", "family",
                 "Not approved and not textable by the schedule: not a cell phone, on hold, campaign switched off, or opted out."),
}
SNAPSHOT = ("loaded", "not_contacted", "in_outreach", "in_followup", "scheduled_sms_ready", "scheduled_no_consent", "approved", "scheduled_new", "awaiting_approval", "excluded")


def _excluded_reason(L: "Ledger", fam: Dict) -> Optional[str]:
    if fam["dnc"]:
        return "Opted out / Do Not Contact"
    if fam["on_hold"]:
        return "On hold: %s" % (fam["hold_reason"] or "review")
    if (fam["line_type"] or "") != "mobile":
        return "Not a cell phone (%s)" % (fam["line_type"] or "line type unknown")
    if fam["campaign_key"] not in L.active_campaigns:
        return "Campaign switched off (%s)" % (fam["campaign"] or "none")
    return None


def _filters_ok(row: Dict, f: Dict) -> bool:
    if f.get("location_id") and row.get("location_id") != f["location_id"]:
        return False
    if f.get("channel") and row.get("channel") is not None and row.get("channel") != f["channel"]:
        return False
    if f.get("touch") in ("2+", "3+"):
        if (row.get("touch") or 0) < int(f["touch"][0]):
            return False
    elif f.get("touch") and touch_bucket(row.get("touch")) != f["touch"]:
        return False
    if f.get("status") and row.get("result") != f["status"]:
        return False
    return True


def _families_from(L: Ledger, rows: Iterable[Dict], what: str) -> List[Dict]:
    """One row per family, carrying how many events and the latest one."""
    by: Dict[str, Dict] = {}
    for r in rows:
        cur = by.get(r["lead_id"])
        if cur is None:
            by[r["lead_id"]] = cur = {"n": 0, "last": r}
        cur["n"] += 1
        if (r.get("at") or datetime.min) >= (cur["last"].get("at") or datetime.min):
            cur["last"] = r
    out = []
    for lid, v in by.items():
        last = v["last"]
        out.append({"kind": "family", "lead_id": lid, "at": last.get("at"), "touch": last.get("touch"),
                    "channel": last.get("channel"), "result": last.get("result"), "error": last.get("error"),
                    "text": last.get("text"), "count": v["n"], "count_label": what,
                    "location_id": L.families[lid]["location_id"]})
    return out


def rows(L: Ledger, metric: str, w: Dict, f: Optional[Dict] = None) -> List[Dict]:
    f = f or {}
    inw = lambda r: _in(r.get("at"), w)                     # noqa: E731
    att = [a for a in L.attempts if inw(a) and _filters_ok(a, f)]
    if metric == "attempts":
        return att
    if metric == "delivered":
        return [a for a in att if a["result"] in ("delivered", "opened")]
    if metric == "failed":
        return [a for a in att if a["result"] == "failed"]
    if metric == "pending":
        return [a for a in att if a["result"] == "pending"]
    if metric == "families_contacted":
        return _families_from(L, att, "attempts")
    if metric == "families_delivered":
        return _families_from(L, [a for a in att if a["result"] in ("delivered", "opened")], "delivered")
    if metric == "new_families":
        return _families_from(L, [a for a in att if a["touch"] == 1], "first touch")
    if metric == "followup_families":
        return _families_from(L, [a for a in att if a["touch"] >= 2], "follow-up touches")
    if metric == "consent_blocked":
        return [b for b in L.blocked if inw(b) and _filters_ok(b, f)]
    src = {"replies": L.replies, "responses_total": L.replies + L.opt_outs,
           "ai_messages": L.ai_msgs, "staff_messages": L.staff_msgs,
           "offers": L.offers, "appointments": L.bookings, "opt_outs": L.opt_outs}
    if metric in src:
        return [r for r in src[metric] if inw(r) and _filters_ok(r, f)]
    if metric == "repliers":
        return _families_from(L, [r for r in L.replies if inw(r) and _filters_ok(r, f)], "replies")
    if metric == "engaged_families":
        return _families_from(L, [r for r in (L.replies + L.opt_outs)
                                  if inw(r) and _filters_ok(r, f)], "meaningful responses")
    if metric in SNAPSHOT:
        out = []
        for lid, fam in L.families.items():
            if f.get("location_id") and fam["location_id"] != f["location_id"]:
                continue
            st = L.states.get(lid)
            if metric == "not_contacted" and lid in L.contacted_ever:
                continue
            if metric == "in_outreach" and not (st is not None and st.status == "active"):
                continue
            contacted = lid in L.contacted_ever
            if metric == "in_followup" and not (st is not None and st.status == "active" and contacted):
                continue
            note = None
            if metric == "approved" and not fam["approved_at"]:
                continue
            if metric in ("scheduled_new", "scheduled_sms_ready", "scheduled_no_consent"):
                if not fam["approved_at"] or contacted:
                    continue
                if metric == "scheduled_sms_ready" and not fam.get("sms_consent"):
                    continue
                if metric == "scheduled_no_consent" and fam.get("sms_consent"):
                    continue
                when = (fam["starts_at"].isoformat() + "Z") if fam["starts_at"] else "when sending opens"
                note = ("First text scheduled %s" % when if fam.get("sms_consent")
                        else "No SMS consent of record - the text will be blocked (email still goes). Start %s" % when)
            if metric in ("awaiting_approval", "excluded"):
                if fam["approved_at"] or contacted:
                    continue
                why = _excluded_reason(L, fam)
                if (metric == "excluded") != (why is not None):
                    continue
                note = why or "Ready for the next daily batch"
            last = (L.attempts_by_lead.get(lid) or [None])[-1]
            out.append({"kind": "family", "lead_id": lid, "at": (last or {}).get("at") or fam["imported_at"],
                        "touch": (last or {}).get("touch"), "channel": (last or {}).get("channel"),
                        "result": (last or {}).get("result"), "error": (last or {}).get("error"),
                        "text": (last or {}).get("text") or ("On hold: %s" % fam["hold_reason"] if fam["on_hold"]
                                                             else "Not contacted yet"),
                        "count": len(L.attempts_by_lead.get(lid, ())), "count_label": "attempts",
                        "location_id": fam["location_id"],
                        "next_touch_at": (fam["starts_at"] if metric == "scheduled_new"
                                          else fam["starts_at"] if metric.startswith("scheduled_")
                                          else (st.next_touch_due_at if st is not None else None)),
                        **({"text": note} if note else {})})
        return out
    raise KeyError(metric)


def count(L: Ledger, metric: str, w: Dict, f: Optional[Dict] = None) -> int:
    return len(rows(L, metric, w, f))


def _pct(a: int, b: int) -> Optional[float]:
    return round(100.0 * a / b, 1) if b else None


def summary(L: Ledger, w: Dict, location_id: Optional[str] = None, daily_batch: Optional[Dict] = None) -> Dict:
    f = {"location_id": location_id} if location_id else {}
    allw = window("all")
    counts = {k: count(L, k, w, f) for k in METRICS}
    all_time = {k: count(L, k, allw, f) for k in METRICS if k not in SNAPSHOT}

    # Percentages must represent real denominators. Unique responding families
    # are intersected with the contacted cohort in the SAME window: replies
    # arriving today from earlier outreach still count as activity, but must
    # not inflate today's contacted-cohort response rate beyond 100%.
    contacted_ids = {r["lead_id"] for r in rows(L, "families_contacted", w, f)}
    engaged_ids = {r["lead_id"] for r in rows(L, "engaged_families", w, f)}
    replying_ids = {r["lead_id"] for r in rows(L, "repliers", w, f)}
    offered_ids = {r["lead_id"] for r in rows(L, "offers", w, f)}
    booked_ids = {r["lead_id"] for r in rows(L, "appointments", w, f)}
    # touch analytics
    touches = []
    for t in ("1", "2", "3", "4+"):
        tf = dict(f, touch=t)
        fam = count(L, "families_contacted", w, tf)
        rep = count(L, "repliers", w, tf)
        cohort = {r["lead_id"] for r in rows(L, "families_contacted", w, tf)}
        eng = {r["lead_id"] for r in rows(L, "engaged_families", w, tf)}
        attempts = count(L, "attempts", w, tf)
        delivered = count(L, "delivered", w, tf)
        responses = count(L, "responses_total", w, tf)
        opted_out = count(L, "opt_outs", w, tf)
        touches.append({"touch": t, "attempts": attempts, "families": fam,
                        "delivered": delivered, "failed": count(L, "failed", w, tf),
                        "replies": count(L, "replies", w, tf), "repliers": rep,
                        "offers": count(L, "offers", w, tf), "appointments": count(L, "appointments", w, tf),
                        "reply_rate": _pct(rep, fam),
                        "engaged": len(eng & cohort),
                        "engagement_rate": _pct(len(eng & cohort), fam),
                        "delivery_rate": _pct(delivered, attempts),
                        "responses_total": responses, "opt_outs": opted_out,
                        "opt_out_share": _pct(opted_out, responses)})
    fc = counts["families_contacted"]
    avg = round(counts["attempts"] / fc, 2) if fc else None
    afc = all_time["families_contacted"]
    avg_all = round(all_time["attempts"] / afc, 2) if afc else None

    # funnel (this period)
    funnel = [{"key": k, "label": METRICS[k][0], "value": counts[k]}
              for k in ("families_contacted", "families_delivered", "repliers", "offers", "appointments")]

    # failure reasons
    reasons: Dict[str, int] = defaultdict(int)
    for a in rows(L, "failed", w, f):
        reasons[a["error"] or "Not delivered"] += 1

    # location performance
    locs = []
    for lid_loc, name in sorted(L.locs.items(), key=lambda kv: kv[1] or ""):
        lf = {"location_id": lid_loc}
        fam = count(L, "families_contacted", w, lf)
        att = count(L, "attempts", w, lf)
        rep = count(L, "repliers", w, lf)
        row = {"location_id": lid_loc, "location": name, "families": fam, "attempts": att,
               "delivered": count(L, "delivered", w, lf), "failed": count(L, "failed", w, lf),
               "replies": count(L, "replies", w, lf), "repliers": rep, "offers": count(L, "offers", w, lf),
               "appointments": count(L, "appointments", w, lf), "reply_rate": _pct(rep, fam),
               "loaded": count(L, "loaded", w, lf), "not_contacted": count(L, "not_contacted", w, lf)}
        if row["loaded"]:
            locs.append(row)
    locs.sort(key=lambda r: (-r["attempts"], -r["loaded"], r["location"] or ""))

    return {
        "window": {k: w[k] for k in ("preset", "label", "start_date", "end_date")},
        "location_id": location_id,
        "metrics": {k: {"label": METRICS[k][0], "help": METRICS[k][2], "value": counts[k],
                        "all_time": all_time.get(k), "snapshot": k in SNAPSHOT} for k in METRICS},
        "rates": {"delivery_rate": _pct(counts["delivered"], counts["delivered"] + counts["failed"]),
                  "reply_rate": _pct(counts["repliers"], counts["families_contacted"]),
                  "engagement_rate": _pct(len(contacted_ids & engaged_ids), len(contacted_ids)),
                  "engaged_contacted": len(contacted_ids & engaged_ids),
                  "engagement_denominator": len(contacted_ids),
                  "opt_out_share": _pct(counts["opt_outs"], counts["responses_total"]),
                  "offer_to_repliers_rate": _pct(len(offered_ids & replying_ids), len(replying_ids)),
                  "offered_repliers": len(offered_ids & replying_ids),
                  "booked_after_offer_rate": _pct(len(booked_ids & offered_ids), len(offered_ids)),
                  "booked_offered": len(booked_ids & offered_ids),
                  "offered_families": len(offered_ids),
                  "offer_rate": _pct(counts["offers"], counts["repliers"]),
                  "booking_rate": _pct(counts["appointments"], counts["families_contacted"]),
                  "avg_touches_per_family": avg, "avg_touches_per_family_all_time": avg_all},
        "touches": touches,
        "funnel": funnel,
        "failure_reasons": sorted(({"reason": k, "count": v} for k, v in reasons.items()), key=lambda r: -r["count"]),
        "locations": locs,
        "trend": trend(L, w, f),
        "projections": projections(L, f, daily_batch),
        "updated_at": _iso(datetime.utcnow()),
    }


def trend(L: Ledger, w: Dict, f: Dict) -> Dict:
    """Per Central day (weekly when the range is long): recorded counts only."""
    end_d = date.fromisoformat(w["end_date"])
    if w.get("start_date"):
        start_d = date.fromisoformat(w["start_date"])
    else:
        firsts = [ct_day(a["at"]) for a in L.attempts if a["at"]]
        start_d = min(firsts) if firsts else end_d
    if w["preset"] == "today":
        start_d = end_d - timedelta(days=6)              # a single day is shown against its week
    span = (end_d - start_d).days + 1
    step = 7 if span > 70 else 1
    buckets = []
    d = start_d
    while d <= end_d:
        buckets.append(d)
        d += timedelta(days=step)
    def key(at):
        cd = ct_day(at)
        if cd is None or cd < start_d or cd > end_d:
            return None
        return buckets[min((cd - start_d).days // step, len(buckets) - 1)]
    series = {k: defaultdict(int) for k in ("attempts", "delivered", "new_families", "replies", "appointments")}
    for a in L.attempts:
        if not _filters_ok(a, f):
            continue
        k = key(a["at"])
        if k is None:
            continue
        series["attempts"][k] += 1
        if a["result"] in ("delivered", "opened"):
            series["delivered"][k] += 1
        if a["touch"] == 1:
            series["new_families"][k] += 1
    for r in L.replies:
        if _filters_ok(r, f) and key(r["at"]) is not None:
            series["replies"][key(r["at"])] += 1
    for b in L.bookings:
        if _filters_ok(b, f) and key(b["at"]) is not None:
            series["appointments"][key(b["at"])] += 1
    return {"step_days": step, "labels": [b.isoformat() for b in buckets],
            "series": {k: [v.get(b, 0) for b in buckets] for k, v in series.items()}}


def projections(L: Ledger, f: Dict, batch: Optional[Dict]) -> Dict:
    """ESTIMATES, not results. Built from the recorded all-time rates and the
    daily-batch setting; never added to any recorded number."""
    allw = window("all")
    remaining = count(L, "not_contacted", allw, f)
    fam = count(L, "families_contacted", allw, f)
    rep = count(L, "repliers", allw, f)
    appts = count(L, "appointments", allw, f)
    per_day = int((batch or {}).get("count") or 0) if (batch or {}).get("on") else 0
    days = -(-remaining // per_day) if per_day else None
    rr = (rep / fam) if fam else None
    br = (appts / fam) if fam else None
    return {"is_projection": True,
            "note": "Projection — an estimate from the rates recorded so far. Not a result and never counted in totals.",
            "remaining_families": remaining, "daily_batch_per_day": per_day,
            "sending_days_to_reach_all": days,
            "expected_reply_rate_pct": round(rr * 100, 1) if rr is not None else None,
            "expected_additional_repliers": round(remaining * rr) if rr is not None else None,
            "expected_additional_appointments": round(remaining * br, 1) if br is not None else None,
            "based_on_families": fam}


# ── drill-down rows, ready for the screen ───────────────────────────────────
def present(L: Ledger, rows_: List[Dict], limit: int = 1000) -> Tuple[List[Dict], bool]:
    rows_ = sorted(rows_, key=lambda r: r.get("at") or datetime.min, reverse=True)
    out = []
    for r in rows_[:limit]:
        fam = L.families.get(r["lead_id"], {})
        out.append({"kind": r["kind"], "id": r.get("id"), "lead_id": r["lead_id"], "name": fam.get("name"),
                    "location": fam.get("location"), "phone": fam.get("phone"), "at": _iso(r.get("at")),
                    "touch": r.get("touch") or None, "channel": r.get("channel"), "result": r.get("result"),
                    "error": r.get("error"), "text": r.get("text"), "count": r.get("count"),
                    "count_label": r.get("count_label"), "appt_at": r.get("appt_at"),
                    "next_touch_at": _iso(r.get("next_touch_at"))})
    return out, len(rows_) > limit


# ── one family's complete recorded history ──────────────────────────────────
# Plain words for a stopped text schedule - never the raw status code.
CADENCE_STATUS_WORDS = {
    "stopped_no_sms_consent": "Texts stopped - no SMS consent on record",
    "stopped_bad_number": "Texts stopped - number cannot take texts",
    "completed": "Outreach complete - all touches sent",
    "cancelled": "Outreach cancelled",
    "paused": "Outreach paused",
    "replied": "Outreach stopped - family replied",
    "booked": "Outreach stopped - appointment booked",
    "dnc": "Outreach stopped - do not contact",
    "opted_out": "Outreach stopped - family opted out",
}


def cadence_words(status: Optional[str]) -> str:
    s = (status or "").strip()
    return CADENCE_STATUS_WORDS.get(s) or ("Outreach " + s.replace("_", " ")).strip()


def journey(db: Session, org_id: str, lead_id: str) -> Optional[Dict]:
    """Imported → approved → every attempt (with delivery result) → replies →
    AI / human actions → offers → bookings, plus the cadence stage and the
    next scheduled touch. Includes skipped touches with their reason."""
    from app.models.models import (BookingLink, CadenceState, CadenceTouchLog, EmailMessage, Lead, Message)
    from app.models.program_models import (ProgramConversation, ProgramConversationTurn, ProgramResponse,
                                           ProgramSourceRecord)
    from app.services import send_source as ss
    from app.services.programs import email_reply as _er
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
    if lead is None:
        return None
    ev: List[Dict] = []

    def add(at, kind, title, detail=None, **kw):
        if at is not None:
            ev.append(dict({"at": at, "kind": kind, "title": title, "detail": detail}, **kw))

    rec = (db.query(ProgramSourceRecord).filter(ProgramSourceRecord.organization_id == org_id,
                                                ProgramSourceRecord.lead_id == lead.id)
           .order_by(ProgramSourceRecord.created_at).first())
    if rec is not None:
        add(rec.created_at, "imported", "Imported into the SCI program",
            " · ".join(x for x in (rec.source_campaign, rec.source_location_name) if x) or None)
        if rec.on_hold:
            add(rec.held_at or rec.created_at, "hold", "Placed on hold", rec.hold_reason)
        add(rec.approved_to_send_at, "approved", "Approved for outreach",
            "Daily automatic enrollment" if (rec.send_mode or "") == "daily" else None)
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is not None:
        add(st.cadence_started_at, "cadence", "Outreach schedule started")
        if st.completed_at:
            add(st.completed_at, "cadence", "Outreach schedule finished", cadence_words(st.status))
    logs = (db.query(CadenceTouchLog).filter(CadenceTouchLog.lead_id == lead.id)
            .order_by(CadenceTouchLog.created_at).all())
    msg_ids = {l.message_id for l in logs if l.message_id}
    em_ids = {l.email_message_id for l in logs if l.email_message_id}
    msgs = {m.id: m for m in db.query(Message).filter(Message.lead_id == lead.id).all()}
    ems = {e.id: e for e in db.query(EmailMessage).filter(EmailMessage.lead_id == lead.id).all()}
    touches_sent = 0
    for l in logs:
        ch = "Email" if (l.channel or "sms") == "email" else "Text"
        at = l.attempted_at or l.created_at
        if l.outcome in ATTEMPT_OUTCOMES:
            if ch == "Text":
                m = msgs.get(l.message_id)
                s = ((m.delivery_status or m.twilio_status) if m else None) or ""
                code = (m.error_code if m else None) or l.provider_error_code
                res = "failed" if (l.outcome != "sent" or s in FAILED) else ("delivered" if s in DELIVERED else "pending")
                err = (ERR.get(str(code or "")) or (m.error_message if m else None) or l.reason) if res == "failed" else None
                body = (m.body if m else None) or l.body_preview
            else:
                e = ems.get(l.email_message_id)
                s = (e.status if e else "") or ""
                res = "failed" if (l.outcome != "sent" or s in FAILED) else ("opened" if (e and e.opened_at) else "delivered")
                err = ((getattr(e, "error", None) if e else None) or l.reason) if res == "failed" else None
                body = ((e.subject + " — ") if (e and e.subject) else "") + ((_er.plain_text(e.body_html or "")[:600]) if e else (l.body_preview or ""))
            if l.outcome == "sent":
                touches_sent += 1
            add(at, "touch", "Touch %s · %s" % (l.touch_number, ch), body, touch=l.touch_number,
                channel=ch.lower() if ch == "Email" else "sms", result=res, error=err)
        elif l.outcome in ("skipped", "suppressed", "refused", "exhausted", "cancelled"):
            add(at, "skipped", "Touch %s · %s not sent" % (l.touch_number, ch), l.reason, touch=l.touch_number,
                result=l.outcome)
    from app.models.program_models import ProgramEmailTouch
    for t in (db.query(ProgramEmailTouch).filter(ProgramEmailTouch.lead_id == lead.id,
                                                 ProgramEmailTouch.organization_id == org_id).all()):
        e = ems.get(t.email_message_id)
        at = t.attempted_at or t.created_at
        if t.status in EMAIL_ATTEMPTS:
            s_ = (e.status if e else "") or ""
            res = ("failed" if (t.status == "failed" or s_ in FAILED) else "pending" if t.status == "unknown"
                   else "opened" if (e and e.opened_at) else "delivered")
            if t.status == "sent":
                touches_sent += 1
            if t.email_message_id:
                em_ids.add(t.email_message_id)
            add(at, "touch", "Email touch %s" % t.touch_number,
                ((e.subject + " — ") if (e and e.subject) else "") + (_er.plain_text(e.body_html or "")[:600] if e else ""),
                touch=t.touch_number, channel="email", result=res,
                error=((getattr(e, "error", None) if e else None) or t.reason) if res == "failed" else None)
        elif t.status in ("blocked", "retry"):
            add(at, "skipped", "Email touch %s not sent" % t.touch_number, t.reason, touch=t.touch_number,
                result=t.status)
    for m in msgs.values():
        if m.id in msg_ids:
            continue
        s = m.delivery_status or m.twilio_status or ""
        who, kind = {ss.SCI_SETTER: ("AI reply (text)", "ai"), ss.MANUAL: ("Staff text", "staff")}.get(
            m.send_source, ("Text", "message"))
        add(m.sent_at, kind, who, m.body, channel="sms",
            result="failed" if s in FAILED else ("delivered" if s in DELIVERED else "pending"),
            error=(ERR.get(str(m.error_code or ""), m.error_message) if s in FAILED else None))
    for e in ems.values():
        if e.id in em_ids:
            continue
        who, kind = {ss.SCI_SETTER: ("AI reply (email)", "ai"), ss.MANUAL: ("Staff email", "staff")}.get(
            e.send_source, ("Email", "message"))
        add(e.sent_at, kind, who,
            ((e.subject + " — ") if e.subject else "") + _er.plain_text(e.body_html or "")[:600], channel="email",
            result="failed" if (e.status or "") in FAILED else ("opened" if e.opened_at else "delivered"),
            error=getattr(e, "error", None) if (e.status or "") in FAILED else None)
    for r in (db.query(ProgramResponse).filter(ProgramResponse.lead_id == lead.id).all()):
        ch = "email" if r.channel == "email" else ("voicemail" if r.channel == "voicemail" else "sms")
        title = {"opt_out": "Opted out", "auto_reply": "Auto-reply received"}.get(r.response_class,
                                                                                 "Family replied" + (" (voicemail)" if ch == "voicemail" else ""))
        add(r.received_at, "opt_out" if r.response_class == "opt_out" else "reply", title,
            r.body_excerpt or r.summary, channel=ch, result=r.handling_status)
    conv = (db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id)
            .order_by(ProgramConversation.created_at.desc()).first())
    if conv is not None:
        from app.services.programs import appointments as A
        if conv.offered_at:
            try:
                times = " or ".join(A.describe(s) for s in A.offered(conv))
            except Exception:                             # noqa: BLE001
                times = ""
            add(conv.offered_at, "offer", "Offered visit times", times or None)
        if conv.owner_changed_at:
            add(conv.owner_changed_at, "handling",
                "Human Handling on" if conv.state in ("human", "stopped") else "AI Handling on", conv.owner_note)
        for t in (db.query(ProgramConversationTurn).filter(ProgramConversationTurn.conversation_id == conv.id,
                                                            ProgramConversationTurn.role == "ai",
                                                            ProgramConversationTurn.delivery.in_(("draft", "held")))):
            add(t.created_at, "ai", "AI draft held (not sent)", t.text, result=t.delivery)
    for b in (db.query(BookingLink).filter(BookingLink.lead_id == lead.id).all()):
        if (b.status or "") in BOOKED and b.booked_time:
            add(b.confirmed_at or b.created_at, "booked",
                "Appointment confirmed" if b.calendar_event_id else "Appointment booked (not yet on calendar)",
                "%s · %s" % (b.appt_label or "Visit",
                             _booked_ct(b).strftime("%a %b %-d, %-I:%M %p CT")),
                result=b.status)
    ev.sort(key=lambda e: e["at"])
    for e in ev:
        e["at"] = _iso(e["at"])
    replies = sum(1 for e in ev if e["kind"] == "reply")
    booked = next((e for e in reversed(ev) if e["kind"] == "booked"), None)
    stage = "Not contacted yet"
    if (lead.status or "") == "dnc" or any(e["kind"] == "opt_out" for e in ev):
        stage = "Opted out"
    elif booked:
        stage = "Appointment confirmed"
    elif conv is not None and conv.offered_at:
        stage = "Times offered"
    elif replies:
        stage = "In conversation"
    elif st is not None and st.status == "active":
        stage = "In outreach · touch %s" % (st.current_touch_number or 1)
    elif st is not None:
        stage = cadence_words(st.status)
    from app.services.cadence_service import _get_org_cadence_schedule
    try:
        steps = [{"touch": t["touch_number"], "day": t["day_offset"], "channel": t.get("channel") or "sms"}
                 for t in _get_org_cadence_schedule(db, org_id)]
    except Exception:                                     # noqa: BLE001
        steps = []
    from app.models.models import User
    adv = db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else None
    from app.models.program_models import LocationProfile
    loc = (db.query(LocationProfile.official_name).filter(LocationProfile.organization_id == org_id,
                                                         LocationProfile.location_id == rec.location_id).scalar()
           if rec is not None and rec.location_id else None)
    sent_touches = sorted({e["touch"] for e in ev if e["kind"] == "touch" and e.get("channel") != "email"
                           and e.get("result") != "failed" and e.get("touch")})
    booking = next((e for e in reversed(ev) if e["kind"] == "booked"), None)
    offered = next((e for e in reversed(ev) if e["kind"] == "offer"), None)
    return {
        "lead_id": lead.id,
        "name": ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title(),
        "location": loc, "advisor": (adv.full_name or adv.email) if adv else None,
        "campaign": (rec.campaign_family or "").replace("_", " ").title() if rec is not None else None,
        "steps": steps, "texts_sent_touches": sent_touches,
        "next_touch_number": ((st.current_touch_number or 0) + 1) if (st is not None and st.status == "active") else None,
        "appointment": {"state": "confirmed" if booking else ("offered" if offered else "none"),
                        "detail": (booking or offered or {}).get("detail")},
        "opted_out": stage == "Opted out",
        "stage": stage,
        "touches_sent": touches_sent,
        "current_touch": st.current_touch_number if st is not None else None,
        "cadence_status": st.status if st is not None else None,
        "next_touch_at": _iso(st.next_touch_due_at) if (st is not None and st.status == "active") else None,
        "replies": replies,
        "handling": ("human" if (conv is not None and conv.state in ("human", "stopped")) else "ai"),
        # The owner holds a consent form for every SCI family, so a schedule
        # stopped for "no consent" can be restarted from the family page.
        "texts_restartable": bool(st is not None and st.status in ("stopped_no_sms_consent", "stopped_bad_number")),
        "sms_links_on": _links_on(),
        # "Resend from text..." - a schedule a restart may reopen (never an
        # opt-out, reply, booking or do-not-contact; the server checks again).
        "can_resend": bool(st is not None and (st.status or "") not in (
            "opted_out", "stopped_opted_out", "dnc", "stopped_dnc", "replied", "stopped_replied",
            "booked", "stopped_booked", "cancelled")
            and (lead.status or "").lower() not in ("dnc", "replied", "booked", "hot") and stage != "Opted out"),
        "events": ev,
    }


def _links_on() -> bool:
    try:
        from app.services import sms_programs
        return sms_programs.sms_links_enabled()
    except Exception:                                     # noqa: BLE001
        return False


def overview(L: Ledger, db: Session, location_id: Optional[str] = None) -> Dict:
    """The dashboard's fixed panels (always "now" / "today"): pipeline,
    today's activity, cadence steps. Every number is a metric + filter, so the
    screen opens its exact records."""
    f = {"location_id": location_id} if location_id else {}
    today, allw, d24, wk = window("today"), window("all"), window("24h"), window("7d")
    c = lambda m, w=allw, **x: count(L, m, w, dict(f, **x))          # noqa: E731
    approved_rows = rows(L, "approved", allw, f)
    approved_today = sum(1 for lid in {r["lead_id"] for r in approved_rows}
                         if L.families[lid]["approved_at"] and _in(L.families[lid]["approved_at"], today))
    pending_new_ids = {r["lead_id"] for r in rows(L, "scheduled_new", allw, f)}
    sched = [L.families[lid]["starts_at"] for lid in pending_new_ids
             if L.families[lid]["starts_at"]]
    # Count actual contact-field coverage separately from SMS consent.
    # Previous UI used sms_ready/no_consent to label families with *no phone*
    # as missing consent. Those are different things and should not be conflated.
    with_phone = sum(1 for lid in pending_new_ids if (L.families[lid].get("phone") or "").strip())
    from app.services.cadence_service import _get_org_cadence_schedule
    try:
        steps = _get_org_cadence_schedule(db, L.org)
    except Exception:                                     # noqa: BLE001
        steps = []
    at_step: Dict[int, int] = defaultdict(int)
    for lid, st in L.states.items():
        if st.status == "active" and lid in L.families and (not location_id or L.families[lid]["location_id"] == location_id):
            at_step[int(st.current_touch_number or 0)] += 1
    return {
        "pipeline": [{"key": k, "label": lbl, "value": c(k)} for k, lbl in (
            ("loaded", "Total imported"), ("approved", "Approved"), ("in_outreach", "In cadence"),
            ("awaiting_approval", "Ready, not approved"), ("excluded", "Excluded"))],
        "approved_today": approved_today,
        "in_followup": c("in_followup"),
        "scheduled_new": {"value": c("scheduled_new"), "sms_ready": c("scheduled_sms_ready"),
                          "no_consent": c("scheduled_no_consent"),
                          "with_phone": with_phone, "no_phone": len(pending_new_ids) - with_phone,
                          "first_start": _iso(min(sched)) if sched else None,
                          "last_start": _iso(max(sched)) if sched else None},
        "replies_24h": {"value": count(L, "replies", d24, f), "repliers": count(L, "repliers", d24, f)},
        "appointments_7d": count(L, "appointments", wk, f),
        "today": [
            {"key": "first", "label": "First touches sent", "metric": "attempts", "touch": "1",
             "value": c("attempts", today, touch="1")},
            {"key": "followup", "label": "Follow-up touches sent", "metric": "attempts", "touch": "2+",
             "value": c("attempts", today, touch="2+")},
            {"key": "email", "label": "Emails sent", "metric": "attempts", "channel": "email",
             "value": c("attempts", today, channel="email")},
            {"key": "blocked", "label": "SMS blocked - no consent", "metric": "consent_blocked",
             "value": c("consent_blocked", today)},
            {"key": "replies", "label": "Replies received", "metric": "replies", "value": c("replies", today)},
            {"key": "offers", "label": "Appointments offered", "metric": "offers", "value": c("offers", today)},
            {"key": "appts", "label": "Appointments confirmed", "metric": "appointments",
             "value": c("appointments", today)},
        ],
        "cadence": [{"touch": t["touch_number"], "day": t["day_offset"], "channel": t.get("channel") or "sms",
                     "label": {1: "First contact", 2: "Follow up"}.get(t["touch_number"],
                                                                       "Follow up %d" % (t["touch_number"] - 1)),
                     "families_past": at_step.get(t["touch_number"], 0)} for t in steps],
        "cadence_waiting": at_step.get(0, 0),
    }


def streams(L: Ledger, location_id: Optional[str] = None) -> Dict:
    """NEW family first touches vs EXISTING family follow-ups, separately:
    the most recent Central day each stream actually sent on, and what."""
    def last_day(pred):
        days = [ct_day(a["at"]) for a in L.attempts if a["at"] and pred(a)
                and (not location_id or a["location_id"] == location_id)]
        if not days:
            return None
        d = max(days)
        rows_ = [a for a in L.attempts if a["at"] and pred(a) and ct_day(a["at"]) == d
                 and (not location_id or a["location_id"] == location_id)]
        return {"date": d.isoformat(), "sent": len(rows_),
                "delivered": sum(1 for a in rows_ if a["result"] in ("delivered", "opened")),
                "failed": sum(1 for a in rows_ if a["result"] == "failed"),
                "touch2": sum(1 for a in rows_ if a["touch"] == 2),
                "touch3plus": sum(1 for a in rows_ if a["touch"] >= 3)}
    return {"new": last_day(lambda a: a["touch"] == 1), "existing": last_day(lambda a: a["touch"] >= 2)}
