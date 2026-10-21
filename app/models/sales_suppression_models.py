"""Sales Do Not Contact — a prospect's explicit request for no further contact.

WHY A SALES TABLE AND NOT `suppression_entries`
----------------------------------------------
`SuppressionEntry` (app/models/models.py) is the customer-workspace list: keyed
to `organizations.id`, phone only, read by every tenant compliance check.
Sales prospects belong to a `BrandSalesOrg`, which is not an organization, and
can be reached by email as well as phone. Writing sales rows into that table
would need a fake organization id or a change every tenant check reads. So this
is a separate, sales-scoped record — but phones are normalised with the SAME
function (`compliance_service.usable_us_phone`), so a number means the same
thing on both sides.

WHAT A ROW MEANS
----------------
"Within this brand sales org, do not contact this phone and/or this email."
It matches by contact detail, not by opportunity, so a duplicate prospect
record with the same phone or email is covered the moment it exists.

NEVER DELETED. A correction (the request was recorded on the wrong person, or
the prospect later asks to be contacted) "lifts" the row: lifted_at / by /
reason are written and the row stays, so the history of who recorded it, who
lifted it and why is permanent. Nothing lifts a row automatically.
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Index

from app.models.models import Base


def gen_uuid():
    return str(uuid.uuid4())


class SalesContactSuppression(Base):
    __tablename__ = "sales_contact_suppressions"

    id = Column(String, primary_key=True, default=gen_uuid)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False)
    # At least one of these is set. Phone is the 11-digit US form produced by
    # compliance_service.usable_us_phone; email is trimmed and lower-cased.
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

    reason = Column(Text, nullable=False)
    # The record it was raised from. Kept for the audit trail only — matching
    # is by phone/email, never by this id.
    source_opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="SET NULL"),
                                   nullable=True)
    recorded_by_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Correction. NULL = in force.
    lifted_at = Column(DateTime, nullable=True)
    lifted_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    lift_reason = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_sales_suppr_brand_phone", "brand_sales_org_id", "phone"),
        Index("ix_sales_suppr_brand_email", "brand_sales_org_id", "email"),
    )
