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


def add_suppression_entry_from_reply(db: Session, organization_id: str, phone: str, reason: str) -> SuppressionEntry:
    """
    Adds a number to the suppression list with source=REPLY_STOP,
    distinguishing it from numbers an admin added manually via the
    Compliance Center. Idempotent - if the number is already
    suppressed (e.g. an admin already added it manually, or they
    replied STOP twice), returns the existing entry rather than
    erroring or creating a duplicate.
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
        source=SuppressionSource.REPLY_STOP,
    )
    db.add(entry)
    db.commit()
    return entry
