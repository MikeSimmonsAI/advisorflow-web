"""DEMO SITES — a customer-facing mockup, hosted on the brand's own domain.

WHY THIS TABLE EXISTS. Every deal that reaches Demo Build needs something to
show the prospect, and `Opportunity.demo_url` was a bare string pointing
wherever somebody happened to host it. A proposal that links a prospect to a
third-party domain undercuts the pitch, and a link nobody controls cannot be
revoked when the deal dies.

WHY THE HTML IS STORED HERE AND NOT UPLOADED AS A FILE. The proposal upload
path deliberately refuses HTML — its allowlist says so in as many words,
because a user-supplied HTML file served from our own origin is stored XSS
against an app that keeps its session token in localStorage. That reasoning is
correct and this table does not weaken it: a demo is AUTHORED through the sales
API by an authenticated brand-sales user, never uploaded by a customer, and the
public route renders it inside a sandboxed iframe with no same-origin access.
Both halves are required. Either alone would be a hole.

THE TOKEN IS THE WHOLE AUTHORIZATION. A prospect has no account, exactly as
with the proposal portal, so the same rules apply: a CSPRNG secret rather than
a uuid, an expiry, and a revocation that takes effect immediately.
"""

from datetime import datetime, timedelta
import secrets
import uuid

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text)

from app.models.models import Base

# Long enough to outlive a sales cycle, short enough that a forgotten link does
# not stay live for ever.
DEFAULT_TTL_DAYS = 90


def gen_uuid() -> str:
    return uuid.uuid4().hex


def mint_token() -> str:
    """A secret, not an identifier.

    `secrets.token_urlsafe` for the same reason the proposal portal uses it: a
    uuid has structure and reduced entropy, and this token is the only thing
    between a stranger and a prospect's unreleased site design.
    """
    return secrets.token_urlsafe(32)


class DemoSite(Base):
    __tablename__ = "demo_sites"

    id = Column(String, primary_key=True, default=gen_uuid)

    # Scoped to the deal it was built for, and to the brand that built it, so a
    # manager of one brand can never list or revoke another brand's demos.
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False)
    brand_sales_org_id = Column(String, ForeignKey("brand_sales_orgs.id", ondelete="CASCADE"),
                                nullable=False)

    # Which KIND of mockup this is. A deal can legitimately carry more than one
    # at a time - the product walkthrough and an optional website concept are
    # different artifacts, not two versions of one pitch - so replacement only
    # retires the live demo in the SAME slot.
    slot = Column(String(32), nullable=False, default="platform",
                  server_default="platform", index=True)
    title = Column(String, nullable=False)
    # What the prospect is being shown. Rendered sandboxed; see the module note.
    html = Column(Text, nullable=False)

    token = Column(String, nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    # Observed, not inferred. "Has the prospect looked at it yet" is the single
    # most useful thing a rep wants to know about a demo they sent.
    view_count = Column(Integer, default=0, nullable=False)
    first_viewed_at = Column(DateTime, nullable=True)
    last_viewed_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)

    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_demo_sites_opportunity", "opportunity_id", "is_active"),
        Index("ix_demo_sites_brand", "brand_sales_org_id"),
    )

    def is_live(self, now=None) -> bool:
        """Whether this link should currently open.

        Every condition is checked here rather than at each call site, because
        a revoked demo that still renders in one forgotten code path is the
        whole failure this method exists to prevent.
        """
        now = now or datetime.utcnow()
        if not self.is_active:
            return False
        if self.revoked_at is not None and self.revoked_at <= now:
            return False
        if self.expires_at is not None and self.expires_at < now:
            return False
        return True

    def default_expiry(self, now=None) -> datetime:
        return (now or datetime.utcnow()) + timedelta(days=DEFAULT_TTL_DAYS)
