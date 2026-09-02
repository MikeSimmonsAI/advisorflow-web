"""
IMPORT INTELLIGENCE - staged historical source records.

THE DISTINCTION THIS FILE EXISTS TO HOLD
----------------------------------------
A `Lead` is an OPERATIONAL record: it has an owner, a status, a cadence, and
every send path in the platform reads it. A `SourceRecord` is a HISTORICAL
record: it is evidence about a person, it is queryable for reconciliation, and
NOTHING SENDS TO IT.

Turning a 93,434-row CRM export into 93,434 Leads would create ninety thousand
sendable rows in order to answer questions about a hundred of them. The export
is a reference table, not a work queue, and this is where a reference table
lives.

WHAT A STAGED RECORD KEEPS
--------------------------
  * the ORIGINAL source identifier - the CRM's own contact GUID - untouched,
    so the same person can be recognised across exports taken months apart
  * the RAW ROW, verbatim, so every derived value can be audited against the
    cell it came from rather than trusted
  * NORMALIZED identity columns, indexed, so reconciliation is a join and not
    a scan of ninety thousand JSON blobs
  * COMPLIANCE as a tri-state, read by the canonical table in
    app/services/permission_values.py
  * HISTORICAL ACTIVITY, DISPOSITION and SALE indicators, which is the evidence
    the operational row usually lacks

WHAT IT DOES NOT HAVE, DELIBERATELY
-----------------------------------
No `assigned_to_id`. No `status`. No cadence, no message relationship, no
consent-of-record. There is no column here that a send path could read as
permission to contact somebody, and a gate asserts that these tables are not
reachable from any send path.

TENANCY
-------
`organization_id` is NOT NULL on both tables and is the first column of every
index. A historical record belongs to exactly one tenant, and a reconciliation
query that forgets to scope is a query that returns nothing rather than
somebody else's data.
"""

import enum

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text)
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


class SourceKind(str, enum.Enum):
    """What a batch was loaded FOR. It decides what may read the rows."""
    OPERATIONAL = "operational"   # became Leads
    HISTORICAL = "historical"     # staged evidence, never contacted directly


class ImportBatch(Base):
    """
    One upload. The provenance record every staged row points back to.

    This is what makes "where did this value come from" answerable a year
    later: the file name, its shape, who uploaded it, when, and the exact
    header row it carried - so a mapping decision can be re-derived rather
    than remembered.
    """
    __tablename__ = "import_batches"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    name = Column(String, nullable=True)              # human label
    source_filename = Column(String, nullable=True)
    source_system = Column(String, nullable=True)     # e.g. "dynamics"
    kind = Column(String, default=SourceKind.HISTORICAL.value, nullable=False)

    row_count = Column(Integer, nullable=True)
    column_count = Column(Integer, nullable=True)
    # The header row verbatim, plus how each column was classified. A mapping
    # audit is then a stored fact rather than something re-run from a file that
    # may no longer exist.
    header_json = Column(Text, nullable=True)
    mapping_json = Column(Text, nullable=True)

    uploaded_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    uploaded_by_name = Column(String, nullable=True)
    source_year = Column(Integer, nullable=True)      # batch metadata, never scored
    notes = Column(Text, nullable=True)

    status = Column(String, default="staged", nullable=False)  # staged/loaded/failed
    error_text = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_import_batches_org_created", "organization_id", "created_at"),
        Index("ix_import_batches_org_kind", "organization_id", "kind"),
    )


