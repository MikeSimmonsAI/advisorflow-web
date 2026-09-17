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


def usable_us_phone(phone) -> str | None:
    """The stored form of a US number, or None if it is not one.

    THIS REPLACES AN IMPORT OF A ROUTER'S VALIDATOR.

    `is_phone_suppressed` used to call `compliance_router.normalize_phone`,
    which raises `HTTPException(422)` on anything that is not a ten-digit US
    number. That is the right behaviour for a request body and the wrong
    behaviour everywhere else this service is called from - the SMS reply
    webhook, the send paths, and the cadence cron, which walks a whole book in
    one loop and catches ValueError. One imported row with a seven-digit phone
    would have thrown an HTTP exception out of a cron and ended the entire
    run, leaving every later lead untouched with no record of why.

    So the service answers in its own terms: a string, or None. The router
    keeps its 422 for the request it validates.
    """
    if not phone:
        return None
    from app.services.dedup_service import normalize_phone as _shared
    try:
        normalized = _shared(phone)
    except Exception:                                        # noqa: BLE001
        return None
    if len(normalized) != 11 or not normalized.startswith("1"):
        return None
    return normalized


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
    normalized = usable_us_phone(phone)
    if normalized is None:
        # NOT a judgement that the number is clear - a statement that it
        # cannot be ON this list, which holds normalized eleven-digit numbers
        # and nothing else. The preflight below refuses such a number on its
        # own terms, with a reason that names the real problem.
        return False
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

    # CAPACITY HOLD BLOCKS EVERYTHING, exactly like DNC and for the same
    # structural reason: it lives on the lead, not on a phone number, so it
    # stops email to a family whose hold arrived by web form just as it stops
    # SMS. A held lead is a prospect the customer has not paid to work yet;
    # spending their Twilio and Resend budget on it would turn a plan ceiling
    # into a bill.
    from app.services import lead_capacity
    if lead_capacity.is_held(lead):
        raise ValueError(
            f"Lead {lead.id} is held over plan capacity - blocked from sending "
            f"on any channel until capacity is available or the plan is upgraded."
        )

    if (channel or CHANNEL_SMS).strip().lower() == CHANNEL_EMAIL:
        _check_email_permission(lead)
        return None

    # Phone channels. An independent guard, never a substitute for the DNC
    # check above: a number can sit in the Compliance Center's list while its
    # matching Lead.status was never updated. A lead with no phone has nothing
    # to check here and passes rather than erroring.
    phone = getattr(lead, "phone", None)
    if phone and usable_us_phone(phone) is None:
        # A NUMBER WE CANNOT READ IS NOT A NUMBER WE MAY TEXT.
        #
        # It cannot be matched against the suppression list, so we cannot say
        # this family has not opted out; and it cannot be dialled, so handing
        # it to the provider buys a billable error instead of a message.
        raise ValueError(
            f"Lead {lead.id}'s phone number is not a usable US number, so it "
            f"cannot be checked against the suppression list - blocked."
        )
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
