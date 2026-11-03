"""SMS Delivery Recovery (#482): reviewable, channel-scoped SMS holds.

THE RULES THIS MODULE KEEPS
---------------------------
1. A carrier failure is never turned into STOP / DNC / an opt-out, and never
   written as a line type. It opens (or adds to) a HOLD on this family's
   texts, with the provider's code, status, message SID and time, and the
   policy version that decided it (app/services/sms_recovery_policy.py).
2. While a hold is open no automated text goes to the family by any path:
   the cadence runner skips the touch (recorded, nothing spent) and
   send_sms refuses ("SMS HOLD:") as a backstop. The failed text is never
   resent. Email is a separate channel and is not touched here.
3. Higher gates always win: STOP/DNC, consent, suppression, provider holds,
   campaign switches, contact hours. Releasing a hold clears none of them -
   the next text still passes every one of them.
4. A hold ends only by: a person releasing it after recording evidence the
   phone can take texts; Twilio itself later reporting the SAME message
   delivered; a temporary hold's time running out; or an opt-out (moot).
   There is no bulk release and no release that sends at once.
5. Every step is an event row; a re-delivered callback or a second worker
   is a no-op (dedupe keys, one open item per family).
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.sms_recovery_models import (
    DISP_DELIVERED_AFTER_FAILURE, DISP_OPTED_OUT, DISP_RELEASED, DISP_TEMPORARY_EXPIRED, HELD_STATES,
    REVIEW_OPEN, REVIEW_REQUESTED, REVIEW_VERIFIED_CAPABLE, REVIEW_VERIFIED_NOT_CAPABLE, SMS_ACTIVE,
    SMS_REVIEW_REQUIRED, SMS_TEMPORARY_HOLD, SMS_VERIFIED_UNREACHABLE, SmsRecoveryEvent, SmsRecoveryItem)
from app.services import sms_recovery_policy as P

log = logging.getLogger(__name__)

HOLD_TAG = "SMS HOLD:"
FAILED_STATUSES = ("undelivered", "failed")
HOLD_RECHECK = timedelta(hours=24)        # a held touch is looked at again a day later
RELEASE_SETTLE = timedelta(hours=2)       # a release never sends at once
OPTED_OUT_LEAD_STATUSES = ("dnc", "opted_out")


def _now() -> datetime:
    return datetime.utcnow()


def _naive(t):
    return t.replace(tzinfo=None) if t is not None and getattr(t, "tzinfo", None) is not None else t


# ── events ──────────────────────────────────────────────────────────────────

def _event(db: Session, org_id: str, lead_id: str, kind: str, *, item=None, message=None, code=None,
           actor=None, detail=None, dedupe=None) -> Optional[SmsRecoveryEvent]:
    """One history row. With `dedupe`, the second identical event is refused
    by the unique index (inside a savepoint) and None comes back."""
    row = SmsRecoveryEvent(organization_id=org_id, lead_id=lead_id, kind=kind,
                           item_id=getattr(item, "id", None),
                           message_id=getattr(message, "id", None),
                           provider_sid=getattr(message, "twilio_sid", None),
                           error_code=code, actor_user_id=actor,
                           detail=json.dumps(detail, default=str) if detail else None,
                           dedupe_key=dedupe)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        return None


def open_item(db: Session, lead_id: str) -> Optional[SmsRecoveryItem]:
    return db.query(SmsRecoveryItem).filter(SmsRecoveryItem.open_lead_id == lead_id).first()


def _lead(db: Session, lead_id: str):
    from app.models.models import Lead
    return db.query(Lead).filter(Lead.id == lead_id).first()


def opted_out(db: Session, lead) -> bool:
    """The existing authoritative STOP / DNC / consent / suppression records."""
    if (getattr(lead, "status", None) or "").lower() in OPTED_OUT_LEAD_STATUSES:
        return True
    if getattr(lead, "allow_sms", None) is False:
        return True
    try:
        from app.services.compliance_service import is_phone_suppressed
        if lead.phone and is_phone_suppressed(db, lead.organization_id, lead.phone):
            return True
    except Exception:                                    # noqa: BLE001
        log.exception("suppression lookup failed for lead %s", lead.id)
    return False


def _close(db: Session, item: SmsRecoveryItem, disposition: str, *, actor=None, detail=None) -> None:
    item.state, item.disposition = SMS_ACTIVE, disposition
    item.open_lead_id, item.closed_at = None, _now()
    _event(db, item.organization_id, item.lead_id, "closed", item=item, actor=actor,
           detail=dict(detail or {}, disposition=disposition))


# ── failures and receipts ───────────────────────────────────────────────────

def record_failure(db: Session, msg, *, source: str) -> Optional[SmsRecoveryItem]:
    """A failed SMS receipt. Idempotent per message: the same failure seen by
    the callback, the cadence runner and the backfill counts once. Returns
    the open item, if this family's texts are (now) held."""
    code = str(msg.error_code or "").strip() or None
    lead = _lead(db, msg.lead_id)
    if lead is None or not lead.organization_id:
        return None
    org = lead.organization_id
    version, rules = P.effective(db, org)
    rule = P.rule_for(rules, code)
    first = _event(db, org, lead.id, "failure", message=msg, code=code,
                   detail={"status": msg.delivery_status, "action": rule["action"], "policy": version,
                           "source": source, "at": msg.sent_at},
                   dedupe="failure:%s" % msg.id)
    item = open_item(db, lead.id)
    if first is None:                       # already recorded: nothing new to do
        return item
    if item is not None:
        item.failures = (item.failures or 0) + 1
        first.item_id = item.id
        return item
    if rule["action"] not in P.HOLD_ACTIONS or opted_out(db, lead):
        return None                         # a STOP/DNC needs no hold; others keep their schedule
    return _open_hold(db, lead, msg, code, rule, version, source)


