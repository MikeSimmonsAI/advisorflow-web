"""
Import Intelligence Models
--------------------------
ImportBatch  — one batch per upload/source session. State machine drives
               the workflow: UPLOADING → PROCESSING → READY_FOR_REVIEW →
               REVIEWING → READY_TO_COMMIT → COMMITTING → COMMITTED.

ImportStagedRow — one row per contact parsed from the source. Never
                  directly creates a live Lead; that happens only at
                  commit time, from explicitly reviewed rows.

These models are imported in main.py for side effects so Base.metadata
registers their tables at startup.

ISOLATION RULE: Nothing in this module creates or modifies a live Lead.
The word "Lead" appears only in FK references for post-commit provenance
(committed_lead_id, merged_into_lead_id, matched_lead_id).
"""

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Index, Integer, String, Text,
)
from sqlalchemy import func
from sqlalchemy.orm import relationship

from app.deps import Base
from app.models.models import gen_uuid


# ─────────────────────────────────────────────────────────────────────────────
# Enumerations  (stored as plain strings — no PG ENUM type, stays portable)
# ─────────────────────────────────────────────────────────────────────────────

class ImportBatchStatus:
    """State machine values for ImportBatch.status."""
    UPLOADING             = "uploading"
    PROCESSING            = "processing"
    READY_FOR_REVIEW      = "ready_for_review"
    REVIEWING             = "reviewing"
    READY_TO_COMMIT       = "ready_to_commit"
    COMMITTING            = "committing"
    COMMITTED             = "committed"
    PARTIALLY_COMMITTED   = "partially_committed"
    FAILED                = "failed"
    ARCHIVED              = "archived"

    ALL = (
        UPLOADING, PROCESSING, READY_FOR_REVIEW, REVIEWING,
        READY_TO_COMMIT, COMMITTING, COMMITTED, PARTIALLY_COMMITTED,
        FAILED, ARCHIVED,
    )
    COMMITTABLE = (READY_TO_COMMIT, REVIEWING)
    TERMINAL    = (COMMITTED, PARTIALLY_COMMITTED, FAILED)


class ImportSourceType:
    EXCEL           = "excel"
    CSV             = "csv"
    GOOGLE_CONTACTS = "google_contacts"
    API             = "api"


class ImportDuplicateStatus:
    NEW                    = "new"
    MATCHED_EXISTING       = "matched_existing"
    POSSIBLE_DUPLICATE     = "possible_duplicate"
    WITHIN_BATCH_DUPLICATE = "within_batch_duplicate"
    DNC_BLOCKED            = "dnc_blocked"


class ImportRowReviewStatus:
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    MERGED    = "merged"
    REJECTED  = "rejected"
    COMMITTED = "committed"


class ImportValidationStatus:
    VALID   = "valid"
    WARNING = "warning"
    INVALID = "invalid"


class ImportMatchConfidence:
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"
    NONE   = "none"


# ─────────────────────────────────────────────────────────────────────────────
# ImportBatch
# ─────────────────────────────────────────────────────────────────────────────

class ImportBatch(Base):
    """One per upload session. Holds aggregate state and stats."""
    __tablename__ = "import_batches"

    id               = Column(String, primary_key=True, default=gen_uuid)
    organization_id  = Column(String, ForeignKey("organizations.id"), nullable=False)
    imported_by_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    imported_by_name    = Column(String, nullable=True)

    source_type       = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)
    display_name      = Column(String, nullable=True)
    source_category   = Column(String, nullable=True)
    campaign_purpose  = Column(String, nullable=True)
    offer_hook        = Column(String, nullable=True)
    relationship_type = Column(String, default="cold_lead", nullable=True)
    source_year       = Column(Integer, nullable=True)
    force_new_inquiry = Column(Boolean, default=False)

    status = Column(String, default=ImportBatchStatus.UPLOADING, nullable=False)

    total_rows              = Column(Integer, default=0)
    valid_rows              = Column(Integer, default=0)
    invalid_rows            = Column(Integer, default=0)
    new_rows                = Column(Integer, default=0)
    matched_rows            = Column(Integer, default=0)
    possible_duplicate_rows = Column(Integer, default=0)
    rejected_rows           = Column(Integer, default=0)
    pending_review_rows     = Column(Integer, default=0)
    committed_rows          = Column(Integer, default=0)
    merged_rows             = Column(Integer, default=0)

    error_message         = Column(Text, nullable=True)
    committed_at          = Column(DateTime, nullable=True)
    committed_by_user_id  = Column(String, ForeignKey("users.id"), nullable=True)
    committed_by_name     = Column(String, nullable=True)
    archived_at           = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    staged_rows = relationship(
        "ImportStagedRow",
        back_populates="import_batch",
        passive_deletes=True,
        lazy="dynamic",
    )

    __table_args__ = (
        Index("ix_import_batches_org_status", "organization_id", "status"),
        Index("ix_import_batches_org_created", "organization_id", "created_at"),
        Index("ix_import_batches_importer", "imported_by_user_id"),
    )

    def recount(self, db):
        """Refresh all row-count columns from ImportStagedRow table."""
        from sqlalchemy import func as _func
        from app.models.import_models import ImportStagedRow as _ISR

        counts = (
            db.query(
                _ISR.duplicate_status, _ISR.review_status,
                _ISR.validation_status, _func.count(_ISR.id).label("n"),
            )
            .filter(_ISR.import_batch_id == self.id)
            .group_by(_ISR.duplicate_status, _ISR.review_status, _ISR.validation_status)
            .all()
        )
        self.total_rows = db.query(_func.count(_ISR.id)).filter(_ISR.import_batch_id == self.id).scalar() or 0
        self.valid_rows = sum(c.n for c in counts if c.validation_status == ImportValidationStatus.VALID)
        self.invalid_rows = sum(c.n for c in counts if c.validation_status == ImportValidationStatus.INVALID)
        self.new_rows = sum(c.n for c in counts if c.duplicate_status == ImportDuplicateStatus.NEW)
        self.matched_rows = sum(c.n for c in counts if c.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING)
        self.possible_duplicate_rows = sum(c.n for c in counts if c.duplicate_status == ImportDuplicateStatus.POSSIBLE_DUPLICATE)
        self.rejected_rows = sum(c.n for c in counts if c.review_status == ImportRowReviewStatus.REJECTED)
        self.committed_rows = sum(c.n for c in counts if c.review_status == ImportRowReviewStatus.COMMITTED and c.duplicate_status == ImportDuplicateStatus.NEW)
        self.merged_rows = sum(c.n for c in counts if c.review_status == ImportRowReviewStatus.COMMITTED and c.duplicate_status in (ImportDuplicateStatus.MATCHED_EXISTING, ImportDuplicateStatus.POSSIBLE_DUPLICATE))
        self.pending_review_rows = sum(c.n for c in counts if c.review_status == ImportRowReviewStatus.PENDING)