class SourceRecord(Base):
    """
    One historical contact record, staged for reconciliation. NOT a lead.

    `source_key` is the source system's own identifier - for a Dynamics export,
    the "(Do Not Modify) Contact" GUID. It is preserved exactly as given,
    because it is the only identifier that survives a person changing their
    phone number, their email, or their surname.
    """
    __tablename__ = "source_records"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    import_batch_id = Column(String, ForeignKey("import_batches.id"), nullable=True)

    # ---- provenance ------------------------------------------------------
    source_system = Column(String, nullable=True)
    source_entity = Column(String, default="contact", nullable=True)
    source_key = Column(String, nullable=True)        # the CRM's own contact id
    source_row_number = Column(Integer, nullable=True)
    row_checksum = Column(String, nullable=True)       # source's own, if given
    # The entire row as it arrived. This is the auditable original; every other
    # column on this table is derived from it and can be re-derived from it.
    raw_json = Column(Text, nullable=True)

    # ---- identity, normalized and indexed --------------------------------
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    full_name = Column(String, nullable=True)
    norm_first_name = Column(String, nullable=True)
    norm_last_name = Column(String, nullable=True)

    email = Column(String, nullable=True)
    norm_email = Column(String, nullable=True)
    email_alt = Column(String, nullable=True)

    phone = Column(String, nullable=True)
    norm_phone = Column(String, nullable=True)
    # A SEPARATE COLUMN BECAUSE IT IS SEPARATE EVIDENCE. The platform has no
    # line-type lookup, so it may not call a number mobile - unless the source
    # system says so in a column of its own, which is what this is.
    mobile_phone = Column(String, nullable=True)
    norm_mobile_phone = Column(String, nullable=True)
    phones_json = Column(Text, nullable=True)          # every other number found

    street_address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    norm_zip = Column(String, nullable=True)

    # ---- compliance, tri-state: True allow / False DENY / NULL not stated --
    allow_email = Column(Boolean, nullable=True)
    allow_bulk_email = Column(Boolean, nullable=True)
    allow_sms = Column(Boolean, nullable=True)
    allow_voice = Column(Boolean, nullable=True)
    permission_review = Column(Boolean, default=False, nullable=True)
    permission_raw = Column(Text, nullable=True)       # the cells, verbatim

    # ---- historical activity --------------------------------------------
    last_activity_at = Column(DateTime, nullable=True)
    last_action = Column(String, nullable=True)        # an ACTION, never a date
    open_activity_at = Column(DateTime, nullable=True)
    last_assigned_at = Column(DateTime, nullable=True)
    activity_count = Column(Integer, nullable=True)

    # ---- status / disposition -------------------------------------------
    status_reason = Column(String, nullable=True)
    lead_type = Column(String, nullable=True)
    lead_source = Column(String, nullable=True)
    owner_name = Column(String, nullable=True)
    original_owner_name = Column(String, nullable=True)

    # ---- sale / contract indicators --------------------------------------
    sale_made = Column(Boolean, nullable=True)
    last_sold_at = Column(DateTime, nullable=True)
    last_sale_type = Column(String, nullable=True)

    source_created_at = Column(DateTime, nullable=True)   # created in the CRM
    source_modified_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        # Reconciliation joins. Organization first on every one of them.
        Index("ix_source_records_org_key", "organization_id", "source_key"),
        Index("ix_source_records_org_email", "organization_id", "norm_email"),
        Index("ix_source_records_org_phone", "organization_id", "norm_phone"),
        Index("ix_source_records_org_last", "organization_id", "norm_last_name"),
        Index("ix_source_records_org_batch", "organization_id", "import_batch_id"),
    )


class SourceOpportunity(Base):
    """
    One historical opportunity / contract row. Also not a lead.

    Joins to `SourceRecord.source_key` through `contact_source_key`. It is kept
    as its own table rather than folded into the contact record because the
    relationship is ONE CONTACT TO MANY OPPORTUNITIES - in the observed export,
    up to thirty-one - and collapsing that into a "sold yes/no" column on the
    person throws away which contract, for how much, and when.
    """
    __tablename__ = "source_opportunities"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    import_batch_id = Column(String, ForeignKey("import_batches.id"), nullable=True)

    source_key = Column(String, nullable=True)          # opportunity id
    # The contact this opportunity belongs to, as the SOURCE states it. NULLABLE
    # ON PURPOSE: in the observed export only a minority of rows carry a usable
    # contact key, and an unjoinable opportunity is a fact to report, not a row
    # to attach to whichever person looks closest.
    contact_source_key = Column(String, nullable=True)
    source_row_number = Column(Integer, nullable=True)
    raw_json = Column(Text, nullable=True)

    status = Column(String, nullable=True)              # Open / Won / Lost
    status_reason = Column(String, nullable=True)       # In Progress / Bought-Sold / ...
    close_status = Column(String, nullable=True)        # Sold / Pending - Sold / Lost
    cancelled = Column(Boolean, nullable=True)

    contract_number = Column(String, nullable=True)
    contract_type = Column(String, nullable=True)       # Purchase / Proposal
    contract_need = Column(String, nullable=True)       # Pre-Need / At-Need / PN -> AN
    contract_total = Column(Float, nullable=True)
    contract_at = Column(DateTime, nullable=True)
    actual_close_at = Column(DateTime, nullable=True)

    location = Column(String, nullable=True)
    advisor_name = Column(String, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_source_opps_org_contact", "organization_id", "contact_source_key"),
        Index("ix_source_opps_org_key", "organization_id", "source_key"),
        Index("ix_source_opps_org_status", "organization_id", "status"),
    )