def _open_hold(db: Session, lead, msg, code, rule, version, source) -> SmsRecoveryItem:
    temp = rule["action"] == P.TEMPORARY_HOLD
    item = SmsRecoveryItem(organization_id=lead.organization_id, lead_id=lead.id, open_lead_id=lead.id,
                           state=SMS_TEMPORARY_HOLD if temp else SMS_REVIEW_REQUIRED,
                           review_status=REVIEW_OPEN, error_code=code,
                           error_message=(msg.error_message or "")[:500] or None,
                           provider_status=msg.delivery_status, trigger_message_id=msg.id,
                           provider_sid=msg.twilio_sid, failed_at=_naive(msg.delivery_status_at or msg.sent_at),
                           policy_version=version, policy_action=rule["action"], opened_by=source,
                           hold_until=(_now() + timedelta(hours=rule["hours"])) if temp else None)
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError:                  # another worker opened it a moment ago
        item = open_item(db, lead.id)
        if item is not None:
            item.failures = (item.failures or 0) + 1
        return item
    _event(db, item.organization_id, lead.id, "opened", item=item, message=msg, code=code,
           detail={"state": item.state, "policy": version, "action": rule["action"], "source": source,
                   "label": rule.get("label")})
    db.query(SmsRecoveryEvent).filter(SmsRecoveryEvent.dedupe_key == "failure:%s" % msg.id) \
        .update({"item_id": item.id}, synchronize_session=False)
    return item


def record_delivered(db: Session, msg) -> None:
    """Twilio reported this message delivered. If it is the very message that
    opened a hold (a failed receipt followed by a delivered one), the
    provider has contradicted itself in the family's favour: the hold closes,
    recorded as such. A later, different delivered message is only evidence."""
    item = (db.query(SmsRecoveryItem)
            .filter(SmsRecoveryItem.trigger_message_id == msg.id, SmsRecoveryItem.open_lead_id.isnot(None)).first())
    if item is not None:
        _close(db, item, DISP_DELIVERED_AFTER_FAILURE, detail={"message_id": msg.id})
        return
    item = open_item(db, msg.lead_id)
    if item is not None:
        _event(db, item.organization_id, item.lead_id, "delivered_evidence", item=item, message=msg,
               dedupe="delivered:%s" % msg.id)


