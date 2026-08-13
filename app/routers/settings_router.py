import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User
from app.utils.crypto import encrypt_value

# Only http/https URLs are safe to store — javascript:, data:, vbscript: etc.
# are blocked to prevent stored-XSS via social-link or booking-page fields.
_SAFE_URL_SCHEMES = ("http://", "https://")


def _validate_url(url: Optional[str], field: str) -> Optional[str]:
    """Return url stripped+lowered if scheme is safe, else raise 400."""
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(_SAFE_URL_SCHEMES):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be an http or https URL.",
        )
    return url


# Profile-photo data-URL: only allow safe raster image MIME types (no SVG).
_SAFE_PHOTO_MIME_RE = re.compile(r'^data:image/(jpeg|jpg|png|gif|webp);base64,')

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
    booking_page_url: Optional[str] = None
    facebook_url: Optional[str] = None
    google_review_url: Optional[str] = None
    instagram_url: Optional[str] = None
    linkedin_url: Optional[str] = None
    profile_photo_url: Optional[str] = None


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


class BookingPageRequest(BaseModel):
    booking_page_url: Optional[str] = None


class SocialLinksRequest(BaseModel):
    facebook_url: Optional[str] = None
    google_review_url: Optional[str] = None
    instagram_url: Optional[str] = None
    linkedin_url: Optional[str] = None


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
        booking_page_url=getattr(current_user, 'booking_page_url', None),
        facebook_url=getattr(current_user, 'facebook_url', None),
        google_review_url=getattr(current_user, 'google_review_url', None),
        instagram_url=getattr(current_user, 'instagram_url', None),
        linkedin_url=getattr(current_user, 'linkedin_url', None),
        profile_photo_url=getattr(current_user, 'profile_photo_url', None),
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
        User.organization_id == current_user.organization_id,
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


