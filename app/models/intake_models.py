"""UNIVERSAL INTAKE — the records an import produces that are NOT leads.

WHY THIS MODULE EXISTS
======================
Uploading a contact is not the same thing as creating a lead, and creating a
lead is not the same thing as making that person outreach-ready. Until this
module existed the platform had exactly one place to put an imported person -
the `leads` table - so a 14,712-row customer database became 14,712 "leads",
inflated every lead count, pipeline and SMS-ready number, and put people who
were never sales opportunities into assignment queues.

The lifecycle is now:

    RAW FILE -> import_batch_files          (the upload, kept for background work)
             -> import_staged_rows          (parsed, normalized, matched, classified)
             -> org_contacts                (the organization's contact database)
             -> leads                       (ONLY records that are real opportunities)

`OrgContact` is the organization's own record of a person or company. It can be
a plain contact, an existing customer, a previous customer, a partner, a vendor,
or a record that needs enrichment. A contact that is also a sales opportunity
carries `lead_id`; every other contact never appears in a lead count, a
pipeline, a forecast, an SMS-ready figure or an assignment queue, because none
of those read this table.

TENANT RULE
===========
Every row here carries `organization_id`, it is NOT NULL, and every query in
app/services/intake filters on it first. Nothing in this module is global and
nothing is owned by the platform pseudo-org.

VERTICAL RULE
=============
Nothing here is specific to energy, insurance, real estate, automotive,
mortgage or cemetery. A vertical's own columns (supplier, contract end, VIN,
policy number, parcel...) live in `vertical_fields` / `custom_fields` JSON,
keyed by whatever the organization's mapping named them.
"""

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        LargeBinary, String, Text, UniqueConstraint, func)

from app.models.models import Base, gen_uuid


# ──────────────────────────────────────────────────────────────────────────────
# Vocabularies. Plain strings, never a database ENUM: adding a value must never
# need a migration, and an organization may add its own classifications.
# ──────────────────────────────────────────────────────────────────────────────

class RecordClass:
    """What KIND of relationship a record represents. Coarse on purpose."""
    CONTACT = "contact"
    LEAD = "lead"
    CUSTOMER = "customer"
    PREVIOUS_CUSTOMER = "previous_customer"
    RENEWAL = "renewal"
    PARTNER = "partner"
    VENDOR = "vendor"
    EMPLOYEE = "employee"
    OTHER = "other"

    ALL = (CONTACT, LEAD, CUSTOMER, PREVIOUS_CUSTOMER, RENEWAL, PARTNER,
           VENDOR, EMPLOYEE, OTHER)


class ContactLifecycle:
    """Whether the record is usable as-is."""
    ACTIVE = "active"
    NEEDS_ENRICHMENT = "needs_enrichment"   # no usable phone AND no usable email
    ARCHIVED = "archived"                   # detached by a rollback, never deleted


class IntakeStatus:
    """IMPORT STATUS of one staged row. Separate from classification and from
    outreach status on purpose - one field representing all three is exactly
    the design this replaces."""
    PARSED = "parsed"
    READY = "ready"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"                 # duplicate of an earlier row in THIS file
    EXISTING_MATCH = "existing_match"       # exact match to a record already in the org
    BLOCKED = "blocked"                     # DNC / suppression on the matched record
    NEEDS_ENRICHMENT = "needs_enrichment"
    INVALID = "invalid"
    FAILED = "failed"
    APPROVED = "approved"                   # a reviewer explicitly approved it
    IMPORTED = "imported"
    SKIPPED = "skipped"

    ALL = (PARSED, READY, NEEDS_REVIEW, DUPLICATE, EXISTING_MATCH, BLOCKED,
           NEEDS_ENRICHMENT, INVALID, FAILED, APPROVED, IMPORTED, SKIPPED)


class SmsStatus:
    READY = "ready"
    PENDING_VALIDATION = "pending_validation"
    SUPPRESSED = "suppressed"
    DNC = "dnc"
    LANDLINE = "landline"
    INVALID = "invalid"
    NO_PHONE = "no_phone"
    OPTED_OUT = "opted_out"
    REVIEW = "review"


