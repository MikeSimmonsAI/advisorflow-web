"""Retail-energy (Atlantis-class) operating records that the lead table cannot hold.

MOVE CONCIERGE. A customer who is moving asks for help getting services started,
transferred or stopped at the new address. That request has its own lifecycle
(move date, services asked for, a checklist a coordinator works through) which
is not a lead stage, so it is its own row - linked to the Lead and/or the
OrgContact it is about, never replacing either.

What this table does NOT do: it executes nothing with an outside vendor. Every
checklist item is ticked by a person. Vendor integrations are reported as
NOT CONFIGURED by the router until a real integration exists.
"""
from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


class EnergyMoveRequest(Base):
    __tablename__ = "energy_move_requests"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    org_contact_id = Column(String, ForeignKey("org_contacts.id", ondelete="SET NULL"),
                            nullable=True, index=True)
    # Display name captured at creation so the row reads even if the link is cleared.
    contact_name = Column(String, nullable=True)
    move_date = Column(Date, nullable=True)            # only when the customer gave one
    from_address = Column(String, nullable=True)
    to_address = Column(String, nullable=True)
    services = Column(Text, nullable=True)             # JSON list of service keys
    checklist = Column(Text, nullable=True)            # JSON list of {key,label,done,done_at,done_by}
    status = Column(String, nullable=False, default="requested")
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_energy_move_org_status", "organization_id", "status"),
    )
