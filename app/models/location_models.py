"""CUSTOMER LOCATIONS — the level between an organization and a person.

This did not exist. The tenant hierarchy was Platform → Organization → User,
and a customer's physical sites lived as two loose strings on the organization
row (`org_address`, `org_phone`). That is fine for a single-site customer and
wrong for every one that matters: SCI has funeral homes, each with its own
address, its own phone, its own hours, and its own Family Service Advisors. You
cannot book a family with the right advisor at the right home if the home is not
a record.

SCOPE IS NOT NEGOTIABLE. `organization_id` is NOT NULL and every query goes
through it. A location belongs to exactly one customer; there is no such thing
as a shared location, and nothing here is nullable "for flexibility" - the
codebase already learned that a nullable tenant column is a leak waiting for
someone to write the wrong filter (see `require_tenant_user`).

ASSIGNMENT IS A MEMBERSHIP OF THE LOCATION, NOT A COPY OF THE PERSON. A user is
linked to a location through `UserLocation`, so one advisor can cover two homes
without becoming two users. That is the same "one human, one identity" rule the
brand-sales side already enforces, applied one level down.

HOURS ARE STORED, NOT INVENTED. `operating_hours` is a JSON string and NULL
means "not configured" - it does not mean "9 to 5". A booking surface that
defaults unknown hours to business hours will cheerfully offer a family a slot
on a day the home is shut.
"""

from datetime import datetime
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint,
)

from app.models.models import Base


def gen_uuid() -> str:
    return str(uuid.uuid4())


class Location(Base):
    """One physical site belonging to one customer organization."""

    __tablename__ = "locations"

    id = Column(String, primary_key=True, default=gen_uuid)

    # NOT NULL, on purpose. A location with no customer is not a location.
    organization_id = Column(
        String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String, nullable=False)
    # Stable, human-typeable handle unique WITHIN the customer - not globally.
    # Two customers may both have a "Northside" and neither should have to care.
    slug = Column(String, nullable=True)

    # Exactly one location per customer may be primary. Enforced in the service
    # layer (set_primary), because a partial unique index is not portable across
    # SQLite and Postgres and this codebase runs both.
    is_primary = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True)

    address_line1 = Column(String, nullable=True)
    address_line2 = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    postal_code = Column(String, nullable=True)
    country = Column(String, nullable=True, default="US")

    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

    # NULL means "inherits the organization's timezone", not "UTC". Guessing UTC
    # is how a 9am Chicago appointment renders as 2pm.
    timezone = Column(String, nullable=True)

    # JSON string. NULL = NOT CONFIGURED, which is a different thing from
    # "closed" and a very different thing from "open 9-5".
    operating_hours = Column(Text, nullable=True)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_location_org_slug"),
        Index("ix_locations_org_active", "organization_id", "is_active"),
    )


class UserLocation(Base):
    """Which people work at which of a customer's locations.

    A link row rather than a column on `users` for one reason: an advisor who
    covers two homes is one person covering two homes. A `location_id` column
    would have forced a second user row for the same human, which is the exact
    failure `sales_staff.py` exists to prevent on the brand-sales side.
    """

    __tablename__ = "user_locations"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    location_id = Column(String, ForeignKey("locations.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    # Denormalised so a location query can be scoped without a join. It is
    # written from the location's own organization_id, never from the caller.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    is_primary = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "location_id", name="uq_user_location"),
        Index("ix_user_locations_org", "organization_id", "location_id"),
    )
