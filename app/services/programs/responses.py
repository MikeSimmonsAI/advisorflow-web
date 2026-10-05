"""Inbound replies for a location outreach program: classify, pause, alert, time.

Called from the inbound SMS webhook and the inbound email pollers AFTER the
platform's own handling (Reply row, STOP/DNC, suppression) has run. It adds,
for program organizations only:

    attach   contact + source Lead ID + location + campaign family
    pause    any active cadence for the lead (never resumed automatically)
    classify HOT / ACTIVE / LOW / OPT-OUT / BAD DATA
    summary  a one-line summary and a recommended next action
    alert    in-app to the primary contact and managers; staff SMS/email only
             when switched on AND a recipient is configured (default: off)
    time     a response-handling record with an SLA due time for HOT replies

It never sends to the CUSTOMER and never raises into the webhook: a failure
here is logged and the inbound message is still recorded by the caller.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import Lead, Notification, NotificationType, User
from app.models.program_models import (
    OutreachProgram, ProgramAlert, ProgramResponse, ProgramSourceRecord,
)
from app.services.programs import identity

log = logging.getLogger(__name__)

HOT, ACTIVE, LOW, OPT_OUT, BAD_DATA = "hot", "active", "low", "opt_out", "bad_data"
LABELS = {HOT: "HOT", ACTIVE: "ACTIVE", LOW: "LOW", OPT_OUT: "OPT-OUT", BAD_DATA: "BAD DATA / WRONG PERSON"}
SLA_BREACH_LABEL = "HOT RESPONSE — NOT YET HANDLED"

_HOT_PATTERNS = [
    (r"\b(appointment|appt|schedule|book|meet(ing)?|visit|tour|come (by|in|out)|stop by)\b", "wants a meeting or visit"),
    (r"\b(price|pricing|cost|costs|how much|payment|plan options|financ)", "asked about price"),
    (r"\b(more info|information|details|send (me|it|the)|brochure|guide|packet|flyer|mail me)\b", "wants more information"),
    (r"(?<!not )(?<!no longer )\b(interested|yes please|sounds good|i('| a)m in|sign me up|let'?s do)\b", "interested"),
    (r"\b(call me|reach (out|me)|get back to me|follow ?up|contact me|text me)\b", "requests follow-up"),
]
# OPT-OUT only on an unmistakable request: the whole message is a carrier stop
# word, or an explicit phrase. "Can I stop by Tuesday?" is a visit, not a STOP.
_STOP_WORDS = r"^\s*(stop|stopall|unsubscribe|end|quit|cancel|optout|opt out|remove)\s*[.!]*\s*$"
_OPT_OUT_PHRASES = (r"\b(remove me|take me off|unsubscribe me|opt me out|do not (contact|text|email|call) me|"
                    r"don'?t (contact|text|email|call) me|stop (texting|emailing|contacting) me|leave me alone|"
                    r"not interested|no thank(s| you)|lose my number)\b")
# BAD DATA is about the RECORD (wrong person / number), never about a death in
# the family - for a funeral home a bereaved reply is the most important one.
_BAD_DATA_PATTERNS = (r"\b(wrong (number|person|email|address)|this is not (him|her|me)|not (him|her)|"
                      r"no one (here )?by that name|don'?t know (who|what) (this|you|that)|who is this)\b")
_BEREAVEMENT = r"\b(passed away|passed on|died|deceased|funeral for|lost my (husband|wife|mother|father|son|daughter))\b"
_LOW_PATTERNS = r"^\s*(ok(ay)?|k|thanks?( you)?|ok thanks?|thank you|thx|got it|received|👍|🙏)[\s.!]*$"

_CLASS_FROM_REPLY = {
    "interested": HOT, "callback": HOT, "question": ACTIVE, "neutral": LOW,
    "not_interested": OPT_OUT, "dnc": OPT_OUT, "wrong_number": BAD_DATA,
}


def classify(body: str, reply_classification: Optional[str] = None) -> Dict:
    """Deterministic and explainable. A stated need beats a stray keyword."""
    text = (body or "").strip().lower()
    hot = [why for pat, why in _HOT_PATTERNS if re.search(pat, text)]
    bereaved = bool(re.search(_BEREAVEMENT, text))
    if re.search(_STOP_WORDS, text):
        return {"class": OPT_OUT, "reasons": ["asked to stop"]}
    if re.search(_BAD_DATA_PATTERNS, text) and not hot:
        return {"class": BAD_DATA, "reasons": ["says it is the wrong person / number"]}
    weak = {"interested", "requests follow-up"}
    if hot and re.search(_OPT_OUT_PHRASES, text) and set(hot) <= weak:
        return {"class": OPT_OUT, "reasons": ["opted out or not interested"]}
    if hot:
        if bereaved:
            hot = ["bereavement mentioned"] + hot
        return {"class": HOT, "reasons": hot}
    if re.search(_OPT_OUT_PHRASES, text):
        return {"class": OPT_OUT, "reasons": ["opted out or not interested"]}
    if bereaved:
        return {"class": HOT, "reasons": ["bereavement mentioned - needs a personal reply"]}
    upstream = _CLASS_FROM_REPLY.get((reply_classification or "").lower())
    if upstream in (OPT_OUT, BAD_DATA, HOT):
        return {"class": upstream, "reasons": ["classified as %s on arrival" % reply_classification]}
    if re.search(_LOW_PATTERNS, text) or not text:
        return {"class": LOW, "reasons": ["acknowledgement only"]}
    if "?" in text or upstream == ACTIVE:
        return {"class": ACTIVE, "reasons": ["asked a question"]}
    return {"class": ACTIVE if len(text.split()) >= 4 else LOW,
            "reasons": ["engaged reply" if len(text.split()) >= 4 else "short reply"]}


RECOMMENDED = {
    HOT: "Reply personally now - they asked for something. Offer a time or send what they asked for.",
    ACTIVE: "Answer their question and keep the conversation going.",
    LOW: "No action needed beyond a courtesy reply if appropriate.",
    OPT_OUT: "Do not contact again. Automation has been stopped for this person.",
    BAD_DATA: "Fix the contact record in Data Review before any further outreach.",
}


def summarize(lead: Lead, body: str, result: Dict, channel: str, location: Optional[str]) -> str:
    name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title() or "A contact"
    where = " (%s)" % location if location else ""
    excerpt = re.sub(r"\s+", " ", (body or "").strip())
    if len(excerpt) > 140:
        excerpt = excerpt[:137] + "..."
    return "%s%s replied by %s - %s: %s. \"%s\"" % (
        name, where, channel.upper(), LABELS[result["class"]], ", ".join(result["reasons"]), excerpt)


def _managers(prog: OutreachProgram) -> List[Dict]:
    try:
        data = json.loads(prog.management_recipients or "[]")
        return [m for m in data if isinstance(m, dict)]
    except ValueError:
        return []


def _org_admin_users(db: Session, org_id: str) -> List[User]:
    from app.models.sales_models import Membership
    ids = {m.user_id for m in db.query(Membership).filter(
        Membership.scope_id == org_id, Membership.is_active.is_(True),
        Membership.role.in_(("org_admin", "super_admin"))).all()}
    q = db.query(User).filter(User.is_active.is_(True))
    users = q.filter((User.id.in_(ids)) | ((User.organization_id == org_id)
                                          & (User.role.in_(("org_admin", "super_admin"))))).all() \
        if ids else q.filter(User.organization_id == org_id,
                             User.role.in_(("org_admin", "super_admin"))).all()
    return users


def _alert(db: Session, prog: OutreachProgram, resp: ProgramResponse, lead: Lead,
           kind: str, message: str, *, management: bool, external: bool) -> List[ProgramAlert]:
    """Raise the alerts a response deserves and record every decision."""
    out: List[ProgramAlert] = []
    ntype = NotificationType.HOT_REPLY if kind in (HOT, "sla_breach") else NotificationType.REPLY_RECEIVED
    link = "/program/responses?id=%s" % resp.id

    # In-app: the primary contact's account if there is one, otherwise and
    # also for management, the organization's admins.
    recipients: Dict[str, str] = {}
    if prog.primary_contact_user_id:
        recipients[prog.primary_contact_user_id] = "primary"
    if management or not prog.primary_contact_user_id:
        for u in _org_admin_users(db, prog.organization_id):
            recipients.setdefault(u.id, "management")
    for uid, audience in recipients.items():
        db.add(Notification(user_id=uid, lead_id=lead.id, type=ntype, message=message, link=link))
        out.append(ProgramAlert(organization_id=prog.organization_id, response_id=resp.id,
                                kind=kind, audience=audience, channel="in_app",
                                recipient=uid, delivered=True, message=message))

    if external:
        targets = [("primary", "sms", prog.alert_phone), ("primary", "email", prog.alert_email)]
        if management and not _managers(prog):
            out.append(ProgramAlert(organization_id=prog.organization_id, response_id=resp.id,
                                    kind=kind, audience="management", channel="sms",
                                    recipient=None, delivered=False,
                                    reason="no management recipients configured", message=message))
        if management:
            for m in _managers(prog):
                targets.append(("management", "sms", m.get("phone")))
                targets.append(("management", "email", m.get("email")))
        for audience, channel, to in targets:
            delivered, reason = False, None
            if not to:
                reason = "no %s configured for %s alerts" % ("phone" if channel == "sms" else "email", audience)
            elif not prog.staff_sms_alerts_enabled:
                reason = "staff alerts are switched off for this program"
            else:
                delivered, reason = _deliver_staff_alert(db, prog, channel, to, message,
                                                         kind=kind, response_id=resp.id)
            out.append(ProgramAlert(organization_id=prog.organization_id, response_id=resp.id,
                                    kind=kind, audience=audience, channel=channel, recipient=to,
                                    delivered=delivered, reason=reason, message=message))
    for a in out:
        db.add(a)
    return out


def _app_link(response_id: Optional[str]) -> Optional[str]:
    import os
    base = (os.environ.get("APP_BASE_URL") or "").strip().rstrip("/")
    return "%s/program?tab=responses%s" % (base, "&id=%s" % response_id if response_id else "") if base else None


def _deliver_staff_alert(db: Session, prog: OutreachProgram, channel: str, to: str,
                         message: str, *, kind: Optional[str] = None, response_id: Optional[str] = None):
    """A real staff alert. Reached only when switched on with a recipient set."""
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == prog.organization_id).first()
    try:
        if channel == "email":
            from app.services.email_service import send_email_via_provider
            from app.services.public_identity import sending_identity_for_org
            import html as _html
            label = {"hot": "HOT RESPONSE", "sla_breach": "HOT RESPONSE - NOT YET HANDLED"}.get(kind, "Response")
            link = _app_link(response_id)
            body = "<p>%s</p>" % _html.escape(message)
            if link:
                body += '<p><a href="%s">Open it in EvoSys</a></p>' % _html.escape(link)
            res = send_email_via_provider(to, "%s - %s" % (label, prog.name), body,
                                          org=sending_identity_for_org(db, prog.organization_id))
            return bool(res.get("success")), res.get("error")
        from app.services.sms_service import Client, _org_twilio_credentials
        sid, token = _org_twilio_credentials(org)
        if not (sid and token and org and org.org_twilio_phone_number):
            return False, "the organization has no Twilio number configured"
        link = _app_link(response_id)
        text = ("%s %s" % (message[:250], link)) if link else message[:300]
        Client(sid, token).messages.create(body=text,
                                           from_=org.org_twilio_phone_number, to=to)
        return True, None
    except Exception as exc:  # pragma: no cover - provider failure path
        log.warning("program staff alert failed: %s", exc)
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])


def _pause_cadence(db: Session, lead: Lead) -> bool:
    from app.models.models import CadenceState
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is not None and str(getattr(st.status, "value", st.status)) == "active":
        st.status = "paused"
        return True
    return bool(st is not None and str(getattr(st.status, "value", st.status)) != "active")


def on_inbound(db: Session, lead: Lead, body: str, channel: str, *,
               reply_id: Optional[str] = None, reply_classification: Optional[str] = None,
               now: Optional[datetime] = None, reply_to_alias: Optional[str] = None,
               alias_location_id: Optional[str] = None) -> Optional[ProgramResponse]:
    """Record and route one inbound reply. None for non-program organizations.

    reply_to_alias / alias_location_id: the location address the family wrote
    to. The conversation is ALWAYS the contact's own; the alias's location is
    used when the contact has none resolved, and a mismatch is called out in
    the summary rather than silently re-homing the family."""
    prog = identity.program_for_org(db, lead.organization_id)
    if prog is None:
        return None
    now = now or datetime.utcnow()
    result = classify(body, reply_classification)
    cls = result["class"]
    rec = (db.query(ProgramSourceRecord)
           .filter(ProgramSourceRecord.organization_id == lead.organization_id,
                   ProgramSourceRecord.lead_id == lead.id).first())
    prof = identity.location_profile_for_lead(db, lead)
    alias_prof = None
    if alias_location_id:
        from app.models.program_models import LocationProfile
        alias_prof = (db.query(LocationProfile)
                      .filter(LocationProfile.organization_id == lead.organization_id,
                              LocationProfile.location_id == alias_location_id).first())
    resp = ProgramResponse(
        organization_id=lead.organization_id, lead_id=lead.id, reply_id=reply_id,
        reply_to_alias=reply_to_alias,
        channel=channel, location_id=prof.location_id if prof else (
            alias_prof.location_id if alias_prof else (rec.location_id if rec else None)),
        campaign_family=rec.campaign_family if rec else None,
        source_lead_id=rec.source_lead_id if rec else None,
        response_class=cls, body_excerpt=(body or "")[:1000],
        summary=summarize(lead, body, result, channel, prof.official_name if prof else None),
        recommended_action=RECOMMENDED[cls], received_at=now,
        handling_status="closed" if cls in (OPT_OUT,) else "new",
    )
    if alias_prof is not None and prof is not None and alias_prof.location_id != prof.location_id:
        resp.summary = "%s [Wrote to the %s address; contact is at %s.]" % (
            resp.summary, alias_prof.official_name, prof.official_name)
    if cls == HOT:
        resp.sla_due_at = now + timedelta(minutes=max(1, int(prog.hot_sla_minutes or 15)))
    # Any meaningful reply pauses the cadence; an acknowledgement does too -
    # a person, not the sequence, decides what happens next.
    resp.cadence_paused = _pause_cadence(db, lead)
    if cls == OPT_OUT:
        from app.services.cadence_service import stop_cadence_for_lead
        from app.models.models import CadenceStatus
        try:
            stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)
        except Exception:
            pass
        resp.closed_at = now
        if channel == "email":
            lead.allow_email = False          # an email opt-out of record
    if cls == BAD_DATA and rec is not None:
        rec.needs_data_review = True
        flags = json.loads(rec.data_note_flags or "[]")
        if "WRONG PERSON (reply)" not in flags:
            flags.append("WRONG PERSON (reply)")
        rec.data_note_flags = json.dumps(flags)
    db.add(resp)
    db.flush()

    msg = "%s %s" % ("🔥" if cls == HOT else "💬", resp.summary)
    if cls == HOT:
        _alert(db, prog, resp, lead, HOT, msg, management=True, external=True)
    elif cls == ACTIVE:
        _alert(db, prog, resp, lead, ACTIVE, msg, management=False, external=True)
    elif cls == LOW:
        _alert(db, prog, resp, lead, LOW, msg, management=False, external=False)
    elif cls == BAD_DATA:
        _alert(db, prog, resp, lead, BAD_DATA, msg, management=False, external=False)
    db.commit()
    return resp


def safe_on_inbound(db: Session, lead: Lead, body: str, channel: str, **kw) -> None:
    """The webhook-facing wrapper: never raises."""
    try:
        on_inbound(db, lead, body, channel, **kw)
    except Exception as exc:
        log.exception("program inbound handling failed for lead %s: %s",
                      getattr(lead, "id", "?"), exc)
        try:
            db.rollback()
        except Exception:
            pass


# ── handling and SLA ─────────────────────────────────────────────────────────

def mark(db: Session, resp: ProgramResponse, state: str, user: User,
         now: Optional[datetime] = None) -> ProgramResponse:
    """NEW -> OPENED -> RESPONDED -> ACTIVE (-> CLOSED). Earlier stamps are kept."""
    now = now or datetime.utcnow()
    order = ["new", "opened", "responded", "active", "closed"]
    if state not in order:
        raise ValueError("unknown handling state %r" % state)
    if order.index(state) >= order.index(resp.handling_status or "new"):
        resp.handling_status = state
    if state in ("opened", "responded", "active", "closed") and not resp.opened_at:
        resp.opened_at, resp.opened_by = now, user.id
    if state in ("responded", "active", "closed") and not resp.responded_at:
        resp.responded_at = now
    if state in ("active", "closed") and not resp.active_at:
        resp.active_at = now
    if state == "closed":
        resp.closed_at = now
    db.commit()
    return resp


def sla_sweep(db: Session, now: Optional[datetime] = None,
              organization_id: Optional[str] = None) -> List[ProgramResponse]:
    """Re-alert HOT responses still untouched past their SLA. Once per SLA window."""
    now = now or datetime.utcnow()
    q = db.query(ProgramResponse).filter(
        ProgramResponse.response_class == HOT,
        ProgramResponse.handling_status == "new",
        ProgramResponse.sla_due_at.isnot(None),
        ProgramResponse.sla_due_at <= now)
    if organization_id:
        q = q.filter(ProgramResponse.organization_id == organization_id)
    breached = []
    for resp in q.all():
        prog = identity.program_for_org(db, resp.organization_id)
        if prog is None:
            continue
        window = timedelta(minutes=max(1, int(prog.hot_sla_minutes or 15)))
        if resp.last_sla_alert_at and now - resp.last_sla_alert_at < window:
            continue
        lead = db.query(Lead).filter(Lead.id == resp.lead_id).first()
        if lead is None:
            continue
        mins = int((now - resp.received_at).total_seconds() // 60)
        msg = "⏰ %s - %d min. %s" % (SLA_BREACH_LABEL, mins, resp.summary or "")
        _alert(db, prog, resp, lead, "sla_breach", msg, management=True, external=True)
        resp.sla_alert_count = (resp.sla_alert_count or 0) + 1
        resp.last_sla_alert_at = now
        # Commit PER ROW: an error on a later row must not roll back the
        # stamp of an alert already raised, or it would be raised again.
        db.commit()
        breached.append(resp)
    db.commit()
    return breached


def record_unmatched(db: Session, prof, *, alias: str, sender: Optional[str], subject: Optional[str],
                     body: Optional[str], received_at: Optional[datetime], mailbox_message_id: Optional[str],
                     reason: str) -> Optional["ProgramUnmatchedReply"]:
    """A reply to a location alias that matches no contact: keep it, alert, queue it."""
    from app.models.program_models import ProgramUnmatchedReply
    prog = identity.program_for_org(db, prof.organization_id)
    if prog is None:
        return None
    if mailbox_message_id and db.query(ProgramUnmatchedReply.id).filter(
            ProgramUnmatchedReply.mailbox_message_id == mailbox_message_id).first():
        return None
    row = ProgramUnmatchedReply(organization_id=prof.organization_id, location_id=prof.location_id,
                                alias=alias, from_address=sender, subject=(subject or "")[:500],
                                body_excerpt=(body or "")[:1000], received_at=received_at,
                                mailbox_message_id=mailbox_message_id, reason=reason)
    db.add(row)
    db.flush()
    msg = ("📩 Reply to %s (%s) from %s matches no contact - open Responses to handle it."
           % (alias, prof.official_name, sender or "unknown sender"))
    recipients = {}
    if prog.primary_contact_user_id:
        recipients[prog.primary_contact_user_id] = "primary"
    for u in _org_admin_users(db, prog.organization_id):
        recipients.setdefault(u.id, "management")
    for uid, audience in recipients.items():
        db.add(Notification(user_id=uid, type=NotificationType.REPLY_RECEIVED, message=msg,
                            link="/program?tab=responses"))
        db.add(ProgramAlert(organization_id=prog.organization_id, response_id=None, kind="unmatched_reply",
                            audience=audience, channel="in_app", recipient=uid, delivered=True, message=msg))
    return row
