from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User
from app.utils.crypto import encrypt_value

router = APIRouter(prefix="/settings", tags=["settings"])


class ProfileResponse(BaseModel):
    full_name: str
    email: str
    role: str
    twilio_account_sid: Optional[str] = None
    twilio_phone_number: Optional[str] = None
    twilio_caller_id_name: Optional[str] = None
    twilio_configured: bool = False
    notification_email: Optional[str] = None
    notify_on_hot_reply: bool = True
    google_calendar_connected: bool = False
    microsoft_365_connected: bool = False
    microsoft_email_address: Optional[str] = None


class TwilioConfigRequest(BaseModel):
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    twilio_caller_id_name: Optional[str] = None


class AdminTwilioAssignRequest(BaseModel):
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_phone_number: str
    twilio_caller_id_name: Optional[str] = None


class NotificationConfigRequest(BaseModel):
    notification_email: Optional[str] = None
    notify_on_hot_reply: bool = True


@router.get("/profile", response_model=ProfileResponse)
def get_profile(current_user: User = Depends(get_current_user)):
    """
    Returns the advisor's own settings - never returns the encrypted
    Twilio auth token itself, only whether it's configured, since that
    token should never be exposed back to the frontend once stored.
    """
    return ProfileResponse(
        full_name=current_user.full_name,
        email=current_user.email,
        role=current_user.role,
        twilio_account_sid=current_user.twilio_account_sid,
        twilio_phone_number=current_user.twilio_phone_number,
        twilio_caller_id_name=current_user.twilio_caller_id_name,
        twilio_configured=bool(current_user.twilio_account_sid and current_user.twilio_auth_token_encrypted),
        notification_email=current_user.notification_email,
        notify_on_hot_reply=current_user.notify_on_hot_reply,
        google_calendar_connected=current_user.google_calendar_connected,
        microsoft_365_connected=current_user.microsoft_365_connected,
        microsoft_email_address=current_user.microsoft_email_address,
    )


@router.put("/twilio")
def update_twilio_config(
    req: TwilioConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lets each advisor enter their own Twilio account details, so each
    person's SMS usage bills to their own Twilio account rather than
    Mike's - matches the multi-tenant design where every advisor brings
    their own number. The auth token is encrypted before it touches the
    database; it's never stored or returned in plaintext.
    """
    current_user.twilio_account_sid = req.twilio_account_sid
    current_user.twilio_auth_token_encrypted = encrypt_value(req.twilio_auth_token)
    current_user.twilio_phone_number = req.twilio_phone_number
    current_user.twilio_caller_id_name = req.twilio_caller_id_name
    db.commit()
    return {"success": True}


@router.put("/admin/twilio/{user_id}")
def admin_assign_twilio(
    user_id: str,
    req: AdminTwilioAssignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Org admin endpoint — assign a Twilio phone number to any advisor
    in the same org. This unblocks cadence for advisors who haven't
    set up their own Twilio credentials.

    If twilio_account_sid and twilio_auth_token are provided they are
    used for that advisor's account. If omitted, only the phone number
    is updated — useful when all advisors share one Twilio account but
    have different phone numbers.
    """
    if current_user.role not in ('org_admin', 'super_admin'):
        raise HTTPException(status_code=403, detail="Admin access required.")

    target = db.query(User).filter(
        User.id == user_id,
        User.org_id == current_user.org_id,
    ).first()

    if not target:
        raise HTTPException(status_code=404, detail="User not found in your organization.")

    target.twilio_phone_number = req.twilio_phone_number.strip()
    target.twilio_caller_id_name = req.twilio_caller_id_name

    if req.twilio_account_sid:
        target.twilio_account_sid = req.twilio_account_sid.strip()
    if req.twilio_auth_token:
        target.twilio_auth_token_encrypted = encrypt_value(req.twilio_auth_token)

    db.commit()
    return {
        "success": True,
        "user_id": user_id,
        "twilio_phone_number": target.twilio_phone_number,
        "twilio_configured": bool(target.twilio_phone_number),
    }


@router.put("/notifications")
def update_notification_config(
    req: NotificationConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    current_user.notification_email = req.notification_email
    current_user.notify_on_hot_reply = req.notify_on_hot_reply
    db.commit()
    return {"success": True}
