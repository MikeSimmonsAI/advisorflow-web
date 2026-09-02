"""
STAGED HISTORICAL SOURCE RECORDS - restored after a merge dropped them.

These two models were added at f7a6b50 in app/models/import_models.py. The
feature/lead-import-intelligence merge (5057da1) resolved that same-path
conflict by taking its own side of the file wholesale, which silently deleted
SourceRecord and SourceOpportunity - and with them the ability of the import
compliance gate to run at all.

They live in their OWN module now so that this cannot happen again by filename
collision: `import_batches` is the merged branch's table and its ImportBatch is
authoritative. Nothing here competes with it. `source_records` and
`source_opportunities` are distinct tables that nothing else defines.

A SourceRecord is HISTORICAL EVIDENCE, not an operational lead: no owner, no
status, no cadence, no consent-of-record, and no column any send path could
read as permission to contact somebody.
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