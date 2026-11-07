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
    # VOLT's own email identity (2026-11-06): the Microsoft 365 mailbox the
    # program sends from and staff answer from. Set by the seed; the connected
    # mailbox must BE this address (app/services/volt/mail.py).
    support_email = Column(String, nullable=True)
    sender_name = Column(String, nullable=True)
    public_origin = Column(String, nullable=True)        # e.g. https://voltmarketing.live
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
    # ── HIRING FILE (person-level; shared by every project application) ──
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    preferred_regions = Column(Text, nullable=True)
    earliest_start = Column(Date, nullable=True)            # calendar date
    availability = Column(Text, nullable=True)              # JSON {"types": [...], "notes": ""}
    work_history = Column(Text, nullable=True)              # JSON list of roles
    no_experience = Column(Boolean, nullable=True)          # explicit "No previous work experience"
    relevant_experience = Column(Text, nullable=True)
    additional_info = Column(Text, nullable=True)
    hiring_updated_at = Column(DateTime, nullable=True)
    # ARCHIVED (2026-11-06): hidden from the default staff queues; every record
    # is kept and the person can be unarchived. NEVER an access decision -
    # sign-in is governed by volt_access alone.
    archived_at = Column(DateTime, nullable=True)
    archived_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltTalentSignup(Base):
    """Get Future Opportunities - an interest, NOT an applicant.

    Kept apart from volt_reps / volt_applications on purpose: a signup never
    creates an account, a rep ID or an application, so it can never inflate
    application or hiring counts. One row per (tenant, email): a repeat
    updates it. `notify_opt_in`, once given, stays on until an unsubscribe.
    """
    __tablename__ = "volt_talent_signups"
    __table_args__ = (UniqueConstraint("organization_id", "email", name="uq_volt_talent_email"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(String, nullable=False)                  # lower-case
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    interests = Column(Text, nullable=True)                 # JSON list of project keys or "any"
    notify_opt_in = Column(Boolean, nullable=False, default=False)
    notify_opt_in_at = Column(DateTime, nullable=True)
    unsubscribed_at = Column(DateTime, nullable=True)
    source = Column(String, nullable=False, default="portal")
    referrer_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="SET NULL"), nullable=True)
    referral_source = Column(String, nullable=True)         # invite | search
    submit_count = Column(Integer, nullable=False, default=1)
    is_test = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltApplication(Base):
    __tablename__ = "volt_applications"
    __table_args__ = (UniqueConstraint("rep_serial", "project_id", name="uq_volt_application_once"),
                      Index("ix_volt_app_org_status", "organization_id", "status"))

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    rep_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(String, ForeignKey("volt_projects.id", ondelete="CASCADE"), nullable=False, index=True)
    # draft | submitted | needs_update | approved | started | withdrawn
    status = Column(String, nullable=False, default="draft")
    # Withdrawn/closed by staff: the stage it was closed from, so reopening
    # returns it exactly there.
    closed_from_status = Column(String, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    # STAFF-CONTROLLED ZOOM STEP (2026-11-06). NULL: not yet. "interview" or
    # "orientation": this application may book one session of that kind for
    # its project. Scheduling is tracked beside the review status, never in it.
    schedule_stage = Column(String, nullable=True)
    schedule_enabled_at = Column(DateTime, nullable=True)
    schedule_enabled_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
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
    cancel_reason = Column(Text, nullable=True)
    # SELF-BOOKING (2026-11-06). capacity NULL = an assigned-only session (the
    # first lane); a number = applicants made eligible by staff may book it,
    # up to that many active bookings, while booking_open is true and before
    # the cutoff. Capacity is enforced in the database transaction that books.
    capacity = Column(Integer, nullable=True)
    booking_open = Column(Boolean, nullable=False, default=False)
    booking_cutoff_minutes = Column(Integer, nullable=True)
    rescheduled_at = Column(DateTime, nullable=True)
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
    # assigned (staff put them here) | booked (the applicant chose it)
    source = Column(String, nullable=True)
    application_id = Column(String, ForeignKey("volt_applications.id", ondelete="SET NULL"), nullable=True, index=True)
    # active | canceled. A canceled booking keeps its row (history) and frees
    # its seat; rebooking the same session reuses the row.
    booking_status = Column(String, nullable=True)
    booked_at = Column(DateTime, nullable=True)
    canceled_at = Column(DateTime, nullable=True)
    cancel_reason = Column(Text, nullable=True)
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


class VoltEmail(Base):
    """One transactional email VOLT sends from its support mailbox.

    `dedupe_key` is unique per organization: a retried request, a status saved
    twice or a second poller run finds the existing row and sends nothing.
    A credential-bearing message (password reset) keeps the envelope only -
    `body_html` stays NULL so the link is never stored, and it is never retried.
    """
    __tablename__ = "volt_emails"
    __table_args__ = (UniqueConstraint("organization_id", "dedupe_key", name="uq_volt_email_dedupe"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String, nullable=False)          # registered | receipt | needs_update | doc_replace | approved | started | password_reset
    dedupe_key = Column(String, nullable=False)
    rep_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="SET NULL"), nullable=True, index=True)
    application_id = Column(String, nullable=True)
    to_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    body_html = Column(Text, nullable=True)
    sensitive = Column(Boolean, nullable=False, default=False)
    is_test = Column(Boolean, nullable=False, default=False)
    state = Column(String, nullable=False, default="queued")   # queued | sent | failed | skipped
    attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    sent_at = Column(DateTime, nullable=True)


