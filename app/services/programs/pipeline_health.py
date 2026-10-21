"""PIPELINE HEALTH (Phase Nine) - what failed, where, and what to do about it.

One read-only report that follows a family's reply through the SCI pipeline:

    received -> processed -> AI answered / a person needed -> team notified
             -> notification delivered -> completed (closed / booked)

and lists every place it is stuck, each with a count, the oldest example, a
few record ids to open, and the action needed, in plain words.

RULES
  * Read-only. Nothing is sent, retried, changed or alerted from here - this
    is a screen, not another notification system.
  * No family PII: no names, phone numbers, email addresses or message words.
    Only counts, record ids (response / alert / turn / booking), location ids,
    timestamps and reasons - and a reason is only ever a fixed plain-words
    label (reason_label), never the stored text, which can name a family.
  * One tenant: every query is filtered by the program's organization_id.
  * Bounded: every row-returning query has a LIMIT and a time window; counts
    run on (organization_id, created_at)-style indexed columns.
  * Test / synthetic contacts (Lead.is_test) are left out, as in every other
    production count.
"""
import os
import re
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead, Reply
from app.models.program_models import (
    LocationProfile, OutreachProgram, ProgramAlert, ProgramConversation, ProgramConversationTurn,
    ProgramResponse, ProgramUnmatchedReply,
)

DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 24 * 14
LOOKBACK = timedelta(days=7)          # how far back an OPEN problem is still looked for
STUCK_SENDING = timedelta(minutes=10)
HELD_TOO_LONG = timedelta(minutes=30)
NOT_PROCESSED_GRACE = timedelta(minutes=5)
RETRY_WINDOW = timedelta(minutes=120)  # alert_ledger.retry_failed max_age_minutes
ROW_LIMIT = 500                        # rows read per check
EXAMPLES = 5                           # ids shown per problem

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _iso(dt) -> Optional[str]:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# Reasons are free text written by code, providers and people, and can carry
# a family's name, number or words ("refused for <name> at <number>"). Masking
# numbers is not enough, so a reason is never passed through: it is mapped to
# a fixed plain-words label. Unknown text becomes a generic label.
_EXC_NAME = re.compile(r"^[A-Z][A-Za-z]+(Error|Exception|Open|Reached)$")
_CODE = re.compile(r"(?i)(?:error|code|twilio)\s*:?\s*(\d{5})\b")
_REASON_LABELS = [
    (r"(?i)outside contact hours", "outside contact hours"),
    (r"(?i)opted out|\bstop\b|\bdnc\b|do not contact|unsubscribed|21610", "the family has opted out"),
    (r"(?i)21211|21614|invalid.{0,20}(to|number|phone)|not a (valid )?mobile", "invalid or non-mobile phone number"),
    (r"(?i)30007|filtered|flagged", "the carrier filtered the text"),
    (r"(?i)3000[3456]|unreachable|landline", "phone unreachable or a landline"),
    (r"(?i)30008|unknown error", "carrier reported an unknown error"),
    (r"(?i)alerts? (are )?switched off|alerts? off", "staff alerts are switched off"),
    (r"(?i)no recipient|nowhere to", "no recipient set"),
    (r"(?i)test or demo", "test or demo family"),
    (r"(?i)time ?d? ?out", "the provider timed out"),
    (r"(?i)rate ?limit|\b429\b|quota|credit", "provider rate limit or no credit"),
    (r"(?i)auth|\b401\b|\b403\b|credential|api key", "provider sign-in failed"),
    (r"(?i)connection|unavailable|\b50[0234]\b|outage", "provider unavailable"),
    (r"(?i)email.{0,40}(not sent|failed|could not)", "the email could not be sent"),
    (r"(?i)safety|rule|refused|not allowed|blocked", "a safety rule refused it"),
    (r"(?i)^undelivered$|^failed$", "carrier did not deliver it"),
]


def reason_label(reason: Optional[str]) -> Optional[str]:
    """A fixed, PII-free label for a reason: an exception class name, a
    provider error code, or a known plain-words category."""
    r = (reason or "").strip()
    if not r:
        return None
    if _EXC_NAME.match(r):
        return r
    for pat, label in _REASON_LABELS:
        if re.search(pat, r):
            return label
    m = _CODE.search(r)
    if m:
        return "provider error code %s" % m.group(1)
    if r.isdigit() and len(r) == 5:
        return "provider error code %s" % r
    return "other - open the record to see the reason"


