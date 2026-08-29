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
                 "public_base_url", "support_phone", "website", "source",
                 "reply_to_email", "cc_email", "customer_facing_name",
                 "business_address", "business_phone",
                 "logo_url", "brand_color", "accent_color", "tagline")

    def __init__(self, organization_id=None, brand_name=None, from_email=None,
                 resend_api_key=None, public_base_url=None, support_phone=None,
                 website=None, source=None, reply_to_email=None, cc_email=None,
                 customer_facing_name=None, business_address=None,
                 business_phone=None, logo_url=None, brand_color=None,
                 accent_color=None, tagline=None):
        self.organization_id = organization_id
        # THE LOOK OF THE BUSINESS, for pages a family opens.
        #
        # Organization-level ONLY, with no platform fallback anywhere below.
        # A missing customer logo must render as no logo; falling back to the
        # platform's would put the EvoSys Pro mark at the top of a funeral
        # home's booking page, which is the same mistake as the Vercel
        # hostname, only louder.
        self.logo_url = logo_url
        self.brand_color = brand_color
        self.accent_color = accent_color
        self.tagline = tagline
        # The business's OWN address and phone, as a family would be told them.
        # Distinct from `support_phone`, which belongs to the platform: a
        # family calling back must reach the funeral home, not EvoSys.
        self.business_address = business_address
        self.business_phone = business_phone
        # THE PLATFORM's name - EvoSys Pro. Infrastructure. A family has never
        # heard of it and must never be shown it.
        self.brand_name = brand_name
        # THE BUSINESS the family believes is contacting them - Restland
        # Cemetery and Funeral Home. This is the display name on anything a
        # family reads. The two were conflated before: `get_brand_name()`
        # returns the platform, and the booking confirmation used it, so a
        # family got mail signed "The EvoSys Pro Team".
        self.customer_facing_name = customer_facing_name
        self.from_email = from_email
        self.resend_api_key = resend_api_key
        # Where a human's reply lands, and an optional second recipient. Both
        # are organization-level only: there is no brand default and no
        # environment default, because copying a family's appointment mail to
        # an address nobody chose is not a thing a fallback should ever do.
        self.reply_to_email = reply_to_email
        self.cc_email = cc_email
        self.public_base_url = public_base_url
        self.support_phone = support_phone
        self.website = website
        # Which precedence level answered, for diagnostics. Never a secret.
        self.source = source or {}

    def as_dict(self, include_secret_presence=True):
        out = {
            "organization_id": self.organization_id,
            "brand_name": self.brand_name,
            "customer_facing_name": self.customer_facing_name,
            "business_address": self.business_address,
            "business_phone": self.business_phone,
            "logo_url": self.logo_url,
            "brand_color": self.brand_color,
            "accent_color": self.accent_color,
            "tagline": self.tagline,
            "from_email": self.from_email,
            "reply_to_email": self.reply_to_email,
            "cc_email": self.cc_email,
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

    # ── names: the platform's, and the customer's own ───────────────────────
    if plat is not None and getattr(plat, "name", None):
        ident.brand_name = plat.name
        ident.source["brand_name"] = "platform"
    if org is not None:
        ident.business_address = getattr(org, "org_address", None) or None
        ident.business_phone = getattr(org, "org_phone", None) or None
        ident.source["business_address"] = ("organization"
                                            if ident.business_address else "unset")
        ident.source["business_phone"] = ("organization"
                                          if ident.business_phone else "unset")
        ident.customer_facing_name = (getattr(org, "brand_name", None)
                                      or getattr(org, "name", None) or None)
        ident.source["customer_facing_name"] = (
            "organization.brand_name" if getattr(org, "brand_name", None)
            else "organization.name")
        # Visual identity. Read from the organization and nowhere else - see
        # the note on these attributes in PublicIdentity.
        ident.logo_url = getattr(org, "brand_logo_url", None) or None
        ident.brand_color = getattr(org, "brand_color_primary", None) or None
        ident.accent_color = getattr(org, "brand_color_accent", None) or None
        ident.tagline = getattr(org, "tagline", None) or None
        for _f in ("logo_url", "brand_color", "accent_color", "tagline"):
            ident.source[_f] = ("organization" if getattr(ident, _f) else "unset")

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

    # ── reply-to and cc: organization only, no inheritance ──────────────────
    #
    # A brand cannot answer "which mailbox does this customer read?", and
    # nothing should ever copy a family's appointment mail to an address that
    # came from a default. Unset means unset.
    if org is not None:
        ident.reply_to_email = getattr(org, "reply_to_email", None) or None
        ident.cc_email = getattr(org, "cc_email", None) or None
    ident.source["reply_to_email"] = ("organization" if ident.reply_to_email
                                      else "unset")
    ident.source["cc_email"] = "organization" if ident.cc_email else "unset"

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

    __slots__ = ("from_email", "resend_api_key", "reply_to_email", "cc_email")

    def __init__(self, from_email=None, resend_api_key=None,
                 reply_to_email=None, cc_email=None):
        self.from_email = from_email
        self.resend_api_key = resend_api_key
        self.reply_to_email = reply_to_email
        self.cc_email = cc_email


def sending_identity_for_org(db: Session, organization_id: Optional[str]) -> SendingIdentity:
    ident = identity_for_org(db, organization_id)
    return SendingIdentity(from_email=ident.from_email,
                           resend_api_key=ident.resend_api_key,
                           reply_to_email=ident.reply_to_email,
                           cc_email=ident.cc_email)


# ── public link builders ────────────────────────────────────────────────────
#
# One place that knows a public path. A caller asks for "the booking link for
# this token in this organization" and never concatenates a host itself.

def public_base_url(db: Session, organization_id: Optional[str]) -> Optional[str]:
    return identity_for_org(db, organization_id).public_base_url


# Hosts that belong to the plumbing, not to any customer. A family must never
# be sent to one: the name tells them nothing, it tells them the wrong thing
# about who is contacting them, and it survives in their message history long
# after the deployment behind it has moved.
#
# These markers are matched against the FALLBACK base only. A platform row or
# registry entry is a deliberate configuration and is trusted as written; the
# environment is a single value shared by three brands and four services, which
# is exactly the shape of mistake this guard exists to catch.
_INFRASTRUCTURE_HOST_MARKERS = (
    ".onrender.com",
    ".vercel.app",
    ".railway.app",
    ".herokuapp.com",
    ".netlify.app",
    "localhost",
    "127.0.0.1",
)


def _is_infrastructure_host(base) -> bool:
    if not base:
        return False
    low = str(base).lower()
    return any(marker in low for marker in _INFRASTRUCTURE_HOST_MARKERS)


def _public_base_or_none(db: Session, organization_id: Optional[str],
                         *env_fallbacks) -> Optional[str]:
    """The branded host for this organization, or None - never plumbing.

    Resolution order is the resolver first, then the configured environment,
    so a deployment that set BOOKING_BASE_URL for an organization with no
    platform domain keeps working. The one thing this will not do is hand back
    an infrastructure hostname: `advisorflow-booking.vercel.app` in a text
    message is a leak whether it arrived from a hard-coded constant or from an
    environment variable, and the env is where the last copies of it live.
    """
    resolved = public_base_url(db, organization_id)
    if resolved:
        return resolved.rstrip("/")
    for candidate in env_fallbacks:
        if not candidate:
            continue
        if _is_infrastructure_host(candidate):
            log.error(
                "public_identity: refusing infrastructure host %r as a public "
                "link for org %s - set the platform domain or BOOKING_BASE_URL "
                "to the branded host",
                candidate, organization_id,
            )
            continue
        return str(candidate).rstrip("/")
    return None


def booking_url(db: Session, organization_id: Optional[str], token: str) -> str:
    """Where a family goes to pick a time.

    Served by the branded frontend at /book/:token, which reuses the existing
    booking endpoints. Falls back to a configured BOOKING_BASE_URL only when
    the brand has no domain at all - and never to an infrastructure host.
    """
    base = _public_base_or_none(db, organization_id, ENV_BOOKING_BASE_URL)
    if not base:
        log.error("public_identity: no branded public host for org %s - booking "
                  "link cannot be built", organization_id)
        return ""
    return "%s/book/%s" % (base, token)


def survey_url(db: Session, organization_id: Optional[str], token: str) -> str:
    """Where a family leaves feedback after an appointment."""
    base = _public_base_or_none(db, organization_id,
                                ENV_PUBLIC_BASE_URL, ENV_BOOKING_BASE_URL)
    if not base:
        log.error("public_identity: no branded public host for org %s - survey "
                  "link cannot be built", organization_id)
        return ""
    return "%s/survey/%s" % (base, token)


# ── what a family is allowed to see on a public page ────────────────────────

def public_branding(db: Session, organization_id: Optional[str]) -> dict:
    """The branding block for a page a FAMILY opens: /book, /survey, /confirm.

    One shape, one resolver, three pages. Written as a function rather than
    left to each router because the failure it prevents is not a crash - it is
    a booking page that renders with a blank header and "EvoSys Pro" in the
    browser tab, which is what a Restland family saw. Nobody notices that in a
    diff; they notice it on their phone.

    WHAT IS DELIBERATELY ABSENT: `brand_name`. That is the PLATFORM's name, and
    this payload is consumed by pages with no login behind them. Leaving it out
    means a frontend cannot render it by accident, which is a stronger
    guarantee than asking every page to remember not to.

    `name` may be empty when an organization has not been given one. The page
    must then render no header at all rather than a placeholder - an unbranded
    page is a gap; a page branded with the platform is a leak.
    """
    ident = identity_for_org(db, organization_id)
    return {
        "name": ident.customer_facing_name or "",
        "logo_url": ident.logo_url or "",
        "address": ident.business_address or "",
        "phone": ident.business_phone or "",
        "brand_color": ident.brand_color or "",
        "accent_color": ident.accent_color or "",
        "tagline": ident.tagline or "",
        # The <title> the page should set. Named explicitly so the frontend
        # never has to compose one, and so an unbranded org yields "" and the
        # page leaves the static title alone rather than inventing something.
        "document_title": (ident.customer_facing_name or ""),
        "source": {k: v for k, v in (ident.source or {}).items()
                   if k in ("customer_facing_name", "logo_url", "brand_color",
                            "accent_color", "tagline", "business_address",
                            "business_phone")},
    }
