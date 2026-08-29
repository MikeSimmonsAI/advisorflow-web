"""One answer to the question "what does a family actually see?"

Every message that leaves this system for a customer carries two pieces of
identity: the address it comes FROM, and the link it points AT. Before this
module each was resolved independently at a dozen call sites, every one with
its own environment variable and its own hard-coded fallback -- and every one
of those fallbacks named either BookaBoost or a Render/Vercel host:

    email_service.py            noreply@bookaboost.com
    calendar_router.py          support@bookaboost.live
    leads_router.py             noreply@bookaboost.com
    sms_service.py              https://advisorflow-booking.vercel.app
    appointment_flow_service.py https://advisorflow-booking.vercel.app
    review_request_cron.py      https://advisorflow-backend.onrender.com
    appointment_invites.py      https://advisorflow-backend.onrender.com

Greenland Cemetery and Funeral Home is an EvoSys Pro customer. With the
environment unset, a Greenland family received mail from a BookaBoost address
pointing at a Vercel host. Neither name means anything to them, and one of them
belongs to a different brand entirely.

PRECEDENCE, most specific first. Each level exists because something real
lives there:

  1. the ORGANIZATION's own override      Organization.from_email
     A customer that has bought and verified its own sending domain.

  2. the PLATFORM that sells to them      Platform.support_email / Platform.domain
     The white-label brand. This is the level that matters: it is already in
     the database, already populated for all three brands, and a new brand
     inherits correct behaviour the moment its row exists.

  3. the verified brand registry in code  BRAND_IDENTITY, keyed on platform slug
     Kept only as a safety net for a platform row that predates a column.

There is deliberately no fourth level for IDENTITY. EMAIL_FROM_ADDRESS is one
value for a deployment that serves three brands, so it cannot answer "which
brand is this customer's?" -- and letting it try is exactly how the bug above
happened. An unresolved brand returns None rather than a plausible-looking
guess, because a message that fails to send is recoverable and a message that
reaches a family under a competitor's name is not.

The link BUILDERS at the bottom of this file may still fall back to the
configured BOOKING_BASE_URL, so an organization whose platform row has no
domain keeps working exactly as it did before this module existed. That is a
compatibility path, marked as one, and it is the only place the environment is
consulted.

THE PUBLIC HOST IS NOT THE API HOST. A family has no account. Sending them to
the backend gives them JSON; sending them to a Render or Vercel hostname tells
them the funeral home they trust outsources to a company they have never heard
of. Public links resolve to the brand's own domain, which fronts the existing
implementation.
"""

import logging
import os
from typing import Optional

from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


# ── level 4: the environment ────────────────────────────────────────────────
#
# Read at import like the constants they replace, so behaviour is identical
# for a deployment that has them set. No brand name appears in a default.

ENV_FROM_EMAIL = os.environ.get("EMAIL_FROM_ADDRESS", "").strip() or None
ENV_BOOKING_BASE_URL = os.environ.get("BOOKING_BASE_URL", "").strip().rstrip("/") or None
ENV_PUBLIC_BASE_URL = (
    os.environ.get("PUBLIC_BASE_URL", "").strip()
    or os.environ.get("TRACKING_BASE_URL", "").strip()
).rstrip("/") or None


class PublicIdentity(object):
    """What a customer of one organization sees. Never contains a secret.

    `from_email` and `public_base_url` may be None. A caller that cannot
    proceed without one should say so rather than substituting a default,
    which is the whole point of this module.
    """

    __slots__ = ("organization_id", "brand_name", "from_email", "resend_api_key",
                 "public_base_url", "support_phone", "website", "source")

    def __init__(self, organization_id=None, brand_name=None, from_email=None,
                 resend_api_key=None, public_base_url=None, support_phone=None,
                 website=None, source=None):
        self.organization_id = organization_id
        self.brand_name = brand_name
        self.from_email = from_email
        self.resend_api_key = resend_api_key
        self.public_base_url = public_base_url
        self.support_phone = support_phone
        self.website = website
        # Which precedence level answered, for diagnostics. Never a secret.
        self.source = source or {}

    def as_dict(self, include_secret_presence=True):
        out = {
            "organization_id": self.organization_id,
            "brand_name": self.brand_name,
            "from_email": self.from_email,
            "public_base_url": self.public_base_url,
            "support_phone": self.support_phone,
            "website": self.website,
            "source": dict(self.source),
        }
        if include_secret_presence:
            out["resend_api_key_set"] = bool(self.resend_api_key)
        return out


def _normalize_host(value):
    """A Platform.domain is stored bare ("app.evosyspro.live"). Make it a URL.

    Accepts a bare host or a full URL so a platform row that already carries a
    scheme keeps working.
    """
    if not value:
        return None
    v = str(value).strip().rstrip("/")
    if not v:
        return None
    if v.startswith("http://") or v.startswith("https://"):
        return v
    return "https://" + v


