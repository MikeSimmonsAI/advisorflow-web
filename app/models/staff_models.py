"""Access activation for CONTROL-PLANE identities: brand sales, god, staff.

WHY THIS IS NOT `CustomerActivation`.
-------------------------------------
`customer_activations.organization_id` is NOT NULL. That is correct for what it
is - an invitation into a specific customer tenant, and a tenant-scoped
invitation that could exist without a tenant would be a bug.

A brand-sales user has `organization_id = NULL`, and that NULL is a positive
architectural assertion: they belong to the selling side and to no customer
tenant. So the customer table structurally cannot represent them. Reusing it
would mean either relaxing its NOT NULL - weakening a real guarantee for every
customer invitation ever issued - or inventing a placeholder organisation to
point at, which is exactly the "automatic tenancy inference" the architecture
forbids.

Two tables, because there are genuinely two things.

WHAT THIS TABLE DELIBERATELY DOES NOT HAVE
------------------------------------------
No `organization_id`. Not nullable, not optional - absent. A row here cannot
express tenant membership even by accident, which is the strongest available
guarantee that activating a sales login can never place somebody inside a
customer's data.

`brand_sales_org_id` records which brand the link was issued in the context of,
for audit and for the authority check. It grants nothing. Access comes from
`memberships`, and activation never touches those rows.
"""

from sqlalchemy import Column, String, DateTime, Integer, ForeignKey, Index
from datetime import datetime
import uuid

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


# Why the link was issued. Both follow the identical secure path; the
# distinction exists so the audit trail can say which one a human asked for.
PURPOSE_SETUP = "setup"    # first-time access, has never signed in
PURPOSE_RESET = "reset"    # lost or rotating access, has signed in before
PURPOSES = (PURPOSE_SETUP, PURPOSE_RESET)

STAFF_INVITE_PENDING = "pending"
STAFF_INVITE_ACCEPTED = "accepted"
STAFF_INVITE_REVOKED = "revoked"
STAFF_INVITE_EXPIRED = "expired"


class StaffActivation(Base):
    """A one-time link letting a control-plane user set their own password.

    ONLY A HASH IS STORED. The token is shown once, to the operator who
    generated it, and exists nowhere afterwards - the same discipline as the
    integration keys and the customer activations. A lost link is replaced by
    issuing a new one, which revokes the old; it is never recovered.
    """
    __tablename__ = "staff_activations"

    id = Column(String, primary_key=True, default=gen_uuid)

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)

    # Context and audit only. NOT tenancy, and NOT the source of access.
    brand_sales_org_id = Column(String, nullable=True, index=True)

    purpose = Column(String, default=PURPOSE_SETUP, nullable=False)

    # Non-secret lookup handle; the secret half is hashed.
    token_prefix = Column(String, nullable=False, unique=True, index=True)
    token_hash = Column(String, nullable=False)

    status = Column(String, default=STAFF_INVITE_PENDING, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    # How many times an operator has re-issued for this person. Issuing a new
    # link revokes the outstanding one rather than extending it, so a link that
    # leaked cannot be rescued by whoever leaked it.
    send_count = Column(Integer, default=1, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_staff_activation_user_status", "user_id", "status"),
    )

    def is_usable(self, now=None) -> bool:
        now = now or datetime.utcnow()
        if self.status != STAFF_INVITE_PENDING:
            return False
        if self.expires_at and self.expires_at <= now:
            return False
        return True
