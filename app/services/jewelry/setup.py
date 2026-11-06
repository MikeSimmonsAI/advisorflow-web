"""Turning an organization into a gold & jewelry business - idempotent, sends nothing.

  * ensure_profile        the JewelryProfile row (business configuration; AI in test mode)
  * register_fields       the program's fields on Organization.crm_custom_fields
  * enable_features (god) the explicit feature keys the program runs on

Deliberately NOT done here:
  * no follow-up sequence is created (nothing can start texting on its own);
  * automated texting is not switched on - that stays the per-workspace
    switch (workspace_switches.texts_on), which only the platform owner flips;
  * no customers, staff or sample data are created.
"""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.jewelry_models import JewelryProfile
from app.models.models import Organization
from app.services.jewelry import common as C

# The program runs on the platform's existing capabilities. "sms"/"email" are
# listed because the shared screens and send gates expect them; sending is
# still refused by the workspace text switch and the campaign send lock.
JEWELRY_FEATURES = ("leads", "imports", "booking", "calendar", "availability", "reports",
                    "compliance", "users", "branding_settings", "sms", "email", C.FEATURE)


def get_profile(db: Session, org_id: str) -> Optional[JewelryProfile]:
    return db.query(JewelryProfile).filter(JewelryProfile.organization_id == org_id).first()


def ensure_profile(db: Session, org: Organization) -> JewelryProfile:
    p = get_profile(db, org.id)
    if p is None:
        p = JewelryProfile(organization_id=org.id, ai_mode="test", ai_paused=False,
                           appointment_types=C.jdump(C.DEFAULT_APPOINTMENT_TYPES),
                           appointment_minutes=30, services=C.jdump([]), approved_answers=C.jdump([]),
                           business_hours=C.jdump({}), hours_confirmed=False)
        db.add(p)
        db.flush()
    return p


def register_fields(db: Session, org: Organization) -> List[str]:
    defs = C.jload(org.crm_custom_fields, [])
    if not isinstance(defs, list):
        defs = []
    have = {d.get("key") for d in defs if isinstance(d, dict)}
    added = []
    for f in C.FIELDS:
        if f["key"] in have:
            continue
        d = {"key": f["key"], "label": f["label"], "type": f["type"], "source": "jewelry_program"}
        if f.get("options"):
            d["options"] = f["options"]
        defs.append(d)
        added.append(f["key"])
    if added:
        org.crm_custom_fields = C.jdump(defs)
    return added


def enable_features(org: Organization) -> List[str]:
    keys = C.jload(org.enabled_features, None)
    keys = list(keys) if isinstance(keys, list) else []
    added = [k for k in JEWELRY_FEATURES if k not in keys]
    org.enabled_features = C.jdump(keys + added)
    return added


def setup(db: Session, org: Organization, by_user_id: Optional[str], grant_features: bool = False) -> dict:
    added_features = enable_features(org) if grant_features else []
    if (org.industry or "generic") in ("generic", "", None):
        org.industry = C.INDUSTRY
    profile = ensure_profile(db, org)
    fields = register_fields(db, org)
    db.commit()
    return {"organization_id": org.id, "profile_id": profile.id, "fields_added": fields,
            "features_added": added_features, "industry": org.industry, "texts_on": False,
            "ai_mode": profile.ai_mode}
