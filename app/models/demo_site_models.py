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
                        String, Text, UniqueConstraint)

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

    # THE HUMAN-READABLE NAME, beside the secret rather than instead of it.
    #
    # `token` is 43 characters of CSPRNG and stays the canonical address, so
    # every link already sent to a prospect keeps working exactly as it did.
    # A slug is the same demo under a name somebody can read down a phone, and
    # it SURVIVES REPUBLISH - which the token deliberately does not, since
    # `create` always inserts a new row and mints a new secret.
    #
    # Unique per BRAND, not globally: two brands may each have a prospect
    # called Countryside, and one taking the name from the other is a
    # white-label leak. NULL slugs do not collide under a UNIQUE index on
    # either Postgres or SQLite, so every existing row is unaffected.
    slug = Column(String(48), nullable=True, index=True)

    __table_args__ = (
        Index("ix_demo_sites_opportunity", "opportunity_id", "is_active"),
        Index("ix_demo_sites_brand", "brand_sales_org_id"),
        UniqueConstraint("brand_sales_org_id", "slug",
                         name="uq_demo_site_brand_slug"),
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


class DealPreviewGrant(Base):
    """ONE PERSON MAY OPEN ONE DEAL'S EXISTING DEMO - granted by God, per person.

    WHY A TABLE OF ITS OWN. A brand-sales Membership is read everywhere as
    employment (sales_access, platform login, the back office). Giving a
    second brand's admin a membership in the deal's brand to let them look at
    one demo would hand them that brand's whole pipeline. And handing them the
    demo's public link would hand them the bearer secret: reusable, shareable,
    and impossible to take back from one person without killing it for the
    prospect too.

    So this row is the whole authorization and nothing else reads it: no
    sales_access, no platform-login slug, no workspace. The grantee opens the
    demo through a signed-in route that re-checks this row on every open and
    returns the stored page - never the token, never the public address.

    `grantee_platform_id` is the brand the grantee works under (e.g.
    BookaBoost): the preview shows only in that brand's Program Access, and
    only while the person still belongs to that brand.
    """
    __tablename__ = "deal_preview_grants"

    id = Column(String, primary_key=True, default=gen_uuid)
    opportunity_id = Column(String, ForeignKey("opportunities.id", ondelete="CASCADE"),
                            nullable=False)
    grantee_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"),
                             nullable=False)
    grantee_platform_id = Column(String, ForeignKey("platforms.id"), nullable=False)
    granted_by = Column(String, ForeignKey("users.id"), nullable=True)
    reason = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # NULL means "until revoked or until the deal/demo closes" - the preview
    # also disappears by itself when the deal is won/lost or its demo expires.
    expires_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_deal_preview_grants_grantee", "grantee_user_id"),
        Index("ix_deal_preview_grants_opportunity", "opportunity_id"),
    )

    def is_active(self, now=None) -> bool:
        now = now or datetime.utcnow()
        if self.revoked_at is not None and self.revoked_at <= now:
            return False
        if self.expires_at is not None and self.expires_at <= now:
            return False
        return True
