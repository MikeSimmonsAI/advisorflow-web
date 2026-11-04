"""Wholesale team access: reusable access profiles, each person's access, and
invitations (#439).

NEW TABLES ONLY - `Base.metadata.create_all` creates them; no COLUMNS_TO_ADD.

The grant itself is still the existing product seat (a Membership row,
scope_type "product", scope_id "wholesale" or "wholesale:<org id>") - see
app/services/product_access.py. These tables only say WHAT a seat may do:

  wholesale_access_profiles   a named, reusable set of permissions in ONE
                              workspace (EvoSys's or a second tenant's).
  wholesale_member_access     one person's access in one workspace: a profile,
                              individual grants and denials, record visibility.
                              A seat with no row keeps exactly the access it had
                              before this existed (the legacy team roles).
  wholesale_team_invites      an invitation to a workspace, for a new login (via
                              the existing one-time setup link) or an existing
                              one (accepted after signing in normally).

Every row carries `organization_id` and `seat_scope`, so one workspace can never
read or change another's profiles, people or invitations.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.models import Base, gen_uuid


class WholesaleAccessProfile(Base):
    __tablename__ = "wholesale_access_profiles"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    seat_scope = Column(String, nullable=False)                 # "wholesale" | "wholesale:<org id>"
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    template_key = Column(String, nullable=True)                # starter it began from, if any
    permissions_json = Column(Text, nullable=False, default="{}")   # {module: [actions]}
    record_scope = Column(String, nullable=False, default="assigned")  # assigned | team | workspace
    is_archived = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_ws_access_profile_scope", "seat_scope", "is_archived"),)


class WholesaleMemberAccess(Base):
    __tablename__ = "wholesale_member_access"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    seat_scope = Column(String, nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    profile_id = Column(String, ForeignKey("wholesale_access_profiles.id"), nullable=True)
    grants_json = Column(Text, nullable=False, default="{}")    # extra {module: [actions]} for this person
    denies_json = Column(Text, nullable=False, default="{}")    # removed {module: [actions]} for this person
    record_scope = Column(String, nullable=True)                # None = the profile's
    updated_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("seat_scope", "user_id", name="uq_ws_member_access_scope_user"),
                      Index("ix_ws_member_access_profile", "profile_id"))


class WholesaleTeamInvite(Base):
    __tablename__ = "wholesale_team_invites"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    seat_scope = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True)
    full_name = Column(String, nullable=True)
    user_id = Column(String, ForeignKey("users.id"), nullable=True)
    kind = Column(String, nullable=False)                       # new_login | existing_login
    profile_id = Column(String, ForeignKey("wholesale_access_profiles.id"), nullable=True)
    grants_json = Column(Text, nullable=False, default="{}")
    denies_json = Column(Text, nullable=False, default="{}")
    record_scope = Column(String, nullable=True)
    reports_to_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    status = Column(String, nullable=False, default="pending")  # pending | accepted | cancelled
    token_hash = Column(String, nullable=True, index=True)      # existing_login accept link
    expires_at = Column(DateTime, nullable=True)
    invited_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    last_sent_at = Column(DateTime, nullable=True)
    send_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_ws_invite_scope_status", "seat_scope", "status"),)