def identity_for_org(db: Session, organization_id: Optional[str]) -> PublicIdentity:
    """Resolve the customer-facing identity for one tenant organization.

    Never raises. A resolution failure returns an identity with None fields and
    a `source` explaining which levels were consulted, which is far easier to
    act on than an exception thrown from inside a send.
    """
    ident = PublicIdentity(organization_id=organization_id, source={})

    org = None
    plat = None
    if organization_id:
        try:
            from app.models.models import Organization, Platform
            org = (db.query(Organization)
                   .filter(Organization.id == organization_id).first())
            if org is not None and getattr(org, "platform_id", None):
                plat = (db.query(Platform)
                        .filter(Platform.id == org.platform_id).first())
        except Exception:
            log.exception("public_identity: could not load org/platform for %s",
                          organization_id)

    # ── brand name ──────────────────────────────────────────────────────────
    if plat is not None and getattr(plat, "name", None):
        ident.brand_name = plat.name
        ident.source["brand_name"] = "platform"

    # ── from address ────────────────────────────────────────────────────────
    # 1. organization override
    if org is not None and getattr(org, "from_email", None):
        ident.from_email = org.from_email
        ident.source["from_email"] = "organization"
    # 2. platform
    elif plat is not None and getattr(plat, "support_email", None):
        ident.from_email = plat.support_email
        ident.source["from_email"] = "platform"
    else:
        # 3. code registry, keyed on the platform slug
        slug = getattr(plat, "slug", None) if plat is not None else None
        reg = _registry_for(slug)
        if reg and reg.get("from_email"):
            ident.from_email = reg["from_email"]
            ident.source["from_email"] = "registry"
        else:
            # DELIBERATELY NOT the environment.
            #
            # EMAIL_FROM_ADDRESS is one value for the whole deployment, and
            # this deployment serves three brands. Letting it answer here is
            # precisely how a Greenland family came to receive mail from
            # support@bookaboost.live: nobody chose that, a shared default
            # simply filled a gap nobody had noticed was empty.
            #
            # An unresolved brand returns None. The caller then either sends
            # under a correctly resolved identity or does not send, and a
            # message that fails loudly is recoverable in a way that a message
            # delivered under a competitor's name is not.
            ident.source["from_email"] = "unresolved"

    # The Resend key follows the address it sends from. An org that has set its
    # own from_email but no key would otherwise send its address through the
    # platform's key, which the receiving domain will not have authorised.
    if org is not None and getattr(org, "resend_api_key", None):
        ident.resend_api_key = org.resend_api_key
        ident.source["resend_api_key"] = "organization"
    else:
        ident.source["resend_api_key"] = "env"

    # ── public base url ─────────────────────────────────────────────────────
    host = _normalize_host(getattr(plat, "domain", None) if plat is not None else None)
    if host:
        ident.public_base_url = host
        ident.source["public_base_url"] = "platform"
    else:
        slug = getattr(plat, "slug", None) if plat is not None else None
        reg = _registry_for(slug)
        if reg and reg.get("app_base_url"):
            ident.public_base_url = reg["app_base_url"].rstrip("/")
            ident.source["public_base_url"] = "registry"
        else:
            # Same reasoning as the from-address: one host for three brands is
            # not an answer to "which brand is this?". The link BUILDERS below
            # may still fall back to the configured host so an organization on
            # a platform row without a domain keeps working exactly as it did
            # before this module existed - but the IDENTITY says honestly that
            # it does not know.
            ident.source["public_base_url"] = "unresolved"

    # ── the rest, registry-only for now ─────────────────────────────────────
    slug = getattr(plat, "slug", None) if plat is not None else None
    reg = _registry_for(slug)
    if reg:
        ident.support_phone = reg.get("support_phone")
        ident.website = reg.get("website")
        if not ident.brand_name:
            ident.brand_name = reg.get("name")

    return ident


def _registry_for(slug):
    """The verified in-code identities. Imported lazily to avoid a cycle."""
    if not slug:
        return None
    try:
        from app.services.appointment_invites import BRAND_IDENTITY
        return BRAND_IDENTITY.get(slug)
    except Exception:
        return None


# ── adapters ────────────────────────────────────────────────────────────────

class SendingIdentity(object):
    """Duck-types the `org=` argument `send_email_via_provider` already takes.

    That function reads exactly two attributes off whatever it is handed. This
    carries the RESOLVED address rather than the raw Organization row, so an
    org with no `from_email` of its own still sends under its brand instead of
    falling through to the global environment default.
    """

    __slots__ = ("from_email", "resend_api_key")

    def __init__(self, from_email=None, resend_api_key=None):
        self.from_email = from_email
        self.resend_api_key = resend_api_key


def sending_identity_for_org(db: Session, organization_id: Optional[str]) -> SendingIdentity:
    ident = identity_for_org(db, organization_id)
    return SendingIdentity(from_email=ident.from_email,
                           resend_api_key=ident.resend_api_key)


# ── public link builders ────────────────────────────────────────────────────
#
# One place that knows a public path. A caller asks for "the booking link for
# this token in this organization" and never concatenates a host itself.

def public_base_url(db: Session, organization_id: Optional[str]) -> Optional[str]:
    return identity_for_org(db, organization_id).public_base_url


def booking_url(db: Session, organization_id: Optional[str], token: str) -> str:
    """Where a family goes to pick a time.

    Falls back to the configured BOOKING_BASE_URL only when the brand has no
    domain at all, so an organization on a platform row without a domain keeps
    working exactly as it did before this module existed.
    """
    base = public_base_url(db, organization_id) or ENV_BOOKING_BASE_URL
    if not base:
        log.error("public_identity: no public host for org %s - booking link "
                  "cannot be branded", organization_id)
        base = ENV_BOOKING_BASE_URL or ""
    return "%s/book/%s" % (base.rstrip("/"), token)


def survey_url(db: Session, organization_id: Optional[str], token: str) -> str:
    """Where a family leaves feedback after an appointment."""
    base = public_base_url(db, organization_id) or ENV_PUBLIC_BASE_URL or ENV_BOOKING_BASE_URL
    return "%s/survey/%s" % ((base or "").rstrip("/"), token)
