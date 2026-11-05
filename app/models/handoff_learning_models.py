"""Human-assisted AI learning (SCI, Nov 4 2026).

When a person answers a family the AI handed to them, the question and the
person's answer are kept as a CANDIDATE - with the family's name, number,
email and address removed. Nothing a candidate says reaches the AI. A manager
rewrites it as a general answer and APPROVES it; only then is it ACTIVE and
offered to the AI, for similar questions, scoped to its organization and -
unless widened on purpose - its location. RETIRED / REJECTED answers are never
used. This is retrieval into the AI's context, not model retraining.

Every use is a row (`program_learning_uses`) with what the AI then did, so the
owner can see whether an approved answer kept a family from needing a person.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.models.models import Base, gen_uuid

CANDIDATE, APPROVED, ACTIVE, RETIRED, REJECTED = "candidate", "approved", "active", "retired", "rejected"
STATUSES = (CANDIDATE, APPROVED, ACTIVE, RETIRED, REJECTED)
SCOPE_LOCATION, SCOPE_PROGRAM = "location", "program"


class ProgramHandoffLearning(Base):
    __tablename__ = "program_handoff_learnings"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, nullable=False, index=True)
    # Where it came from - internal links only, never shown to the AI.
    lead_id = Column(String, nullable=True)
    conversation_id = Column(String, nullable=True)
    response_id = Column(String, nullable=True, unique=True)    # one candidate per answered message
    location_id = Column(String, nullable=True)
    campaign_family = Column(String, nullable=True)
    channel = Column(String, nullable=True)                     # sms | email
    escalation_reason = Column(String, nullable=True)
    # Redacted capture
    question = Column(Text, nullable=True)
    context = Column(Text, nullable=True)                       # JSON: a few earlier lines, redacted
    human_answer = Column(Text, nullable=True)
    outcome = Column(String, nullable=True)                     # ai_resumed | human_retained
    # The reviewed, general version the AI may use
    approved_question = Column(Text, nullable=True)
    approved_answer = Column(Text, nullable=True)
    scope = Column(String, nullable=False, default=SCOPE_LOCATION)
    status = Column(String, nullable=False, default=CANDIDATE)
    created_by_user_id = Column(String, nullable=True)
    reviewed_by_user_id = Column(String, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    activated_at = Column(DateTime, nullable=True)
    retired_at = Column(DateTime, nullable=True)
    uses = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("ix_handoff_learning_org_status", "organization_id", "status"),)


class ProgramLearningUse(Base):
    __tablename__ = "program_learning_uses"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, nullable=False, index=True)
    learning_id = Column(String, nullable=False, index=True)
    response_id = Column(String, nullable=True)
    action = Column(String, nullable=True)                      # what the AI then did
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
