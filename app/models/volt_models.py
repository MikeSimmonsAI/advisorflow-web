"""VOLT Marketing - the recruiting program (Issue #535).

One VOLT program belongs to exactly one organization (the tenant). Everything
below carries that organization's id and every query filters on it, so a second
recruiting tenant can never read the first one's people.

WHAT LIVES WHERE
----------------
    volt_programs           the tenant's public identity (slug for /volt/<slug>)
                            and its profile-level document requirements
    volt_projects           ACA, Fiber, Midterms ... status open/closed is DATA,
                            maintained by staff without a deploy
    volt_reps               ONE PERSON. `serial` is the permanent rep ID: an
                            autoincrement primary key, so the database - not the
                            application - guarantees two concurrent registrations
                            never share one. It survives an email change because
                            it is not derived from the email.
    volt_applications       ONE PERSON x ONE PROJECT. Unique on (rep, project):
                            a double click, a retry or two tabs cannot make two.
    volt_documents          every uploaded version is kept; review status per
                            version, so "which version was reviewed" is answerable
    volt_sessions /         Zoom interviews and orientations with staff-supplied
    volt_session_attendees  participant join links
    volt_audit_events       who changed what, from what, to what, and why

REFERRAL FACTS ARE TWO FACTS
----------------------------
`volt_reps.original_recruiter_serial` - who first recruited this person. Set
once, at registration, and only from a confirmed selection. Never written by a
later application.
`volt_applications.referrer_serial` - who referred them to THIS project.
Both hold the recruiter's stable database id, never a typed name.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)

from app.models.models import Base, gen_uuid


class VoltProgram(Base):
    __tablename__ = "volt_programs"

    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    slug = Column(String, unique=True, nullable=False)
    name = Column(String, nullable=False, default="VOLT Marketing")
    # JSON list of {"key", "label"} every applicant's profile drawer requests.
    profile_required_docs = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltProject(Base):
    __tablename__ = "volt_projects"
    __table_args__ = (UniqueConstraint("organization_id", "key", name="uq_volt_project_key"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String, nullable=False)
    name = Column(String, nullable=False)          # card title
    short_name = Column(String, nullable=True)     # tables, filters
    summary = Column(String, nullable=True)
    icon = Column(String, nullable=True)
    question = Column(String, nullable=True)       # the one project question
    status = Column(String, nullable=False, default="open")   # open | closed
    sort_order = Column(Integer, nullable=False, default=0)
    # JSON list of {"key", "label"} this project's applicants must upload.
    required_docs = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltRep(Base):
    __tablename__ = "volt_reps"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_volt_rep_user"),)

    # THE PERMANENT REP ID. Database-assigned; shown as REP-<serial>.
    serial = Column(Integer, primary_key=True, autoincrement=True)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    # A calendar date, never a timestamp: no time zone can move a birthday.
    date_of_birth = Column(Date, nullable=True)
    # Lower-case, apostrophes removed, single spaces: "dangelo simmons".
    search_name = Column(String, nullable=False, index=True)
    # In the recruiter directory? Staff can switch a rep out of it.
    recruiter_active = Column(Boolean, nullable=False, default=True)
    original_recruiter_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="SET NULL"), nullable=True)
    original_recruited_at = Column(DateTime, nullable=True)
    # Synthetic QA record: never counted in reports unless asked for.
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltApplication(Base):
    __tablename__ = "volt_applications"
    __table_args__ = (UniqueConstraint("rep_serial", "project_id", name="uq_volt_application_once"),
                      Index("ix_volt_app_org_status", "organization_id", "status"))

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    rep_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("volt_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # draft | submitted | needs_update | approved | started
    status = Column(String, nullable=False, default="draft")
    answers = Column(Text, nullable=True)                         # JSON
    referrer_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="SET NULL"), nullable=True, index=True)
    referral_source = Column(String, nullable=True)                # search | invite
    referral_confirmed_at = Column(DateTime, nullable=True)
    return_reason = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    approved_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltDocument(Base):
    __tablename__ = "volt_documents"
    __table_args__ = (UniqueConstraint("rep_serial", "upload_key", name="uq_volt_doc_upload_key"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    rep_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="CASCADE"), nullable=False, index=True)
    # NULL = the profile drawer; otherwise that project application's drawer.
    application_id = Column(String, ForeignKey("volt_applications.id", ondelete="CASCADE"), nullable=True, index=True)
    doc_key = Column(String, nullable=False)
    label = Column(String, nullable=True)
    version = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default="under_review")   # under_review | accepted | replace
    reason = Column(Text, nullable=True)
    storage_backend = Column(String, nullable=False)
    storage_key = Column(String, nullable=False)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String, nullable=False)
    # Client-generated per attempt: a retried upload returns the first row.
    upload_key = Column(String, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    reviewed_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)


class VoltSession(Base):
    __tablename__ = "volt_sessions"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("volt_projects.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False)                  # interview | orientation
    title = Column(String, nullable=True)
    starts_at = Column(DateTime, nullable=False)           # UTC
    timezone = Column(String, nullable=False)              # IANA, e.g. America/Chicago
    duration_minutes = Column(Integer, nullable=False, default=30)
    # Participant join link the staff member created in Zoom. Never a start link.
    join_url = Column(String, nullable=False)
    link_method = Column(String, nullable=False, default="staff_supplied_link")
    instructions = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="scheduled")   # scheduled | canceled
    canceled_at = Column(DateTime, nullable=True)
    created_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltSessionAttendee(Base):
    __tablename__ = "volt_session_attendees"
    __table_args__ = (UniqueConstraint("session_id", "rep_serial", name="uq_volt_session_rep"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    session_id = Column(String, ForeignKey("volt_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    rep_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="CASCADE"), nullable=False, index=True)
    attendance = Column(String, nullable=False, default="invited")    # invited | attended | no_show
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltAuditEvent(Base):
    __tablename__ = "volt_audit_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False, index=True)
    action = Column(String, nullable=False)
    field = Column(String, nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