class EmailStatus:
    READY = "ready"
    PENDING = "pending"
    INVALID = "invalid"
    HARD_BOUNCE = "hard_bounce"
    UNSUBSCRIBED = "unsubscribed"
    SUPPRESSED = "suppressed"
    NO_EMAIL = "no_email"
    REVIEW = "review"


class MatchType:
    EXACT = "exact"
    POSSIBLE = "possible"
    NEW = "new"


class DuplicateResolution:
    """What a reviewer decided to do with a matched row."""
    UPDATE_EXISTING = "update_existing"
    MERGE = "merge"                 # update existing AND mark the row merged
    SKIP_INCOMING = "skip_incoming"
    KEEP_SEPARATE = "keep_separate"
    REVIEW = "review"               # undecided - never auto-resolved

    ALL = (UPDATE_EXISTING, MERGE, SKIP_INCOMING, KEEP_SEPARATE, REVIEW)


class CommitMode:
    """The decision-screen actions. STAGE_ONLY is the default and writes nothing
    to the CRM."""
    STAGE_ONLY = "stage_only"
    READY_ONLY = "ready_only"
    READY_AND_REVIEW = "ready_and_review"

    ALL = (STAGE_ONLY, READY_ONLY, READY_AND_REVIEW)


# ──────────────────────────────────────────────────────────────────────────────
# OrgContact
# ──────────────────────────────────────────────────────────────────────────────

