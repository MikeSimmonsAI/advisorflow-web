"""
Platform utility helpers — shared across services and routers.

get_brand_name(db, org_id) returns the human-readable platform brand name
for the given org (e.g. "EvoSys Pro", "Harmony Hustle", "BookaBoost").

This is the single source of truth so that notification emails, SMS alerts,
and voice greetings all say the right brand for every platform — no more
hardcoded "BookaBoost" appearing in EvoSys Pro advisor inboxes.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

# THE LAST RESORT NAMES THE CORE PLATFORM, NOT ANOTHER LIVE BRAND.
#
# This was "BookaBoost", which is a real, separate, currently-operating brand.
# `Organization.platform_id` is nullable, so any organization whose platform is
# unset — a new tenant mid-provisioning, a legacy row, a lookup that misses —
# had its staff alerts signed with a different company's name. Every caller is
# an internal notification to an advisor or FSA rather than anything a customer
# reads, so nothing leaked outward; it was still one brand's people being told
# they work for another.
#
# "AdvisorFlow" is the core platform every brand runs on. Naming it is honest
# when the brand cannot be resolved; naming a peer brand never is.
_DEFAULT_BRAND = "AdvisorFlow"


def get_brand_name(db: Session, org_id: str | None) -> str:
    """Return the platform brand name for the given org_id.

    Resolution order, most specific first:
      1. the organization's OWN white-label name (`brand_name`), which is what
         a white-labelled customer calls themselves and beats the platform's,
      2. its platform's name,
      3. the core platform, because a brand we cannot resolve must not be
         given the name of a brand we can.

    Never raises — safe to call from any notification path.
    """
    if not org_id:
        return _DEFAULT_BRAND
    try:
        from app.models.models import Organization, Platform  # avoid circular import at module level
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org:
            return _DEFAULT_BRAND
        own = (getattr(org, "brand_name", None) or "").strip()
        if own:
            return own
        if not org.platform_id:
            return _DEFAULT_BRAND
        platform = db.query(Platform).filter(Platform.id == org.platform_id).first()
        return platform.name if platform and platform.name else _DEFAULT_BRAND
    except Exception:
        return _DEFAULT_BRAND