@router.put("/booking-page")
def update_booking_page(
    req: BookingPageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save the advisor's personal booking page URL."""
    current_user.booking_page_url = _validate_url(req.booking_page_url, "booking_page_url")
    db.commit()
    return {"success": True}


@router.put("/social-links")
def update_social_links(
    req: SocialLinksRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    DEPRECATED — social links have moved to the org level (PATCH /org-settings/social-links).
    The org-level links are what gets pushed out in surveys and outreach.
    This advisor-level endpoint is kept for backwards compatibility but is no longer
    wired up in the frontend Settings page. Will be removed in a future cleanup.
    """
    current_user.facebook_url = _validate_url(req.facebook_url, "facebook_url")
    current_user.google_review_url = _validate_url(req.google_review_url, "google_review_url")
    current_user.instagram_url = _validate_url(req.instagram_url, "instagram_url")
    current_user.linkedin_url = _validate_url(req.linkedin_url, "linkedin_url")
    db.commit()
    return {"success": True}


class ProfilePhotoRequest(BaseModel):
    # base64 data URL from the browser — e.g. "data:image/jpeg;base64,/9j/..."
    # The frontend encodes it via FileReader.readAsDataURL() so no multipart
    # upload is needed. Max size enforced in frontend (< 2MB before encoding).
    photo_data_url: str


@router.patch("/profile-photo")
def update_profile_photo(
    req: ProfilePhotoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Store the advisor's profile headshot as a base64 data URL.
    Encoded in the browser before sending so no file storage / S3 is needed.
    The data URL is served directly as the <img> src everywhere the avatar appears.
    """
    # Enforce a strict MIME allowlist (jpeg/png/gif/webp only).
    # Rejecting image/svg+xml prevents stored XSS — SVG can embed <script>.
    if not _SAFE_PHOTO_MIME_RE.match(req.photo_data_url):
        raise HTTPException(
            status_code=400,
            detail="photo_data_url must be a valid JPEG, PNG, GIF, or WebP data URL."
        )
    # Rough size guard — base64 of a 2MB file is ~2.7MB of text
    if len(req.photo_data_url) > 3_000_000:
        raise HTTPException(status_code=400, detail="Photo too large. Please use an image under 2MB.")
    current_user.profile_photo_url = req.photo_data_url
    db.commit()
    db.refresh(current_user)
    return {"success": True, "profile_photo_url": current_user.profile_photo_url}


@router.delete("/profile-photo")
def delete_profile_photo(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove the advisor's profile photo, reverting to the initials avatar."""
    current_user.profile_photo_url = None
    db.commit()
    return {"success": True}


# ── Admin: full profile setup for any team member ─────────────────────────────

class AdminProfileRequest(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    booking_page_url: Optional[str] = None
    notification_email: Optional[str] = None
    notify_on_hot_reply: Optional[bool] = None
    profile_photo_url: Optional[str] = None  # base64 data URL or None to clear
    twilio_phone_number: Optional[str] = None
    twilio_caller_id_name: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None   # plaintext — encrypted before storage


@router.patch("/admin/profile/{user_id}")
def admin_update_profile(
    user_id: str,
    req: AdminProfileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Org admin / super admin endpoint — set up any team member's full profile
    without that advisor needing to log in first. Covers name, photo, booking
    page, notification preferences, and Twilio phone assignment.
    Super admins can update users in any org; org admins are restricted to
    their own org.
    """
    if current_user.role not in ("org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")

    q = db.query(User).filter(User.id == user_id)
    if current_user.role == "org_admin":
        q = q.filter(User.organization_id == current_user.organization_id)

    target = q.first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    if req.full_name is not None:
        target.full_name = req.full_name.strip() or target.full_name
    if req.email is not None:
        target.email = req.email.strip().lower() or target.email
    if req.role is not None and req.role in ("advisor", "org_admin", "super_admin"):
        # Org admins can't promote to super_admin
        if current_user.role == "org_admin" and req.role == "super_admin":
            raise HTTPException(status_code=403, detail="Org admins cannot assign super_admin role.")
        target.role = req.role
    if req.booking_page_url is not None:
        target.booking_page_url = req.booking_page_url.strip() or None
    if req.notification_email is not None:
        target.notification_email = req.notification_email.strip() or None
    if req.notify_on_hot_reply is not None:
        target.notify_on_hot_reply = req.notify_on_hot_reply
    if req.twilio_phone_number is not None:
        target.twilio_phone_number = req.twilio_phone_number.strip() or None
    if req.twilio_caller_id_name is not None:
        target.twilio_caller_id_name = req.twilio_caller_id_name.strip() or None
    if req.twilio_account_sid is not None:
        target.twilio_account_sid = req.twilio_account_sid.strip() or None
    if req.twilio_auth_token:
        target.twilio_auth_token_encrypted = encrypt_value(req.twilio_auth_token)
    if req.profile_photo_url is not None:
        if req.profile_photo_url == "":
            target.profile_photo_url = None
        else:
            if not req.profile_photo_url.startswith("data:image/"):
                raise HTTPException(status_code=400, detail="profile_photo_url must be an image data URL.")
            if len(req.profile_photo_url) > 3_000_000:
                raise HTTPException(status_code=400, detail="Photo too large (max 2MB).")
            target.profile_photo_url = req.profile_photo_url

    db.commit()
    db.refresh(target)
    return {
        "success": True,
        "user_id": target.id,
        "full_name": target.full_name,
        "email": target.email,
        "role": target.role,
        "profile_photo_url": target.profile_photo_url,
        "booking_page_url": getattr(target, "booking_page_url", None),
        "twilio_phone_number": target.twilio_phone_number,
        "notification_email": target.notification_email,
    }


@router.get("/admin/profile/{user_id}")
def admin_get_profile(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Load a user's full profile for admin editing."""
    if current_user.role not in ("org_admin", "super_admin"):
        raise HTTPException(status_code=403, detail="Admin access required.")

    q = db.query(User).filter(User.id == user_id)
    if current_user.role == "org_admin":
        q = q.filter(User.organization_id == current_user.organization_id)

    target = q.first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    return {
        "id": target.id,
        "full_name": target.full_name,
        "email": target.email,
        "role": target.role,
        "profile_photo_url": getattr(target, "profile_photo_url", None),
        "booking_page_url": getattr(target, "booking_page_url", None),
        "notification_email": target.notification_email,
        "notify_on_hot_reply": target.notify_on_hot_reply,
        "twilio_phone_number": target.twilio_phone_number,
        "twilio_account_sid": target.twilio_account_sid,
        "twilio_caller_id_name": target.twilio_caller_id_name,
        "twilio_configured": bool(target.twilio_account_sid and target.twilio_auth_token_encrypted),
        "google_calendar_connected": target.google_calendar_connected,
        "microsoft_365_connected": target.microsoft_365_connected,
    }
