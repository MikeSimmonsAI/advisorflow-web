"""HOW THE CUSTOMER'S LAUNCH EXPERIENCE LOOKS AND READS, AS CONFIGURATION.

WHY THIS IS A TABLE AND NOT A PAGE PER CUSTOMER
-----------------------------------------------
The approved customer onboarding experience is a premium, branded, guided
journey: a hero with the customer's own identity, a numbered programme, a
white workspace, a progress rail, a help card, a downloadable guide. The
tempting way to deliver that for the first customer is one React page with
their name, their logo, their imagery and their integration typed into it.

The second customer then needs a second page. The tenth needs a release.

So the SHELL is code — one of it, at the platform layer — and everything that
makes it look like a particular customer's own system is a row here.

FOUR LAYERS, LOWEST WINS LAST
-----------------------------
  platform_default   what a fresh install renders with no rows at all
  industry           what an energy customer, a funeral home or a roofer gets
  brand              the white-label brand's own presentation
  organization       this one customer's overrides

Each layer supplies only what it wants to change; the resolver deep-merges
them in that order. A brand that sets nothing inherits the platform default
and still renders completely — the shell never depends on a row existing.

WHAT A LAYER MAY CARRY
----------------------
  presentation   identity and copy: eyebrow, title, subtitle, intro, hero
                 imagery, customer tagline, rail imagery, help card, guide,
                 quote, footer.
  journey        the named stages of the programme, their order and labels.
                 One customer's integration stage names their marketplace;
                 another's says "Integrations". Neither is in the code.
  form           extra sections and fields beyond the platform intake — an
                 industry's own questions, a customer's own requests.

WHAT A LAYER MAY NOT CARRY
--------------------------
Anything authoritative. No completion state, no progress figure, no milestone
status, no commercial term. Those are read from the implementation, the
intake, the commercial agreement and the launch programme, which are the
records that already exist. Configuration decides what the customer is SHOWN
and asked; it never decides what is TRUE.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Index, String, Text,
    UniqueConstraint,
)

from app.models.models import Base, gen_uuid

# ── the four layers ─────────────────────────────────────────────────────────
SCOPE_PLATFORM_DEFAULT = "platform_default"   # scope_id is NULL
SCOPE_INDUSTRY         = "industry"           # scope_id is an industry key
SCOPE_BRAND            = "brand"              # scope_id is a platform id
SCOPE_ORGANIZATION     = "organization"       # scope_id is an organization id

EXPERIENCE_SCOPES = (SCOPE_PLATFORM_DEFAULT, SCOPE_INDUSTRY, SCOPE_BRAND,
                     SCOPE_ORGANIZATION)

# Merge order, lowest priority first. The resolver walks this list, so adding a
# layer is a line here rather than a rewrite of the merge.
SCOPE_PRECEDENCE = (SCOPE_PLATFORM_DEFAULT, SCOPE_INDUSTRY, SCOPE_BRAND,
                    SCOPE_ORGANIZATION)

SCOPE_LABELS = {
    SCOPE_PLATFORM_DEFAULT: "Platform default",
    SCOPE_INDUSTRY:         "Industry template",
    SCOPE_BRAND:            "Brand",
    SCOPE_ORGANIZATION:     "Customer",
}


class LaunchExperienceConfig(Base):
    """One layer of the customer launch experience.

    `scope_id` is NULL only for the platform default, which is why there is a
    unique constraint over the pair rather than over the id alone: a brand and
    an industry may share a key string and still be different rows.
    """
    __tablename__ = "launch_experience_configs"

    id = Column(String, primary_key=True, default=gen_uuid)

    scope_type = Column(String, nullable=False, index=True)   # EXPERIENCE_SCOPES
    scope_id = Column(String, nullable=True, index=True)

    name = Column(String, nullable=True)
    description = Column(Text, nullable=True)

    presentation = Column(JSON, nullable=True)
    journey = Column(JSON, nullable=True)
    form = Column(JSON, nullable=True)

    is_active = Column(Boolean, nullable=False, default=True)

    created_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    updated_by_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id",
                         name="uq_launch_experience_scope"),
        Index("ix_launch_experience_active", "scope_type", "is_active"),
    )
