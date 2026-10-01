"""GOD MODE — prospect demo workspaces. /god/demo/*  (god_admin only)

Mike's standing process: every prospect gets a demo rendition of the platform
running their own workflow, hosted on the platform. Production has no shell or
database access, so a demo must be provisionable through the API by the
platform owner. This router is that path for the Max Life (insurance agency)
demo; the seed itself is app/services/agency/demo_seed.py, shared with
scripts/seed_maxlife_demo.py.

  POST /god/demo/maxlife   create (or reuse by stable slug) the DEMO organization,
                           enable its modules through the entitlement allow-list,
                           and seed it. Idempotent. Sends nothing.
  GET  /god/demo/maxlife   status: exists?, counts, effective features.

Never touches a real customer: an organization_id that is not an is_demo
organization in the demo seed's slug family is refused (409). The owner views
the result through the ordinary God "enter customer" path (X-Org-Override);
no membership is granted to the owner.
"""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_god
from app.models.models import Organization, User
from app.routers.audit_log_router import log_action
from app.services.agency import demo_seed as ds

router = APIRouter(prefix="/god/demo", tags=["god-demo"])


class MaxLifeIn(BaseModel):
    organization_id: Optional[str] = None
    platform_id: Optional[str] = None   # only used when the demo org is first created


def _status(db: Session, org: Optional[Organization]) -> dict:
    if org is None:
        return {"exists": False, "organization_id": None, "organization_name": None,
                "is_demo": None, "counts": None, "features": [], "insurance_agency_enabled": False,
                "slug": ds.SLUG}
    from app.services import entitlements as ent
    eff = ent.enabled_for(org)
    return {"exists": True, "organization_id": org.id, "organization_name": org.name,
            "slug": org.slug, "is_demo": bool(org.is_demo), "platform_id": org.platform_id,
            "counts": ds.counts(db, org.id),
            "features": sorted(eff) if eff is not None else sorted(ent.ALL_FEATURE_KEYS),
            "insurance_agency_enabled": ent.org_has_feature(org, "insurance_agency"),
            "enter_path": "/god/platform/context/customer/%s" % org.id}


@router.get("/maxlife")
def maxlife_status(db: Session = Depends(get_db), god: User = Depends(require_god)):
    return _status(db, ds.find_demo_org(db))


@router.post("/maxlife")
def maxlife_provision(body: MaxLifeIn = Body(default=MaxLifeIn()), db: Session = Depends(get_db),
                      god: User = Depends(require_god)):
    try:
        if body.organization_id:
            org = db.query(Organization).filter(Organization.id == body.organization_id).first()
            if org is None:
                raise HTTPException(404, "Organization not found")
            if not ds.is_maxlife_demo_org(org):
                # Recorded: an attempt to seed demo data into a non-demo org is worth seeing.
                log_action(db, org.id, god.id, "god.demo.maxlife.refused", "organization", org.id,
                           details={"reason": "not a Max Life demo organization"},
                           platform_id=org.platform_id, commit=True)
                raise HTTPException(409, "Refusing: '%s' is not a Max Life demo organization. Demo "
                                         "data is only ever seeded into an is_demo organization "
                                         "created by this endpoint." % org.name)
            created = False
        else:
            platform_id = body.platform_id
            if platform_id:
                from app.models.models import Platform
                if db.query(Platform).filter(Platform.id == platform_id).first() is None:
                    raise HTTPException(404, "Platform not found")
            org, created = ds.ensure_demo_org(db, actor=god, platform_id=platform_id)
        report = ds.seed_maxlife_demo(db, organization=org, created_by=god, commit=False)
    except ds.DemoSeedError as e:
        db.rollback()
        raise HTTPException(409, str(e))
    report["organization_created"] = created
    if created:
        log_action(db, org.id, god.id, "god.demo.maxlife.org_created", "organization", org.id,
                   details={"name": org.name, "slug": org.slug, "is_demo": True},
                   platform_id=org.platform_id, commit=False)
    log_action(db, org.id, god.id, "god.demo.maxlife.seeded", "organization", org.id,
               details={"added": report["added"], "counts": report["counts"], "sent": report["sent"]},
               platform_id=org.platform_id, commit=False)
    db.commit()
    db.refresh(org)
    report["status"] = _status(db, org)
    return report
