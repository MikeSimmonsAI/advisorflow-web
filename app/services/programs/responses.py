"""Inbound replies for a location outreach program: classify, pause, alert, time.

Called from the inbound SMS webhook and the inbound email pollers AFTER the
platform's own handling (Reply row, STOP/DNC, suppression) has run. It adds,
for program organizations only:

    attach   contact + source Lead ID + location + campaign family
    pause    any active cadence for the lead (never resumed automatically)
    classify urgency HOT / ACTIVE / LOW (or OPT-OUT / BAD DATA / WRONG PERSON)
             plus intents: appointment, information, pricing, benefits,
             veteran, cemetery, cremation, general planning, objection...
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

from app.services.programs.reply_rules import (  # noqa: F401  (re-exported: other modules use responses.X)
    ACKNOWLEDGMENT, ACTIVE, APPOINTMENT, BAD_DATA, BENEFITS, CEMETERY, CREMATION, GENERAL_PLANNING, HOT,
    INFORMATION, INTENT_LABELS, I_BAD_DATA, I_OPT_OUT, I_WRONG_PERSON, LABELS, LOW, NOT_INTERESTED, OBJECTION,
    OPT_OUT, PRICING, RECOMMENDED, SLA_BREACH_LABEL, VETERAN, WRONG_PERSON,
    alert_plan, cadence_action, classify, intents, staff_alert_gate, suggested_reply, urgency,
)

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
            delivered = False
            ok, reason = staff_alert_gate(to, channel, audience, bool(prog.staff_sms_alerts_enabled))
            if ok:
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
            label = {"hot": "HOT RESPONSE", "sla_breach": "HOT RESPONSE - NOT YET HANDLED",
                     "mailbox_reconnect": "ACTION NEEDED - reply mailbox disconnected"}.get(kind, "Response")
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
    if st is None:
        return False
    act = cadence_action(None, str(getattr(st.status, "value", st.status)))
    if act["new_status"] == "paused" and str(getattr(st.status, "value", st.status)) == "active":
        st.status = "paused"
    return act["paused"]


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
    from app.services.programs import campuses as _campuses
    if alias_prof is not None and prof is not None and alias_prof.location_id != prof.location_id \
            and not _campuses.same_campus(db, lead.organization_id, alias_prof.location_id, prof.location_id):
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
            log.exception("cadence stop failed for lead %s", lead.id)
        # stop_cadence_for_lead only acts on an ACTIVE cadence, and the reply
        # has just paused it - an opt-out must END it, paused or not.
        from app.models.models import CadenceState
        st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
        if st is not None and str(getattr(st.status, "value", st.status)) in ("active", "paused"):
            st.status = CadenceStatus.STOPPED_DNC.value
            from datetime import timezone as _tz
            st.completed_at = datetime.now(_tz.utc)
        resp.closed_at = now
        if channel == "email":
            lead.allow_email = False          # an email opt-out of record
    found = intents(body, cls)
    resp.intents = json.dumps(found) if found else None
    resp.urgency = urgency(cls)
    resp.suggested_reply = suggested_reply(
        cls, found, first_name=lead.first_name, contact=prog.primary_contact_name,
        location=(prof.official_name if prof else (alias_prof.official_name if alias_prof else None)),
        channel=channel, bereaved="bereavement mentioned" in " ".join(result.get("reasons") or []))
    if cls in (BAD_DATA, WRONG_PERSON) and rec is not None:
        rec.needs_data_review = True
        flags = json.loads(rec.data_note_flags or "[]")
        flag = "WRONG PERSON (reply)" if cls == WRONG_PERSON else "WRONG NUMBER / ADDRESS (reply)"
        if flag not in flags:
            flags.append(flag)
        rec.data_note_flags = json.dumps(flags)
    db.add(resp)
    db.flush()

    msg = "%s %s" % ("🔥" if cls == HOT else "💬", resp.summary)
    plan = alert_plan(cls)
    if plan:
        _alert(db, prog, resp, lead, plan["kind"], msg, management=plan["management"], external=plan["external"])
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


DEFAULT_SLA_REALERTS = 3


def sla_realert_cap() -> int:
    """How many times an unhandled HOT reply re-alerts management (one per SLA
    window). After that it stays flagged "past SLA" on the dashboard and in the
    Health tab, but management's phone and inbox stop being paged."""
    import os
    try:
        return max(1, int(os.environ.get("PROGRAM_SLA_MAX_REALERTS") or DEFAULT_SLA_REALERTS))
    except ValueError:
        return DEFAULT_SLA_REALERTS


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
    cap = sla_realert_cap()
    for resp in q.all():
        prog = identity.program_for_org(db, resp.organization_id)
        if prog is None:
            continue
        if (resp.sla_alert_count or 0) >= cap:
            continue                     # escalated enough; it stays red on the dashboard
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


class RegionalReviewBucket:
    """Stand-in profile for an unknown sender to a shared regional number: no
    location (never guessed), so the item queues for regional review."""
    location_id = None

    def __init__(self, organization_id, pool_id, label):
        self.organization_id = organization_id
        self.pool_id = pool_id
        self.official_name = "regional pool %s (%s) - location unknown" % (pool_id, label)


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


def mailbox_reconnect_alert(db: Session, address: str, error: str) -> int:
    """The central mailbox lost its Microsoft sign-in. Every active program
    depends on it for replies, so each program's admins get one in-app alert
    (and management one staff email when staff alerts are on)."""
    from app.models.program_models import OutreachProgram
    progs = db.query(OutreachProgram).filter(OutreachProgram.is_active.is_(True)).all()
    msg = ("⚠️ The reply mailbox %s needs to be reconnected - EvoSys cannot read family replies "
           "until it is. Owner console → Email → Inbound mailboxes → Reconnect." % address)
    n = 0
    for prog in progs:
        for u in _org_admin_users(db, prog.organization_id):
            db.add(Notification(user_id=u.id, type=NotificationType.REPLY_RECEIVED, message=msg,
                                link="/program?tab=health"))
            db.add(ProgramAlert(organization_id=prog.organization_id, kind="mailbox_reconnect",
                                audience="management", channel="in_app", recipient=u.id,
                                delivered=True, message=msg, reason=(error or "")[:200]))
            n += 1
        if prog.staff_sms_alerts_enabled:
            for m in _managers(prog):
                if m.get("email"):
                    ok, why = _deliver_staff_alert(db, prog, "email", m["email"], msg, kind="mailbox_reconnect")
                    db.add(ProgramAlert(organization_id=prog.organization_id, kind="mailbox_reconnect",
                                        audience="management", channel="email", recipient=m["email"],
                                        delivered=ok, reason=why, message=msg))
    db.commit()
    return n