def _problem(key: str, stage: str, severity: str, what: str, action: str, *,
             count: int, oldest=None, ids: Iterable[str] = (), id_type: str = "",
             reasons: Optional[List[str]] = None, location_ids: Optional[List[str]] = None) -> Dict:
    out = {"key": key, "stage": stage, "severity": severity, "what": what, "count": int(count),
           "oldest_at": _iso(oldest), "example_ids": [i for i in ids if i][:EXAMPLES],
           "id_type": id_type, "action": action}
    if reasons:
        seen = []
        for r in reasons:
            m = reason_label(r)
            if m and m not in seen:
                seen.append(m)
        out["reasons"] = seen[:3]
    if location_ids:
        out["location_ids"] = sorted({x for x in location_ids if x})[:10]
    return out


def _test_lead_ids(db: Session, org_id: str):
    return db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.is_test.is_(True))


def _real_alerts(db: Session, org_id: str):
    """Staff text / email pages for this tenant, test pages left out."""
    from app.services.test_records import exclude_test_alerts
    q = db.query(ProgramAlert).filter(ProgramAlert.organization_id == org_id,
                                      ProgramAlert.channel.in_(("sms", "email")))
    q = exclude_test_alerts(q)
    return q.filter(or_(ProgramAlert.lead_id.is_(None), ~ProgramAlert.lead_id.in_(_test_lead_ids(db, org_id))))


# ── configuration (booleans only - never a value) ───────────────────────────

def calendar_connected(db: Session, user_id: Optional[str]) -> bool:
    if not user_id:
        return False
    try:
        from app.models.calendar_models import CalendarConnection
        if db.query(CalendarConnection.id).filter(CalendarConnection.user_id == user_id,
                                                  CalendarConnection.is_connected.is_(True)).first():
            return True
    except Exception:                                    # noqa: BLE001
        db.rollback()
    from app.models.models import User
    u = db.query(User).filter(User.id == user_id).first()
    return bool(u is not None and getattr(u, "google_calendar_connected", False))


def configuration(db: Session, prog: OutreachProgram) -> Dict:
    mode = (getattr(prog, "ai_setter_mode", None) or "off").lower()
    return {
        "booking_user_set": bool(getattr(prog, "booking_user_id", None)),
        "calendar_connected": calendar_connected(db, getattr(prog, "booking_user_id", None)),
        "staff_alerts_on": bool(prog.staff_sms_alerts_enabled),
        "alert_phone_set": bool((prog.alert_phone or "").strip()),
        "alert_email_set": bool((prog.alert_email or "").strip()),
        "ai_setter_mode": mode if mode in ("off", "draft", "auto") else "off",
        "openai_key_present": bool((os.environ.get("OPENAI_API_KEY") or "").strip()),
    }


def _config_problems(cfg: Dict) -> List[Dict]:
    out = []
    if not cfg["booking_user_set"]:
        out.append(_problem("config_no_booking_user", "configuration", "medium",
                            "No booking calendar owner is set, so texts carry no booking link and the AI cannot book.",
                            "In SCI Settings, choose whose calendar bookings land on.", count=1))
    if not cfg["staff_alerts_on"]:
        out.append(_problem("config_staff_alerts_off", "configuration", "high",
                            "Staff alerts are switched off - nobody is texted or emailed when a family replies.",
                            "Turn staff alerts on in SCI Settings.", count=1))
    if not cfg["alert_phone_set"] and not cfg["alert_email_set"]:
        out.append(_problem("config_no_alert_recipient", "configuration", "high",
                            "There is no alert phone and no alert email - alerts have nowhere to go.",
                            "Add the alert phone and/or alert email in SCI Settings.", count=1))
    elif not cfg["alert_phone_set"]:
        out.append(_problem("config_no_alert_phone", "configuration", "low",
                            "There is no alert phone - alerts go by email only.",
                            "Add an alert phone in SCI Settings if the team should be texted.", count=1))
    elif not cfg["alert_email_set"]:
        out.append(_problem("config_no_alert_email", "configuration", "low",
                            "There is no alert email - alerts go by text only.",
                            "Add an alert email in SCI Settings if the team should be emailed.", count=1))
    if cfg["ai_setter_mode"] != "off" and not cfg["openai_key_present"]:
        out.append(_problem("config_no_ai_key", "configuration", "high",
                            "The appointment setter is on but the AI key is missing on the server - every reply goes to a person.",
                            "Set OPENAI_API_KEY on the server (platform owner).", count=1))
    return out


