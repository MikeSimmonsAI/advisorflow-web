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
