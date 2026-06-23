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
from app.models.models import SuppressionEntry, SuppressionSource
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


def add_suppression_entry_from_reply(
    db: Session,
    organization_id: str,
    phone: str,
    reason: str,
    source: SuppressionSource = SuppressionSource.REPLY_STOP,
) -> SuppressionEntry:
    """
    Adds a number to the suppression list, distinguishing WHO/WHAT
    flagged it via the source parameter (defaults to REPLY_STOP, the
    original caller - the automatic webhook keyword/AI detection path in
    sms_router.py). leads_router.py's manual quick-DNC action passes
    source=ADVISOR_FLAGGED instead.

    Idempotent - if the number is already suppressed (e.g. an admin
    already added it manually, or they replied STOP twice), returns the
    EXISTING entry rather than erroring or creating a duplicate.
    Specifically does NOT overwrite an existing entry's source on a
    repeat call - if it was already suppressed for one reason, a second
    flagging attempt shouldn't silently rewrite the original
    attribution; the existing record stands as the source of truth for
    "who/what suppressed this first."
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
