"""Buyer network import batches (Wholesale).

One batch per uploaded file, one row per line in it. Rows keep the raw cells,
the normalized buyer, the duplicate check with its reasons, a person's decision
where identity was uncertain, and what the commit did (buyer created, buyer
updated with a before-snapshot, buy box added). That is what makes a batch
reviewable before commit, retryable after errors are fixed, and reversible.

Tables are created by Base.metadata.create_all (registered in app/models/registry.py).
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.models.models import Base, gen_uuid

BATCH_STATES = ("mapping", "analyzed", "committed", "partially_committed", "rolled_back", "cancelled")
ROW_STATES = ("new", "update", "duplicate_in_file", "conflict", "error", "skipped",
              "created", "updated", "rolled_back")


class WholesaleBuyerImportBatch(Base):
    __tablename__ = "wholesale_buyer_import_batches"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    filename = Column(String, nullable=True)
    file_format = Column(String, nullable=True)          # csv, xlsx, tsv
    list_name = Column(String, nullable=True)            # "Lower-48 cash buyers 2026"
    status = Column(String, nullable=False, default="mapping")
    headers = Column(Text, nullable=True)                # JSON list
    mapping = Column(Text, nullable=True)                # JSON {header: field}
    row_count = Column(Integer, nullable=False, default=0)
    counts = Column(Text, nullable=True)                 # JSON {state: n}
    file_stats = Column(Text, nullable=True)             # JSON (blank / malformed rows)
    is_test = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    committed_at = Column(DateTime, nullable=True)
    committed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)
    rolled_back_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_wsbimp_org_created", "organization_id", "created_at"),)


class WholesaleBuyerImportRow(Base):
    __tablename__ = "wholesale_buyer_import_rows"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    batch_id = Column(String, ForeignKey("wholesale_buyer_import_batches.id", ondelete="CASCADE"), nullable=False)
    row_number = Column(Integer, nullable=False)         # line in the file (header = 1)
    raw = Column(Text, nullable=True)                    # JSON {header: cell}
    normalized = Column(Text, nullable=True)             # JSON buyer + buy box
    state = Column(String, nullable=False, default="new")
    errors = Column(Text, nullable=True)                 # JSON [sentence]
    warnings = Column(Text, nullable=True)               # JSON [sentence]
    match = Column(Text, nullable=True)                  # JSON {candidates:[{buyer_id, name, reasons}], kind}
    decision = Column(String, nullable=True)             # create | merge | skip  (a person's call)
    decision_buyer_id = Column(String, nullable=True)    # merge target chosen by a person
    decided_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    buyer_id = Column(String, nullable=True)             # what the commit created / updated
    created_buyer = Column(Boolean, nullable=False, default=False)
    box_id = Column(String, nullable=True)               # buy box the commit added
    before = Column(Text, nullable=True)                 # JSON snapshot of fields an update changed
    applied_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_wsbimprow_batch_state", "batch_id", "state"),
                      Index("ix_wsbimprow_org_buyer", "organization_id", "buyer_id"))
