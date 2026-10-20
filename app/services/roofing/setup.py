"""Turning an organization into a roofing company - idempotent, sends nothing.

  * ensure_profile           the RoofingProfile row (business configuration)
  * register_fields          roofing fields on Organization.crm_custom_fields,
                             which is what the universal importer reads to
                             auto-map "Roof Issue" -> custom:roof_issue
  * ensure_sequence          the roofing follow-up as a CadenceTemplate - made
                             the default ONLY when the org has no default yet
  * enable_features (god)    the explicit feature keys roofing runs on

Automated texting is NOT switched on here. That stays the per-workspace
switch (workspace_switches.texts_on), which only the platform owner flips.
"""
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.models import CadenceTemplate, CadenceTemplateTouch, Organization
from app.models.roofing_models import RoofingProfile
from app.services.roofing import common as C

ROOFING_FEATURES = ("leads", "imports", "sms", "email", "cadences", "ai_assist", "booking",
                    "calendar", "availability", "reports", "compliance", "users",
                    "branding_settings", C.FEATURE)


def get_profile(db: Session, org_id: str) -> Optional[RoofingProfile]:
    return db.query(RoofingProfile).filter(RoofingProfile.organization_id == org_id).first()


def ensure_profile(db: Session, org: Organization) -> RoofingProfile:
    p = get_profile(db, org.id)
    if p is None:
        p = RoofingProfile(organization_id=org.id, inspection_types=C.jdump(C.DEFAULT_INSPECTION_TYPES),
                           inspection_duration_minutes=C.DEFAULT_DURATION, service_areas=C.jdump([]))
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
        d = {"key": f["key"], "label": f["label"], "type": f["type"], "source": "roofing_program"}
        if f.get("options"):
            d["options"] = f["options"]
        defs.append(d)
        added.append(f["key"])
    if added:
        org.crm_custom_fields = C.jdump(defs)
    return added


def ensure_sequence(db: Session, org: Organization, by_user_id: Optional[str]) -> CadenceTemplate:
    t = (db.query(CadenceTemplate)
         .filter(CadenceTemplate.organization_id == org.id, CadenceTemplate.name == C.SEQUENCE_NAME).first())
    if t is None:
        has_default = (db.query(CadenceTemplate)
                       .filter(CadenceTemplate.organization_id == org.id,
                               CadenceTemplate.is_default == True,  # noqa: E712
                               CadenceTemplate.is_active == True).first()) is not None  # noqa: E712
        t = CadenceTemplate(organization_id=org.id, name=C.SEQUENCE_NAME, industry="roofing",
                            description="Turns existing roofing leads into booked inspections.",
                            is_default=not has_default, created_by_id=by_user_id,
                            created_at=datetime.utcnow(), updated_at=datetime.utcnow(), is_active=True)
        db.add(t)
        db.flush()
        for touch in C.SEQUENCE_TOUCHES:
            db.add(CadenceTemplateTouch(template_id=t.id, is_active=True, **touch))
    return t


def enable_features(org: Organization) -> List[str]:
    keys = C.jload(org.enabled_features, None)
    keys = list(keys) if isinstance(keys, list) else []
    added = [k for k in ROOFING_FEATURES if k not in keys]
    org.enabled_features = C.jdump(keys + added)
    return added


def setup(db: Session, org: Organization, by_user_id: Optional[str], grant_features: bool = False) -> dict:
    added_features = enable_features(org) if grant_features else []
    if (org.industry or "generic") in ("generic", "", None):
        org.industry = "roofing"
    profile = ensure_profile(db, org)
    fields = register_fields(db, org)
    seq = ensure_sequence(db, org, by_user_id)
    db.commit()
    return {"organization_id": org.id, "profile_id": profile.id, "fields_added": fields,
            "features_added": added_features, "sequence_id": seq.id, "sequence_is_default": bool(seq.is_default)}