# ── funnel ──────────────────────────────────────────────────────────────────

def funnel(db: Session, org_id: str, since: datetime, now: datetime) -> Dict:
    test_ids = _test_lead_ids(db, org_id)
    org_leads = db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.is_test.isnot(True))

    replies = (db.query(func.count(Reply.id))
               .filter(Reply.lead_id.in_(org_leads), Reply.received_at >= since).scalar() or 0)
    unmatched = (db.query(func.count(ProgramUnmatchedReply.id))
                 .filter(ProgramUnmatchedReply.organization_id == org_id,
                         func.coalesce(ProgramUnmatchedReply.received_at, ProgramUnmatchedReply.created_at) >= since)
                 .scalar() or 0)
    processed = (db.query(func.count(ProgramResponse.id))
                 .filter(ProgramResponse.organization_id == org_id, ProgramResponse.received_at >= since,
                         ~ProgramResponse.lead_id.in_(test_ids)).scalar() or 0)
    ai_answered = (db.query(func.count(ProgramConversationTurn.id))
                   .filter(ProgramConversationTurn.organization_id == org_id,
                           ProgramConversationTurn.created_at >= since,
                           ProgramConversationTurn.role == "ai",
                           ProgramConversationTurn.delivery.in_(("sent", "simulated")),
                           ~ProgramConversationTurn.lead_id.in_(test_ids)).scalar() or 0)

    # A person is needed NOW: conversations a person owns that are not closed
    # out, plus HOT replies nobody has opened. One family counts once.
    human = {i for (i,) in (db.query(ProgramConversation.lead_id)
                            .filter(ProgramConversation.organization_id == org_id,
                                    ProgramConversation.state == "human",
                                    ProgramConversation.is_test.isnot(True),
                                    ~ProgramConversation.lead_id.in_(test_ids))
                            .limit(5000).all())}
    hot_open = {i for (i,) in (db.query(ProgramResponse.lead_id)
                               .filter(ProgramResponse.organization_id == org_id,
                                       ProgramResponse.response_class == "hot",
                                       ProgramResponse.handling_status == "new",
                                       ~ProgramResponse.lead_id.in_(test_ids))
                               .limit(5000).all())}

    alerts = _real_alerts(db, org_id).filter(ProgramAlert.created_at >= since)
    attempted = alerts.filter(ProgramAlert.send_state.in_(("sending", "sent", "failed"))).count()
    carrier_failed = ProgramAlert.provider_status.in_(("failed", "undelivered"))
    delivered = alerts.filter(ProgramAlert.send_state == "sent", ProgramAlert.delivered.is_(True)).count()
    failed = alerts.filter(or_(ProgramAlert.send_state == "failed", carrier_failed)).count()
    merged = alerts.filter(ProgramAlert.send_state == "merged").count()
    skipped = alerts.filter(ProgramAlert.send_state == "skipped").count()

    closed = (db.query(func.count(ProgramResponse.id))
              .filter(ProgramResponse.organization_id == org_id, ProgramResponse.handling_status == "closed",
                      ProgramResponse.closed_at >= since, ~ProgramResponse.lead_id.in_(test_ids)).scalar() or 0)
    booked = (db.query(func.count(ProgramConversation.id))
              .filter(ProgramConversation.organization_id == org_id, ProgramConversation.state == "booked",
                      ProgramConversation.is_test.isnot(True), ProgramConversation.updated_at >= since,
                      ~ProgramConversation.lead_id.in_(test_ids)).scalar() or 0)
    return {
        "funnel": {
            "received": int(replies + unmatched),
            "processed": int(processed),
            "ai_answered": int(ai_answered),
            "human_action_required": len(human | hot_open),
            "notification_attempted": int(attempted),
            "notification_delivered": int(delivered),
            "notification_failed": int(failed),
            "completed": int(closed + booked),
        },
        "detail": {
            "received_matched": int(replies), "received_unmatched": int(unmatched),
            "human_conversations": len(human), "hot_unopened": len(hot_open),
            "notification_sent_awaiting_confirmation": max(0, int(attempted) - int(delivered) - int(failed)),
            "notification_merged": int(merged), "notification_skipped": int(skipped),
            "completed_closed": int(closed), "completed_booked": int(booked),
        },
    }


