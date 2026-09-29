"""Human work records on a lead: notes, tasks, reply review/assignment state.
Owned by WS5 (overnight build 2026-09-28).

THREE NEW TABLES, NOTHING EXISTING CHANGED.

  lead_notes    A person's note on a lead. `Lead.notes` stays exactly what it
                was (one free-text field edited from the lead page); these are
                authored, timestamped entries, so "who wrote what, when" has an
                answer. `kind` separates an ordinary note from an INTERNAL one
                written from the Communications composer - neither is ever sent
                to the customer; the word "internal" is about intent, not a
                privacy flag on top of something that is otherwise shared.

  lead_tasks    A follow-up somebody owes. `lead_id` is nullable so a task can
                exist before there is a lead (e.g. "call back unknown number")
                but every row is still organization-scoped. `source` records
                where it was created ("reply", "manual", ...) and `reply_id`
                which reply, when one did.

  reply_states  Per-reply triage state that the `replies` table has no column
                for: who it is assigned to, who reviewed it, and a human status
                (new / needs_attention / callback / reviewed / closed). A SIDE
                TABLE, deliberately: `replies` is written by the Twilio inbound
                webhook and read by a dozen screens, and none of them needs to
                change for this. `Reply.reviewed_at` remains the authority for
                "reviewed" - the review action writes both, so the existing
                /sms/replies/counts and the new summary agree.

TENANT RULE: every row carries organization_id (NOT NULL) and every query in
app/routers/work_router.py filters on the acting workspace org first.
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid

NOTE_KINDS = ("note", "internal")
TASK_STATUSES = ("open", "done", "cancelled")
REPLY_STATUSES = ("new", "needs_attention", "callback", "reviewed", "closed")


class LeadNote(Base):
    __tablename__ = "lead_notes"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    body = Column(Text, nullable=False)
    kind = Column(String, nullable=False, default="note")      # note | internal
    pinned = Column(Boolean, nullable=False, default=False)
    reply_id = Column(String, nullable=True)                    # the reply it was written against, if any
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_lead_notes_org_lead", "organization_id", "lead_id"),
    )


class LeadTask(Base):
    __tablename__ = "lead_tasks"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String, nullable=False)
    details = Column(Text, nullable=True)
    due_at = Column(DateTime, nullable=True)
    status = Column(String, nullable=False, default="open")    # open | done | cancelled
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    source = Column(String, nullable=True)                      # manual | reply | callback
    reply_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_lead_tasks_org_status_due", "organization_id", "status", "due_at"),
    )


class ReplyState(Base):
    __tablename__ = "reply_states"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    reply_id = Column(String, ForeignKey("replies.id", ondelete="CASCADE"), nullable=False, unique=True)
    status = Column(String, nullable=False, default="new")
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
