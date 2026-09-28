"""/wholesale/exceptions — the VA exception queue.

    GET   /wholesale/exceptions/kinds                 kinds and outcomes
    GET   /wholesale/exceptions?scope=mine|all|escalated|unassigned
    GET   /wholesale/exceptions/summary               counts for My Work (assigned to me; admin: + unassigned, escalated)
    POST  /wholesale/exceptions                       raise one by hand (admin)
    POST  /wholesale/exceptions/sweep                 raise what the data shows (admin, idempotent;
                                                      sandbox records only with ?include_test=true)
    POST  /wholesale/exceptions/{id}/assign           (admin)
    POST  /wholesale/exceptions/{id}/resolve          complete | unable_to_verify | needs_more_info | escalate

Least privilege: `exception_queue_work` lets a person see and work ONLY the
exceptions assigned to them. Org admins qualify by role and manage the queue.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation
from app.models.models import User
from app.models.wholesale_models import EXCEPTION_KINDS, EXCEPTION_OUTCOMES
from app.services import wholesale_exceptions as EX
from app.services import wholesale_service as svc
from app.services.capabilities import require_feature_capability
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/exceptions", tags=["wholesale-exceptions"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])
require_queue = require_feature_capability("exception_queue_work")


def _manager(db, user):
    if not EX.is_manager(db, user):
        raise HTTPException(403, "Only an administrator can do that.")


class RaiseIn(BaseModel):
    kind: str
    subject_type: str
    subject_id: str
    title: str
    detail: Optional[str] = None
    priority: int = 50
    assigned_to_id: Optional[str] = None


class AssignIn(BaseModel):
    assigned_to_id: Optional[str] = None


class ResolveIn(BaseModel):
    outcome: str
    note: Optional[str] = None


@router.get("/kinds")
def kinds(user: User = Depends(require_queue)):
    return {"kinds": [{"key": k, "label": EX.KIND_LABELS[k]} for k in EXCEPTION_KINDS],
            "outcomes": list(EXCEPTION_OUTCOMES)}


@router.get("/summary")
def summary(db: Session = Depends(get_db), user: User = Depends(require_queue)):
    return EX.summary_for(db, svc.write_org_id(db, user), user)


@router.get("")
def list_queue(scope: str = "mine", include_closed: bool = False,
               limit: int = Query(200, ge=1, le=500), offset: int = Query(0, ge=0),
               db: Session = Depends(get_db), user: User = Depends(require_queue)):
    org_id = svc.write_org_id(db, user)
    manager = EX.is_manager(db, user)
    rows = EX.queue(db, org_id, user, include_closed=include_closed, scope=scope,
                    limit=limit, offset=offset, manager=manager)
    ids = {r.assigned_to_id for r in rows if r.assigned_to_id}
    names = ({u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids)).all()}
             if ids else {})
    subjects = EX.load_subjects(db, org_id, rows)
    return {"manager": manager, "scope": scope if manager else "mine",
            "limit": limit, "offset": offset,
            "items": [EX.exception_json(db, org_id, r, names, subjects=subjects) for r in rows]}


@router.post("")
def raise_one(payload: RaiseIn, db: Session = Depends(get_db), user: User = Depends(require_queue),
              _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    _manager(db, user)
    ex, created = EX.raise_exception(db, org_id, kind=payload.kind, subject_type=payload.subject_type,
                                     subject_id=payload.subject_id, title=payload.title,
                                     detail=payload.detail, priority=payload.priority,
                                     source="manual", user=user)
    if payload.assigned_to_id and created:
        EX.assign(db, org_id, ex, user, payload.assigned_to_id)
    db.commit()
    return dict(EX.exception_json(db, org_id, ex), created=created)


@router.post("/sweep")
def sweep(include_test: bool = False, db: Session = Depends(get_db),
          user: User = Depends(require_queue), _g: User = Depends(require_not_observation)):
    """Test (sandbox) records are left out unless `include_test=true` is asked
    for explicitly - the same rule as the hourly pass, so a sweep never puts
    sandbox work in front of a real person by default."""
    org_id = svc.write_org_id(db, user)
    _manager(db, user)
    made = EX.sweep(db, org_id, user=user, include_test=include_test)
    db.commit()
    return {"raised": made}


@router.post("/{exception_id}/assign")
def assign(exception_id: str, payload: AssignIn, db: Session = Depends(get_db),
           user: User = Depends(require_queue), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    ex = EX.get(db, org_id, exception_id)
    EX.assign(db, org_id, ex, user, payload.assigned_to_id)
    db.commit()
    return EX.exception_json(db, org_id, ex)


@router.post("/{exception_id}/resolve")
def resolve(exception_id: str, payload: ResolveIn, db: Session = Depends(get_db),
            user: User = Depends(require_queue), _g: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    ex = EX.get(db, org_id, exception_id)
    EX.resolve(db, org_id, ex, user, outcome=payload.outcome, note=payload.note)
    db.commit()
    return EX.exception_json(db, org_id, ex)
