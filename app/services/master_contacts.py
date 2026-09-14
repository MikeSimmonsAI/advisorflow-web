"""THE MASTER LEAD DATABASE WRITER.

`record_lead(db, lead)` is the only thing that writes `master_contacts` and
`lead_occurrences`. Ingestion calls it after the tenant's own Lead row exists;
the backfill calls the same function over history. One writer, one set of
rules, one place to fix a matching bug.

===========================================================================
THE THREE PROMISES THIS FILE HAS TO KEEP
===========================================================================

1. IT CANNOT BREAK LEAD CREATION. A customer importing fifty thousand people
   must not lose one of them because the platform's own bookkeeping had a bad
   day. Every call runs inside a SAVEPOINT and swallows its own failures: on
   error the savepoint rolls back, the caller's transaction survives intact,
   and the lead is still created. What the platform loses is knowledge, which
   the backfill can recover; what it must never lose is the customer's data.

   The savepoint is not belt-and-braces, it is load-bearing. A failed INSERT
   on Postgres aborts the whole transaction, so a plain try/except here would
   catch the exception and still leave the caller unable to commit the lead it
   just made. Rolling back to a savepoint is what actually contains the damage.

2. IT IS IDEMPOTENT. `UniqueConstraint(organization_id, lead_id)` on
   LeadOccurrence means one tenant lead yields one occurrence no matter how
   often ingestion or the backfill runs over it. Re-recording refreshes
   last-seen and status; it never double-counts.

3. IT NEVER TOUCHES THE TENANT'S RECORD. Nothing here writes to `leads`,
   sends anything, enqueues anything or deletes anything. It reads a Lead and
   writes two platform-owned rows.

===========================================================================
MATCHING, V1 — AND THE MERGE THAT DOES NOT HAPPEN
===========================================================================

Exact match on normalized email. Exact match on normalized valid phone.
Nothing else: no name matching, no fuzzy scoring, no address heuristics.

When the email matches one master person and the phone matches a DIFFERENT
one, V1 does not merge them. It attaches the occurrence to the email match
and sets `needs_review` on both, with a reason naming the other id. Two
people wrongly fused share a history that cannot be cleanly separated
afterwards; two people left unmerged are simply visible, and a human can
merge them in ten seconds when Stage 2 gives them the button.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from app.models.master_contact_models import LeadOccurrence, MasterContact
from app.models.models import Lead, Organization
from app.services.dedup_service import normalize_phone

logger = logging.getLogger(__name__)


def normalize_email(raw: str) -> str:
    """The platform's email normalizer, imported at CALL time on purpose.

    `source_reconciliation` imports `_check_email_quality` from
    `import_service`, and `import_service` imports this module — so importing
    it at the top of this file closes an import cycle and every module in the
    ring fails to load. Deferring the import to first use breaks the ring
    without forking the normalizer, which is what actually matters: two
    different definitions of "the same address" is how one human becomes two
    master people.
    """
    from app.services.source_reconciliation import normalize_email as _impl
    return _impl(raw)

# Which organization sits on which platform. Read once per organization per
# process: a fifty-thousand-row import would otherwise make fifty thousand
# identical primary-key lookups to answer a question whose answer changes
# perhaps once in an organization's lifetime. Bounded so a platform with many
# tenants cannot grow it without limit.
_PLATFORM_CACHE: dict[str, Optional[str]] = {}
_PLATFORM_CACHE_MAX = 2000


def _platform_for_org(db: Session, organization_id: str) -> Optional[str]:
    if organization_id in _PLATFORM_CACHE:
        return _PLATFORM_CACHE[organization_id]
    try:
        org = db.query(Organization).filter(Organization.id == organization_id).first()
    except Exception:
        return None
    value = getattr(org, "platform_id", None) if org else None
    if len(_PLATFORM_CACHE) >= _PLATFORM_CACHE_MAX:
        _PLATFORM_CACHE.clear()
    _PLATFORM_CACHE[organization_id] = value
    return value


def reset_platform_cache() -> None:
    """Tests and long-lived workers; production never needs to call this."""
    _PLATFORM_CACHE.clear()


# ---------------------------------------------------------------------------
# What counts as a real person
# ---------------------------------------------------------------------------

def _looks_synthetic(db: Session, lead: Lead, explicit: Optional[bool]) -> tuple[bool, Optional[str]]:
    """Demo data is data. It is simply not evidence about a human being.

    Flagged rather than dropped: a record that quietly never arrives is a
    record nobody can audit later. The God browser hides these by default and
    shows them on request.
    """
    if explicit is not None:
        return bool(explicit), ("caller" if explicit else None)

    if getattr(lead, "is_test", False):
        return True, "lead.is_test"

    org_id = getattr(lead, "organization_id", None)
    if org_id:
        try:
            org = db.query(Organization).filter(Organization.id == org_id).first()
            if org is not None and getattr(org, "is_demo", False):
                return True, "organization.is_demo"
        except Exception:
            pass

    email = (getattr(lead, "email", "") or "").lower()
    # Reserved-by-RFC test domains. Anything addressed here is, by definition,
    # incapable of belonging to a real person.
    if email.endswith((".invalid", ".test", ".example", "@example.com")):
        return True, "reserved test domain"

    return False, None


# ---------------------------------------------------------------------------
# The identity keys
# ---------------------------------------------------------------------------

def identity_keys(lead: Lead) -> tuple[Optional[str], Optional[str]]:
    """The normalized email and phone this lead should be matched on.

    Empty string never becomes a key. `normalize_phone` returns "" for a
    number it cannot make sense of and `normalize_email` returns "" for an
    address it considers unusable; storing that would make every unmatchable
    lead match every other unmatchable lead, which is the single worst thing
    a deduplicating system can do. Unusable means NULL means matches nothing.
    """
    email = normalize_email(getattr(lead, "email", None) or "") or None
    raw_phone = getattr(lead, "phone", None) or getattr(lead, "phone_raw", None) or ""
    phone = normalize_phone(raw_phone) or None
    return email, phone


# ---------------------------------------------------------------------------
# The writer
# ---------------------------------------------------------------------------

def record_lead(
    db: Session,
    lead: Lead,
    *,
    source: Optional[str] = None,
    source_detail: Optional[str] = None,
    import_batch_id: Optional[str] = None,
    platform_id: Optional[str] = None,
    synthetic: Optional[bool] = None,
    ingestion_path: Optional[str] = None,
) -> Optional[LeadOccurrence]:
    """Retain `lead` in the platform master database. Never raises.

    Returns the occurrence, or None when nothing could be recorded — which is
    a fact worth logging and never a reason to fail the caller.

    The lead must already have an id, so callers flush before calling. A lead
    with no id has not been written yet and there is nothing to point at.
    """
    if lead is None:
        return None

    lead_id = getattr(lead, "id", None)
    org_id = getattr(lead, "organization_id", None)
    if not lead_id or not org_id:
        return None

    try:
        with db.begin_nested():
            return _record(
                db, lead, lead_id, org_id,
                source=source,
                source_detail=source_detail,
                import_batch_id=import_batch_id,
                platform_id=platform_id,
                synthetic=synthetic,
            )
    except Exception:
        # Deliberately swallowed. See promise 1 at the top of this file.
        logger.exception(
            "master_contacts: could not record lead %s (org %s, path %s)",
            lead_id, org_id, ingestion_path or "unspecified",
        )
        return None


def _record(
    db: Session,
    lead: Lead,
    lead_id: str,
    org_id: str,
    *,
    source: Optional[str],
    source_detail: Optional[str],
    import_batch_id: Optional[str],
    platform_id: Optional[str],
    synthetic: Optional[bool],
) -> Optional[LeadOccurrence]:
    now = datetime.utcnow()
    is_synthetic, synthetic_reason = _looks_synthetic(db, lead, synthetic)

    src = source or getattr(lead, "source", None)
    src_detail = source_detail or getattr(lead, "source_detail", None)
    status = getattr(lead, "status", None)

    # ── already known? ──────────────────────────────────────────────────
    # The idempotence path. Refresh what can legitimately change and return
    # without touching occurrence_count: this is the same appearance we have
    # already counted, seen again.
    existing = (
        db.query(LeadOccurrence)
        .filter(LeadOccurrence.organization_id == org_id,
                LeadOccurrence.lead_id == lead_id)
        .first()
    )
    if existing is not None:
        existing.last_seen_at = now
        if status:
            existing.tenant_lead_status = status
        if src and not existing.source:
            existing.source = src
        if src_detail and not existing.source_detail:
            existing.source_detail = src_detail
        if import_batch_id and not existing.import_batch_id:
            existing.import_batch_id = import_batch_id
        contact = (
            db.query(MasterContact)
            .filter(MasterContact.id == existing.master_contact_id)
            .first()
        )
        if contact is not None:
            contact.last_seen_at = now
            # A person who reappears through a real tenant is a real person,
            # even if we first met them in a demo org.
            if contact.is_synthetic and not is_synthetic:
                contact.is_synthetic = False
                contact.synthetic_reason = None
        db.flush()
        return existing

    # ── who is this? ────────────────────────────────────────────────────
    email, phone = identity_keys(lead)
    contact = _resolve_contact(db, lead, email, phone, is_synthetic,
                               synthetic_reason, now)
    if contact is None:
        return None

    occurrence = LeadOccurrence(
        master_contact_id=contact.id,
        platform_id=platform_id or _platform_for_org(db, org_id),
        organization_id=org_id,
        lead_id=lead_id,
        source=src,
        source_detail=src_detail,
        import_batch_id=import_batch_id,
        first_seen_at=now,
        last_seen_at=now,
        tenant_lead_status=status,
        is_synthetic=is_synthetic,
    )
    db.add(occurrence)
    db.flush()
    return occurrence


def _resolve_contact(
    db: Session,
    lead: Lead,
    email: Optional[str],
    phone: Optional[str],
    is_synthetic: bool,
    synthetic_reason: Optional[str],
    now: datetime,
) -> Optional[MasterContact]:
    """Find this human, or decide they are new. The V1 rules, and only those."""

    by_email = (
        db.query(MasterContact).filter(MasterContact.normalized_email == email).first()
        if email else None
    )
    by_phone = (
        db.query(MasterContact).filter(MasterContact.normalized_phone == phone).first()
        if phone else None
    )

    conflict_note = None
    if by_email is not None and by_phone is not None and by_email.id != by_phone.id:
        # THE CASE THAT MUST NOT BECOME A MERGE.
        contact = by_email
        conflict_note = (
            f"email {email} matches {by_email.id}; phone {phone} matches "
            f"{by_phone.id}. Not merged by V1 matching."
        )
        _flag(by_email, conflict_note, now)
        _flag(by_phone, conflict_note, now)
    else:
        contact = by_email or by_phone

    if contact is None:
        contact = MasterContact(
            normalized_email=email,
            normalized_phone=phone,
            display_first_name=getattr(lead, "first_name", None),
            display_last_name=getattr(lead, "last_name", None),
            first_seen_at=now,
            last_seen_at=now,
            occurrence_count=1,
            is_synthetic=is_synthetic,
            synthetic_reason=synthetic_reason,
        )
        db.add(contact)
        db.flush()
        return contact

    # Known person, seen again.
    contact.occurrence_count = (contact.occurrence_count or 0) + 1
    contact.last_seen_at = now
    if contact.is_synthetic and not is_synthetic:
        contact.is_synthetic = False
        contact.synthetic_reason = None
    if not contact.display_first_name and getattr(lead, "first_name", None):
        contact.display_first_name = lead.first_name
    if not contact.display_last_name and getattr(lead, "last_name", None):
        contact.display_last_name = lead.last_name

    # Learn the key we did not have. Only ever fills a NULL, and only with a
    # value no other contact already holds — `by_email`/`by_phone` being None
    # above is exactly that proof. Overwriting a key would rewrite somebody's
    # identity from a single new row, which is not something one row earns.
    if email and not contact.normalized_email and by_email is None and conflict_note is None:
        contact.normalized_email = email
    if phone and not contact.normalized_phone and by_phone is None and conflict_note is None:
        contact.normalized_phone = phone

    db.flush()
    return contact


def _flag(contact: MasterContact, reason: str, now: datetime) -> None:
    contact.needs_review = True
    stamped = f"[{now.isoformat(timespec='seconds')}] {reason}"
    if contact.review_reason:
        if reason in contact.review_reason:
            return
        contact.review_reason = (contact.review_reason + "\n" + stamped)[:4000]
    else:
        contact.review_reason = stamped


def record_leads(
    db: Session,
    leads: Iterable[Lead],
    *,
    source: Optional[str] = None,
    source_detail: Optional[str] = None,
    import_batch_id: Optional[str] = None,
    synthetic: Optional[bool] = None,
    ingestion_path: Optional[str] = None,
) -> int:
    """Record many leads. Returns how many occurrences were written or refreshed.

    Each lead gets its own savepoint, so one unrecordable row out of fifty
    thousand costs exactly that one row.
    """
    recorded = 0
    platform_id = None
    for lead in leads:
        if lead is None:
            continue
        if platform_id is None and getattr(lead, "organization_id", None):
            platform_id = _platform_for_org(db, lead.organization_id)
        if record_lead(
            db, lead,
            source=source,
            source_detail=source_detail,
            import_batch_id=import_batch_id,
            platform_id=platform_id,
            synthetic=synthetic,
            ingestion_path=ingestion_path,
        ) is not None:
            recorded += 1
    return recorded
