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
