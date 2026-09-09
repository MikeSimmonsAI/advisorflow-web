"""
Launch Engine intake — what the CUSTOMER fills in, attached to the
implementation that already exists.

===========================================================================
WHY THESE TABLES AND NOT A "LAUNCH" TABLE
===========================================================================

There is already a record of a customer's implementation: `Implementation`
(app/models/implementation_models.py). It carries organization_id UNIQUE,
platform_id, owner_user_id, status across ten stages, target_launch_date,
kickoff/launched timestamps and the milestone rows. A second table with a
status column would create a second answer to "how far along is this
customer", and the two would disagree within a month.

So the Launch Engine does NOT own a launch record. It owns the one thing
Implementation genuinely has nowhere to put: the customer's ANSWERS.

  Implementation             the project        (exists — reused)
  ImplementationMilestone    the checklist      (exists — reused)
  LaunchIntakeStep           the answers        (new — this file)
  LaunchIntakeFile           the documents      (new — this file)
  LaunchIntakeSubmission     the sign-off       (new — this file)

===========================================================================
WHY organization_id IS DENORMALISED ONTO ALL THREE
===========================================================================

Every one of these rows is reachable only through an implementation, so
organization_id is derivable by a join and is stored anyway. That is
deliberate. Tenant isolation is the single most important property of these
tables, and a filter you can forget is a filter that will eventually be
forgotten — one query written without the join, one `.first()` on an id that
came from a URL, and a customer reads another customer's contract terms.

With the column present, every read and write is `WHERE organization_id = ?`
and the isolation is visible in the query rather than inferred from the join
path. It is written on insert from the implementation, never from the
request, so it cannot be spoofed by a body field.
"""

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, LargeBinary,
    String, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


# ── step completion vocabulary ──────────────────────────────────────────────
# Mirrors the milestone vocabulary's spirit but is about ANSWERS, not work.
STEP_NOT_STARTED = "not_started"
STEP_IN_PROGRESS = "in_progress"
STEP_COMPLETE    = "complete"

STEP_STATUSES = (STEP_NOT_STARTED, STEP_IN_PROGRESS, STEP_COMPLETE)


class LaunchIntakeStep(Base):
    """One customer's answers for one step of the intake.

    A ROW PER STEP, not a column per question. The intake has ~70 fields today
    and will have more the first time a brand asks for something new; a schema
    that needs a migration to ask a question is a schema that stops being asked
    questions. The step schema lives in code (services/launch_intake.py) and
    the answers live here as JSON.

    `answers` NEVER holds a secret. Credential fields (hosting password,
    registrar password) are Fernet-encrypted into `secrets_encrypted` and are
    write-only — no read path in this application returns them.
    """

    __tablename__ = "implementation_intake_steps"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    # See module docstring — written from the implementation, never the request.
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    step_key = Column(String(48), nullable=False)

    # Non-secret answers, exactly as the step schema defines them.
    answers = Column(JSON, nullable=True)

    # {field_key: fernet_ciphertext}. Write-only by design.
    secrets_encrypted = Column(JSON, nullable=True)

    status = Column(String(16), default=STEP_NOT_STARTED, nullable=False)
    # Cached so a list view does not have to re-run validation per row. The
    # service recomputes and rewrites it on every save — it is a cache, never
    # a source of truth, and nothing reads it to make an access decision.
    completion_pct = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now(), nullable=False)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        # One answers row per step. Makes save idempotent at the DB level:
        # a double-submitted form updates, it does not duplicate.
        UniqueConstraint("implementation_id", "step_key",
                         name="uq_intake_step_key"),
        Index("ix_intake_step_org", "organization_id", "step_key"),
    )


class LaunchIntakeFile(Base):
    """A document the customer uploaded during intake.

    STORED AS A DATABASE BLOB, ON PURPOSE. `mobile_storage.backend()` reports
    `ephemeral` on this deployment because no S3 bucket is configured, and its
    upload path refuses with 503 rather than accept a file that will not
    survive the next restart. That refusal is right for a phone that can keep
    the photo and retry; it is wrong here, because the customer is being asked
    to hand over the documents the build depends on and "try again later"
    is not an answer to that.

    The platform already solves this exact problem twice — ProposalFile and
    ExecWorkspaceFile both hold their bytes in the database — so this follows
    the pattern that actually works today rather than inventing storage. When
    MEDIA_STORAGE_BACKEND=s3 is configured, `file_data` becomes the fallback
    and `storage_key` carries the object key.
    """

    __tablename__ = "implementation_intake_files"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    # Which step asked for it. Free-form label the customer chose, separate.
    step_key = Column(String(48), nullable=True)
    label = Column(String(160), nullable=True)

    filename = Column(String(255), nullable=False)
    content_type = Column(String(128), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_data = Column(LargeBinary, nullable=True)
    storage_key = Column(String(512), nullable=True)   # set when S3 is live

    uploaded_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # SOFT DELETE. A customer removing a file must not erase the fact that it
    # was once provided — an implementation argument six months later turns on
    # exactly that. The bytes are dropped; the record of the act is kept.
    deleted_at = Column(DateTime, nullable=True)
    deleted_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_intake_file_org", "organization_id", "implementation_id"),
    )


class LaunchIntakeSubmission(Base):
    """An append-only record of the customer pressing Submit.

    SNAPSHOT, NOT A POINTER. The live answers keep changing after submission —
    the implementation team asks for a correction, the customer edits a phone
    number — and a submission that pointed at the live rows would silently
    rewrite what was signed off. What was handed over is a fact about a moment,
    so it is copied, not referenced.

    Secrets are NOT copied into the snapshot. A signed-off record that
    duplicates credentials is a second place they have to be protected.
    """

    __tablename__ = "implementation_intake_submissions"

    id = Column(String, primary_key=True, default=gen_uuid)

    implementation_id = Column(
        String, ForeignKey("implementations.id", ondelete="CASCADE"),
        nullable=False, index=True)
    organization_id = Column(
        String, ForeignKey("organizations.id"), nullable=False, index=True)

    # {step_key: {field: value}} — non-secret answers only.
    snapshot = Column(JSON, nullable=True)
    # {step_key: pct} plus overall, as it stood at submission.
    completion = Column(JSON, nullable=True)
    file_count = Column(Integer, default=0, nullable=False)

    # Typed sign-off from the Review step.
    signed_name = Column(String(160), nullable=True)
    signed_title = Column(String(160), nullable=True)
    signed_company = Column(String(200), nullable=True)

    submitted_at = Column(DateTime, server_default=func.now(), nullable=False)
    submitted_by = Column(String, ForeignKey("users.id"), nullable=True)

    # Staff acknowledgement, so "submitted" and "someone has looked at it" are
    # different facts rather than one hopeful one.
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by = Column(String, ForeignKey("users.id"), nullable=True)
    review_note = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_intake_submission_org", "organization_id", "submitted_at"),
    )