# ── problems ────────────────────────────────────────────────────────────────

def _alerts_stuck(db, org_id, now) -> Optional[Dict]:
    q = (_real_alerts(db, org_id)
         .filter(ProgramAlert.send_state == "sending", ProgramAlert.created_at >= now - LOOKBACK,
                 func.coalesce(ProgramAlert.last_attempt_at, ProgramAlert.created_at) < now - STUCK_SENDING))
    rows = q.order_by(ProgramAlert.created_at).limit(ROW_LIMIT).all()
    if not rows:
        return None
    return _problem("alerts_stuck_sending", "notification", "high",
                    "Staff alerts started sending more than 10 minutes ago and never finished.",
                    "Check the texting / email provider is up, then resend these alerts from the response screen "
                    "(the automatic retry only picks up alerts marked failed).",
                    count=len(rows), oldest=rows[0].created_at, ids=[r.id for r in rows], id_type="alert")


def _alerts_failed_final(db, org_id, now) -> Optional[Dict]:
    from app.services.programs.alert_ledger import MAX_ATTEMPTS
    q = (_real_alerts(db, org_id)
         .filter(ProgramAlert.send_state == "failed", ProgramAlert.created_at >= now - LOOKBACK,
                 or_(func.coalesce(ProgramAlert.attempts, 0) >= MAX_ATTEMPTS,
                     ProgramAlert.created_at < now - RETRY_WINDOW)))
    rows = q.order_by(ProgramAlert.created_at).limit(ROW_LIMIT).all()
    if not rows:
        return None
    return _problem("alerts_failed_final", "notification", "high",
                    "Staff alerts failed and will not be retried again (all attempts used or retry window over).",
                    "Read the reason; fix the recipient number/email or the provider, then call the family "
                    "back from the response screen - the team may never have been told.",
                    count=len(rows), oldest=rows[0].created_at, ids=[r.id for r in rows], id_type="alert",
                    reasons=[r.provider_error or r.reason for r in rows])


def _alerts_undelivered(db, org_id, now) -> Optional[Dict]:
    q = (_real_alerts(db, org_id)
         .filter(ProgramAlert.send_state == "sent", ProgramAlert.created_at >= now - LOOKBACK,
                 ProgramAlert.provider_status.in_(("failed", "undelivered"))))
    rows = q.order_by(ProgramAlert.created_at).limit(ROW_LIMIT).all()
    if not rows:
        return None
    return _problem("alerts_undelivered", "notification", "high",
                    "Staff alert texts were accepted but the carrier reported them not delivered.",
                    "Check the staff phone can receive texts from the SCI line (carrier block, landline, "
                    "opted out); make sure the team saw these families another way.",
                    count=len(rows), oldest=rows[0].created_at, ids=[r.id for r in rows], id_type="alert",
                    reasons=[r.provider_error or r.provider_status for r in rows])


def _held_turns(db, org_id, now, test_ids) -> Optional[Dict]:
    from app.services import contact_hours
    rows = (db.query(ProgramConversationTurn)
            .filter(ProgramConversationTurn.organization_id == org_id,
                    ProgramConversationTurn.delivery == "held",
                    ProgramConversationTurn.created_at >= now - LOOKBACK,
                    ProgramConversationTurn.created_at < now - HELD_TOO_LONG,
                    ~ProgramConversationTurn.lead_id.in_(test_ids))
            .order_by(ProgramConversationTurn.created_at).limit(ROW_LIMIT).all())
    if not rows:
        return None
    leads = {l.id: l for l in db.query(Lead).filter(Lead.id.in_({r.lead_id for r in rows})).all()}
    late = []
    for r in rows:
        lead = leads.get(r.lead_id)
        try:
            ok = lead is not None and contact_hours.check(lead, now).get("permitted")
        except Exception:                                # noqa: BLE001
            ok = False
        if ok:                         # allowed to text right now, and still waiting
            late.append(r)
    if not late:
        return None
    return _problem("ai_turns_held_too_long", "ai_answer", "high",
                    "AI replies have waited more than 30 minutes for contact hours, and contact hours are open now.",
                    "The held-reply sender may not be running - check the background jobs; or answer these "
                    "families by hand from SCI Live.",
                    count=len(late), oldest=late[0].created_at, ids=[r.id for r in late], id_type="turn")


