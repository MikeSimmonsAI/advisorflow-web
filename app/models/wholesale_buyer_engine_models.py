"""Buyer Intelligence Engine - the evidence a buyer record rests on (Wholesale).

NOT A SECOND BUYER DATABASE. Every row here hangs off `wholesale_buyers` (the
one buyer table the CRM, matching and disposition already use):

    wholesale_buyer_acquisitions   one row per transaction we have EVIDENCE of
                                   (a recorded deed in the county roll, a deal
                                   they closed with us, a purchase their own file
                                   claims). The evidence class says how strong it
                                   is; nothing is upgraded by inference.
    wholesale_buyer_entities       the legal names an investor buys under. A
                                   second LLC is linked to an investor only by a
                                   person (or an exact-name match), never because
                                   two names look alike.
    wholesale_buyer_conversations  buyer QUALIFICATION conversations: what was
                                   asked, what the buyer said, what was learned
                                   (with the quote it came from), the hand-off.
                                   It records; it sends nothing. Real sending
                                   stays with the existing disposition gateway.

Tables are created by Base.metadata.create_all (registered in app/models/registry.py).
"""
from datetime import datetime

from sqlalchemy import (Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)

from app.models.models import Base, gen_uuid

# How strong a purchase record is. Ordered weakest -> strongest is NOT implied:
# a recorded title transfer is a public fact but says nothing about price or cash.
RECORDED_TITLE_TRANSFER = "RECORDED_TITLE_TRANSFER"   # deed to them in county records; no price (Texas)
VERIFIED_PURCHASE = "VERIFIED_PURCHASE"               # closed a deal with us / closing documents seen
VERIFIED_CASH_PURCHASE = "VERIFIED_CASH_PURCHASE"     # documented cash close (no lender on the closing)
REPORTED_INVESTOR_PURCHASE = "REPORTED_INVESTOR_PURCHASE"  # a person on the team reports it (named source)
CLAIMED_PURCHASE = "CLAIMED_PURCHASE"                 # the buyer or their file says so
EVIDENCE_CLASSES = (RECORDED_TITLE_TRANSFER, VERIFIED_PURCHASE, VERIFIED_CASH_PURCHASE,
                    REPORTED_INVESTOR_PURCHASE, CLAIMED_PURCHASE)
EVIDENCE_LABELS = {
    RECORDED_TITLE_TRANSFER: "Recorded title transfer",
    VERIFIED_PURCHASE: "Verified purchase",
    VERIFIED_CASH_PURCHASE: "Verified cash purchase",
    REPORTED_INVESTOR_PURCHASE: "Reported investor purchase",
    CLAIMED_PURCHASE: "Claimed purchase history",
}


class WholesaleBuyerAcquisition(Base):
    __tablename__ = "wholesale_buyer_acquisitions"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)

    evidence_class = Column(String, nullable=False, default=RECORDED_TITLE_TRANSFER)
    source = Column(String, nullable=False)          # dcad, tad, deal:<id>, buyer_file, staff
    reference = Column(String, nullable=True)        # parcel account, deal id, document number
    grantee_name = Column(String, nullable=True)     # the name on the record (which entity took title)
    property_kind = Column(String, nullable=True)    # single_family, multifamily, land, commercial ...
    street = Column(String, nullable=True)
    city = Column(String, nullable=True)
    county = Column(String, nullable=True)
    state = Column(String, nullable=True, default="TX")
    zip_code = Column(String, nullable=True)
    transfer_date = Column(Date, nullable=True)      # deed / closing date as recorded
    price = Column(Integer, nullable=True)           # ONLY when a document states it; never from Texas deeds
    confidence = Column(String, nullable=False, default="medium")   # high, medium, low
    note = Column(Text, nullable=True)
    source_file_date = Column(String, nullable=True) # the county file's own generation stamp
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "buyer_id", "source", "reference", "transfer_date",
                         name="uq_wsbuyacq_unique_record"),
        Index("ix_wsbuyacq_org_buyer", "organization_id", "buyer_id"),
    )


ENTITY_CONFIRMED = "confirmed"        # a person linked it, or the name is the buyer's own
ENTITY_REVIEW = "needs_review"        # looks related - a person must decide
ENTITY_REJECTED = "rejected"          # a person said: not the same investor
ENTITY_STATUSES = (ENTITY_CONFIRMED, ENTITY_REVIEW, ENTITY_REJECTED)


class WholesaleBuyerEntity(Base):
    __tablename__ = "wholesale_buyer_entities"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)
    legal_name = Column(String, nullable=False)
    norm_name = Column(String, nullable=False)
    # When this entity is another buyer row in the list (a second LLC that was
    # imported separately), its id. Linking never deletes that row.
    linked_buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, nullable=False, default=ENTITY_CONFIRMED)
    reason = Column(Text, nullable=True)             # why it was suggested / linked
    source = Column(String, nullable=True)           # county_roll, staff, import
    decided_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "buyer_id", "norm_name", name="uq_wsbuyent_name"),
        Index("ix_wsbuyent_org_norm", "organization_id", "norm_name"),
    )


CONV_OPEN = "open"
CONV_HANDOFF = "handed_off"
CONV_PAUSED = "paused"
CONV_DONE = "completed"
CONV_STOPPED = "stopped"          # the buyer said stop / not interested
CONV_STATUSES = (CONV_OPEN, CONV_HANDOFF, CONV_PAUSED, CONV_DONE, CONV_STOPPED)


class WholesaleBuyerConversation(Base):
    __tablename__ = "wholesale_buyer_conversations"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)
    mode = Column(String, nullable=False, default="practice")    # practice | live
    channel = Column(String, nullable=False, default="practice")
    status = Column(String, nullable=False, default=CONV_OPEN)
    # [{role: ai|buyer|staff|system, text, at, reader?, extracted?}] - every turn, in order
    transcript = Column(Text, nullable=True)
    # {field: {value, quote, source: ai|rules|staff, at}} - what the buyer SAID
    answers = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    objections = Column(Text, nullable=True)        # JSON list of quotes
    commitments = Column(Text, nullable=True)       # JSON list {what, when, quote}
    handoff_reason = Column(String, nullable=True)
    handoff_exception_id = Column(String, nullable=True)
    followup_exception_id = Column(String, nullable=True)
    started_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    # Deal Room (#415): the same conversation record carries the investor's
    # portal threads. kind: qualification | concierge | relationship | deal.
    # Turns with role "internal" are staff-only and never served to a buyer.
    kind = Column(String, nullable=True)
    deal_id = Column(String, nullable=True)
    account_id = Column(String, nullable=True)
    ai_enabled = Column(Boolean, nullable=False, default=True)
    assigned_user_id = Column(String, nullable=True)
    buyer_unread = Column(Integer, nullable=False, default=0)
    staff_unread = Column(Integer, nullable=False, default=0)
    last_message_at = Column(DateTime, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsbuyconv_org_buyer", "organization_id", "buyer_id"),
    )
