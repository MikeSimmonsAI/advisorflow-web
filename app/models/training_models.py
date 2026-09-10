"""TRAINING AND READINESS — two tables, and deliberately not a learning system.

WHAT PROBLEM THIS SOLVES

Mike is the only person who can teach anybody to use the platform. Every new
executive, sales manager, rep and implementation engineer learns it by being
walked through it personally, which means the company can onboard exactly as
many people as he has afternoons.

WHAT IT IS NOT

It is not an LMS. There is no course authoring, no quiz engine, no scoring, no
certificates, no media library, no SCORM. Those are the parts of an LMS that
take a year to build and answer a question nobody here is asking.

WHAT IT IS

An ASSIGNMENT and a POSITION. God says "Christina should be able to run a
product demo"; the platform remembers that, shows her the path, remembers how
far she got, and answers "is she ready" without anybody having to ask her.

    TrainingAssignment      this person should complete this path
    TrainingStepProgress    this person has completed this step of it

The CONTENT is code, not rows — `app/services/training_catalog.py`. That is a
deliberate trade. Content in the database means an editor, a migration story,
a draft state and a way for a path to reference a screen that no longer exists.
Content in code means a training step that names a screen is checked by the
same review that would catch the screen being renamed, and adding a path is a
pull request rather than an evening of data entry. If somebody later needs to
edit a path without a deploy, these two tables do not change — only the
catalogue's source does.

WHY PROGRESS IS PER STEP AND NOT A PERCENTAGE

Because "70% complete" cannot be resumed. A person coming back after a week
needs to be put back where they were, and a manager asking whether the team is
ready needs to know WHICH part is unfinished — "everyone stalls on the
objection-handling step" is a finding; "the team averages 62%" is not.
"""

from datetime import datetime
import uuid

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, String,
                        Text, UniqueConstraint)

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# The three states, and there are only three on purpose. "Overdue" is derived
# from `due_at` at read time rather than stored, because a stored overdue flag
# is a flag somebody has to remember to set.
TRAINING_NOT_STARTED = "not_started"
TRAINING_IN_PROGRESS = "in_progress"
TRAINING_COMPLETE = "complete"
TRAINING_STATUSES = (TRAINING_NOT_STARTED, TRAINING_IN_PROGRESS,
                     TRAINING_COMPLETE)


class TrainingAssignment(Base):
    """One person has been asked to complete one path.

    UNIQUE ON (user, path). Re-assigning a path somebody already holds
    REACTIVATES the existing row rather than writing a second one, so
    "assigned twice by two operators" is one assignment with one history
    instead of two competing progress records. That mirrors
    `workspace_access.grant_workspace_membership`, which learned the same
    lesson about invitations.

    REVOCATION DEACTIVATES. Un-assigning training somebody has already
    completed must not erase the fact that they completed it — "was Christina
    ever signed off to present?" is exactly the question a revocation makes
    people ask.
    """
    __tablename__ = "training_assignments"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    # A key in app/services/training_catalog.py. A row whose key is no longer
    # in the catalogue is inert rather than dangerous: the service drops it,
    # exactly as `capabilities.grants_for` drops an unregistered capability.
    path_key = Column(String, nullable=False)

    status = Column(String, default=TRAINING_NOT_STARTED, nullable=False)

    assigned_by = Column(String, ForeignKey("users.id"), nullable=True)
    assigned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    due_at = Column(DateTime, nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String, ForeignKey("users.id"), nullable=True)

    # Why this was assigned, in the assigner's words. Optional, and shown to
    # the person doing the training — "because you are presenting to
    # Brightwater on the 12th" is worth more than a due date on its own.
    note = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "path_key", name="uq_training_user_path"),
        Index("ix_training_assignments_status", "status", "is_active"),
    )


class TrainingStepProgress(Base):
    """One step of one assignment, marked done by the person doing it.

    Hangs off the ASSIGNMENT rather than off (user, path) directly, so that
    revoking and re-assigning a path keeps its history attached to the
    assignment it belongs to instead of silently merging two attempts.
    """
    __tablename__ = "training_step_progress"

    id = Column(String, primary_key=True, default=gen_uuid)
    assignment_id = Column(String,
                           ForeignKey("training_assignments.id",
                                      ondelete="CASCADE"),
                           nullable=False, index=True)
    step_key = Column(String, nullable=False)

    completed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Set when the step was completed by PRACTISING it in the Demo Suite
    # rather than by reading it. The distinction matters for the Demo Presenter
    # path, whose whole claim is that the person has actually driven the
    # software, not that they have read about driving it.
    practised = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("assignment_id", "step_key",
                         name="uq_training_step_assignment_step"),
    )