def _blocked_turns(db, org_id, since, test_ids) -> Optional[Dict]:
    rows = (db.query(ProgramConversationTurn)
            .filter(ProgramConversationTurn.organization_id == org_id,
                    ProgramConversationTurn.delivery == "blocked",
                    ProgramConversationTurn.created_at >= since,
                    ~ProgramConversationTurn.lead_id.in_(test_ids))
            .order_by(ProgramConversationTurn.created_at).limit(ROW_LIMIT).all())
    # A deliberate block (opted out, campaign off, review hold, test) is the
    # system working - never "answer them yourself".
    from app.services.programs.ai_setter import _POLICY_BLOCK
    rows = [r for r in rows if not _POLICY_BLOCK.search(r.reason or "")]
    if not rows:
        return None
    return _problem("ai_turns_blocked", "ai_answer", "medium",
                    "AI replies were stopped before sending (a safety rule refused them or the send failed).",
                    "Open each family in SCI Live and answer them yourself; the reason says what stopped the reply.",
                    count=len(rows), oldest=rows[0].created_at, ids=[r.id for r in rows], id_type="turn",
                    reasons=[r.reason for r in rows])


def _ai_unavailable(db, org_id, since, test_ids) -> Optional[Dict]:
    rows = (db.query(ProgramConversationTurn)
            .filter(ProgramConversationTurn.organization_id == org_id,
                    ProgramConversationTurn.role == "system",
                    ProgramConversationTurn.reason.like("AI unavailable%"),
                    ProgramConversationTurn.created_at >= since,
                    ~ProgramConversationTurn.lead_id.in_(test_ids))
            .order_by(ProgramConversationTurn.created_at).limit(ROW_LIMIT).all())
    if not rows:
        return None
    return _problem("ai_provider_failures", "ai_answer", "high",
                    "The AI could not be reached for some replies; those families were handed to a person.",
                    "Make sure each of these families gets a reply from a person; check the AI provider "
                    "(credit, key, outage).",
                    count=len(rows), oldest=rows[0].created_at, ids=[r.id for r in rows], id_type="turn")


def _ai_breaker() -> Optional[Dict]:
    try:
        from app.services import ai_gateway
        snap = ai_gateway._BUDGET.snapshot()
    except Exception:                                    # noqa: BLE001
        return None
    if not snap.get("breaker_open_for_s"):
        return None
    return _problem("ai_provider_paused", "ai_answer", "high",
                    "Background AI is paused for %d more seconds after a provider failure." % snap["breaker_open_for_s"],
                    "Replies in this time go to a person. Check the AI provider (credit, key, outage).",
                    count=1, reasons=[snap.get("breaker_reason") or ""])


def _hot_past_sla(db, org_id, now, test_ids) -> Optional[Dict]:
    rows = (db.query(ProgramResponse)
            .filter(ProgramResponse.organization_id == org_id, ProgramResponse.response_class == "hot",
                    ProgramResponse.handling_status == "new", ProgramResponse.sla_due_at.isnot(None),
                    ProgramResponse.sla_due_at < now, ProgramResponse.received_at >= now - timedelta(days=30),
                    ~ProgramResponse.lead_id.in_(test_ids))
            .order_by(ProgramResponse.received_at).limit(ROW_LIMIT).all())
    if rows:
        # A HOT reply the AI already answered (times offered, follow-up agreed)
        # is not a family waiting on staff.
        from app.services.programs import responses as _R
        lids = list({r.lead_id for r in rows})
        latest = {}
        for c in (db.query(ProgramConversation).filter(ProgramConversation.lead_id.in_(lids))
                  .order_by(ProgramConversation.created_at).all()):
            latest[c.lead_id] = c
        rows = [r for r in rows if not _R.ai_answered(db, latest.get(r.lead_id), r)]
    if not rows:
        return None
    return _problem("hot_past_sla", "human_action", "high",
                    "HOT replies (a family asking to meet) are past their response time and nobody has opened them.",
                    "Open each reply in SCI Live and contact the family now, then mark it handled.",
                    count=len(rows), oldest=rows[0].received_at, ids=[r.id for r in rows], id_type="response",
                    location_ids=[r.location_id for r in rows])


