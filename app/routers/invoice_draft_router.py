"""LOCAL INVOICE DRAFTS (tenant workspace, source-only; stops at approval_ready).

    GET  /invoice-drafts                          list this workspace's drafts
    POST /invoice-drafts                          create (state=draft)
    GET  /invoice-drafts/{id}                     one draft with exact cents + audit events
    POST /invoice-drafts/{id}/lines               add a line
    POST /invoice-drafts/{id}/lines/{n}/remove    remove a line
    POST /invoice-drafts/{id}/adjustments         set discount / tax amount (cents)
    POST /invoice-drafts/{id}/transition          draft <-> approval_ready, or void

Authority is the existing workspace-admin gate (require_admin); it is not broadened. There is
deliberately NO send / finalize / pay endpoint and no provider call: provider_status is always
"not_started". Mutations require expected_version (409 when stale) and honour Idempotency-Key.
Errors: 400 rule refusal, 404 unknown or other-tenant id, 409 stale / key reuse, 503 store down.
"""
# No `from __future__ import annotations` in a router module (see god_access_router).
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel

from app.deps import require_admin
from app.models.models import User
from app.services import invoice_draft as inv
from app.services import invoice_draft_store as store

router = APIRouter(prefix="/invoice-drafts", tags=["Invoice Drafts (local)"])


class DraftIn(BaseModel):
    customer_name: str
    memo: str = ""


class LineIn(BaseModel):
    description: str
    quantity: int
    unit_price_cents: int
    expected_version: int


class AdjustIn(BaseModel):
    discount_cents: int = 0
    tax_cents: int = 0
    expected_version: int


class TransitionIn(BaseModel):
    to: str
    reason: str = ""
    expected_version: int


class VersionIn(BaseModel):
    expected_version: int


def _scope(u: User) -> str:
    org = getattr(u, "organization_id", None)
    if not org:
        raise HTTPException(status_code=403, detail="Select a customer workspace to use invoice drafts")
    return org


def _actor(u: User) -> str:
    return getattr(u, "email", None) or str(getattr(u, "id", "user"))


def _run(fn):
    try:
        return fn()
    except inv.NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except inv.StaleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except inv.StorageError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except inv.InvoiceError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _out(draft, replayed: bool = False) -> dict:
    out = inv.view(draft)
    out["replayed"] = replayed
    return out


def _nocache(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("")
def list_drafts(response: Response, user: User = Depends(require_admin)):
    _nocache(response)
    org = _scope(user)
    return {"provider_status": inv.PROVIDER_STATUS,
            "drafts": [inv.view(d, with_events=False) for d in _run(lambda: store.get_store().list(org))]}


@router.post("")
def create(body: DraftIn, user: User = Depends(require_admin),
           idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    org = _scope(user)
    d, replayed = _run(lambda: store.get_store().create(org, body.customer_name, body.memo, _actor(user),
                                                        idempotency_key or None))
    return _out(d, replayed)


@router.get("/{draft_id}")
def get_draft(draft_id: str, response: Response, user: User = Depends(require_admin)):
    _nocache(response)
    org = _scope(user)
    return _out(_run(lambda: store.get_store().get(org, draft_id)))


def _mutate(user, draft_id, version, action, fn, key):
    org = _scope(user)
    d, replayed = _run(lambda: store.get_store().mutate(org, draft_id, fn, version, key or None, action))
    return _out(d, replayed)


@router.post("/{draft_id}/lines")
def add_line(draft_id: str, body: LineIn, user: User = Depends(require_admin),
             idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    return _mutate(user, draft_id, body.expected_version, "line_added",
                   lambda d: inv.add_line(d, body.description, body.quantity, body.unit_price_cents, _actor(user)),
                   idempotency_key)


@router.post("/{draft_id}/lines/{line_no}/remove")
def remove_line(draft_id: str, line_no: int, body: VersionIn, user: User = Depends(require_admin),
                idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    return _mutate(user, draft_id, body.expected_version, "line_removed",
                   lambda d: inv.remove_line(d, line_no, _actor(user)), idempotency_key)


@router.post("/{draft_id}/adjustments")
def adjustments(draft_id: str, body: AdjustIn, user: User = Depends(require_admin),
                idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    return _mutate(user, draft_id, body.expected_version, "adjustments_set",
                   lambda d: inv.set_adjustments(d, body.discount_cents, body.tax_cents, _actor(user)),
                   idempotency_key)


@router.post("/{draft_id}/transition")
def transition(draft_id: str, body: TransitionIn, user: User = Depends(require_admin),
               idempotency_key: Optional[str] = Header(default=None, max_length=120)):
    return _mutate(user, draft_id, body.expected_version, "state_changed",
                   lambda d: inv.transition(d, body.to, body.reason, _actor(user)), idempotency_key)
