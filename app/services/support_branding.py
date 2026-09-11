"""WHOSE NAME IS ON THE SUPPORT EXPERIENCE.

THE RULE
--------
    THE BRAND OWNS THE FACE. ADVISORFLOW OWNS THE BRAIN AND THE FIXER.

A customer of EvoSys Pro asks "Ask Evo" and reads the "EvoSys Pro Help
Centre". A customer of BookaBoost asks "Ask BookaBoost". Neither ever sees
the word AdvisorFlow, and neither is served by a second copy of anything —
the same engine answers both, wearing whichever name this function returns.

WHY THIS IS A MODULE AND NOT A CONSTANT
---------------------------------------
`brand_config.py` already exists and already made this mistake once, at
length: brand presentation lived in four unsynchronised places and had
visibly drifted (two different accents for the same brand). This module does
NOT repeat that. It reads `brand_config` for everything `brand_config`
already knows — display name, support email, app URL, accent — and adds only
the three names that are specific to the support product, from
`support_brand_settings`.

WHY `assistant_name` IS STORED RATHER THAN DERIVED
---------------------------------------------------
"Ask Evo" cannot be computed from "EvoSys Pro" by any rule that would not
also produce "Ask Booka" from "BookaBoost". So the derivation is not
attempted: an unconfigured brand gets "Ask <its own display name>", which is
never wrong and occasionally clumsy, and a brand that cares writes the name
it actually uses into its settings row.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models.models import Organization, Platform
from app.models.support_models import SupportBrandSettings

log = logging.getLogger(__name__)

# The only place AdvisorFlow's own name is allowed to appear in a
# customer-facing string, and even here it is the last resort for a request
# that resolved to no brand at all.
_NEUTRAL_DISPLAY = "Support"


def settings_for(db: Optional[Session],
                 platform_id: Optional[str]) -> Optional[SupportBrandSettings]:
    if db is None or not platform_id:
        return None
    try:
        return (db.query(SupportBrandSettings)
                .filter(SupportBrandSettings.platform_id == platform_id).first())
    except Exception:                                          # noqa: BLE001
        log.exception("support_branding: settings lookup failed for %s", platform_id)
        return None


def brand_for_platform(db: Optional[Session],
                       platform_id: Optional[str]) -> Dict[str, Any]:
    """Everything the support surfaces need to wear one brand's name.

    Never raises. A request that cannot resolve a brand gets neutral wording
    rather than another brand's — showing a BookaBoost customer EvoSys Pro's
    support email would be worse than showing them nothing.
    """
    slug = None
    display = None
    if db is not None and platform_id:
        try:
            row = db.query(Platform).filter(Platform.id == platform_id).first()
            if row is not None:
                slug = row.slug
                display = row.name
        except Exception:                                      # noqa: BLE001
            log.exception("support_branding: platform lookup failed for %s",
                          platform_id)

    cfg: Dict[str, Any] = {}
    if slug:
        try:
            from app.services import brand_config
            cfg = brand_config.config_for_slug(db, slug)
        except Exception:                                      # noqa: BLE001
            log.exception("support_branding: brand_config lookup failed for %s", slug)
            cfg = {}

    display_name = (display or cfg.get("display_name") or _NEUTRAL_DISPLAY)
    settings = settings_for(db, platform_id)

    assistant = (getattr(settings, "assistant_name", None)
                 or ("Ask %s" % display_name))
    help_center = (getattr(settings, "help_center_name", None)
                   or ("%s Help Centre" % display_name))
    support_name = (getattr(settings, "support_display_name", None)
                    or ("%s Support" % display_name))
    greeting = (getattr(settings, "greeting", None)
                or ("Hi — I'm %s. Tell me what's happening and I'll take a look "
                    "at your account." % assistant))

    return {
        "platform_id": platform_id,
        "slug": slug,
        "display_name": display_name,
        "assistant_name": assistant,
        "help_center_name": help_center,
        "support_display_name": support_name,
        "greeting": greeting,
        "support_email": cfg.get("support_email"),
        "support_phone": cfg.get("support_phone"),
        "app_base_url": cfg.get("app_base_url"),
        "accent_color": cfg.get("accent_color"),
        "configured": settings is not None,
    }


def brand_for_org(db: Optional[Session],
                  org: Optional[Organization]) -> Dict[str, Any]:
    """The brand a customer belongs to. Their platform, never a guess.

    An organization with no `platform_id` is a real state — one created before
    brands existed — and it gets neutral wording rather than being assigned to
    whichever brand happens to be first in the table. `billing_catalog` refuses
    to bill such an organization for the same reason.
    """
    return brand_for_platform(db, getattr(org, "platform_id", None)
                              if org is not None else None)


def brand_for_ticket(db: Optional[Session], ticket: Any) -> Dict[str, Any]:
    """The brand a TICKET belongs to. Its own platform, then its org's.

    `SupportTicket.platform_id` is stamped when the ticket is created and is
    the authoritative statement of whose support product this conversation
    happened inside. It is read FIRST, and the organization's platform is only
    a repair path for a ticket written before the column was populated.

    Never a default brand, never the first brand in the table, never the
    environment.
    """
    platform_id = getattr(ticket, "platform_id", None)
    if not platform_id and db is not None:
        org_id = getattr(ticket, "organization_id", None)
        if org_id:
            try:
                org = (db.query(Organization)
                       .filter(Organization.id == org_id).first())
                platform_id = getattr(org, "platform_id", None)
            except Exception:                                  # noqa: BLE001
                log.exception("support_branding: org lookup failed for ticket "
                              "%s", getattr(ticket, "ticket_number", None))
    return brand_for_platform(db, platform_id)


class SupportSendingIdentity(object):
    """WHO A SUPPORT EMAIL COMES FROM. Duck-types `send_email_via_provider`.

    THE BUG THIS EXISTS TO CLOSE. `support_tickets._notify` called
    `send_email_via_provider(to, subject, html)` with no `org=` at all. With
    no org that function falls straight through to the module-level
    `FROM_EMAIL`, which defaulted to `noreply@bookaboost.com` — so a support
    ticket raised inside EvoSys Pro produced an email that announced itself as
    BookaBoost. Nothing in the support engine chose that; a shared default
    filled a gap nobody had noticed was empty, exactly as it did for booking
    mail before `public_identity` was written.

    WHY NOT `public_identity.sending_identity_for_org`. That resolver's first
    level is `Organization.from_email` — the CUSTOMER's own verified sending
    domain, which is the right answer for mail the customer's business sends
    to a family, and the wrong one here. A support acknowledgement comes FROM
    THE BRAND that sells them the software; sending it from the funeral home's
    own domain would mean Restland emailing Restland about a ticket EvoSys Pro
    is answering.

    So this walks the BRAND only:

        1. `Platform.support_email`      the brand's configured support address
        2. the brand registry / frozen defaults, keyed on that platform's slug
        3. nothing — and `resolved = True` says so, which makes
           `send_email_via_provider` refuse rather than substitute.

    `resolved` is the flag that turns an unresolved brand into a refusal. A
    support email that fails to send is a ticket somebody chases; a support
    email delivered under another brand's name is a customer being told their
    vendor is a company they have never heard of.
    """

    __slots__ = ("from_email", "reply_to_email", "cc_email", "resend_api_key",
                 "resolved", "platform_id", "display_name", "source")

    def __init__(self, *, from_email=None, reply_to_email=None,
                 platform_id=None, display_name=None, source="unresolved"):
        self.from_email = from_email
        self.reply_to_email = reply_to_email
        # No CC on support mail, ever. Copying a customer's support thread to
        # an address nobody chose is the same class of mistake as the default
        # sender this class exists to remove.
        self.cc_email = None
        # The brand sends through the platform's own Resend account. There is
        # no per-brand key column, so this stays None and the env key applies —
        # which is correct: one Resend account, several verified domains.
        self.resend_api_key = None
        self.resolved = True
        self.platform_id = platform_id
        self.display_name = display_name
        self.source = source


def sending_identity_for_ticket(db: Optional[Session],
                                ticket: Any) -> SupportSendingIdentity:
    """The FROM and REPLY-TO for every email this ticket generates."""
    brand = brand_for_ticket(db, ticket)
    address = brand.get("support_email") or None
    source = "unresolved"
    if address:
        source = "brand"
    return SupportSendingIdentity(
        from_email=address,
        # Replies land in the brand's own support inbox, which is the address
        # the ticket already belongs to. Nothing here invents a mailbox.
        reply_to_email=address,
        platform_id=brand.get("platform_id"),
        display_name=brand.get("display_name"),
        source=source,
    )


def upsert_settings(db: Session, *, platform_id: str,
                    values: Dict[str, Any]) -> SupportBrandSettings:
    """God writes a brand's support identity and hours.

    Only the keys present are written, so a form that edits the naming half
    cannot blank the hours half.
    """
    row = (db.query(SupportBrandSettings)
           .filter(SupportBrandSettings.platform_id == platform_id).first())
    if row is None:
        row = SupportBrandSettings(platform_id=platform_id)
        db.add(row)

    for field in ("assistant_name", "help_center_name", "support_display_name",
                  "greeting", "timezone", "business_days", "business_start",
                  "business_end", "holidays_json", "emergency_is_24x7"):
        if field in values:
            setattr(row, field, values[field])
    db.flush()
    return row