def on_receipt(db: Session, msg, status: Optional[str]) -> None:
    """Called by the Twilio status callback after it has stored the receipt."""
    st = (status or "").lower()
    if st in FAILED_STATUSES and msg.error_code:
        record_failure(db, msg, source="callback")
    elif st == "delivered":
        record_delivered(db, msg)


def latest_sms(db: Session, lead_id: str):
    from app.models.models import Message
    return (db.query(Message).filter(Message.lead_id == lead_id)
            .order_by(Message.sent_at.desc()).first())


def ensure_hold_from_history(db: Session, lead) -> Optional[SmsRecoveryItem]:
    """The cadence runner's backstop: if the family's LATEST text failed with
    a hold code and no item covers it yet (the callback was missed, or it
    predates #482), record it now. Returns the open HELD item, if any."""
    item = active_hold(db, lead)
    if item is not None:
        return item
    msg = latest_sms(db, lead.id)
    if msg is None or (msg.delivery_status or "").lower() not in FAILED_STATUSES or not msg.error_code:
        return None
    record_failure(db, msg, source="cadence")
    return active_hold(db, lead)


def active_hold(db: Session, lead) -> Optional[SmsRecoveryItem]:
    """The open item that holds this family's texts right now, or None. A
    temporary hold whose time is up closes here (it does not send - the next
    text keeps its normal day)."""
    item = open_item(db, lead.id)
    if item is None or item.state not in HELD_STATES:
        return None
    if item.state == SMS_TEMPORARY_HOLD and item.hold_until is not None and item.hold_until <= _now():
        _close(db, item, DISP_TEMPORARY_EXPIRED)
        return None
    if opted_out(db, lead):
        _close(db, item, DISP_OPTED_OUT, detail={"note": "STOP / DNC on record - texts stay stopped by it"})
        return None
    return item


def hold_text(item: SmsRecoveryItem) -> str:
    what = {SMS_REVIEW_REQUIRED: "held for review", SMS_TEMPORARY_HOLD: "on a temporary hold",
            SMS_VERIFIED_UNREACHABLE: "stopped - a person verified this phone cannot take texts"}[item.state]
    return "%s texts to this number are %s after carrier error %s (not DNC)" % (
        HOLD_TAG, what, item.error_code or "unknown")


def send_refusal(db: Session, lead, send_source: Optional[str] = None) -> Optional[str]:
    """For send_sms: the reason this family must not be texted, or None.
    A family who has texted US since the hold opened can be answered - their
    own message is the evidence the phone works for that conversation."""
    item = active_hold(db, lead)
    if item is None:
        return None
    from app.models.models import Reply
    replied = (db.query(Reply.id).filter(Reply.lead_id == lead.id, Reply.received_at >= item.opened_at,
                                         Reply.source == "sms").first())
    return None if replied else hold_text(item)


def note_prevented(db: Session, item: SmsRecoveryItem, touch_number: int) -> None:
    """A scheduled text the hold kept from going - counted once per touch."""
    if _event(db, item.organization_id, item.lead_id, "skip", item=item,
              detail={"touch": touch_number}, dedupe="skip:%s:%s" % (item.id, touch_number)) is not None:
        item.prevented = (item.prevented or 0) + 1


# ── email fallback (Phase C) ────────────────────────────────────────────────

