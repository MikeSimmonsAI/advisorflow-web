"""A public web page that belongs to a CUSTOMER, not to a brand.

The platform already hosts two kinds of public page and neither of them fits
a customer's own website. `public-site/` is the brand's marketing site, PHP
uploaded to a web host by hand. `demo_sites` is a mockup shown to a prospect
during a sale: deliberately tokenised, deliberately expiring, deliberately
rendered in a sandboxed iframe so nobody mistakes it for a live system.

What was missing is the ordinary case. A customer buys the platform, the
platform builds them a website, and the enquiries that website collects have
to land in THAT CUSTOMER'S workspace - not in the brand's intake organization,
which is where `Platform.public_intake_organization_id` sends everything and
is correct for the brand's own marketing site and wrong for a customer's.

So: one row per page, addressed by a slug the customer chooses, owned by an
organization, and an inquiry route that files into that organization. The page
is stored rather than served from disk for the same reason a demo site is -
publishing must not require a deploy, and a customer's copy changing must not
rebuild five Python services.

WHAT THIS IS NOT. It is not a CMS, it is not a page builder, and it holds no
customer records of its own. Every enquiry it takes becomes a `Lead` in the
owning organization through the same `public_capture` path every other public
form uses, so consent evidence, deduplication, provenance and plan capacity
behave identically whether the person filled in the brand's form or the
customer's.
"""

from datetime import datetime
import uuid

from sqlalchemy import (Boolean, Column, DateTime, ForeignKey, Index, Integer,
                        String, Text)

from app.models.models import Base


def _gen_id() -> str:
    return uuid.uuid4().hex


# The enquiry a site form becomes. Values are `public_capture` kinds, and the
# list is closed for the same reason it is closed there: the kind decides
# whether the arrival carries sales intent, and that is a decision, not a
# free-text field a form can set for itself.
FORM_KIND_INQUIRY = "demo_request"
FORM_KIND_OPTIN = "sms_optin"
FORM_KIND_WAITLIST = "waitlist"
FORM_KINDS = (FORM_KIND_INQUIRY, FORM_KIND_OPTIN, FORM_KIND_WAITLIST)


class CustomerSite(Base):
    """One published public page belonging to one customer organization."""

    __tablename__ = "customer_sites"

    id = Column(String, primary_key=True, default=_gen_id)

    # CASCADE: a customer that is permanently deleted takes its website with
    # it. There is nothing here worth keeping once the organization is gone,
    # and an orphan row would keep answering on a live URL.
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"),
                             nullable=False, index=True)

    # The address. Global, not per-organization, because it appears in a URL
    # people type - two customers cannot both own /site/energy.
    slug = Column(String(64), unique=True, nullable=False, index=True)

    title = Column(String(200))

    # The page itself. Authored by staff and reviewed before publishing; a
    # customer cannot write here, which is what keeps arbitrary markup off the
    # platform's own origin.
    html = Column(Text, nullable=False)

    # What a form submission on this page becomes.
    form_kind = Column(String(32), nullable=False, default=FORM_KIND_INQUIRY)

    # THE WORDING SHOWN, VERBATIM. Copied onto every consent record the page
    # produces. A paraphrase is not evidence, so if the page has no consent
    # language this stays NULL and the page must not ask for consent.
    consent_text = Column(Text)
    consent_version = Column(String(32))

    # NULL means "the page decides". Set it to hold every enquiry from this
    # page for a named owner instead of leaving it unassigned.
    default_owner_user_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"))

    is_active = Column(Boolean, nullable=False, default=True)

    view_count = Column(Integer, nullable=False, default=0)
    inquiry_count = Column(Integer, nullable=False, default=0)
    last_viewed_at = Column(DateTime)
    last_inquiry_at = Column(DateTime)

    published_at = Column(DateTime, default=datetime.utcnow)
    published_by = Column(String, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_customer_sites_org_active", "organization_id", "is_active"),
    )

    def is_live(self) -> bool:
        return bool(self.is_active)
