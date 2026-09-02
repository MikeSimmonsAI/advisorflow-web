"""
Import Intelligence Models
--------------------------
ImportBatch  — one per upload/source session. State machine drives the
               workflow: UPLOADING → PROCESSING → READY_FOR_REVIEW →
               REVIEWING → READY_TO_COMMIT → COMMITTING → COMMITTED.

ImportStagedRow — one row per contact parsed from the source. Never
                  directly creates a live Lead; that happens only at
                  commit time, from explicitly reviewed rows.

Column naming convention: the router and services drove the names; the
model matches them exactly so there is one canonical name per field with
no translation layer.

ISOLATION RULE: Nothing in this module creates or modifies a live Lead.
The word "Lead" appears only in FK references for post-commit provenance
(committed_lead_id, merged_into_lead_id, matched_lead_id).
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import func
from sqlalchemy.orm import relationship

from app.models.models import Base, gen_uuid


# ──────────────────────────────────────────────────────────────────────────────
# Enumerations  (plain strings — no PG ENUM type, stays portable and safe to
# add values without a migration)
# ──────────────────────────────────────────────────────────────────────────────

class ImportBatchStatus:
    UPLOADING           = "uploading"
    PROCESSING          = "processing"
    READY_FOR_REVIEW    = "ready_for_review"
    REVIEWING           = "reviewing"
    READY_TO_COMMIT     = "ready_to_commit"
    COMMITTING          = "committing"
    COMMITTED           = "committed"
    PARTIALLY_COMMITTED = "partially_committed"
    FAILED              = "failed"
    ARCHIVED            = "archived"

    ALL = (
        UPLOADING, PROCESSING, READY_FOR_REVIEW, REVIEWING,
        READY_TO_COMMIT, COMMITTING, COMMITTED, PARTIALLY_COMMITTED,
        FAILED, ARCHIVED,
    )
    COMMITTABLE = (READY_TO_COMMIT, REVIEWING, READY_FOR_REVIEW)
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


# ──────────────────────────────────────────────────────────────────────────────
# ImportBatch
# ──────────────────────────────────────────────────────────────────────────────

class ImportBatch(Base):
    """One per upload session. Holds aggregate state and stats."""
    __tablename__ = "import_batches"

    id              = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    # Who kicked off this import
    created_by_id   = Column(String, ForeignKey("users.id"), nullable=True)
    created_by_name = Column(String, nullable=True)

    display_name      = Column(String, nullable=True)
    source_type       = Column(String, nullable=False)   # csv | xlsx | google_contacts
    source_filename   = Column(String, nullable=True)    # original uploaded filename

    status = Column(String, default=ImportBatchStatus.UPLOADING, nullable=False)

    # Row-level counters (refreshed by recount())
    total_rows    = Column(Integer, default=0)
    new_rows      = Column(Integer, default=0)
    matched_rows  = Column(Integer, default=0)
    warning_rows  = Column(Integer, default=0)  # possible duplicates / low-confidence
    rejected_rows = Column(Integer, default=0)
    pending_rows  = Column(Integer, default=0)
    committed_rows = Column(Integer, default=0)
    merged_rows    = Column(Integer, default=0)
    invalid_rows   = Column(Integer, default=0)

    error_message    = Column(Text, nullable=True)
    committed_at     = Column(DateTime, nullable=True)
    committed_by_id  = Column(String, ForeignKey("users.id"), nullable=True)
    committed_by_name = Column(String, nullable=True)
    archived_at      = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    staged_rows = relationship(
        "ImportStagedRow",
        back_populates="import_batch",
        passive_deletes=True,
        lazy="dynamic",
    )

    __table_args__ = (
        Index("ix_import_batches_org_status",  "organization_id", "status"),
        Index("ix_import_batches_org_created", "organization_id", "created_at"),
        Index("ix_import_batches_creator",     "created_by_id"),
    )

    def recount(self, db):
        """Refresh all row-count columns from live ImportStagedRow table.
        Idempotent — safe to call after any row-level change."""
        from sqlalchemy import func as _func

        rows = (
            db.query(ImportStagedRow)
            .filter(ImportStagedRow.batch_id == self.id)
            .all()
        )
        self.total_rows    = len(rows)
        self.new_rows      = sum(1 for r in rows if r.duplicate_status == ImportDuplicateStatus.NEW)
        self.matched_rows  = sum(1 for r in rows if r.duplicate_status == ImportDuplicateStatus.MATCHED_EXISTING)
        self.warning_rows  = sum(1 for r in rows if r.duplicate_status == ImportDuplicateStatus.POSSIBLE_DUPLICATE)
        self.rejected_rows = sum(1 for r in rows if r.review_status == ImportRowReviewStatus.REJECTED)
        self.pending_rows  = sum(1 for r in rows if r.review_status in (
            ImportRowReviewStatus.PENDING, ImportRowReviewStatus.ACCEPTED))
        self.invalid_rows  = sum(1 for r in rows if r.validation_status == ImportValidationStatus.INVALID)
        self.committed_rows = sum(
            1 for r in rows
            if r.review_status == ImportRowReviewStatus.COMMITTED
            and r.duplicate_status not in (ImportDuplicateStatus.MATCHED_EXISTING,
                                           ImportDuplicateStatus.POSSIBLE_DUPLICATE)
        )
        self.merged_rows = sum(
            1 for r in rows
            if r.review_status == ImportRowReviewStatus.COMMITTED
            and r.duplicate_status in (ImportDuplicateStatus.MATCHED_EXISTING,
                                       ImportDuplicateStatus.POSSIBLE_DUPLICATE)
        )

    def counter_reconciliation(self) -> dict:
        """Return a dict showing row accounting. Sum must equal total_rows."""
        accounted = (
            self.committed_rows + self.merged_rows +
            self.rejected_rows + self.pending_rows
        )
        return {
            "total": self.total_rows,
            "committed": self.committed_rows,
            "merged": self.merged_rows,
            "rejected": self.rejected_rows,
            "pending": self.pending_rows,
            "accounted": accounted,
            "unaccounted": self.total_rows - accounted,
            "balanced": accounted == self.total_rows,
        }


# ──────────────────────────────────────────────────────────────────────────────
# ImportStagedRow
# ──────────────────────────────────────────────────────────────────────────────

class ImportStagedRow(Base):
    """One row per contact parsed from the source file.
    Never creates a live Lead until commit.
    DNC blocks are authoritative and cannot be overridden by import."""
    __tablename__ = "import_staged_rows"

    id         = Column(String, primary_key=True, default=gen_uuid)
    batch_id   = Column(String, ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    row_number = Column(Integer, nullable=False)

    raw_data = Column(Text, nullable=True)   # JSON: original col→val map

    first_name       = Column(String, nullable=True)
    last_name        = Column(String, nullable=True)
    phone_raw        = Column(String, nullable=True)
    phone_normalized = Column(String, nullable=True)
    email_raw        = Column(String, nullable=True)
    email_normalized = Column(String, nullable=True)

    tier             = Column(String, nullable=True)   # inferred tier label
    relationship_type = Column(String, nullable=True)
    source_category  = Column(String, nullable=True)

    street_address = Column(String, nullable=True)
    city           = Column(String, nullable=True)
    state          = Column(String, nullable=True)
    zip_code       = Column(String, nullable=True)

    extra_fields = Column(Text, nullable=True)   # JSON: unmapped columns

    validation_status = Column(String, default=ImportValidationStatus.VALID, nullable=False)
    validation_errors = Column(Text, nullable=True)   # JSON list of error strings

    duplicate_status           = Column(String, default=ImportDuplicateStatus.NEW, nullable=False)
    match_confidence           = Column(String, nullable=True)
    matched_lead_id            = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    duplicate_of_staged_row_id = Column(String, ForeignKey("import_staged_rows.id", ondelete="SET NULL"), nullable=True)
    match_reason               = Column(String, nullable=True)

    review_status    = Column(String, default=ImportRowReviewStatus.PENDING, nullable=False)
    review_note      = Column(Text, nullable=True)
    reviewed_by_id   = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at      = Column(DateTime, nullable=True)

    # Set at commit time
    committed_lead_id   = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    merged_into_lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    committed_by_id     = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    committed_at        = Column(DateTime, nullable=True)


    # ── Compliance / consent (4 channels, independently preserved) ─────────
    # True=allowed, False=denied, None=unknown/ambiguous.
    # NEVER let None silently become consent.  More-restrictive wins on MERGE.
    consent_email            = Column(Boolean, nullable=True)
    consent_email_raw        = Column(String, nullable=True)   # exact source value
    consent_bulk_email       = Column(Boolean, nullable=True)
    consent_bulk_email_raw   = Column(String, nullable=True)
    consent_sms              = Column(Boolean, nullable=True)
    consent_sms_raw          = Column(String, nullable=True)
    consent_voice            = Column(Boolean, nullable=True)
    consent_voice_raw        = Column(String, nullable=True)
    consent_review_required  = Column(Boolean, default=False, nullable=False)

    # ── Source identity ────────────────────────────────────────────────────
    # Preserve external CRM IDs (e.g. Dynamics Contact GUID) as first-class
    # provenance.  Used for dedup before weaker phone/email matching.
    source_id      = Column(String, nullable=True)   # e.g. "6a1b2c3d-…"
    source_id_type = Column(String, nullable=True)   # e.g. "dynamics_contact_guid"

    # ── Historical activity ────────────────────────────────────────────────
    # Last Activity Date from CRM — authoritative for "was this lead ever
    # contacted?" evidence.  NOT the same as Last Action (free text).
    last_activity_date     = Column(DateTime, nullable=True)
    last_activity_date_raw = Column(String, nullable=True)

    # ── Mobile phone provenance ────────────────────────────────────────────
    # Preserved when the source has a dedicated Mobile Phone column.
    mobile_phone_raw        = Column(String, nullable=True)
    mobile_phone_normalized = Column(String, nullable=True)
    # known_mobile | known_landline | unknown (never inferred from value alone)
    phone_type = Column(String, nullable=True)

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
        Index("ix_isr_batch_id",            "batch_id"),
        Index("ix_isr_org_id",              "organization_id"),
        Index("ix_isr_batch_review",        "batch_id", "review_status"),
        Index("ix_isr_batch_dup",           "batch_id", "duplicate_status"),
        Index("ix_isr_phone_norm",          "phone_normalized"),
        Index("ix_isr_committed_lead",      "committed_lead_id"),
        Index("ix_isr_merged_lead",         "merged_into_lead_id"),
        Index("ix_isr_matched_lead",        "matched_lead_id"),
    )