def email_fallback(db: Session, lead) -> Dict:
    """Can this family's ALREADY-PERMITTED email sequence carry on? It is its
    own schedule (5 emails, Email 1 never twice); this only says whether it
    may, and why not. Nothing here sends."""
    from app.models.models import EmailMessage
    out = {"eligible": False, "reason": None, "emails_sent": 0, "remaining": None, "provider": None}
    addr = (lead.email or "").strip()
    try:
        from app.services.programs import email_touches as ET, identity
        prog = identity.program_for_org(db, lead.organization_id)
        out["provider"] = ET.email_provider(addr)
    except Exception:                                    # noqa: BLE001
        prog, ET = None, None
    reason = None
    if not addr or "@" not in addr:
        reason = "no email address"
    elif lead.allow_email is False:
        reason = "email opt-out of record"
    elif (lead.status or "").lower() in OPTED_OUT_LEAD_STATUSES:
        reason = "do not contact"
    elif (getattr(lead, "manual_flag", None) or "") in ("bad_email", "remove_all"):
        reason = "email address flagged as bad (bounced or marked)"
    elif db.query(EmailMessage.id).filter(EmailMessage.lead_id == lead.id,
                                          EmailMessage.status == "bounced").first():
        reason = "an email to this address bounced"
    elif prog is not None and ET is not None and ET.provider_hold_reason(prog, addr):
        reason = ET.provider_hold_reason(prog, addr)
    if prog is not None and ET is not None:
        from app.models.program_models import ProgramEmailTouch
        sent = db.query(ProgramEmailTouch.id).filter(ProgramEmailTouch.lead_id == lead.id,
                                                     ProgramEmailTouch.status == "sent").count()
        out["emails_sent"], out["remaining"] = sent, max(0, ET.max_touches() - sent)
        if reason is None and out["remaining"] == 0:
            reason = "all %d emails already sent" % ET.max_touches()
    out["reason"], out["eligible"] = reason, reason is None
    return out


# ── operator actions (all audited by the router) ────────────────────────────

class ActionRefused(ValueError):
    pass


def _need_open(item) -> None:
    if item is None or item.open_lead_id is None:
        raise ActionRefused("This hold is already closed.")


def request_review(db: Session, item, user_id: str, note: str) -> None:
    _need_open(item)
    item.review_status = REVIEW_REQUESTED
    _event(db, item.organization_id, item.lead_id, "review_requested", item=item, actor=user_id,
           detail={"note": note})


def mark_capable(db: Session, item, user_id: str, evidence: str) -> None:
    """A person recorded independent evidence the phone CAN take texts (the
    family confirmed it, a line check says mobile, ...). Makes the hold
    releasable; sends nothing and releases nothing by itself."""
    _need_open(item)
    if not (evidence or "").strip():
        raise ActionRefused("Say what shows this phone can take texts.")
    if item.state == SMS_VERIFIED_UNREACHABLE:
        item.state = SMS_REVIEW_REQUIRED
    item.review_status = REVIEW_VERIFIED_CAPABLE
    item.reviewed_by_user_id, item.reviewed_at, item.evidence = user_id, _now(), evidence.strip()[:2000]
    _event(db, item.organization_id, item.lead_id, "verified_capable", item=item, actor=user_id,
           detail={"evidence": item.evidence})


def mark_not_capable(db: Session, item, user_id: str, evidence: str) -> None:
    """Independent evidence the phone can NOT take texts. Texts stay stopped
    (sms_verified_unreachable). Not DNC; email is unaffected."""
    _need_open(item)
    if not (evidence or "").strip():
        raise ActionRefused("Say what shows this phone cannot take texts.")
    item.state, item.review_status = SMS_VERIFIED_UNREACHABLE, REVIEW_VERIFIED_NOT_CAPABLE
    item.reviewed_by_user_id, item.reviewed_at, item.evidence = user_id, _now(), evidence.strip()[:2000]
    _event(db, item.organization_id, item.lead_id, "verified_not_capable", item=item, actor=user_id,
           detail={"evidence": item.evidence})


