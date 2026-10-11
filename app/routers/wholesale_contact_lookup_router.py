"""/wholesale/contact-lookup - "Get phones & emails" for any list that holds people.

    POST /wholesale/contact-lookup/estimate   record by record: what would happen + the most it can cost
    POST /wholesale/contact-lookup/address    give a record a street address to look it up by
    POST /wholesale/contact-lookup/run        look up the records sent (25 max), only within the approved cost

`kind` is one of: buyer, funding_partner, property. See app.services.contact_lookup.
Every route is organization-scoped; another tenant's record comes back "not_found".
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_not_observation, require_tenant_or_observer, require_tenant_user
from app.models.models import User
from app.models.wholesale_models import ACTOR_USER
from app.services import contact_lookup as CL
from app.services import wholesale_buyer_contacts as BC
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

router = APIRouter(prefix="/wholesale/contact-lookup", tags=["wholesale-contact-lookup"],
                   dependencies=[Depends(require_feature("wholesale_real_estate"))])


class LookupIn(BaseModel):
    kind: str
    ids: List[str]
    again: bool = False                    # look up records already looked up
    max_cost_cents: Optional[int] = None   # required by /run


class AddressIn(BaseModel):
    kind: str
    id: str
    address: str                           # "street, city, ST zip"


def _kind(key: str) -> CL.Kind:
    try:
        return CL.kind(key)
    except ValueError:
        raise HTTPException(status_code=400, detail="kind must be one of: %s" % ", ".join(CL.KINDS))


@router.post("/estimate")
def estimate(payload: LookupIn, db: Session = Depends(get_db),
             user: User = Depends(require_tenant_or_observer)):
    _kind(payload.kind)
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=400, detail="Pick an organization first.")
    out = CL.plan(db, org_id, payload.kind, payload.ids, again=payload.again)
    settings = svc.resolve_settings(db, org_id, commit=False)
    out["cap_refusal"] = (CL.cap_refusal(db, org_id, settings, out["count"], whole_list=True)
                          if out["count"] else None)
    db.rollback()                          # an estimate never writes
    return out


@router.post("/address")
def set_address(payload: AddressIn, db: Session = Depends(get_db),
                user: User = Depends(require_tenant_user),
                _guard: User = Depends(require_not_observation)):
    k = _kind(payload.kind)
    org_id = svc.write_org_id(db, user)
    r = CL.get(db, org_id, k, payload.id)
    if r is None:
        raise HTTPException(status_code=404, detail="Not found")
    if CL.parse_address(payload.address) is None:
        raise HTTPException(status_code=400, detail=(
            "Write the address as: street, city, state zip - for example "
            "\"4736 Trail Lake Dr, Fort Worth, TX 76133\"."))
    k.save_address(db, r, payload.address)
    svc.log_event(db, org_id, "contact_lookup.address_set", actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Address added for lookup: %s" % k.name(r), after={"kind": k.key, "id": r.id})
    db.commit()
    return CL.plan(db, org_id, k.key, [r.id])["rows"][0]


@router.post("/run")
def run(payload: LookupIn, db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """Look up phones and emails for up to 25 of the records sent. Charges only for finds."""
    k = _kind(payload.kind)
    org_id = svc.write_org_id(db, user)
    if not CL.configured():
        raise HTTPException(status_code=400, detail=(
            "Phone/email lookup is not connected yet. Add your Tracerfy API token "
            "as TRACERFY_API_TOKEN on the server, then try again."))
    if len(payload.ids) > CL.MAX_PER_CALL:
        raise HTTPException(status_code=400, detail="Send at most %d at a time." % CL.MAX_PER_CALL)
    p = CL.plan(db, org_id, k.key, payload.ids, again=payload.again)
    ids = p["ready_ids"]
    if not ids:
        return {"looked_up": 0, "found": 0, "cost_cents": 0, "results": [], "skipped": p["counts"]}
    most = len(ids) * p["cost_per_find_cents"]
    if payload.max_cost_cents is None or payload.max_cost_cents < most:
        raise HTTPException(status_code=409, detail=(
            "This batch can cost up to $%.2f, more than you approved. Check the cost again." % (most / 100.0)))
    settings = svc.resolve_settings(db, org_id, commit=False)
    refusal = CL.cap_refusal(db, org_id, settings, len(ids))
    if refusal:
        raise HTTPException(status_code=429, detail=refusal)

    provider = CL.provider_factory()
    results, found, spent = [], 0, 0
    for rid in ids:
        r = CL.get(db, org_id, k, rid)
        out = CL.lookup_one(db, org_id, k, r, user, settings, provider)
        db.flush()
        if out["filled"]:
            found += 1
            if k.key == "buyer":
                BC.write_through(db, r, out["filled"], user)
        spent += out["cost_cents"]
        if k.key != "property":           # the property path logs its own event
            svc.log_event(db, org_id, "contact_lookup.returned", actor_type=ACTOR_USER, actor_user_id=user.id,
                          summary="Phone/email lookup for %s (%s): %s" % (out["name"], k.label, out["status"]),
                          after={"kind": k.key, "id": r.id, "status": out["status"],
                                 "filled": sorted(out["filled"]), "cost_cents": out["cost_cents"]})
        db.commit()                       # a paid answer is never lost to a later failure
        results.append(out)
        if out["status"] == "failed" and "slow down" in (out.get("message") or ""):
            break
    return {"looked_up": len(results), "found": found, "cost_cents": spent,
            "results": results, "skipped": {s: n for s, n in p["counts"].items() if s != CL.READY}}
