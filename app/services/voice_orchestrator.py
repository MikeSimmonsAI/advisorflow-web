"""
Voice orchestration — EvoSys decides, the provider executes.

    lead
      -> eligibility + suppression   (EvoSys, channel-agnostic)
      -> resolve org voice config    (organization -> provider -> agent -> number)
      -> create VoiceCall row        (before the vendor call, so a crash is visible)
      -> provider.start_call()
      -> persist provider_call_id
      -> lifecycle events arrive by webhook

WHY THE ROW IS WRITTEN FIRST. If we called the vendor first and then died, a
real call would be in flight with nothing in our database to attach its
webhooks to — an untraceable call to a real person. Writing first means the
worst case is a row marked `failed`, which is recoverable and visible.

WHY ELIGIBILITY LIVES HERE AND NOT IN THE PROVIDER. There is one suppression
authority for every channel. If voice had its own copy of the rules, the exact
failure this architecture exists to prevent — Twilio honouring a STOP while
Retell keeps dialling — would be one refactor away. Providers never decide who
may be contacted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.models import Lead, Organization, User, VoiceCall
from app.services.comms import active_voice_config, get_voice_provider
from app.services.comms.base import VoiceCallRequest

log = logging.getLogger(__name__)

USE_CASE_FILE_CHECK = "file_check"
MAX_CALL_ATTEMPTS = 3


@dataclass
class Eligibility:
    ok: bool
    reason: Optional[str] = None
    code: Optional[str] = None


def check_call_eligibility(db: Session, lead: Lead, organization_id: str,
                           use_case: str = USE_CASE_FILE_CHECK) -> Eligibility:
    """Every reason EvoSys may refuse to place a call. Read-only.

    Ordered cheapest-and-most-absolute first. Each check exists because it
    protects a real person or a real boundary, not for symmetry.
    """
    if lead is None:
        return Eligibility(False, "Lead not found.", "no_lead")

    # Tenant boundary. A lead is only ever callable by its own organization.
    if lead.organization_id != organization_id:
        return Eligibility(False, "Lead belongs to another organization.",
                           "cross_org")

    if not lead.phone:
        return Eligibility(False, "Lead has no phone number.", "no_phone")

    # The lead-level flag.
    if (lead.status or "").lower() == "dnc":
        return Eligibility(False, "Lead is marked do-not-contact.", "lead_dnc")

    # THE ORG-WIDE SUPPRESSION AUTHORITY — the same list Twilio SMS consults.
    # This is the check the legacy Twilio voice path never made: a number could
    # sit on the suppression list while its Lead.status was never flipped, and
    # remain callable. Voice and SMS now read the same source of truth.
    from app.services.compliance_service import is_phone_suppressed
    if is_phone_suppressed(db, organization_id, lead.phone):
        return Eligibility(False, "Number is on the organization's suppression list.",
                           "suppressed")

    # Attempt cap, mirroring the existing voice convention.
    prior = (db.query(VoiceCall)
             .filter(VoiceCall.lead_id == lead.id)
             .count())
    if prior >= MAX_CALL_ATTEMPTS:
        return Eligibility(False,
                           "Maximum of %d call attempts already made." % MAX_CALL_ATTEMPTS,
                           "max_attempts")

    config = active_voice_config(db, organization_id, use_case)
    if config is None:
        return Eligibility(False,
                           "No active voice agent is configured for this organization.",
                           "no_config")

    provider = get_voice_provider(db, config)
    ready, why = provider.is_ready()
    if not ready:
        return Eligibility(False, why or "Voice provider is not configured.",
                           "provider_not_ready")

    return Eligibility(True)


def _dynamic_variables(lead: Lead, org: Organization,
                       advisor: Optional[User]) -> dict:
    """Only what the agent speaks with.

    Deliberately small. These values are sent to a third party and appear in
    that vendor's prompt and logs, so this carries a first name and business
    names — never an email, an address, an internal id, or anything about
    other leads.
    """
    out = {
        "first_name": (lead.first_name or "").strip() or "there",
        "organization_name": (getattr(org, "name", "") or "").strip(),
    }
    business = (getattr(org, "brand_name", None) or getattr(org, "name", "") or "")
    if business:
        out["business_name"] = business.strip()
    if advisor is not None and getattr(advisor, "full_name", None):
        out["advisor_name"] = advisor.full_name
    out["appointment_type"] = "File Check"
    return out


def start_file_check_call(db: Session, lead: Lead, organization_id: str,
                          advisor: Optional[User] = None,
                          campaign_id: Optional[str] = None,
                          use_case: str = USE_CASE_FILE_CHECK) -> VoiceCall:
    """Place one voice call. Returns the VoiceCall row in every outcome.

    Raises PermissionError when EvoSys refuses on eligibility — that is a
    decision, not a failure, and no row is written for a call that must never
    happen. A vendor failure DOES write a row, marked `failed`, because that
    attempt is a real event worth keeping.
    """
    elig = check_call_eligibility(db, lead, organization_id, use_case)
    if not elig.ok:
        raise PermissionError(elig.reason or "Call not permitted.")

    config = active_voice_config(db, organization_id, use_case)
    org = db.query(Organization).filter(
        Organization.id == organization_id).first()
    if advisor is None and lead.assigned_to_id:
        advisor = db.query(User).filter(User.id == lead.assigned_to_id).first()

    prior = db.query(VoiceCall).filter(VoiceCall.lead_id == lead.id).count()

    call = VoiceCall(
        lead_id=lead.id,
        advisor_id=(advisor.id if advisor is not None else lead.assigned_to_id),
        organization_id=organization_id,
        to_phone=lead.phone,
        from_phone=config.from_number,
        call_number=prior + 1,
        status="initiating",
        provider=config.provider,
        direction="outbound",
        agent_id=config.agent_id,
        campaign_id=campaign_id,
        created_at=datetime.utcnow(),
    )
    db.add(call)
    db.commit()
    db.refresh(call)

    provider = get_voice_provider(db, config)
    result = provider.start_call(VoiceCallRequest(
        to_number=lead.phone,
        from_number=config.from_number,
        agent_id=config.agent_id,
        # Read from the row alongside the agent id and the number. The version
        # is configuration, not a constant: changing which version an
        # organization runs must cost one column update, never a code change.
        agent_version=getattr(config, "agent_version", None),
        # Correlation only — re-derived, never trusted, when events return.
        metadata={
            "evosys_call_id": call.id,
            "lead_id": lead.id,
            "organization_id": organization_id,
            **({"campaign_id": campaign_id} if campaign_id else {}),
        },
        dynamic_variables=_dynamic_variables(lead, org, advisor),
    ))

    if not result.ok:
        call.status = "failed"
        call.outcome = "failed"
        call.error_message = ("%s: %s" % (result.error_code or "error",
                                          result.error_message or ""))[:480]
        call.ended_at = datetime.utcnow()
        db.commit()
        db.refresh(call)
        return call

    call.provider_call_id = result.provider_call_id
    call.status = "ringing"
    db.commit()
    db.refresh(call)
    return call
