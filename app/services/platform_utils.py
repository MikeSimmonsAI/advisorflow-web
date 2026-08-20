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

# Default fallback when no platform is found (legacy BookaBoost orgs).
_DEFAULT_BRAND = "BookaBoost"


def get_brand_name(db: Session, org_id: str | None) -> str:
    """Return the platform brand name for the given org_id.

    Looks up Organization → Platform.name.
    Falls back to _DEFAULT_BRAND if org or platform not found.
    Never raises — safe to call from any notification path.
    """
    if not org_id:
        return _DEFAULT_BRAND
    try:
        from app.models.models import Organization, Platform  # avoid circular import at module level
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org or not org.platform_id:
            return _DEFAULT_BRAND
        platform = db.query(Platform).filter(Platform.id == org.platform_id).first()
        return platform.name if platform and platform.name else _DEFAULT_BRAND
    except Exception:
        return _DEFAULT_BRAND
