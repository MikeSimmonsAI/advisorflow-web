"""Inbound reply mailboxes - the shared addresses workspaces SEND from.

2026-09-29: an Atlantis email went out From "Atlantis Light & Power
<support@evosyspro.live>" (the provider path). The lead replied; the reply sat
in the support@evosyspro.live Outlook mailbox and never reached EvoSys,
because the only inbound path polled the personal mailboxes of advisors who had
connected Microsoft 365 - and nobody polled the address the mail actually came
from.

An InboundMailbox is that sending address, connected once by a person through
Microsoft sign-in (delegated Mail.Read; we never hold a password). The poller
reads it, routes each message to the right workspace and lead, and writes one
InboundMailboxMessage per message it looked at - matched or not - so
"why didn't this reply show up?" is answered from the product, not from logs.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from app.models.models import Base


def _uuid():
    return str(uuid.uuid4())


class InboundMailbox(Base):
    __tablename__ = "inbound_mailboxes"

    id = Column(String, primary_key=True, default=_uuid)
    address = Column(String, nullable=False, unique=True)       # lower-cased mailbox address
    # NULL = a platform/brand mailbox shared by every workspace that sends from
    # it; set = one workspace's own mailbox.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=True, index=True)
    refresh_token_encrypted = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    connected_by_user_id = Column(String, nullable=True)
    connected_at = Column(DateTime, default=datetime.utcnow)

    # Poll state. The cursor is the newest receivedDateTime handled, so a
    # missed run never loses mail (the next run starts from the cursor).
    cursor_received_at = Column(DateTime, nullable=True)
    last_polled_at = Column(DateTime, nullable=True)
    last_status = Column(String, nullable=True)                 # ok | error | auth_error
    last_error = Column(Text, nullable=True)
    last_checked = Column(Integer, nullable=True)
    last_matched = Column(Integer, nullable=True)


class InboundMailboxMessage(Base):
    """Every message the poller looked at, and what it decided."""
    __tablename__ = "inbound_mailbox_messages"

    id = Column(String, primary_key=True, default=_uuid)
    mailbox_id = Column(String, ForeignKey("inbound_mailboxes.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    graph_message_id = Column(String, nullable=False)
    internet_message_id = Column(String, nullable=True)
    from_address = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    received_at = Column(DateTime, nullable=True)
    # matched | no_lead | ambiguous | own_mail | error
    outcome = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    organization_id = Column(String, nullable=True, index=True)
    lead_id = Column(String, nullable=True, index=True)
    reply_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("mailbox_id", "graph_message_id", name="uq_inbound_mailbox_message"),
        Index("ix_inbound_mailbox_msg_received", "mailbox_id", "received_at"),
    )
