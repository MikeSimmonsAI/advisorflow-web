"""Web Push endpoints for the mobile/PWA shell.

  GET    /push/config      public VAPID key + configured flag (no auth needed:
                           the public key is public by definition)
  POST   /push/subscribe   record this browser for the CALLER
  DELETE /push/subscribe   revoke the caller's own subscription (by endpoint or id)
  GET    /push/status      the caller's subscriptions (no endpoints/keys) + recent events
  POST   /push/test        send a test alert to the caller's own subscriptions,
                           only when the server is configured

Everything is scoped to the signed-in user; the workspace recorded on a
subscription is the one the request is in (lead_scope.active_workspace_org_id,
which validates X-Workspace-Id against memberships).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_not_observation
from app.models.models import User
from app.services import web_push_service as wps

router = APIRouter(prefix="/push", tags=["push"])


class _Keys(BaseModel):
    p256dh: str = Field(..., min_length=10, max_length=300)
    auth: str = Field(..., min_length=8, max_length=100)


class SubscribeBody(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=2000)
    keys: _Keys
    user_agent: Optional[str] = Field(None, max_length=1000)
    platform: Optional[str] = Field("web", max_length=20)


class UnsubscribeBody(BaseModel):
    endpoint: Optional[str] = Field(None, max_length=2000)
    id: Optional[str] = Field(None, max_length=64)


def _workspace(user: User, db: Session, request: Request) -> Optional[str]:
    try:
        from app.services.lead_scope import active_workspace_org_id
        return active_workspace_org_id(user, db, request)
    except Exception:  # noqa: BLE001
        return getattr(user, "organization_id", None)


@router.get("/config")
def push_config():
    return wps.config_status()


@router.post("/subscribe", status_code=201)
def push_subscribe(body: SubscribeBody, request: Request, db: Session = Depends(get_db),
                   current_user: User = Depends(require_not_observation)):
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=422, detail="A push endpoint must be an https URL.")
    row = wps.subscribe(db, current_user, organization_id=_workspace(current_user, db, request),
                        endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth,
                        user_agent=body.user_agent, platform=body.platform or "web")
    return {"id": row.id, "configured": wps.is_configured(), "subscription": row.to_public_dict()}


@router.delete("/subscribe")
def push_unsubscribe(body: Optional[UnsubscribeBody] = None, id: Optional[str] = None,
                     endpoint_sha256: Optional[str] = None,
                     db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    """Revoke the caller's own subscription, named by `id` (query or body) or
    by `endpoint` (body), or by `endpoint_sha256` (query — lets a browser name
    its endpoint without putting the capability URL in a query string).
    Another user's id/endpoint is a 404 and is untouched."""
    sub_id = id or (body.id if body else None)
    endpoint = body.endpoint if body else None
    if not endpoint and not sub_id and not endpoint_sha256:
        raise HTTPException(status_code=422, detail="Give the endpoint or the subscription id.")
    n = wps.unsubscribe(db, current_user, endpoint=endpoint, subscription_id=sub_id,
                        endpoint_hash=(endpoint_sha256 or "").lower() or None)
    if n == 0:
        raise HTTPException(status_code=404, detail="No such subscription.")
    return {"revoked": n}


@router.get("/status")
def push_status(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return wps.status_for(db, current_user)


@router.post("/test")
def push_test(request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(require_not_observation)):
    if not wps.is_configured():
        raise HTTPException(status_code=409, detail="Push alerts are not configured on this server.")
    if not wps.live_subscriptions(db, current_user.id, any_workspace=True):
        raise HTTPException(status_code=409, detail="Enable push alerts on this device first.")
    return wps.send_test(db, current_user, organization_id=_workspace(current_user, db, request))
