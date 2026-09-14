"""THE ADVISORFLOW MASTER LEAD DATABASE — the platform's own record of people.

===========================================================================
WHAT THIS IS, AND WHAT IT IS NOT
===========================================================================

Every lead that enters any organization on any AdvisorFlow-powered platform
is ALSO retained here, with its origin. Atlantis imports fifty thousand
people: Atlantis keeps fifty thousand tenant-scoped operational leads, and
the platform keeps fifty thousand master people plus the fact that they were
first seen in Atlantis, from that import, on that date.

IT IS NOT A SECOND COPY OF THE TENANT'S CRM. No status pipeline, no
assignment, no messages, no consent decisions — those stay in `leads`, which
remains the only record a customer's own screens ever read. Nothing here is
reachable from a customer API. `ContactRegistry` is the per-organization
dedup ledger and is unchanged; this is the layer above it.

===========================================================================
TWO TABLES, AND WHY
===========================================================================

    MasterContact     one human
    LeadOccurrence    one appearance of that human in one organization

They are separate because the same person legitimately appears in several
customers' books — a funeral home's family who later becomes an energy
customer is one human and two relationships, and collapsing that into one
row would either lose an origin or invent a merge nobody asked for. The
occurrence carries the lineage; the contact carries the identity.

===========================================================================
MATCHING, V1
===========================================================================

Exact match on a normalized email, or exact match on a normalized valid
phone. Nothing fuzzy, no name matching, no probabilistic scoring.

WHEN EMAIL AND PHONE DISAGREE — the address matches one master person and
the number matches a different one — V1 DOES NOT MERGE. It attaches the
occurrence to the email match and flags both contacts for review. An
incorrect merge silently fuses two people's histories and is very hard to
undo; an unmerged pair is visible, safe, and reversible.
"""

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text, UniqueConstraint, func)

from app.models.models import Base, gen_uuid


class MasterContact(Base):
    """One human, as the platform knows them."""

    __tablename__ = "master_contacts"

    id = Column(String, primary_key=True, default=gen_uuid)

    # ── the identity keys ───────────────────────────────────────────────
    # Normalized forms ONLY, because a lookup that has to guess at casing or
    # punctuation is a lookup that will one day create a second person.
    # `normalize_email` lowercases and trims; `normalize_phone` returns the
    # 11-digit US form or "" when the number is unusable — and an unusable
    # number must never become a match key, so "" is stored as NULL.
    normalized_email = Column(String, nullable=True, index=True)
    normalized_phone = Column(String, nullable=True, index=True)

    # ── what we can say about them, best-effort ─────────────────────────
    # Display only. Never a match key: two people share a name far more often
    # than they share a mobile number.
    display_first_name = Column(String, nullable=True)
    display_last_name = Column(String, nullable=True)

    first_seen_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, server_default=func.now())
    # How many occurrences point at this contact. Denormalized on purpose:
    # the God browser sorts and filters on it, and counting rows across a
    # multi-million-row child table on every page load is not a query.
    occurrence_count = Column(Integer, nullable=False, default=1)

    # ── synthetic and QA ────────────────────────────────────────────────
    # Demo scenarios, proof environments and acceptance tests all create real
    # Lead rows, and every one of them would otherwise sit in the master
    # database looking like a person. Flagged at write time and hidden by
    # default in the browser, never deleted: a record that disappears is a
    # record nobody can audit.
    is_synthetic = Column(Boolean, nullable=False, default=False)
    synthetic_reason = Column(String, nullable=True)

    # ── the safe answer to a conflict ───────────────────────────────────
    # Set when this contact's email matched one master person and its phone
    # matched another. Nothing is merged; both are flagged and a human
    # decides later. See `master_contacts.record_lead`.
    needs_review = Column(Boolean, nullable=False, default=False)
    review_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # Partial-unique would be better on Postgres, but this table has to
        # create identically on SQLite for the test suite. Uniqueness is
        # enforced in `record_lead`, which is the only writer.
        Index("ix_master_contacts_email", "normalized_email"),
        Index("ix_master_contacts_phone", "normalized_phone"),
        Index("ix_master_contacts_synthetic", "is_synthetic"),
    )


class LeadOccurrence(Base):
    """One appearance of one human inside one organization."""

    __tablename__ = "lead_occurrences"

    id = Column(String, primary_key=True, default=gen_uuid)
    master_contact_id = Column(String, ForeignKey("master_contacts.id"),
                               nullable=False, index=True)

    # ── the lineage, which is the whole point ───────────────────────────
    platform_id = Column(String, nullable=True, index=True)
    organization_id = Column(String, nullable=False, index=True)
    # The tenant's own record. NOT a foreign key with a cascade: a customer
    # deleting a lead must not silently delete the platform's knowledge that
    # the person was once theirs. Stale ids are tolerated and resolved by
    # reading `leads` when a screen needs live detail.
    lead_id = Column(String, nullable=False)

    source = Column(String, nullable=True)
    source_detail = Column(String, nullable=True)
    import_batch_id = Column(String, nullable=True, index=True)

    first_seen_at = Column(DateTime, server_default=func.now())
    last_seen_at = Column(DateTime, server_default=func.now())

    # A snapshot, refreshed when the occurrence is re-recorded. Useful for
    # "how many of these did that customer ever work?" and explicitly NOT
    # authoritative — `leads.status` is.
    tenant_lead_status = Column(String, nullable=True)

    is_synthetic = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # IDEMPOTENCE, ENFORCED BY THE DATABASE. One tenant lead produces one
        # occurrence however many times ingestion or the backfill runs over
        # it. This constraint is what makes re-running the backfill safe.
        UniqueConstraint("organization_id", "lead_id",
                         name="uq_lead_occurrence_org_lead"),
        Index("ix_lead_occurrence_org", "organization_id"),
        Index("ix_lead_occurrence_master", "master_contact_id"),
    )
