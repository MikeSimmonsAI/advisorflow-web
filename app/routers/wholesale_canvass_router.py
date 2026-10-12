"""/wholesale/canvass - door-knocking routes (build list #17, #18).

    GET    /wholesale/canvass/preview?zips=75208,75211   how many doors a route there would have
    GET    /wholesale/canvass/routes?status=open|done|all
    POST   /wholesale/canvass/routes                     build one {name, zips, limit, assigned_to_id, route_date}
    GET    /wholesale/canvass/routes/{route_id}          the route and its doors
    PATCH  /wholesale/canvass/routes/{route_id}          hand to someone else, mark done
    PATCH  /wholesale/canvass/doors/{door_id}            what happened at the door (hot -> call-back)

Rules: app/services/wholesale_canvass.py. Nothing here texts or calls anyone.
"""
from __future__ import annotations

from typing import Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_user
from app.models.models import User
from app.services import wholesale_canvass as CV
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/canvass", tags=["wholesale-canvass"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


class RouteIn(BaseModel):
    zips: str
    name: Optional[str] = None
    limit: Optional[int] = 40
    assigned_to_id: Optional[str] = None
    route_date: Optional[str] = None
    include_test: bool = False


class RouteChange(BaseModel):
    assigned_to_id: Optional[str] = None
    status: Optional[str] = None


class DoorIn(BaseModel):
    result: Optional[str] = None
    talked_to: Optional[str] = None
    phone: Optional[Union[str, int]] = None
    permission: Optional[str] = None
    interest: Optional[str] = None
    notes: Optional[str] = None


@router.get("/preview")
def preview(zips: str, include_test: bool = False, db: Session = Depends(get_db),
            user: User = Depends(require_tenant_user)):
    return CV.preview(db, svc.read_org_id(db, user), user, zips, include_test)


@router.get("/routes")
def list_routes(status: str = "open", db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return CV.routes(db, svc.read_org_id(db, user), user, status)


@router.post("/routes", dependencies=[Depends(require_not_observation)])
def create_route(body: RouteIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return CV.create_route(db, svc.write_org_id(db, user), user, body.model_dump())


@router.get("/routes/{route_id}")
def get_route(route_id: str, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return CV.get_route(db, svc.read_org_id(db, user), user, route_id)


@router.patch("/routes/{route_id}", dependencies=[Depends(require_not_observation)])
def change_route(route_id: str, body: RouteChange, db: Session = Depends(get_db),
                 user: User = Depends(require_tenant_user)):
    return CV.update_route(db, svc.write_org_id(db, user), user, route_id, body.model_dump(exclude_unset=True))


@router.patch("/doors/{door_id}", dependencies=[Depends(require_not_observation)])
def knock(door_id: str, body: DoorIn, db: Session = Depends(get_db), user: User = Depends(require_tenant_user)):
    return CV.knock(db, svc.write_org_id(db, user), user, door_id, body.model_dump(exclude_unset=True))
