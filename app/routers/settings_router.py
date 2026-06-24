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
    notification_phone: Optional[str] = None
    notify_on_hot_reply: bool = True
    notify_via_sms: bool = False
    google_calendar_connected: bool = False
    microsoft_365_connected: bool = False
    microsoft_email_address: Optional[str] = None


class TwilioConfigRequest(BaseModel):
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_phone_number: str
    twilio_caller_id_name: Optional[str] = None


class NotificationConfigRequest(BaseModel):
    notification_email: Optional[str] = None
    notification_phone: Optional[str] = None
    notify_on_hot_reply: bool = True
    notify_via_sms: bool = False


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
        notification_phone=current_user.notification_phone,
        notify_on_hot_reply=current_user.notify_on_hot_reply,
        notify_via_sms=current_user.notify_via_sms,
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


@router.put("/notifications")
def update_notification_config(
    req: NotificationConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if req.notify_via_sms and not (req.notification_phone or current_user.notification_phone):
        raise HTTPException(status_code=400, detail="A notification phone number is required to enable SMS alerts.")

    current_user.notification_email = req.notification_email
    current_user.notification_phone = req.notification_phone
    current_user.notify_on_hot_reply = req.notify_on_hot_reply
    current_user.notify_via_sms = req.notify_via_sms
    db.commit()
    return {"success": True}
