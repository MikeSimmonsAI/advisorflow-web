"""Portfolio Acquisitions: one seller, many properties, several purchase scenarios.

WHAT THIS ADDS, AND WHAT IT DELIBERATELY REUSES
-----------------------------------------------
A portfolio is a LIST OF LINKS to properties the Wholesale module already
keeps. Every property in a portfolio is an ordinary `WholesaleProperty` with its
ordinary `WholesaleDeal`, so valuation (ARV engine, comps), repairs (with their
history), the maximum allowable offer (wholesale_analysis.calculate_offer), the
underwriting scenarios and the deal room all work on portfolio properties with
no second engine and no copied record.

What the property record cannot hold is what the SELLER said about it on their
list (price, rent, how they described it, which row of which sheet) and what WE
might pay for it in one purchase scenario versus another. Those are the only
things stored here:

  wholesale_portfolios               the seller's portfolio (#001, #002 ...)
  wholesale_portfolio_sources        each list that was brought in (file, photo,
                                     manual entry) - staged for review, then
                                     committed, and undoable
  wholesale_portfolio_items          property <-> portfolio, with the seller's
                                     row exactly as listed (unverified)
  wholesale_portfolio_packages       a named purchase scenario
  wholesale_portfolio_package_items  a property in a scenario, with the
                                     scenario's own offer and a SNAPSHOT of the
                                     numbers it was built on

THE SELLER'S FIGURES ARE NEVER MARKET VALUES. `seller_price` and `seller_rent`
are what the seller wrote. They are kept apart from the deal's ARV and from the
property's value on purpose, and every screen labels them "seller stated".

SCENARIOS DO NOT MOVE ON THEIR OWN. A package item carries its own offer and a
snapshot of the valuation it was built on. A later valuation change flags the
scenario as built on older numbers; it never rewrites it.

Every table carries organization_id, NOT NULL, indexed: the same isolation rule
as the rest of the module (see wholesale_models.py).
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
                        UniqueConstraint)

from app.models.models import Base, gen_uuid

PORTFOLIO_STATUSES = ("researching", "evaluating", "negotiating", "offer_made", "under_contract", "closed",
                      "passed")
SOURCE_KINDS = ("csv", "xlsx", "photo", "manual", "existing")
SOURCE_STATUSES = ("preview", "committed", "undone", "discarded")
CONFIDENCE = ("high", "medium", "low")
REVIEW_STATUSES = ("needs_review", "reviewed")
DISCOUNT_MODES = ("none", "amount", "percent", "total")


class WholesalePortfolio(Base):
    __tablename__ = "wholesale_portfolios"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    number = Column(Integer, nullable=False)                 # SELLER PORTFOLIO #001, per workspace
    name = Column(String, nullable=False)
    # The seller is a Lead (wholesale_models: there is no second contact
    # database). Optional: a portfolio can be opened before the seller is on file.
    seller_lead_id = Column(String, ForeignKey("leads.id"), nullable=True)
    seller_name = Column(String, nullable=True)              # selling entity as written ("ABC Holdings LLC")
    source = Column(String, nullable=True)                   # how it reached us ("seller's printed list")
    status = Column(String, nullable=False, default="researching")
    notes = Column(Text, nullable=True)
    # A whole-portfolio target the owner sets by hand. Shown beside, never
    # instead of, the sum of the property-level offers.
    target_offer_total = Column(Numeric(14, 2), nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    archived_at = Column(DateTime, nullable=True)
    archived_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "number", name="uq_wsportfolio_org_number"),
        Index("ix_wsportfolio_org_created", "organization_id", "created_at"),
    )


class WholesalePortfolioSource(Base):
    """One list brought into a portfolio, reviewed before anything is created.

    `rows` holds the staged rows (as read, the person's corrections, the
    duplicate findings and the decision for each). `created` records exactly
    what a commit made, so an undo removes that and nothing else.
    """
    __tablename__ = "wholesale_portfolio_sources"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    portfolio_id = Column(String, ForeignKey("wholesale_portfolios.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False)                    # SOURCE_KINDS
    file_name = Column(String, nullable=True)
    # Where the original lives (e.g. "Drive: seller sheet 1 of 2", an image
    # file name). The original image itself is never stored here.
    reference = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="preview")
    rows = Column(Text, nullable=True)                       # JSON list
    created = Column(Text, nullable=True)                    # JSON {"items": [...], "properties": [...]}
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    committed_at = Column(DateTime, nullable=True)
    committed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    undone_at = Column(DateTime, nullable=True)
    undone_by_id = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (Index("ix_wsportsrc_org_portfolio", "organization_id", "portfolio_id"),)


class WholesalePortfolioItem(Base):
    """A property in a portfolio, with the seller's row as they listed it."""
    __tablename__ = "wholesale_portfolio_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    portfolio_id = Column(String, ForeignKey("wholesale_portfolios.id", ondelete="CASCADE"), nullable=False)
    property_id = Column(String, ForeignKey("wholesale_properties.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(String, ForeignKey("wholesale_portfolio_sources.id"), nullable=True)
    source_row = Column(Integer, nullable=True)              # the row/entry number on the seller's list
    source_ref = Column(String, nullable=True)               # which sheet/image/file it came from
    seller_address_text = Column(String, nullable=True)      # exactly as the seller wrote it
    seller_price = Column(Numeric(14, 2), nullable=True)     # SELLER STATED - not a value
    seller_rent = Column(Numeric(12, 2), nullable=True)      # SELLER STATED monthly rent - not collected rent
    seller_type = Column(String, nullable=True)              # as listed ("LAND", "duplex" ...)
    is_land = Column(Boolean, nullable=False, default=False)
    source_notes = Column(Text, nullable=True)
    extraction_confidence = Column(String, nullable=True)    # CONFIDENCE; photo/manual rows start low
    review_status = Column(String, nullable=False, default="needs_review")
    # [{"price": .., "at": .., "by": .., "note": ..}] - every change to the
    # seller's asking price, oldest first. The first entry is the list itself.
    price_history = Column(Text, nullable=True)
    removed_at = Column(DateTime, nullable=True)
    removed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        # One property appears in a portfolio once. Removing it sets removed_at;
        # adding it back reuses the row and its history.
        UniqueConstraint("portfolio_id", "property_id", name="uq_wsportitem_portfolio_property"),
        Index("ix_wsportitem_org_portfolio", "organization_id", "portfolio_id"),
        Index("ix_wsportitem_org_property", "organization_id", "property_id"),
    )


class WholesalePortfolioPackage(Base):
    """A purchase scenario: some or all of a portfolio's properties."""
    __tablename__ = "wholesale_portfolio_packages"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    portfolio_id = Column(String, ForeignKey("wholesale_portfolios.id", ondelete="CASCADE"), nullable=False)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False, default="draft")  # draft | archived
    discount_mode = Column(String, nullable=False, default="none")  # DISCOUNT_MODES
    discount_value = Column(Numeric(14, 2), nullable=True)
    notes = Column(Text, nullable=True)
    copied_from_id = Column(String, ForeignKey("wholesale_portfolio_packages.id"), nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    archived_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_wsportpkg_org_portfolio", "organization_id", "portfolio_id"),)


class WholesalePortfolioPackageItem(Base):
    __tablename__ = "wholesale_portfolio_package_items"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    package_id = Column(String, ForeignKey("wholesale_portfolio_packages.id", ondelete="CASCADE"), nullable=False)
    item_id = Column(String, ForeignKey("wholesale_portfolio_items.id", ondelete="CASCADE"), nullable=False)
    # THIS SCENARIO'S offer for this property. Starts as the property's target
    # offer at the moment it was added; edits here change this scenario only.
    offer_amount = Column(Numeric(14, 2), nullable=True)
    # The figures this line was built on (asking, ARV, repairs, MAO, target,
    # costs) and when. Compared with today's figures to flag stale scenarios.
    snapshot = Column(Text, nullable=True)
    snapshot_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("package_id", "item_id", name="uq_wsportpkgitem_package_item"),
        Index("ix_wsportpkgitem_org_package", "organization_id", "package_id"),
    )