# ─────────────────────────────────────────────────────────────────────────────
# ImportStagedRow
# ─────────────────────────────────────────────────────────────────────────────

class ImportStagedRow(Base):
    """One row per contact parsed from the source file. Never creates a live Lead
    until commit. DNC blocks are authoritative and cannot be overridden."""
    __tablename__ = "import_staged_rows"

    id              = Column(String, primary_key=True, default=gen_uuid)
    import_batch_id = Column(String, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    row_number      = Column(Integer, nullable=False)

    raw_data          = Column(Text, nullable=True)    # JSON: original col→val map

    first_name        = Column(String, nullable=True)
    last_name         = Column(String, nullable=True)
    phone_raw         = Column(String, nullable=True)
    phone_normalized  = Column(String, nullable=True)
    email_raw         = Column(String, nullable=True)
    email_normalized  = Column(String, nullable=True)

    tier_raw          = Column(String, nullable=True)
    tier_inferred     = Column(String, nullable=True)
    tier_override     = Column(String, nullable=True)
    relationship_type = Column(String, nullable=True)
    contact_channel   = Column(String, nullable=True)
    message_track     = Column(String, nullable=True)
    source_category   = Column(String, nullable=True)

    street_address = Column(String, nullable=True)
    city           = Column(String, nullable=True)
    state          = Column(String, nullable=True)
    zip_code       = Column(String, nullable=True)

    last_action_raw       = Column(String, nullable=True)
    last_contact_date_raw = Column(String, nullable=True)
    status_reason_raw     = Column(String, nullable=True)
    allow_calls_raw       = Column(String, nullable=True)

    extra_fields = Column(Text, nullable=True)   # JSON: unmapped columns

    validation_status = Column(String, default=ImportValidationStatus.VALID, nullable=False)
    validation_errors = Column(Text, nullable=True)   # JSON list of error strings

    duplicate_status          = Column(String, default=ImportDuplicateStatus.NEW, nullable=False)
    match_confidence          = Column(String, nullable=True)
    matched_lead_id           = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    duplicate_of_staged_row_id = Column(String, ForeignKey("import_staged_rows.id", ondelete="SET NULL"), nullable=True)
    match_reason              = Column(String, nullable=True)

    review_status    = Column(String, default=ImportRowReviewStatus.PENDING, nullable=False)
    review_action    = Column(String, nullable=True)
    review_note      = Column(Text, nullable=True)
    rejection_reason = Column(String, nullable=True)
    reviewed_by_id   = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at      = Column(DateTime, nullable=True)

    intended_assignment_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    committed_lead_id   = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    merged_into_lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    committed_at        = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    import_batch            = relationship("ImportBatch", back_populates="staged_rows")
    matched_lead            = relationship("Lead", foreign_keys=[matched_lead_id])
    committed_lead          = relationship("Lead", foreign_keys=[committed_lead_id])
    merged_into_lead        = relationship("Lead", foreign_keys=[merged_into_lead_id])
    duplicate_of_staged_row = relationship(
        "ImportStagedRow",
        foreign_keys=[duplicate_of_staged_row_id],
        remote_side="ImportStagedRow.id",
    )

    __table_args__ = (
        Index("ix_isr_batch_id",           "import_batch_id"),
        Index("ix_isr_org_id",             "organization_id"),
        Index("ix_isr_batch_review_status","import_batch_id", "review_status"),
        Index("ix_isr_batch_phone",        "import_batch_id", "phone_normalized"),
        Index("ix_isr_committed_lead",     "committed_lead_id"),
        Index("ix_isr_merged_lead",        "merged_into_lead_id"),
        Index("ix_isr_matched_lead",       "matched_lead_id"),
    )

    @property
    def effective_tier(self) -> str:
        return self.tier_override or self.tier_inferred or "partial"
