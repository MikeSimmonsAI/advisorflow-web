"""
Dedup Service
Implements the org-wide "no double contact" rule:
  - Dedup key = normalized phone number + normalized last name
  - When advisor A uploads a lead that matches an existing ContactRegistry
    entry, it's flagged as a duplicate. It stays VISIBLE to advisor A (so
    they know it exists) but marked is_duplicate=True and is excluded from
    the SMS send queue, since someone (possibly advisor B) already owns
    that relationship.
  - This means we never have to host one giant shared lead database -
    each advisor's Excel upload only adds the registry footprint
    (phone + last name + which lead/user first claimed it), not full PII,
    so storage stays light.

Phone normalization: strips everything to E.164-ish digits (assumes US
numbers padded to 10 digits, prefixed with country code 1 if missing).
Last name normalization: lowercase, strip whitespace, strip punctuation.
"""

import re
from sqlalchemy.orm import Session
from app.models.models import ContactRegistry, Lead

# Used by scripts/seed_registry_from_sent_log.py to register historical
# phone numbers (from the old desktop ADB pipeline's sent log) that have
# no recoverable last name. check_and_register() below treats a match
# against this placeholder as a duplicate, but ONLY for this exact
# placeholder value - never as a general phone-only rule - to preserve
# the household-sharing case (one phone, multiple real people) Mike
# specifically flagged.
PLACEHOLDER_LAST_NAME = "__unknown__"


def normalize_phone(raw_phone: str) -> str:
    """Strip to digits only, ensure 11-digit US format (1 + 10 digits)."""
    if not raw_phone:
        return ""
    digits = re.sub(r"\D", "", raw_phone)
    if len(digits) == 10:
        digits = "1" + digits
    return digits


def normalize_last_name(raw_name: str) -> str:
    """Lowercase, strip whitespace and punctuation for stable matching."""
    if not raw_name:
        return ""
    cleaned = re.sub(r"[^a-zA-Z]", "", raw_name)
    return cleaned.lower().strip()


def normalize_first_name(raw_name: str) -> str:
    """
    Same normalization as normalize_last_name, but kept as a distinct
    function (not just calling normalize_last_name with a different
    argument name) since first-name matching is used differently - see
    admin_router.py's potential_duplicate_leads, where it's a required
    CORROBORATING signal alongside last name, not a standalone match key.
    A first name on its own (like a last name on its own) is too common
    to mean anything by itself.
    """
    if not raw_name:
        return ""
    cleaned = re.sub(r"[^a-zA-Z]", "", raw_name)
    return cleaned.lower().strip()


def normalize_email(raw_email: str) -> str:
    """Lowercase and strip whitespace for stable email matching."""
    if not raw_email:
        return ""
    return raw_email.strip().lower()


def check_and_register(
    db: Session,
    organization_id: str,
    phone_raw: str,
    last_name_raw: str,
    lead_id: str,
    user_id: str,
):
    """
    Checks the ContactRegistry for an existing entry matching this
    phone+lastname combo within the org. If found -> returns
    (is_duplicate=True, existing_entry). If not found -> creates a new
    registry entry and returns (is_duplicate=False, new_entry).

    Also checks for a PHONE-ONLY match (ignoring last name) as a fallback -
    this catches historical entries seeded from the old desktop pipeline's
    sent log (see scripts/seed_registry_from_sent_log.py), which only have
    a phone number and a placeholder last name since the raw SMS outbox
    doesn't carry contact metadata. Without this fallback, a new import
    with the correct real last name would NOT match the placeholder entry
    and the dedup protection would silently fail for every historical
    number - this was caught and fixed via testing, not assumed safe.

    Call this once per imported lead row during the upload/ingestion flow.
    """
    norm_phone = normalize_phone(phone_raw)
    norm_last = normalize_last_name(last_name_raw)

    if not norm_phone or not norm_last:
        # Can't dedup without both fields - let it through but don't register
        return False, None

    exact_match = (
        db.query(ContactRegistry)
        .filter(
            ContactRegistry.organization_id == organization_id,
            ContactRegistry.normalized_phone == norm_phone,
            ContactRegistry.normalized_last_name == norm_last,
        )
        .first()
    )
    if exact_match:
        return True, exact_match

    # Fallback: phone-only match against PLACEHOLDER entries only (i.e.
    # historical numbers seeded from the old desktop pipeline's sent log,
    # which only have a phone number, not a real last name - see
    # scripts/seed_registry_from_sent_log.py). This is intentionally
    # scoped to placeholder entries only, NOT a general phone-only dedup
    # rule, because Mike specifically noted a phone number can represent
    # two different real people in the same household (e.g. father and
    # son sharing a landline, same or different last name) - blocking on
    # phone alone for real leads would incorrectly merge those people.
    placeholder_match = (
        db.query(ContactRegistry)
        .filter(
            ContactRegistry.organization_id == organization_id,
            ContactRegistry.normalized_phone == norm_phone,
            ContactRegistry.normalized_last_name == PLACEHOLDER_LAST_NAME,
        )
        .first()
    )
    if placeholder_match:
        return True, placeholder_match

    new_entry = ContactRegistry(
        organization_id=organization_id,
        normalized_phone=norm_phone,
        normalized_last_name=norm_last,
        first_seen_lead_id=lead_id,
        owning_user_id=user_id,
    )
    db.add(new_entry)
    db.flush()  # get it persisted within the current transaction without full commit
    return False, new_entry


def bulk_dedup_check(db: Session, organization_id: str, rows: list[dict]) -> dict:
    """
    Used during Excel import preview - checks a batch of rows BEFORE
    committing any leads, so the advisor can see "X new, Y duplicates"
    before confirming the import.

    rows: list of dicts with keys 'phone' and 'last_name'
    Returns: {'new_count': int, 'duplicate_count': int, 'results': [...]}
    """
    results = []
    new_count = 0
    dup_count = 0

    seen_in_batch = set()  # catch duplicates WITHIN the same uploaded file too

    for row in rows:
        norm_phone = normalize_phone(row.get("phone", ""))
        norm_last = normalize_last_name(row.get("last_name", ""))
        key = (norm_phone, norm_last)

        if not norm_phone or not norm_last:
            results.append({**row, "dedup_status": "missing_fields"})
            continue

        if key in seen_in_batch:
            results.append({**row, "dedup_status": "duplicate_in_file"})
            dup_count += 1
            continue

        existing = (
            db.query(ContactRegistry)
            .filter(
                ContactRegistry.organization_id == organization_id,
                ContactRegistry.normalized_phone == norm_phone,
                ContactRegistry.normalized_last_name == norm_last,
            )
            .first()
        )

        if existing:
            results.append({**row, "dedup_status": "duplicate_existing"})
            dup_count += 1
        else:
            results.append({**row, "dedup_status": "new"})
            new_count += 1
            seen_in_batch.add(key)

    return {
        "new_count": new_count,
        "duplicate_count": dup_count,
        "total": len(rows),
        "results": results,
    }
