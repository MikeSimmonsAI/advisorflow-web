"""THE COMPLIANCE GATE FOR AUTOMATED (AI) VOICE CALLS - bulk campaigns included.

The bulk campaign path in app/routers/voice_router.py used to filter on
`Lead.status != "dnc"` and nothing else: a number on the organization's
suppression list, a lead whose record says "no calls", a test record, a lead
held over plan capacity, the EvoSense voice kill switch and a conversation a
person had taken over were all dialled anyway. This module is the one answer
every AI-call path asks, built from the platform's existing authorities -
never a copy of them:

    compliance_service.check_compliance_preflight   DNC (any channel), test
                                                    record, capacity hold,
                                                    unusable number, suppression
    DNC by NUMBER                                   another record in this org
                                                    with the same number is DNC
    Lead.manual_flag == "remove_all"                no outreach on any channel
    Lead.allow_voice is False                       the record says: no calls
    communication_eligibility.voice (sellers)       a Wholesale seller needs a
                                                    permission basis (they
                                                    contacted us / allowed calls)
    EvoSenseControl.paused_all / paused_voice       organization kill switches
    wholesale_ops.ai_send_refusal                   Pause AI / human takeover

It only ever REFUSES. It places no call and has no override parameter.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def org_voice_paused(db, org_id: str) -> Optional[str]:
    """The organization-level switch, read without creating a row. An error
    reading it is treated as paused (fail closed)."""
    try:
        from app.models.evosense_models import EvoSenseControl
        ctl = db.query(EvoSenseControl).filter(EvoSenseControl.organization_id == org_id).first()
    except Exception:                                       # noqa: BLE001
        log.exception("voice_bulk_gate: kill-switch lookup failed for %s", org_id)
        return "Voice outreach state could not be read - treated as paused."
    if ctl is not None and (ctl.paused_all or ctl.paused_voice):
        return "Voice outreach is paused for this organization."
    return None


def _dnc_by_number(db, org_id: str, phone: str) -> bool:
    from app.models.models import Lead
    from app.services import wholesale_sms
    e164 = wholesale_sms.normalize_e164(phone)
    if not e164:
        return False
    forms = wholesale_sms._phone_forms(e164)
    return (db.query(Lead.id).filter(Lead.organization_id == org_id, Lead.status == "dnc",
                                     Lead.phone.in_(forms)).first() is not None)


def call_refusal(db, lead, org_id: str, *, check_org_pause: bool = True) -> Optional[str]:
    """None when an AI call to this lead is permitted by compliance, else the
    reason in words a person can act on."""
    if lead is None:
        return "Lead not found."
    if lead.organization_id != org_id:
        return "Lead belongs to another organization."
    if not lead.phone:
        return "Lead has no phone number."
    if check_org_pause:
        paused = org_voice_paused(db, org_id)
        if paused:
            return paused
    from app.services.compliance_service import check_compliance_preflight
    try:
        check_compliance_preflight(db, lead, channel="voice")
    except ValueError as exc:
        return str(exc)
    if _dnc_by_number(db, org_id, lead.phone):
        return "A record with this number is Do Not Contact."
    if (getattr(lead, "manual_flag", None) or "") == "remove_all":
        return "Manually flagged: no outreach on any channel."
    if getattr(lead, "allow_voice", None) is False:
        return "The record says: no calls."
    from app.services import wholesale_ops
    held = wholesale_ops.ai_send_refusal(db, lead, "voice_ai")
    if held:
        return ("A person has taken over this conversation - the AI does not call."
                if held == "HUMAN_TAKEOVER" else "AI is paused for this conversation.")
    from app.services import wholesale_sms
    if wholesale_sms.is_program_lead(db, lead):
        from app.services import communication_eligibility as CE
        verdict = CE.voice(db, org_id, lead.phone, lead=lead)
        if not verdict.get("permitted"):
            return verdict.get("reason") or "No permission to call this seller."
    return None
