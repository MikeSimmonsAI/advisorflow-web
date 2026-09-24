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
    # Universal intake only (app/services/intake):
    MAPPING             = "mapping"             # uploaded, awaiting field mapping
    STAGED              = "staged"              # "stage everything, do not activate"
    ROLLED_BACK         = "rolled_back"
    PARTIALLY_ROLLED_BACK = "partially_rolled_back"
    CANCELLED           = "cancelled"

    ALL = (
        UPLOADING, PROCESSING, READY_FOR_REVIEW, REVIEWING,
        READY_TO_COMMIT, COMMITTING, COMMITTED, PARTIALLY_COMMITTED,
        FAILED, ARCHIVED, MAPPING, STAGED, ROLLED_BACK,
        PARTIALLY_ROLLED_BACK, CANCELLED,
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

    # ── Batch-level import options ────────────────────────────────────────────
    #
    # What the person doing the upload chose ON THE UPLOAD FORM, as opposed to
    # anything read out of the file. They live on the BATCH, not on each staged
    # row, because that is what they are: one choice made once, applied to
    # every row in this upload. Copying them onto 5,000 rows would invite the
    # rows to disagree with each other.
    #
    # These exist because the upload endpoint had been ACCEPTING all four as
    # multipart form fields and then dropping every one of them on the floor:
    # confirm_upload declared source_year/force_new_inquiry/relationship_type/
    # import_list_name, called stage_batch(), and stage_batch's signature had
    # nowhere to put them. So the UI showed a "Source year" box that looked
    # like it worked, the value parsed correctly, and the lead was still
    # written with source_year=None - the second time that same field has been
    # silently discarded by this endpoint, the first being the Form(...) bug
    # test_upload_endpoints.py was written to catch.
    source_year        = Column(Integer, nullable=True)
    force_new_inquiry  = Column(Boolean, default=False, nullable=False,
                                server_default="false")
    relationship_type  = Column(String, nullable=True)
    import_list_name   = Column(String, nullable=True)

    status = Column(String, default=ImportBatchStatus.UPLOADING, nullable=False)

    # ── UNIVERSAL INTAKE (additive; all nullable) ────────────────────────────
    #
    # Everything below is written only by app/services/intake. A batch created
    # by the legacy upload paths leaves every one of these NULL and behaves
    # exactly as it always did. `pipeline` says which engine owns the batch.
    pipeline          = Column(String, nullable=True)     # None=legacy | "universal"
    batch_code        = Column(String, nullable=True)     # e.g. ATL-20260924-001
    stage             = Column(String, nullable=True)     # mapping|parsing|normalizing|...
    progress_pct      = Column(Integer, nullable=True)
    heartbeat_at      = Column(DateTime, nullable=True)   # background worker liveness

    # Who, and in what capacity. `acting_role` is the role the importer held;
    # `acted_as_platform_owner` is True when a God admin imported while
    # explicitly acting on behalf of this organization.
    acting_user_id          = Column(String, nullable=True)
    acting_user_name        = Column(String, nullable=True)
    acting_role             = Column(String, nullable=True)
    acted_as_platform_owner = Column(Boolean, nullable=True)

    source_label      = Column(String, nullable=True)     # "HubSpot", "CSV Import"
    source_detail     = Column(String, nullable=True)
    source_system     = Column(String, nullable=True)     # "hubspot" (matching key)
    campaign_purpose  = Column(String, nullable=True)
    offer_hook        = Column(String, nullable=True)
    tags_json         = Column(Text, nullable=True)       # JSON list applied to the batch
    original_row_count = Column(Integer, nullable=True)
    headers_json      = Column(Text, nullable=True)       # the file's headers, in order
    mapping_json      = Column(Text, nullable=True)       # {header: {kind, target}}
    classification_json = Column(Text, nullable=True)     # rules + fallback
    update_policy_json  = Column(Text, nullable=True)     # which fields may update existing
    analysis_json     = Column(Text, nullable=True)       # the preview counts
    commit_mode       = Column(String, nullable=True)
    commit_report_json = Column(Text, nullable=True)
    rolled_back_at    = Column(DateTime, nullable=True)
    rolled_back_by_id = Column(String, nullable=True)
    rollback_report_json = Column(Text, nullable=True)
    completed_at      = Column(DateTime, nullable=True)

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

    # ── UNIVERSAL INTAKE (additive; all nullable) ────────────────────────────
    # Three status concepts, three columns - never one field for all of them:
    #   intake_status  -> IMPORT STATUS     (ready / needs_review / blocked ...)
    #   record_class + classification -> CRM CLASSIFICATION
    #   sms_status / email_status     -> OUTREACH STATUS, per channel
    intake_status        = Column(String, nullable=True)
    status_reasons       = Column(Text, nullable=True)    # JSON list of {code, detail}
    record_class         = Column(String, nullable=True)
    classification       = Column(String, nullable=True)
    classification_source = Column(String, nullable=True) # row|rule|fallback|manual
    classification_raw   = Column(String, nullable=True)  # the cell that decided it
    creates_lead         = Column(Boolean, nullable=True)
    historical_customer  = Column(Boolean, nullable=True)
    needs_enrichment     = Column(Boolean, nullable=True)
    sms_status           = Column(String, nullable=True)
    email_status         = Column(String, nullable=True)
    email_status_raw     = Column(String, nullable=True)  # the source's own verdict
    phone_line_type      = Column(String, nullable=True)

    full_name            = Column(String, nullable=True)
    company              = Column(String, nullable=True)
    company_norm         = Column(String, nullable=True)
    source_system        = Column(String, nullable=True)
    source_record_id     = Column(String, nullable=True)  # exact, never normalized

    match_type           = Column(String, nullable=True)  # exact|possible|new
    match_target_type    = Column(String, nullable=True)  # org_contact|lead|staged_row
    matched_contact_id   = Column(String, nullable=True)
    match_keys           = Column(Text, nullable=True)    # JSON list: which keys matched
    duplicate_resolution = Column(String, nullable=True)

    normalized_json      = Column(Text, nullable=True)    # every normalized value
    custom_fields_json   = Column(Text, nullable=True)
    vertical_fields_json = Column(Text, nullable=True)
    tags_json            = Column(Text, nullable=True)
    committed_contact_id = Column(String, nullable=True)
    commit_action        = Column(String, nullable=True)  # created|updated|skipped|...

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
        Index("ix_isr_batch_intake",        "batch_id", "intake_status"),
    )
