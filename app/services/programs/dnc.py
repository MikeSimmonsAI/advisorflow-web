"""Put a family on Do Not Contact by hand (Mike, Oct 25).

Does everything a texted STOP does, from one place, so a person marking a
family is honoured by every path - cadence texts, campaign emails, the AI,
follow-ups, bulk sends and voice:

  * the lead is DNC and email is off (lead.status / allow_email - every send
    path refuses on these);
  * the phone goes on the organization's suppression list (the one table every
    channel checks), source MANUAL, with who did it and why;
  * the cadence ends (STOPPED_DNC), whether it was active or paused;
  * their open replies close, the AI conversation stops, nothing held goes out;
  * it is audited.

Idempotent: marking an already-DNC family again changes nothing.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def mark_do_not_contact(db: Session, lead, user, reason: Optional[str] = None) -> Dict:
    from app.models.models import CadenceState, CadenceStatus, SuppressionSource
    from app.models.program_models import ProgramConversation, ProgramConversationTurn, ProgramResponse
    from app.services.compliance_service import add_suppression_entry, usable_us_phone
    who = getattr(user, "full_name", None) or getattr(user, "email", None) or "a person"
    why = (reason or "").strip()[:200]
    note = "Marked Do Not Contact by %s%s" % (who, (": " + why) if why else "")
    before = {"status": lead.status, "allow_email": lead.allow_email}
    now = datetime.utcnow()
    out = {"lead_id": lead.id, "already": (lead.status or "").lower() == "dnc", "phone_suppressed": False}

    lead.status = "dnc"
    lead.allow_email = False
    # Cadence: end it, paused or not (same as a texted STOP).
    try:
        from app.services.cadence_service import stop_cadence_for_lead
        stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)
    except Exception:                                    # noqa: BLE001
        log.exception("cadence stop failed for lead %s", lead.id)
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is not None and str(getattr(st.status, "value", st.status)) in ("active", "paused"):
        st.status = CadenceStatus.STOPPED_DNC.value
        st.completed_at = datetime.now(timezone.utc)
    # Replies: nothing left waiting on staff for a family we must not contact.
    for r in (db.query(ProgramResponse).filter(ProgramResponse.organization_id == lead.organization_id,
                                               ProgramResponse.lead_id == lead.id,
                                               ProgramResponse.handling_status != "closed").all()):
        r.handling_status, r.sla_due_at, r.closed_at = "closed", None, now
    # The AI: stopped, no follow-up, nothing held for later.
    conv = db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).first()
    if conv is not None:
        conv.state, conv.next_followup_at = "stopped", None
        conv.handoff_reason = note[:250]
        for t in (db.query(ProgramConversationTurn)
                  .filter(ProgramConversationTurn.conversation_id == conv.id,
                          ProgramConversationTurn.delivery.in_(("held", "draft"))).all()):
            t.delivery, t.reason = "dropped", "family marked Do Not Contact"
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, lead.organization_id, getattr(user, "id", None), action="sci.do_not_contact",
                   target_type="lead", target_id=lead.id, before=before,
                   after={"status": "dnc", "allow_email": False, "reason": why or None}, commit=False)
    except Exception:                                    # noqa: BLE001
        log.exception("audit failed for DNC on lead %s", lead.id)
    db.commit()
    # The phone: on the suppression list every channel checks (commits itself).
    if usable_us_phone(lead.phone):
        try:
            add_suppression_entry(db, lead.organization_id, lead.phone, note, source=SuppressionSource.MANUAL)
            out["phone_suppressed"] = True
        except Exception:                                # noqa: BLE001
            db.rollback()
            log.exception("suppression entry failed for lead %s", lead.id)
    return out


# ── taking a family OFF Do Not Contact (owner, Nov 24) ──────────────────────
#
# "If someone got marked Do Not Contact by accident, by the AI or by me, we need
# to be able to unmark it." Undoes everything a STOP or a manual mark sets, from
# one place, by a manager, with a reason, audited:
#
#   * lead.status back to what it was before (from the audit), else "contacted";
#     texts, email and calls allowed again;
#   * the phones off the suppression list (suppression_entries) and the phones
#     and email off the family-wide block list (contact_suppressions);
#   * the SMS consent record's opt-out cleared, where an opt-in exists;
#   * a reply the AI read as an opt-out becomes an ordinary reply;
#   * a schedule ended by the DNC is paused (a person restarts it) and a stopped
#     conversation goes to Human Handling - nothing is sent by this step.
#
# A family who really texted STOP is also blocked by the CARRIER until they text
# START; that cannot be lifted from here, and the result says so.

STOPPED_BY_DNC = ("stopped_dnc", "dnc", "stopped_opted_out", "opted_out")


def _phone_forms(raw):
    import re
    d = re.sub(r"\D", "", str(raw or ""))
    if len(d) == 10:
        d = "1" + d
    if len(d) != 11 or not d.startswith("1"):
        return set()
    return {d, "+" + d, d[1:]}


def clear_do_not_contact(db: Session, lead, user, reason: str) -> Dict:
    from app.models.models import CadenceState, ContactSuppression, Message, SuppressionEntry
    from app.models.program_models import ProgramConversation, ProgramResponse
    why = (reason or "").strip()[:200]
    if not why:
        raise ValueError("Say why this family is coming off Do Not Contact.")
    who = getattr(user, "full_name", None) or getattr(user, "email", None) or "a person"
    org = lead.organization_id
    before = {"status": lead.status, "allow_sms": lead.allow_sms, "allow_email": lead.allow_email,
              "allow_voice": lead.allow_voice}
    out = {"lead_id": lead.id, "was_dnc": (lead.status or "").lower() == "dnc", "cleared": []}

    # 1. the family's own status and channels
    prior = None
    try:
        from app.models.models import AuditLogEntry
        last = (db.query(AuditLogEntry).filter(AuditLogEntry.target_id == lead.id,
                                               AuditLogEntry.action == "sci.do_not_contact")
                .order_by(AuditLogEntry.created_at.desc()).first())
        raw = getattr(last, "before_state", None)
        if raw:
            import json
            b = json.loads(raw) if isinstance(raw, str) else dict(raw)
            prior = (b.get("status") or "").strip() or None
    except Exception:                                    # noqa: BLE001 - fall back below
        prior = None
    if (lead.status or "").lower() == "dnc":
        lead.status = prior if prior and prior.lower() != "dnc" else "contacted"
        out["cleared"].append("status")
    for f in ("allow_sms", "allow_email", "allow_voice"):
        if getattr(lead, f, None) is False:
            setattr(lead, f, True)
            out["cleared"].append(f)

    # 2. the block lists, for every phone and the email of this family
    phones = set()
    for f in ("phone", "mobile_phone", "secondary_phone", "callback_phone"):
        phones |= _phone_forms(getattr(lead, f, None))
    if phones:
        n = (db.query(SuppressionEntry).filter(SuppressionEntry.organization_id == org,
                                               SuppressionEntry.phone.in_(phones))
             .delete(synchronize_session=False))
        if n:
            out["cleared"].append("suppression list (%d)" % n)
        n = (db.query(ContactSuppression).filter(ContactSuppression.organization_id == org,
                                                 ContactSuppression.kind == "phone",
                                                 ContactSuppression.value.in_(phones))
             .delete(synchronize_session=False))
        if n:
            out["cleared"].append("family phone block (%d)" % n)
    email = (lead.email or "").strip().lower()
    if email:
        n = (db.query(ContactSuppression).filter(ContactSuppression.organization_id == org,
                                                 ContactSuppression.kind == "email",
                                                 ContactSuppression.value == email)
             .delete(synchronize_session=False))
        if n:
            out["cleared"].append("email block (%d)" % n)

    # 3. the texting consent's opt-out (only where they had opted in)
    try:
        from app.models.sms_consent_models import SmsConsentRecord
        e164 = {p for p in phones if p.startswith("+")}
        if e164:
            for r in (db.query(SmsConsentRecord).filter(SmsConsentRecord.organization_id == org,
                                                        SmsConsentRecord.phone_normalized.in_(e164)).all()):
                if (r.status != "opted_in" or r.opted_out_at) and r.consent_given:
                    r.status, r.opted_out_at = "opted_in", None
                    out["cleared"].append("texting opt-out")
    except Exception:                                    # noqa: BLE001
        log.exception("consent restore failed for lead %s", lead.id)

    # 4. a reply read as an opt-out is an ordinary reply again
    texted_stop = False
    for r in (db.query(ProgramResponse).filter(ProgramResponse.organization_id == org,
                                               ProgramResponse.lead_id == lead.id,
                                               ProgramResponse.response_class == "opt_out").all()):
        texted_stop = texted_stop or (r.channel or "sms") == "sms"
        r.response_class = "neutral"
        out["cleared"].append("reply marked opt-out")

    # 5. schedule paused (a person restarts it), conversation to a person
    st = db.query(CadenceState).filter(CadenceState.lead_id == lead.id).first()
    if st is not None and str(getattr(st.status, "value", st.status) or "").lower() in STOPPED_BY_DNC:
        st.status, st.completed_at = "paused", None
        out["cleared"].append("text schedule (now paused)")
    conv = db.query(ProgramConversation).filter(ProgramConversation.lead_id == lead.id).first()
    if conv is not None and conv.state == "stopped":
        conv.state = "human"
        conv.handoff_reason = ("Taken off Do Not Contact by %s: %s" % (who, why))[:250]
        out["cleared"].append("conversation (now Human Handling)")

    # The carrier keeps its own STOP block until the family texts START.
    carrier = texted_stop or bool(db.query(Message.id).filter(Message.lead_id == lead.id,
                                                             Message.error_code == "21610").first())
    out["carrier_stop_possible"] = carrier
    try:
        from app.routers.audit_log_router import log_action
        log_action(db, org, getattr(user, "id", None), action="sci.clear_do_not_contact",
                   target_type="lead", target_id=lead.id, before=before,
                   after={"status": lead.status, "reason": why, "cleared": out["cleared"]}, commit=False)
    except Exception:                                    # noqa: BLE001
        log.exception("audit failed for clearing DNC on lead %s", lead.id)
    db.commit()
    out["status"] = lead.status
    return out
