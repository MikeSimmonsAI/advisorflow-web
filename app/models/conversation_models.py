"""CONVERSATION MEMORY - what a conversation has established, with where it came from.

WHY TWO TABLES
--------------
`conversation_memory_items` is the durable, item-by-item memory: a fact the
customer stated, a question they asked, an objection, a promise we made, a
"call me Friday". Every row carries its PROVENANCE (which message, which
channel, the customer's own words) and its CERTAINTY:

    fact       - stated by the customer, or trusted system data
    inference  - a reasonable reading of what was said; never shown or used as fact

A correction never edits a row. "Call me Friday" then "actually Monday" leaves
two rows: Friday becomes `superseded` (and points at Monday), Monday is
`active`. History keeps Friday; the operational truth is Monday; there is never
more than one ACTIVE row for a single-valued key.

`conversation_states` is one row per (organization, lead): the rolled-up
current picture - conversation state, who is driving (AI or a human), health,
priority, the incremental-processing cursor and the last meaningful event. It
is derived from the memory and the message history and can be rebuilt from
them; it exists so lists and queues do not recompute every thread.

TENANT SCOPE. Both tables carry organization_id and every query in
app/services/conversation_intel filters on it together with the lead id. A lead
id alone is never enough to read memory.
"""
from datetime import datetime
import uuid

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)

from app.models.models import Base


def _uuid():
    return str(uuid.uuid4())


# certainty
FACT = "fact"
INFERENCE = "inference"

# status
ACTIVE = "active"
SUPERSEDED = "superseded"
RESOLVED = "resolved"          # an objection overcome, a question answered, a promise kept

# categories
CAT_FACT = "known_fact"
CAT_INFERENCE = "inference"
CAT_QUESTION = "customer_question"
CAT_OBJECTION = "objection"
CAT_COMMITMENT = "commitment"
CAT_PREFERENCE = "preference"
CAT_FOLLOW_UP = "follow_up_request"
CAT_INTENT = "current_intent"
CATEGORIES = (CAT_FACT, CAT_INFERENCE, CAT_QUESTION, CAT_OBJECTION, CAT_COMMITMENT,
              CAT_PREFERENCE, CAT_FOLLOW_UP, CAT_INTENT)

# conversation modes - who may speak
MODE_AI_ACTIVE = "ai_active"
MODE_HUMAN_ACTIVE = "human_active"
MODE_AI_PAUSED = "ai_paused"
MODE_WAITING_CUSTOMER = "waiting_on_customer"
MODE_WAITING_HUMAN = "waiting_on_human"


class ConversationMemoryItem(Base):
    __tablename__ = "conversation_memory_items"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)

    category = Column(String(40), nullable=False)
    key = Column(String(120), nullable=False)          # e.g. household.children, follow_up.when
    value = Column(Text, nullable=True)                # human-readable value
    value_date = Column(DateTime, nullable=True)       # for follow-ups: the workspace-local day (date at 00:00)
    certainty = Column(String(20), nullable=False, default=FACT)
    status = Column(String(20), nullable=False, default=ACTIVE)

    # provenance - never optional for anything derived from a message
    source_type = Column(String(30), nullable=True)    # reply | message | email | note | form | system
    source_id = Column(String, nullable=True)
    source_channel = Column(String(20), nullable=True) # sms | email | web | note | phone | chat
    source_quote = Column(String(400), nullable=True)  # the customer's own words
    observed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    superseded_by_id = Column(String, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_source_id = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_conv_mem_lead", "organization_id", "lead_id", "status"),
        Index("ix_conv_mem_key", "organization_id", "key", "status"),
        # one derivation per (lead, source message, key): reprocessing a message is a no-op
        UniqueConstraint("lead_id", "source_type", "source_id", "key", name="uq_conv_mem_source_key"),
    )


class ConversationState(Base):
    __tablename__ = "conversation_states"

    id = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)

    state = Column(String(30), nullable=False, default="new")
    mode = Column(String(30), nullable=False, default=MODE_AI_ACTIVE)
    mode_set_by_user_id = Column(String, nullable=True)
    mode_set_at = Column(DateTime, nullable=True)
    mode_reason = Column(String(300), nullable=True)
    health = Column(String(20), nullable=False, default="healthy")
    priority = Column(String(30), nullable=False, default="low_priority")
    current_intent = Column(String(60), nullable=True)
    needs_human_reason = Column(String(300), nullable=True)

    last_inbound_at = Column(DateTime, nullable=True)
    last_outbound_at = Column(DateTime, nullable=True)
    last_meaningful_event = Column(String(300), nullable=True)
    last_meaningful_at = Column(DateTime, nullable=True)
    processed_through = Column(DateTime, nullable=True)   # incremental cursor
    events_processed = Column(Integer, nullable=False, default=0)
    has_open_question = Column(Boolean, nullable=False, default=False)
    follow_up_due_at = Column(DateTime, nullable=True)
    summary_json = Column(Text, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("organization_id", "lead_id", name="uq_conv_state_lead"),
        Index("ix_conv_state_queue", "organization_id", "priority", "updated_at"),
    )
