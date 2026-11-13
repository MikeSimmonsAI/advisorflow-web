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


# ═══════════════════════════════ VOLT DEVELOPMENT RELAY (#571, 2026-11-09) ═══

class VoltDevJob(Base):
    """One development request for VOLT Marketing, from submission to a
    verified production deploy. `number` is the human reference (DEV-12)."""
    __tablename__ = "volt_dev_jobs"
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_volt_dev_job_number"),
                      UniqueConstraint("organization_id", "requested_by", "idempotency_key", name="uq_volt_dev_job_idem"))

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    number = Column(Integer, nullable=False)
    kind = Column(String, nullable=False, default="change")          # change | revert
    title = Column(String, nullable=False)
    request = Column(Text, nullable=False)
    requested_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    via = Column(String, nullable=False, default="console")          # console | api
    idempotency_key = Column(String, nullable=True)
    status = Column(String, nullable=False, default="queued", index=True)
    status_detail = Column(Text, nullable=True)
    attempt = Column(Integer, nullable=False, default=1)
    lease_run_id = Column(String, nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    run_url = Column(String, nullable=True)
    branch = Column(String, nullable=True)
    pr_number = Column(Integer, nullable=True)
    pr_url = Column(String, nullable=True)
    head_sha = Column(String, nullable=True)
    tests_passed = Column(Boolean, nullable=True)
    tests_summary = Column(Text, nullable=True)
    changed_files = Column(Text, nullable=True)                      # JSON list
    preview_url = Column(String, nullable=True)
    merge_requested_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    merge_requested_at = Column(DateTime, nullable=True)
    merge_approved_sha = Column(String, nullable=True)
    merge_sha = Column(String, nullable=True)
    merged_at = Column(DateTime, nullable=True)
    deploy_targets = Column(String, nullable=True)                   # "backend,frontend" | "" (none)
    deployed_at = Column(DateTime, nullable=True)
    deploy_detail = Column(Text, nullable=True)
    revert_of = Column(String, ForeignKey("volt_dev_jobs.id", ondelete="SET NULL"), nullable=True)
    # Event-driven start (2026-11-09): the last GitHub workflow_dispatch this
    # job caused. "dispatched" proves only that GitHub accepted the request;
    # "running" is proven by the runner claiming the job.
    dispatch_state = Column(String, nullable=True)          # dispatched | failed | not_configured
    dispatch_purpose = Column(String, nullable=True)        # start | merge | retry | rollback | resend
    dispatched_at = Column(DateTime, nullable=True)
    dispatch_error = Column(Text, nullable=True)
    dispatch_count = Column(Integer, nullable=False, default=0)
    # 2026-11-10: set when the relay stopped trying on its own (start requests
    # never picked up, or Claude cannot run). Cleared by "Send start request
    # again" or a retry. The status is unchanged; the stage shows "Blocked".
    blocked_reason = Column(Text, nullable=True)
    deploy_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VoltDevEvent(Base):
    """Audit trail and timeline: who/what moved a job, when."""
    __tablename__ = "volt_dev_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    job_id = Column(String, ForeignKey("volt_dev_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False)
    actor_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_kind = Column(String, nullable=False, default="user")      # user | runner | system
    action = Column(String, nullable=False)
    status = Column(String, nullable=True)
    detail = Column(Text, nullable=True)


class VoltDevToken(Base):
    """A personal access key for the ChatGPT action. Only a hash is stored;
    the key is shown once. Tied to one person and one VOLT tenant, and it is
    worthless the moment that person stops being an authorized principal."""
    __tablename__ = "volt_dev_tokens"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    prefix = Column(String, nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    # 2026-11-10: a ChatGPT sign-in ("Connect ChatGPT", OAuth) is a row here
    # too, so it shows and is revoked in the same list as a key. kind "key" is
    # a pasted personal key (vdt_), kind "oauth" an access token (vdo_) that
    # expires, renewed with its refresh token (vdr_, hash only, rotated on use).
    kind = Column(String, nullable=False, default="key")
    client_id = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    refresh_hash = Column(String, nullable=True, unique=True)
    refresh_expires_at = Column(DateTime, nullable=True)


class VoltDevOAuthClient(Base):
    """An app allowed to ask a principal to connect (ChatGPT). Registered
    dynamically (RFC 7591) or by hand; it may only send people back to an
    allowed ChatGPT callback, so a registration grants nothing by itself."""
    __tablename__ = "volt_dev_oauth_clients"

    id = Column(String, primary_key=True)                     # the client_id
    name = Column(String, nullable=False)
    secret_hash = Column(String, nullable=True)                # NULL = public client (PKCE only)
    redirect_uris = Column(Text, nullable=False)               # JSON list
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltDevOAuthGrant(Base):
    """One "Connect ChatGPT" attempt: the request ChatGPT made, the person's
    decision on the VOLT sign-in page, and the one-time code it produced."""
    __tablename__ = "volt_dev_oauth_grants"

    id = Column(String, primary_key=True)                     # opaque request id in the consent URL
    client_id = Column(String, nullable=False, index=True)
    redirect_uri = Column(Text, nullable=False)
    state = Column(Text, nullable=True)
    code_challenge = Column(String, nullable=True)
    scope = Column(String, nullable=True)
    resource = Column(String, nullable=True)                   # RFC 8707 resource the token is for
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    decision = Column(String, nullable=True)                   # allowed | denied
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    organization_id = Column(String, nullable=True)
    code_hash = Column(String, nullable=True, unique=True)
    code_expires_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)


class VoltDevRunnerSeen(Base):
    """What the relay last heard from its GitHub runner - one row. Lets the
    backend say, without a job and without reading GitHub logs, whether the
    runner works, how it was started, and whether a Claude credential exists
    in the repository's secrets (presence only, never the value)."""
    __tablename__ = "volt_dev_runner_seen"

    id = Column(String, primary_key=True, default="runner")
    last_seen_at = Column(DateTime, nullable=True)
    run_id = Column(String, nullable=True)
    event = Column(String, nullable=True)                      # schedule | workflow_dispatch
    reason = Column(String, nullable=True)                     # the dispatch reason the run reported
    claude_credentials = Column(Boolean, nullable=True)
    token_check_at = Column(DateTime, nullable=True)
    token_check = Column(Text, nullable=True)                  # JSON: the backend's own GitHub token check
    selftest_dispatch_at = Column(DateTime, nullable=True)


class VoltDevNonce(Base):
    """GitHub Actions identity tokens already used (replay protection)."""
    __tablename__ = "volt_dev_nonces"

    jti = Column(String, primary_key=True)
    used_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class VoltApplicantAccess(Base):
    __tablename__ = "volt_applicant_access"

    organization_id = Column(String, ForeignKey("volt_programs.organization_id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    active = Column(Boolean, nullable=False, default=True)
    view_fields = Column(Text, nullable=False)
    export_fields = Column(Text, nullable=False)


class VoltExportPreset(Base):
    __tablename__ = "volt_export_presets"

    organization_id = Column(String, ForeignKey("volt_programs.organization_id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    name = Column(String(80), primary_key=True)
    columns = Column(Text, nullable=False)