def _handoffs_without_alert(db, org_id, since, test_ids) -> Optional[Dict]:
    convs = (db.query(ProgramConversation)
             .filter(ProgramConversation.organization_id == org_id, ProgramConversation.state == "human",
                     ProgramConversation.is_test.isnot(True),
                     ProgramConversation.owner_changed_by.is_(None),        # a person's own take-over needs no page
                     ~func.coalesce(ProgramConversation.handoff_reason, "").like("taken by%"),
                     ProgramConversation.last_family_at >= since,           # the family wrote in this window
                     ~ProgramConversation.lead_id.in_(test_ids))
             .order_by(ProgramConversation.updated_at).limit(ROW_LIMIT).all())
    if not convs:
        return None
    lead_ids = list({c.lead_id for c in convs})
    first_wrote = min((c.last_family_at for c in convs if c.last_family_at), default=since)
    # A staff page is a text or an email - the in-app notice is always written.
    last_alert = dict(db.query(ProgramAlert.lead_id, func.max(ProgramAlert.created_at))
                      .filter(ProgramAlert.organization_id == org_id, ProgramAlert.lead_id.in_(lead_ids),
                              ProgramAlert.channel.in_(("sms", "email")),
                              ProgramAlert.created_at >= first_wrote - timedelta(minutes=10))
                      .group_by(ProgramAlert.lead_id).all())
    missing = []
    for c in convs:
        start = (c.last_family_at or c.updated_at or since) - timedelta(minutes=10)
        seen = last_alert.get(c.lead_id)
        if seen is None or seen < start:
            missing.append(c)
    if not missing:
        return None
    # Ids a person can open: the family's latest response.
    resp_ids = {}
    for lid, rid, _at in (db.query(ProgramResponse.lead_id, ProgramResponse.id, ProgramResponse.received_at)
                          .filter(ProgramResponse.organization_id == org_id,
                                  ProgramResponse.lead_id.in_([c.lead_id for c in missing][:ROW_LIMIT]))
                          .order_by(ProgramResponse.received_at).limit(ROW_LIMIT * 4).all()):
        resp_ids[lid] = rid
    return _problem("handoff_without_alert", "notification", "high",
                    "Conversations were handed to a person but no staff alert was recorded for them.",
                    "Check staff alerts are on and have a recipient; open these families in SCI Live - "
                    "nobody was told they need a person.",
                    count=len(missing), oldest=missing[0].updated_at,
                    ids=[resp_ids.get(c.lead_id) for c in missing], id_type="response")


def _booked_without_calendar(db, org_id, now, cfg, test_ids, booking_user_id=None) -> Optional[Dict]:
    if not cfg["calendar_connected"] or not booking_user_id:
        return None
    rows = (db.query(BookingLink)
            .filter(BookingLink.lead_id.in_(db.query(Lead.id).filter(Lead.organization_id == org_id)),
                    BookingLink.user_id == booking_user_id,              # only the calendar that is connected
                    BookingLink.status.in_(("booked", "confirmed")),
                    BookingLink.calendar_event_id.is_(None),
                    BookingLink.created_at >= now - timedelta(days=30),
                    ~BookingLink.lead_id.in_(test_ids))
            .order_by(BookingLink.created_at).limit(ROW_LIMIT).all())
    if not rows:
        return None
    return _problem("booked_without_calendar_event", "completed", "medium",
                    "Appointments are booked but never reached the calendar.",
                    "Check the calendar connection in Settings, then add these appointments to the calendar "
                    "by hand so nobody is missed.",
                    count=len(rows), oldest=rows[0].booked_time or rows[0].created_at,
                    ids=[r.id for r in rows], id_type="booking")


def _unmapped_location(db, org_id, since, test_ids) -> Optional[Dict]:
    review = [i for (i,) in db.query(LocationProfile.location_id)
              .filter(LocationProfile.organization_id == org_id, LocationProfile.is_review_bucket.is_(True)).all()]
    cond = ProgramResponse.location_id.is_(None)
    if review:
        cond = or_(cond, ProgramResponse.location_id.in_(review))
    rows = (db.query(ProgramResponse)
            .filter(ProgramResponse.organization_id == org_id, ProgramResponse.received_at >= since, cond,
                    ~ProgramResponse.lead_id.in_(test_ids))
            .order_by(ProgramResponse.received_at).limit(ROW_LIMIT).all())
    if not rows:
        return None
    return _problem("responses_without_location", "processed", "medium",
                    "Replies came in from families with no location (or still in Location Review).",
                    "Assign these records a location in Records -> Location Review so replies, alerts and "
                    "bookings go to the right home.",
                    count=len(rows), oldest=rows[0].received_at, ids=[r.id for r in rows], id_type="response")


