"""THE AUTHORITATIVE RECORD OF EVERY PROVIDER TRANSACTION.

===========================================================================
WHY THIS TABLE EXISTS
===========================================================================

An enrollment submitted to an energy retailer is an obligation created for a
real person: their electricity supplier changes, contract documents are
generated, and the provider emails and texts them. The platform's own lead
and pipeline rows describe OUR view of the relationship. They are the wrong
place to keep the provider's, for three reasons:

  1. A provider reference number is an external primary key. It must survive
     every pipeline transition, every merge, every reassignment. Put it in a
     status field and the first stage change loses it, permanently — this
     API has no lookup that would let us find it again.

  2. The provider's verdict and ours must be separately visible. `Status` and
     `Message` are the provider's sentences; `normalized_status` is our
     summary of them. When the two disagree the raw pair is what a human
     needs, and a schema that stores only the summary has thrown away the
     evidence.

  3. An attempt is a fact whether it worked or not. A refused enrollment, a
     validation failure and a transport error are all things that happened
     and all things somebody will ask about.

Nothing here is Chariot-specific. `provider` is a column.

===========================================================================
WHAT THIS TABLE MUST NEVER CONTAIN
===========================================================================

No SSN. No driver's licence. No date of birth. No card number, expiry or
CVC. No bank routing or account number. No security question or answer. No
password.

That is not a convention. `provider_transactions.record()` runs
`base.assert_no_sensitive` over everything written to the JSON columns and
RAISES — the write fails rather than succeeding with a redaction nobody
notices. The enrollment path holds that data in memory for the length of one
outbound request and never hands it to this module at all.
"""

from sqlalchemy import (Column, String, Text, DateTime, Integer, Boolean,
                        ForeignKey, Index)
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


class ProviderTransaction(Base):
    """One attempt to make a provider do something, and what it said back."""

    __tablename__ = "provider_transactions"

    id = Column(String, primary_key=True, default=gen_uuid)

    # ── WHOSE, AND ABOUT WHOM ───────────────────────────────────────────
    # organization_id is not nullable and is never widened by a caller: every
    # read goes through the same authorized scope the rest of the workspace
    # uses, so one customer's provider history is unreachable from another's.
    organization_id = Column(String, ForeignKey("organizations.id"),
                             nullable=False, index=True)
    # Nullable on purpose: a plan lookup for an anonymous website visitor is
    # a real provider call with no lead attached yet.
    lead_id = Column(String, ForeignKey("leads.id"), nullable=True, index=True)
    # Who asked. Null for automation.
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)

    # ── WHAT WAS ATTEMPTED ──────────────────────────────────────────────
    provider = Column(String, nullable=False)          # "chariot"
    operation = Column(String, nullable=False)         # providers.base.OP_*
    environment = Column(String, nullable=True)        # test | production

    # ── THE SUBJECT OF THE TRANSACTION ──────────────────────────────────
    product_id = Column(String, nullable=True)         # Chariot "ProductId"
    esiid = Column(String, nullable=True, index=True)  # the meter
    utility_id = Column(String, nullable=True)
    service_type = Column(String, nullable=True)       # Moving | Switching
    service_start_date = Column(String, nullable=True) # as sent, unparsed

    # ── THE EXTERNAL PRIMARY KEY ────────────────────────────────────────
    # Chariot spells it "RefrenceNumber". Indexed because reconciling a batch
    # by reference is the reason a human will ever open this table.
    provider_reference = Column(String, nullable=True, index=True)

    # ── THE VERDICT, BOTH WAYS ──────────────────────────────────────────
    raw_status = Column(Text, nullable=True)      # provider's own Status
    raw_message = Column(Text, nullable=True)     # provider's own Message
    normalized_status = Column(String, nullable=False, index=True)
    reason_code = Column(String, nullable=True)   # providers.base.REASON_*
    amount_quoted = Column(String, nullable=True) # "$632.00", as written
    http_status = Column(Integer, nullable=True)

    # ── OPERATIONAL STATE ───────────────────────────────────────────────
    # An unresolved transaction is one a human still owes an answer on. It is
    # set from the normalized status and cleared only by a person or by a
    # later provider answer — never by elapsed time.
    needs_human = Column(Boolean, default=False, nullable=False, index=True)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    resolution_note = Column(Text, nullable=True)
    attempt_count = Column(Integer, default=1, nullable=False)

    # ── AUDIT PAYLOADS, REDACTED BEFORE THEY GET HERE ───────────────────
    # request_echo: the outbound fields with every sensitive value replaced
    # and the API key removed entirely.
    # response_body: the provider's parsed answer, redacted.
    # plan_snapshot: product and terms as displayed at selection. No person.
    request_echo = Column(Text, nullable=True)
    response_body = Column(Text, nullable=True)
    plan_snapshot = Column(Text, nullable=True)

    # ── TIME ────────────────────────────────────────────────────────────
    # submitted_at is when WE sent it. last_provider_update_at is when the
    # provider last told us anything about it — the same on first write, and
    # different the moment a reconciliation or a future status endpoint
    # updates the row. Keeping them apart is what makes "pending for eleven
    # days" answerable.
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_provider_update_at = Column(DateTime, default=datetime.utcnow,
                                     nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow)


Index("ix_provider_tx_org_status", ProviderTransaction.organization_id,
      ProviderTransaction.normalized_status)
Index("ix_provider_tx_open", ProviderTransaction.organization_id,
      ProviderTransaction.needs_human, ProviderTransaction.submitted_at)
