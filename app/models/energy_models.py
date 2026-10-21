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
from sqlalchemy import Column, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
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


class EnergyTeamSettings(Base):
    """TEAM & ALERTS for one energy workspace, managed by its own managers in the
    app (Settings -> Team & Alerts) - never by editing the website, server
    variables or code when someone joins or leaves.

    One row per organization; `data` is JSON:
      members        {user_id: {"in_app": bool, "email": bool, "assignable": bool}}
                     (a person with no entry gets the defaults in
                     services/energy_team.py - managers alerted in-app,
                     everyone assignable, nobody emailed)
      default_advisor_id   new requests go straight to this person; empty = the
                     team claims them from the shared New Requests inbox
      mailbox        the shared alert mailbox and its verification state
                     (address, code hash, sent/verified times, what it was
                     verified against). Email is never sent until verified.

    A NEW table, so create_all builds it; no existing table is altered.
    """
    __tablename__ = "energy_team_settings"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, unique=True, index=True)
    data = Column(Text, nullable=True)
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class EnergyRequestAlert(Base):
    """ONE row per new-request alert event - the "one request, one alert" rule
    is enforced by the database: (organization, event_key) is unique, so a retry
    or a repeat form that races the first one cannot alert twice. event_key is
    the request's first-contact task (website) or the referral (field).
    `details` (JSON) records who was alerted in-app and exactly what happened
    to each email - sent or not, never assumed. A NEW table (create_all)."""
    __tablename__ = "energy_request_alerts"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    event_key = Column(String, nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    origin = Column(String, nullable=True)          # website | field
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "event_key", name="uq_energy_request_alert_event"),
    )
