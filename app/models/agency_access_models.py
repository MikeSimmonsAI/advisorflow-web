"""INSURANCE TEAM ACCESS — profiles, member access, teams, invitations (#523, part of #499).

Adapted from the Wholesale pattern (#514), not copied: Wholesale grants a
PRODUCT seat; an insurance agency member is an ordinary workspace member
(memberships, scope_type customer_org) of the agency organization, located
through UserLocation. These tables only say WHAT that member may do inside
the retention workspace.

  agency_access_profiles   a named, reusable set of {module: [actions]} plus
                           record visibility and a dashboard, in ONE agency.
  agency_member_access     one person's access in one agency: profile, extra
                           and removed permissions, visibility override,
                           reports-to, status (active | suspended | removed).
                           A member with NO row keeps exactly the access they
                           had before (owners, legacy managers/advisors).
  agency_teams /           named groups inside an agency, with a lead and the
  agency_team_members      locations the team works. Team membership never
                           widens location or permission scope.
  agency_invites           an invitation for a new login (existing one-time
                           setup link, app/services/staff_activation.py) or an
                           existing login (single-use token, accepted after a
                           normal sign-in). Only a SHA-256 of a token is kept.

NEW TABLES ONLY: create_all builds them. Every row carries organization_id.
"""
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.models import Base, gen_uuid


def _org():
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


class AgencyAccessProfile(Base):
    __tablename__ = "agency_access_profiles"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    template_key = Column(String, nullable=True)          # starter it began from
    permissions_json = Column(Text, nullable=False, default="{}")
    record_scope = Column(String, nullable=False, default="assigned")   # assigned | team | locations | agency
    dashboard = Column(String, nullable=False, default="auto")          # admin | manager | producer | retention | scheduler | analyst | auto
    aggregate_only = Column(Boolean, nullable=False, default=False)     # reports without customer identities
    is_archived = Column(Boolean, nullable=False, default=False)
    frozen_from_id = Column(String, nullable=True)        # a frozen copy kept for members when the original widened
    version = Column(Integer, nullable=False, default=1)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(String, nullable=True)
    updated_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (Index("ix_agency_access_profile_org", "organization_id", "is_archived"),)


class AgencyMemberAccess(Base):
    __tablename__ = "agency_member_access"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    profile_id = Column(String, ForeignKey("agency_access_profiles.id", ondelete="SET NULL"), nullable=True)
    grants_json = Column(Text, nullable=False, default="{}")
    denies_json = Column(Text, nullable=False, default="{}")
    record_scope = Column(String, nullable=True)          # None = the profile's
    reports_to_user_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="active")   # active | suspended | removed
    status_changed_at = Column(DateTime, nullable=True)
    prefs_json = Column(Text, nullable=True)              # the member's own preferences
    is_sample = Column(Boolean, nullable=False, default=False)  # synthetic demo staff (cannot sign in)
    updated_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_agency_member_access_user"),)


class AgencyTeam(Base):
    __tablename__ = "agency_teams"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    lead_user_id = Column(String, nullable=True)
    location_ids_json = Column(Text, nullable=False, default="[]")
    shared_queue = Column(Boolean, nullable=False, default=True)   # members may claim the team's unassigned work
    routing_eligible = Column(Boolean, nullable=False, default=True)
    is_archived = Column(Boolean, nullable=False, default=False)
    is_demo = Column(Boolean, nullable=False, default=False)
    created_by_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AgencyTeamMember(Base):
    __tablename__ = "agency_team_members"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    team_id = Column(String, ForeignKey("agency_teams.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("team_id", "user_id", name="uq_agency_team_member"),)


class AgencyInvite(Base):
    __tablename__ = "agency_invites"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    email = Column(String, nullable=False, index=True)
    full_name = Column(String, nullable=True)
    user_id = Column(String, nullable=True)               # the login it is for (created for a new login)
    kind = Column(String, nullable=False)                 # new_login | existing_login
    profile_id = Column(String, nullable=True)
    grants_json = Column(Text, nullable=False, default="{}")
    denies_json = Column(Text, nullable=False, default="{}")
    record_scope = Column(String, nullable=True)
    location_ids_json = Column(Text, nullable=False, default="[]")
    team_ids_json = Column(Text, nullable=False, default="[]")
    reports_to_user_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default="pending")   # pending | accepted | revoked
    token_hash = Column(String, nullable=True, index=True)       # existing_login acceptance token (hash only)
    expires_at = Column(DateTime, nullable=True)
    invited_by_id = Column(String, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    last_sent_at = Column(DateTime, nullable=True)
    send_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (Index("ix_agency_invite_org_status", "organization_id", "status"),)