class OrgContact(Base):
    """One person or organization in ONE customer organization's database."""

    __tablename__ = "org_contacts"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    # ── CRM classification (not import status, not outreach status) ────────
    record_class = Column(String, nullable=False, default=RecordClass.CONTACT)
    classification = Column(String, nullable=True)        # e.g. "win_back"
    lifecycle = Column(String, nullable=False, default=ContactLifecycle.ACTIVE)
    historical_customer = Column(Boolean, nullable=True)  # None = not stated

    # ── identity ────────────────────────────────────────────────────────────
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    company = Column(String, nullable=True)               # display / legal name, as given
    company_norm = Column(String, nullable=True)          # matching key only
    job_title = Column(String, nullable=True)

    # ── channels: normalized value + the value as it arrived ──────────────
    email = Column(String, nullable=True)                 # lowercased, trimmed
    email_raw = Column(String, nullable=True)
    phone = Column(String, nullable=True)                 # E.164
    phone_raw = Column(String, nullable=True)
    mobile_phone = Column(String, nullable=True)          # E.164, only from a dedicated column
    mobile_phone_raw = Column(String, nullable=True)
    phone_line_type = Column(String, nullable=True)       # mobile|landline|voip|unknown

    # ── OUTREACH STATUS, per channel ────────────────────────────────────────
    sms_status = Column(String, nullable=True)
    email_status = Column(String, nullable=True)
    outreach_reasons = Column(Text, nullable=True)        # JSON list

    # ── address ─────────────────────────────────────────────────────────────
    street_address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    country = Column(String, nullable=True)

    # ── provenance ──────────────────────────────────────────────────────────
    source = Column(String, nullable=True)                # "HubSpot", "CSV Import"...
    source_detail = Column(String, nullable=True)
    source_system = Column(String, nullable=True)         # "hubspot"
    source_record_id = Column(String, nullable=True)      # the source system's own id, exact
    import_batch_id = Column(String, ForeignKey("import_batches.id", ondelete="SET NULL"),
                             nullable=True)
    source_row_number = Column(Integer, nullable=True)
    last_activity_at = Column(DateTime, nullable=True)
    owner_name = Column(String, nullable=True)            # source owner, as text

    tags = Column(Text, nullable=True)                    # JSON list
    custom_fields = Column(Text, nullable=True)           # JSON {key: value}
    vertical_fields = Column(Text, nullable=True)         # JSON {key: value}
    # Source columns the organization chose to keep as metadata rather than as
    # CRM fields (and every column nobody mapped). Never dropped.
    source_fields = Column(Text, nullable=True)           # JSON {key: value}

    # Fields a person has corrected by hand. An import may NEVER overwrite
    # these, whatever the batch's update policy says.
    manually_edited_fields = Column(Text, nullable=True)  # JSON list of field names

    # The lead this contact became, when (and only when) it is an opportunity.
    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)

    archived_at = Column(DateTime, nullable=True)
    archived_reason = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Organization first on every index: no lookup here can span tenants.
        Index("ix_org_contacts_org_class", "organization_id", "record_class"),
        Index("ix_org_contacts_org_email", "organization_id", "email"),
        Index("ix_org_contacts_org_phone", "organization_id", "phone"),
        Index("ix_org_contacts_org_mobile", "organization_id", "mobile_phone"),
        Index("ix_org_contacts_org_source_id", "organization_id", "source_system",
              "source_record_id"),
        Index("ix_org_contacts_org_company", "organization_id", "company_norm"),
        Index("ix_org_contacts_org_batch", "organization_id", "import_batch_id"),
        Index("ix_org_contacts_org_lifecycle", "organization_id", "lifecycle"),
        Index("ix_org_contacts_lead", "lead_id"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# OrgContactSourceId — every source id that identifies a contact
# ──────────────────────────────────────────────────────────────────────────────

class OrgContactSourceId(Base):
    """Every external id known for a contact: its own, plus the ids of source
    records merged into it (the same account held twice in HubSpot, say).

    Matching reads THIS table, so re-importing either id finds the one
    contact instead of creating a second. Organization first on the index."""

    __tablename__ = "org_contact_source_ids"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    org_contact_id = Column(String, ForeignKey("org_contacts.id", ondelete="CASCADE"),
                            nullable=False)
    source_system = Column(String, nullable=True)
    source_record_id = Column(String, nullable=False)
    import_batch_id = Column(String, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_ocsid_org_src", "organization_id", "source_system", "source_record_id"),
        Index("ix_ocsid_contact", "org_contact_id"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# ImportBatchFile — the upload itself
# ──────────────────────────────────────────────────────────────────────────────

class ImportBatchFile(Base):
    """The uploaded bytes, stored in the database rather than on local disk.

    Mapping happens in a separate request from the upload, and analysis runs
    in the background. A file on one web instance's temp disk is invisible to
    the next request if it lands on another instance, and vanishes on restart.
    """

    __tablename__ = "import_batch_files"

    id = Column(String, primary_key=True, default=gen_uuid)
    batch_id = Column(String, ForeignKey("import_batches.id", ondelete="CASCADE"),
                      nullable=False, unique=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    filename = Column(String, nullable=True)
    content_type = Column(String, nullable=True)
    size_bytes = Column(Integer, nullable=True)
    sha256 = Column(String, nullable=True)
    content = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# ──────────────────────────────────────────────────────────────────────────────
# ImportRecordVersion — what an import changed, so it can be undone
# ──────────────────────────────────────────────────────────────────────────────

class ImportRecordVersion(Base):
    """One write an import made to one record.

    action = "created"  -> rollback may remove it if nothing else touched it
    action = "updated"  -> `before_json` holds the exact prior values of every
                           field the import changed; rollback restores them
                           unless the record changed again after the import.
    """

    __tablename__ = "import_record_versions"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    batch_id = Column(String, ForeignKey("import_batches.id", ondelete="CASCADE"),
                      nullable=False)
    staged_row_id = Column(String, nullable=True)
    target_type = Column(String, nullable=False)          # "org_contact" | "lead"
    target_id = Column(String, nullable=False)
    action = Column(String, nullable=False)               # created | updated | linked
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    applied_at = Column(DateTime, server_default=func.now())
    rolled_back_at = Column(DateTime, nullable=True)
    rollback_outcome = Column(String, nullable=True)      # removed|restored|archived|kept
    rollback_note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_irv_org_batch", "organization_id", "batch_id"),
        Index("ix_irv_target", "target_type", "target_id"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# IntakeClassification — the organization's own classifications
# ──────────────────────────────────────────────────────────────────────────────

class IntakeClassification(Base):
    """An organization-defined classification, on top of the platform defaults
    in app/services/intake/classification.py. Same shape as a default."""

    __tablename__ = "intake_classifications"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    key = Column(String, nullable=False)
    label = Column(String, nullable=False)
    record_class = Column(String, nullable=False, default=RecordClass.CONTACT)
    creates_lead = Column(Boolean, nullable=False, default=False)
    aliases = Column(Text, nullable=True)                 # JSON list of source values
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "key", name="uq_intake_class_org_key"),
    )
