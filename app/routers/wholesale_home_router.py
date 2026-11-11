# -*- coding: utf-8 -*-
"""GET /wholesale/home - this person's Wholesale dashboard (#439).

One endpoint for every kind of person: the panels, numbers and shortcuts are
composed on the server from their effective access and record visibility
(app.services.wholesale_home). A team admin may preview what a profile gets
(`preview_profile_id`), filled with records visible to them under it.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.deps import get_db, require_tenant_or_observer
from app.models.models import User
from app.services import wholesale_home as H
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale", tags=["wholesale-home"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


@router.get("/home")
def my_home(include_test: bool = False, preview_profile_id: Optional[str] = Query(None),
            db: Session = Depends(get_db), user: User = Depends(require_tenant_or_observer)):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409, detail="No customer organization selected.")
    prof = None
    if preview_profile_id:
        from app.routers.wholesale_team_router import _access_admin
        from app.services import wholesale_team_access as TA
        _access_admin(db, user, org_id, "view")          # only people who run the team may preview
        try:
            prof = TA._profile(db, org_id, preview_profile_id)
        except LookupError:
            raise HTTPException(status_code=404, detail="That access profile isn't in this workspace.")
    return H.home(db, org_id, user, include_test=include_test, preview_profile=prof)