def _not_processed(db, org_id, since, now) -> Optional[Dict]:
    org_leads = db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.is_test.isnot(True))
    done = db.query(ProgramResponse.reply_id).filter(ProgramResponse.organization_id == org_id,
                                                     ProgramResponse.reply_id.isnot(None))
    rows = (db.query(Reply.id, Reply.received_at)
            .filter(Reply.lead_id.in_(org_leads), Reply.received_at >= since,
                    Reply.received_at < now - NOT_PROCESSED_GRACE, ~Reply.id.in_(done))
            .order_by(Reply.received_at).limit(ROW_LIMIT).all())
    if not rows:
        return None
    return _problem("replies_not_processed", "processed", "high",
                    "Family replies were stored but never processed (no response record, no alert, no AI answer).",
                    "Check the server logs for errors around these times; open the families' conversations "
                    "and answer them by hand.",
                    count=len(rows), oldest=rows[0][1], ids=[r[0] for r in rows], id_type="reply")


def _unmatched_open(db, org_id, now) -> Optional[Dict]:
    rows = (db.query(ProgramUnmatchedReply)
            .filter(ProgramUnmatchedReply.organization_id == org_id, ProgramUnmatchedReply.status == "open",
                    ProgramUnmatchedReply.created_at >= now - LOOKBACK)
            .order_by(ProgramUnmatchedReply.created_at).limit(ROW_LIMIT).all())
    if not rows:
        return None
    return _problem("unmatched_replies_open", "received", "medium",
                    "Replies arrived at a location address but match no family.",
                    "Open Responses -> Unmatched, find the family (another email or number) and mark it handled.",
                    count=len(rows), oldest=rows[0].received_at or rows[0].created_at,
                    ids=[r.id for r in rows], id_type="unmatched_reply",
                    location_ids=[r.location_id for r in rows])


def report(db: Session, prog: OutreachProgram, *, hours: int = DEFAULT_WINDOW_HOURS,
           now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    hours = max(1, min(int(hours or DEFAULT_WINDOW_HOURS), MAX_WINDOW_HOURS))
    since = now - timedelta(hours=hours)
    org_id = prog.organization_id
    test_ids = _test_lead_ids(db, org_id)
    cfg = configuration(db, prog)
    fun = funnel(db, org_id, since, now)

    checks = [
        lambda: _not_processed(db, org_id, since, now),
        lambda: _unmatched_open(db, org_id, now),
        lambda: _unmapped_location(db, org_id, since, test_ids),
        lambda: _held_turns(db, org_id, now, test_ids),
        lambda: _blocked_turns(db, org_id, since, test_ids),
        lambda: _ai_unavailable(db, org_id, since, test_ids),
        _ai_breaker,
        lambda: _hot_past_sla(db, org_id, now, test_ids),
        lambda: _handoffs_without_alert(db, org_id, since, test_ids),
        lambda: _alerts_stuck(db, org_id, now),
        lambda: _alerts_failed_final(db, org_id, now),
        lambda: _alerts_undelivered(db, org_id, now),
        lambda: _booked_without_calendar(db, org_id, now, cfg, test_ids, getattr(prog, "booking_user_id", None)),
    ]
    problems: List[Dict] = []
    for fn in checks:
        try:
            p = fn()
        except Exception as exc:                         # noqa: BLE001 - a broken check is itself a finding
            db.rollback()
            p = _problem("check_failed", "diagnostics", "low",
                         "One pipeline check could not run (%s)." % type(exc).__name__,
                         "Tell the platform owner; the rest of this report is still accurate.", count=1)
        if p:
            problems.append(p)
    problems += _config_problems(cfg)
    problems.sort(key=lambda p: (SEVERITY_ORDER.get(p["severity"], 9), -p["count"]))
    return {
        "generated_at": _iso(now), "window_hours": hours, "since": _iso(since),
        "ok": not any(p["severity"] in ("high", "medium") for p in problems),
        "funnel": fun["funnel"], "funnel_detail": fun["detail"],
        "problems": problems, "configuration": cfg,
        "notes": ["Test and synthetic contacts are not counted.",
                  "Read-only: nothing here sends, retries or changes anything."],
    }