def release(db: Session, item, user_id: str, note: str = "") -> Dict:
    """hold -> reviewed -> eligible. Only after `mark_capable`. The family's
    NEXT scheduled text becomes eligible on its normal day, never sooner than
    RELEASE_SETTLE from now; the failed text is not resent, and every other
    gate (STOP/DNC, consent, hours, campaign switch, spacing) still applies."""
    _need_open(item)
    if item.review_status != REVIEW_VERIFIED_CAPABLE:
        raise ActionRefused("Record evidence that this phone can take texts before releasing it.")
    lead = _lead(db, item.lead_id)
    if lead is None or opted_out(db, lead):
        raise ActionRefused("This family opted out or is do-not-contact - texts stay stopped.")
    from app.models.models import CadenceState
    st = db.query(CadenceState).filter(CadenceState.lead_id == item.lead_id).first()
    next_at = None
    if st is not None and st.status == "active":
        floor = _now() + RELEASE_SETTLE
        if st.next_touch_due_at is None or _naive(st.next_touch_due_at) < floor:
            st.next_touch_due_at = floor
        next_at = st.next_touch_due_at
    _close(db, item, DISP_RELEASED, actor=user_id, detail={"note": note, "next_text_not_before": next_at})
    return {"next_text_not_before": next_at.isoformat() + "Z" if next_at else None,
            "text_schedule": st.status if st is not None else None}


def route_email(db: Session, item, user_id: str) -> Dict:
    """Confirm the family's own email sequence carries on while texts are
    held. Refused (with the reason) when email is not eligible - then the
    item stays in review with that reason. Email 1 is never sent again."""
    _need_open(item)
    lead = _lead(db, item.lead_id)
    fb = email_fallback(db, lead)
    if not fb["eligible"]:
        _event(db, item.organization_id, item.lead_id, "no_eligible_channel", item=item, actor=user_id,
               detail=fb)
        raise ActionRefused("Email can't carry on: %s. The family stays in review; nothing is sent." % fb["reason"])
    item.email_fallback = "routed"
    _event(db, item.organization_id, item.lead_id, "email_fallback", item=item, actor=user_id, detail=fb)
    return fb


# ── one-time pickup of failures recorded before #482 (SCI pilot) ────────────

def backfill(db: Session, organization_id: str) -> Dict:
    """Give every family whose LATEST text failed with a hold code an item,
    and turn the old terminal stop (`stopped_bad_number`) into a held, still
    reviewable schedule. Idempotent (per-message dedupe, one open item per
    family). Sends nothing; releases nothing; never touches an opt-out."""
    from sqlalchemy import func
    from app.models.models import CadenceState, Lead, Message
    version, rules = P.effective(db, organization_id)
    hold_codes = [c for c, r in rules.items() if r["action"] in P.HOLD_ACTIONS]
    out = {"opened": 0, "converted": 0, "already": 0}
    if not hold_codes:
        return out
    leads = [lid for (lid,) in db.query(Message.lead_id).join(Lead, Lead.id == Message.lead_id)
             .filter(Lead.organization_id == organization_id, Message.error_code.in_(hold_codes),
                     func.lower(Message.delivery_status).in_(FAILED_STATUSES)).distinct().all()]
    for lid in leads:
        lead = _lead(db, lid)
        if lead is None or opted_out(db, lead):
            continue
        before = open_item(db, lid)
        item = ensure_hold_from_history(db, lead)
        if item is None:
            continue
        if before is None:
            out["opened"] += 1
        else:
            out["already"] += 1
        st = db.query(CadenceState).filter(CadenceState.lead_id == lid).first()
        if st is not None and st.status == "stopped_bad_number":
            st.status, st.completed_at = "active", None
            st.next_touch_due_at = _now() + HOLD_RECHECK
            _event(db, organization_id, lid, "converted", item=item,
                   detail={"from": "stopped_bad_number", "to": "active, texts held for review"},
                   dedupe="converted:%s" % item.id)
            out["converted"] += 1
    db.commit()
    if out["opened"] or out["converted"]:
        log.warning("sms recovery backfill org=%s: %s", organization_id, out)
    return out


def backfill_for_sci(db: Session) -> Optional[Dict]:
    from app.services import sms_programs
    org = sms_programs.sci_org_id(db)
    return backfill(db, org) if org else None