class VoltSupportMessage(Base):
    """A message in VOLT's support inbox: one row per email read from the
    mailbox (direction "in") or answered by staff (direction "out").

    Incoming content is untrusted. Only the plain-text body Microsoft renders
    is kept, it is shown as text (never HTML), and attachments are listed by
    name and size and handed out only as downloads to VOLT staff.
    """
    __tablename__ = "volt_support_messages"
    __table_args__ = (UniqueConstraint("mailbox_id", "graph_message_id", name="uq_volt_support_graph_id"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    mailbox_id = Column(String, nullable=False, index=True)
    graph_message_id = Column(String, nullable=False)
    internet_message_id = Column(String, nullable=True)
    conversation_id = Column(String, nullable=True, index=True)
    direction = Column(String, nullable=False, default="in")    # in | out
    from_address = Column(String, nullable=True)
    from_name = Column(String, nullable=True)
    to_addresses = Column(Text, nullable=True)
    subject = Column(String, nullable=True)
    body_text = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True)
    rep_serial = Column(Integer, ForeignKey("volt_reps.serial", ondelete="SET NULL"), nullable=True, index=True)
    attachments = Column(Text, nullable=True)       # JSON [{id, name, size, content_type}]
    kind = Column(String, nullable=False, default="message")    # message | bounce | auto_reply
    staff_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    handled_at = Column(DateTime, nullable=True)
    handled_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltAccess(Base):
    """VOLT ACCESS STATE FOR ONE LOGIN IN ONE VOLT TENANT (2026-11-06).

    No row = active. `suspended` and `removed` refuse every VOLT request -
    applicant and staff - and VOLT sign-in, whatever token is presented.
    `sessions_before`: any session created at or before this instant is not
    accepted by VOLT again, so restoring access never revives an old session;
    the person signs in fresh. It is scoped to VOLT: a shared platform login
    keeps its other workspaces and its other sessions.

    `removed_role`: the VOLT staff membership role a removal deactivated, so a
    restore can put back exactly that and nothing more.
    """
    __tablename__ = "volt_access"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_volt_access_user"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    state = Column(String, nullable=False, default="active")          # active | suspended | removed
    sessions_before = Column(DateTime, nullable=True)
    removed_role = Column(String, nullable=True)
    reason = Column(Text, nullable=True)
    changed_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
