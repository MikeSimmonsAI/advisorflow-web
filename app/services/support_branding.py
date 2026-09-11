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
