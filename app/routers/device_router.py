"""Device registration for the mobile app: push tokens and file uploads.

Everything here is scoped to the CALLER. There is no user_id parameter on any
route, and every query filters on `current_user.id` in the query itself rather
than loading a row and then checking it — the difference matters because a
loaded-then-checked row can leak existence through a 403 where a filtered query
can only ever say "not found".
"""

from typing import Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile, status)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.models import User
from app.models.device_models import DEVICE_PLATFORMS, PLATFORM_IOS

router = APIRouter(prefix="/me", tags=["mobile"])


class DeviceRegisterIn(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)
    platform: str = Field(PLATFORM_IOS)
    device_id: Optional[str] = Field(None, max_length=200)
    device_name: Optional[str] = Field(None, max_length=200)
    app_version: Optional[str] = Field(None, max_length=50)
    # Which experience the app was in when it registered. Used to decide what
    # NOT to send; never to decide what may be read.
    active_context: Optional[str] = Field(None, max_length=100)
    active_scope_id: Optional[str] = Field(None, max_length=100)


class DeviceUnregisterIn(BaseModel):
    token: str = Field(..., min_length=8, max_length=512)


@router.post("/devices")
def register_device(body: DeviceRegisterIn, request: Request,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """Register this install for push, owned by the caller and their session.

    The session id comes from `request.state.auth_session` — the row
    `get_current_user` already resolved — and never from the body. A client that
    could name its own session id could attach a push token to somebody else's
    device, which is the one way a notification could be made to arrive on the
    wrong lock screen.
    """
    from app.services import push_service

    if body.platform not in DEVICE_PLATFORMS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Unsupported device platform.")

    sess = getattr(request.state, "auth_session", None)
    row = push_service.register_token(
        db, current_user,
        token=body.token,
        platform=body.platform,
        session_id=getattr(sess, "id", None),
        device_id=body.device_id,
        device_name=body.device_name,
        app_version=body.app_version,
        active_context=body.active_context,
        active_scope_id=body.active_scope_id,
    )
    return {"device": row.to_public_dict(),
            "push_enabled": push_service.push_enabled()}


@router.get("/devices")
def list_devices(db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """The caller's own registered devices. Tokens are never returned."""
    from app.services import push_service
    rows = push_service.tokens_for_user(db, current_user.id)
    return {"devices": [r.to_public_dict() for r in rows],
            "push_enabled": push_service.push_enabled()}


@router.delete("/devices")
def unregister_device(body: DeviceUnregisterIn,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """Stop pushing to this token. Idempotent — an unknown token is success.

    Returning 404 for a token this user does not own would answer the question
    "does this token exist somewhere on the platform", which is not a question
    an unregister endpoint should be able to answer.
    """
    from app.services import push_service
    push_service.deactivate_token(db, current_user, body.token)
    return {"success": True}


# ── uploads ─────────────────────────────────────────────────────────────────

@router.get("/upload-capability")
def upload_capability(db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """Can this deployment durably keep a file the field sends it?

    THE APP ASKS BEFORE IT SHOWS A CAMERA BUTTON. `sms_router` writes uploads to
    /tmp/bookaboost_media with its own comment saying to replace it with object
    storage; on Render that path does not survive a restart and is not shared
    between instances. A photo taken at a graveside and lost on the next deploy
    is worse than a camera button that was never offered, so the capability is
    reported honestly and the app hides capture when it is `ephemeral`.

    This is a deployment dependency, not a code gap: point MEDIA_STORAGE_BACKEND
    at object storage and the same endpoints become durable.
    """
    from app.services import mobile_storage
    return mobile_storage.capability()


@router.post("/uploads")
async def upload_file(file: UploadFile = File(...),
                      purpose: Optional[str] = Form(None),
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """Accept one file from the field and return a reference to it.

    Refuses outright when storage is ephemeral rather than accepting a file it
    knows it will lose. An upload that returns 200 and then evaporates is the
    worst of the three available behaviours; the app keeps the capture queued
    locally and tells the person it has not been sent.
    """
    from app.services import mobile_storage
    return await mobile_storage.store_upload(db, current_user, file,
                                             purpose=purpose)
