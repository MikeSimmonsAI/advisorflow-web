"""
Shared compliance logic, extracted out of compliance_router.py so it
can be called from the SMS reply webhook (sms_router.py) too, not just
admin-initiated requests. Connects the reply-based STOP keyword
detection to the Compliance Center's suppression list - these were two
separate, unconnected systems until this was added: a lead could be
marked DNC from a reply while the org-wide suppression list stayed
completely unaware of it.
"""

from sqlalchemy.orm import Session
from app.models.models import Lead, SuppressionEntry, SuppressionSource
from app.routers.compliance_router import normalize_phone


def is_phone_suppressed(db: Session, organization_id: str, phone: str) -> bool:
    """
    THE REAL ENFORCEMENT CHECK that was missing entirely. Confirmed by
    testing: a number could sit in the suppression list while its
    matching Lead.status never got updated to DNC (especially likely
    given the phone-format bug this module also fixes), and the SMS
    send path only ever checked Lead.status - never the suppression
    list itself. This function is the single source of truth every
    send path must check directly, not as a substitute for the
    Lead.status check but as an additional, independent guard.
    """
    if not phone:
        return False
    normalized = normalize_phone(phone)
    return (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.organization_id == organization_id, SuppressionEntry.phone == normalized)
        .first()
        is not None
    )


CHANNEL_SMS = "sms"
CHANNEL_EMAIL = "email"


def check_compliance_preflight(db: Session, lead: Lead,
                               channel: str = CHANNEL_SMS) -> None:
    """
    Pre-send compliance gate. Returns None when the lead is clear to contact
    on `channel`, and raises ValueError naming the reason otherwise.

    THIS CONSOLIDATES RULES THAT ALREADY EXIST. IT INVENTS NONE.

    ONE rule is channel-agnostic and one is not, and conflating them is the
    mistake this signature exists to prevent:

      DNC blocks EVERYTHING. Lead.status == 'dnc' is a person asking not to
      be contacted. It lives on the lead, not on a phone number, so a STOP
      received by text stops email to the same family too, and an email-only
      lead with no phone to suppress is still blocked by it.

      THE SUPPRESSION LIST IS A PHONE LIST. suppression_entries has exactly
      one contact column - `phone` - and one uniqueness rule,
      (organization_id, phone). There is no email suppression list to
      consult, so a suppressed number says nothing whatsoever about whether
      the family may be emailed. Treating it as an email prohibition would
      invent a cross-channel rule the business never made, and would
      silently stop mail that is permitted today.

    Email is therefore checked against the permission fields the platform
    actually keeps for email, the same three app/services/qualification.py
    already excludes on:

      * Lead.allow_email is False - an explicit opt-out of record, imported
        from the source system. Only False blocks; NULL means the source
        never said, which is the state most rows are in and is not a denial.
      * manual_flag == 'bad_email' - an address an advisor marked unusable.
        Mailing it costs a hard bounce against the sending domain.
      * no address at all.

    SMS keeps exactly the two checks sms_service.send_sms already performs
    inline before every text - Lead.status and the suppression list - and
    deliberately adds nothing to them. Calling this before send_sms is a
    cheap double-check that cannot diverge from it, never a replacement.

    WIRING NOTE: adding this call to a send path changes what that path
    blocks. app/routers/auto_send_router.py calls it on both send paths.
    email_service.send_email_to_lead still checks neither DNC nor
    allow_email; routing it through here is a deliberate decision for a
    separate batch, not a side effect of this function existing.
    """
    status = getattr(lead, "status", None)
    # LeadStatus is a str enum, so a plain string column value and the enum
    # member compare equal; normalise anyway so neither form slips through.
    status_value = getattr(status, "value", status)
    if status_value == "dnc":
        raise ValueError(
            f"Lead {lead.id} is marked DNC - blocked from sending on any channel."
        )

    if (channel or CHANNEL_SMS).strip().lower() == CHANNEL_EMAIL:
        _check_email_permission(lead)
        return None

    # Phone channels. An independent guard, never a substitute for the DNC
    # check above: a number can sit in the Compliance Center's list while its
    # matching Lead.status was never updated. A lead with no phone has nothing
    # to check here and passes rather than erroring.
    phone = getattr(lead, "phone", None)
    if phone and is_phone_suppressed(db, lead.organization_id, phone):
        raise ValueError(
            f"Lead {lead.id}'s phone number is on the suppression list - "
            f"blocked from sending."
        )

    return None


def _check_email_permission(lead: Lead) -> None:
    """The email half of the preflight. Raises ValueError, or returns None."""
    if not getattr(lead, "email", None):
        raise ValueError(f"Lead {lead.id} has no email address.")

    # Only an explicit False. NULL means the source system never stated a
    # preference, which is not the same as a denial and must not be read as
    # one - most imported rows are NULL.
    if getattr(lead, "allow_email", None) is False:
        raise ValueError(
            f"Lead {lead.id} has opted out of email - blocked from sending."
        )

    if (getattr(lead, "manual_flag", None) or "") == "bad_email":
        detail = getattr(lead, "manual_flag_reason", None) or ""
        # A FLAGGED ADDRESS IS AS GOOD AS NO ADDRESS. Sending there costs a
        # hard bounce against the domain's sending reputation, and every
        # bounce makes the deliverability of the REAL families' mail worse.
        raise ValueError(
            f"{lead.first_name or 'This lead'} is flagged with an unusable "
            f"email address ({lead.email}) - blocked from sending. Correct "
            f"the address before emailing. {detail}".strip()
        )


def add_suppression_entry(
    db: Session,
    organization_id: str,
    phone: str,
    reason: str,
    source: SuppressionSource = SuppressionSource.MANUAL,
) -> SuppressionEntry:
    """
    THE one write path into the suppression authority, whatever channel the
    opt-out arrived on.

    Every provider funnels here on purpose. The failure this prevents is a
    provider keeping its own list: someone replies STOP to a Twilio text, and
    the Retell voice agent rings them the next morning because voice consulted
    a different source of truth. There is one table, one uniqueness rule
    (organization_id, phone), and `source` records only WHERE the opt-out came
    from — never WHO gets to honour it. All of them do.

    Idempotent: an existing entry is returned untouched rather than duplicated
    or overwritten, so the earliest opt-out keeps its original reason and
    provenance. Re-suppressing is a no-op, which is what makes it safe to call
    from a webhook that may be delivered more than once.
    """
    normalized = normalize_phone(phone)
    existing = (
        db.query(SuppressionEntry)
        .filter(SuppressionEntry.organization_id == organization_id, SuppressionEntry.phone == normalized)
        .first()
    )
    if existing:
        return existing

    entry = SuppressionEntry(
        organization_id=organization_id,
        phone=normalized,
        reason=reason,
        source=source,
    )
    db.add(entry)
    db.commit()
    return entry


def add_suppression_entry_from_reply(db: Session, organization_id: str, phone: str, reason: str) -> SuppressionEntry:
    """
    SMS STOP-keyword opt-out. Unchanged behaviour, unchanged signature, and
    still the function sms_router calls — it now delegates so there is exactly
    one implementation to keep correct.
    """
    return add_suppression_entry(
        db, organization_id, phone, reason, source=SuppressionSource.REPLY_STOP
    )
