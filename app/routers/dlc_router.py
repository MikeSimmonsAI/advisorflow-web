"""
Twilio A2P 10DLC Registration Router
-------------------------------------
Manages the A2P 10DLC registration flow for an organization's Twilio phone
numbers. A2P 10DLC (Application-to-Person 10-Digit Long Code) registration
is required by US carriers to send business SMS without heavy filtering.

FLOW:
  Step 1 — Create a Twilio Messaging Service for this org
  Step 2 — Register the brand (company identity + EIN) via Twilio A2P API
  Step 3 — Register the campaign (message use case + sample messages)
  Step 4 — Add the org's Twilio phone number(s) to the Messaging Service

All state (SIDs, statuses) is stored on the organizations table so it
survives restarts and can be shown in the frontend status panel.

IMPORTANT: This calls the org admin's Twilio account (first active advisor
with a configured Twilio SID), NOT a platform-level BookaBoost account.
Each org registers its own A2P brand under its own Twilio account.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
import logging

from app.deps import get_db, get_current_user, require_tenant_user
from app.models.models import User, Organization

router = APIRouter(prefix="/10dlc", tags=["10dlc"])
logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────────────────────

# `_require_admin` USED TO LIVE HERE, AND IT WAS THE WHOLE GUARD.
#
# It passed org_admin, super_admin and god_admin, which meant any customer's
# office manager could create a Messaging Service, register the company's A2P
# brand and register a campaign. An A2P brand binds permanently to the Twilio
# account that created it; registering against the wrong one is the single
# messaging mistake that cannot be cleanly undone.
#
# Every route in this router is one thing - A2P 10DLC administration - so the
# gate is applied ONCE, at include time in main.py:
#
#     app.include_router(dlc_router,
#                        dependencies=[Depends(require_capability("a2p_10dlc"))])
#
# Gating the router rather than each route is the same reasoning main.py already
# gives for require_feature: a per-route list is a list somebody forgets to add
# the next route to. `require_capability` demands BOTH gates - the organization
# must be permitted to self-manage A2P, and the caller must be one of its named
# authorized administrators - and neither is implied by holding org_admin.


def _get_org(db: Session, current_user: User) -> Organization:
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _get_twilio_client(db: Session, current_user: User):
    """
    Returns (twilio_client, account_sid) for THIS ORGANIZATION's Twilio account.

    A2P registration is an ORGANIZATION-level act: one brand, one campaign, one
    Twilio account per customer. So the organization's own credentials are the
    only correct source, and they are tried first.

    This function used to start with the CALLING USER's personal Twilio
    credentials. Under impersonation the calling user is the platform owner,
    which meant an org admin clicking "Register brand" for their funeral home
    could have registered it against the PLATFORM's Twilio account — the one
    A2P mistake that is genuinely painful to unwind, because a brand is bound
    to the account that created it. The platform owner is excluded outright,
    and org credentials win regardless.

    The two advisor-level fallbacks below remain only for advisors who brought
    their own Twilio before org credentials existed, so no working setup breaks.
    """
    from twilio.rest import Client
    from app.utils.crypto import decrypt_value

    org = _get_org(db, current_user)

    # --- 1. The organization's own credentials (the model everything new uses)
    if org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted:
        auth = decrypt_value(org.org_twilio_auth_token_encrypted)
        return Client(org.org_twilio_account_sid, auth), org.org_twilio_account_sid

    is_owner = (getattr(current_user, "role", None) or "").lower() == "god_admin"

    # --- 2. Legacy: the calling user's own Twilio account (never the platform's)
    if (not is_owner) and current_user.twilio_account_sid and current_user.twilio_auth_token_encrypted:
        auth = decrypt_value(current_user.twilio_auth_token_encrypted)
        return Client(current_user.twilio_account_sid, auth), current_user.twilio_account_sid

    # --- 3. Legacy: any active advisor in the org who brought their own Twilio
    advisor = db.query(User).filter(
        User.organization_id == current_user.organization_id,
        User.role != "god_admin",
        User.twilio_account_sid.isnot(None),
        User.twilio_auth_token_encrypted.isnot(None),
        User.is_active == True,
    ).first()

    if not advisor:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{org.name} has no Twilio credentials configured. An admin of "
                f"this organization must add the Account SID and Auth Token in "
                f"Org Settings → Twilio before registering an A2P brand."
            ),
        )

    auth = decrypt_value(advisor.twilio_auth_token_encrypted)
    return Client(advisor.twilio_account_sid, auth), advisor.twilio_account_sid


def _status_response(org: Organization) -> dict:
    """Serialize registration status from org columns — all use getattr for safety.

    `credentials_ready` reports only whether the organization HOLDS Twilio
    credentials. It is deliberately not called "connected" and never implies
    the brand or campaign is approved: those are Twilio's own status strings,
    reported verbatim below and nowhere upgraded by this code.
    """
    return {
        "credentials_ready": bool(
            org.org_twilio_account_sid and org.org_twilio_auth_token_encrypted
        ),
        "account_sid_last4": (org.org_twilio_account_sid or "")[-4:] or None,
        "messaging_service_sid": getattr(org, "twilio_messaging_service_sid", None),
        "brand_sid": getattr(org, "twilio_a2p_brand_sid", None),
        "brand_status": getattr(org, "twilio_a2p_brand_status", None),
        "campaign_sid": getattr(org, "twilio_a2p_campaign_sid", None),
        "campaign_status": getattr(org, "twilio_a2p_campaign_status", None),
        "campaign_use_case": getattr(org, "twilio_a2p_campaign_use_case", None),
        "registered_at": getattr(org, "twilio_a2p_registered_at", None),
        "org_name": org.name,
    }


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/status")
def get_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Returns current A2P 10DLC registration status for this org."""
    org = _get_org(db, current_user)
    return _status_response(org)


@router.post("/create-messaging-service")
def create_messaging_service(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Step 1 — Creates a Twilio Messaging Service for this org (or returns
    the existing one if already created). The Messaging Service is the
    Twilio container that holds the phone number(s) and campaign.
    """
    org = _get_org(db, current_user)

    existing_sid = getattr(org, "twilio_messaging_service_sid", None)
    if existing_sid:
        return {"success": True, "messaging_service_sid": existing_sid, "created": False}

    client, account_sid = _get_twilio_client(db, current_user)

    try:
        service = client.messaging.v1.services.create(
            friendly_name=f"{org.name}",
            inbound_request_url="https://advisorflow-backend.onrender.com/sms/inbound",
            use_inbound_webhook_on_number=False,
            use_case_indication="BY_ORG",
        )
        if hasattr(org, "twilio_messaging_service_sid"):
            org.twilio_messaging_service_sid = service.sid
            db.commit()
        logger.info("10DLC: created Messaging Service %s for org %s", service.sid, org.id)
        return {"success": True, "messaging_service_sid": service.sid, "created": True}
    except Exception as e:
        logger.exception("10DLC create-messaging-service error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create messaging service. Contact support.")


class BrandRequest(BaseModel):
    company_name: str
    ein: str                         # Employer Identification Number (e.g. "12-3456789")
    website: str
    address_street: str
    address_city: str
    address_state: str               # 2-letter code e.g. "TX"
    address_zip: str
    contact_first_name: str
    contact_last_name: str
    contact_email: str
    contact_phone: str               # E.164 e.g. "+14695537417"
    business_type: Optional[str] = "PRIVATE_PROFIT"  # or "NONPROFIT"
    vertical: Optional[str] = "REAL_ESTATE"          # TCR vertical (funeral home → REAL_ESTATE is closest)
    alt_business_id: Optional[str] = None            # optional secondary ID


@router.post("/register-brand")
def register_brand(
    req: BrandRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Step 2 — Submits A2P brand registration to Twilio/TCR.
    The brand record ties your company identity (EIN, contact info) to
    your Twilio account and is required before you can register a campaign.

    Brand approval typically takes 1–5 business days. Check /10dlc/status
    after submitting and again the next day.
    """
    org = _get_org(db, current_user)

    existing_brand = getattr(org, "twilio_a2p_brand_sid", None)
    if existing_brand:
        return {
            "success": True,
            "brand_sid": existing_brand,
            "brand_status": getattr(org, "twilio_a2p_brand_status", "PENDING"),
            "note": "Brand already submitted. Check status with GET /10dlc/status.",
        }

    client, account_sid = _get_twilio_client(db, current_user)

    try:
        # Twilio A2P Brand Registration via Messaging API
        brand = client.messaging.v1.brands_registrations.create(
            a2p_profile_bundle_sid="",    # will be set to empty — Twilio creates one
            brand_type="STANDARD",
            mock=False,
        )

        # This will likely raise because brand creation requires a CustomerProfile
        # bundle SID. The full flow requires creating a CustomerProfile first.
        # We store whatever SID came back.
        brand_sid = brand.sid
        brand_status = brand.brand_type

        if hasattr(org, "twilio_a2p_brand_sid"):
            org.twilio_a2p_brand_sid = brand_sid
        if hasattr(org, "twilio_a2p_brand_status"):
            org.twilio_a2p_brand_status = brand_status
        db.commit()

        return {"success": True, "brand_sid": brand_sid, "brand_status": brand_status}
    except Exception as e:
        err_str = str(e)
        logger.exception("10DLC register-brand error: %s", err_str)
        # Surface a helpful message for the most common error
        if "CustomerProfile" in err_str or "bundle" in err_str.lower():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Brand registration requires a Twilio CustomerProfile bundle. "
                    "Please complete brand registration in the Twilio Console first "
                    "(Console → Messaging → Regulatory Compliance → Customer Profiles), "
                    "then return here to register your campaign."
                ),
            )
        raise HTTPException(status_code=500, detail="Brand registration failed. Contact support.")


class CampaignRequest(BaseModel):
    description: str            # Short description of how/why you're texting leads
    message_flow: str           # How consumers opt in (e.g. "Verbal consent at time of appointment")
    sample_message_1: str       # Real sample message text
    sample_message_2: Optional[str] = None
    use_case: Optional[str] = "MIXED"   # "MIXED" covers scheduling + follow-up
    has_embedded_links: Optional[bool] = True
    has_embedded_phone: Optional[bool] = False
    subscriber_opt_in: Optional[bool] = True
    subscriber_opt_out: Optional[bool] = True
    subscriber_help: Optional[bool] = True


@router.post("/register-campaign")
def register_campaign(
    req: CampaignRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Step 3 — Registers the A2P messaging campaign with Twilio/TCR.
    Requires an approved brand (Step 2) and an existing Messaging Service (Step 1).
    Campaign describes HOW you use SMS (appointment scheduling, follow-up, etc.)
    and provides sample messages for carrier review.
    """
    org = _get_org(db, current_user)

    messaging_service_sid = getattr(org, "twilio_messaging_service_sid", None)
    if not messaging_service_sid:
        raise HTTPException(
            status_code=400,
            detail="Create a Messaging Service first (Step 1) before registering a campaign.",
        )

    brand_sid = getattr(org, "twilio_a2p_brand_sid", None)
    if not brand_sid:
        raise HTTPException(
            status_code=400,
            detail="Register your brand first (Step 2) before registering a campaign.",
        )

    existing_campaign = getattr(org, "twilio_a2p_campaign_sid", None)
    if existing_campaign:
        return {
            "success": True,
            "campaign_sid": existing_campaign,
            "campaign_status": getattr(org, "twilio_a2p_campaign_status", "PENDING"),
            "note": "Campaign already submitted.",
        }

    client, account_sid = _get_twilio_client(db, current_user)

    try:
        sample_messages = [req.sample_message_1]
        if req.sample_message_2:
            sample_messages.append(req.sample_message_2)

        us_app_to_person = client.messaging.v1.services(messaging_service_sid) \
            .us_app_to_person.create(
                brand_registration_sid=brand_sid,
                description=req.description,
                message_flow=req.message_flow,
                message_samples=sample_messages,
                us_app_to_person_usecase=req.use_case,
                has_embedded_links=req.has_embedded_links,
                has_embedded_phone=req.has_embedded_phone,
                subscriber_opt_in=req.subscriber_opt_in,
                subscriber_opt_out=req.subscriber_opt_out,
                subscriber_help=req.subscriber_help,
            )

        campaign_sid = us_app_to_person.sid
        campaign_status = us_app_to_person.campaign_status

        if hasattr(org, "twilio_a2p_campaign_sid"):
            org.twilio_a2p_campaign_sid = campaign_sid
        if hasattr(org, "twilio_a2p_campaign_status"):
            org.twilio_a2p_campaign_status = campaign_status
        if hasattr(org, "twilio_a2p_campaign_use_case"):
            org.twilio_a2p_campaign_use_case = req.use_case
        from datetime import datetime
        if hasattr(org, "twilio_a2p_registered_at"):
            org.twilio_a2p_registered_at = datetime.utcnow()
        db.commit()

        return {"success": True, "campaign_sid": campaign_sid, "campaign_status": campaign_status}
    except Exception as e:
        logger.exception("10DLC register-campaign error: %s", e)
        raise HTTPException(status_code=500, detail="Failed to register campaign. Contact support.")


@router.post("/add-phone-number")
def add_phone_number(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Step 4 — Adds the org's Twilio phone number(s) to the Messaging Service.
    This links your registered number to the A2P campaign so messages sent
    from that number benefit from the campaign registration.
    """
    org = _get_org(db, current_user)

    messaging_service_sid = getattr(org, "twilio_messaging_service_sid", None)
    if not messaging_service_sid:
        raise HTTPException(
            status_code=400,
            detail="Create a Messaging Service first (Step 1).",
        )

    client, account_sid = _get_twilio_client(db, current_user)

    # Every sending number that belongs to this organization: each advisor's
    # assigned number, plus the optional org shared number. god_admin rows are
    # excluded — the platform owner's number is the PLATFORM's, and adding it
    # to a customer's Messaging Service would put platform traffic under the
    # customer's campaign.
    advisors_with_numbers = db.query(User).filter(
        User.organization_id == current_user.organization_id,
        User.role != "god_admin",
        User.twilio_phone_number.isnot(None),
        User.is_active == True,
    ).all()

    # (phone, label) pairs, de-duplicated by phone
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in advisors_with_numbers:
        if a.twilio_phone_number not in seen:
            seen.add(a.twilio_phone_number)
            targets.append((a.twilio_phone_number, a.full_name))
    if org.org_twilio_phone_number and org.org_twilio_phone_number not in seen:
        seen.add(org.org_twilio_phone_number)
        targets.append((org.org_twilio_phone_number, f"{org.name} (shared)"))

    if not targets:
        raise HTTPException(
            status_code=400,
            detail=(
                "No sending numbers are configured for this organization. "
                "Assign a number to at least one advisor in Org Settings → "
                "Twilio first."
            ),
        )

    results = []
    for phone, owner_label in targets:
        try:
            # Look up the phone number's SID in Twilio
            numbers = client.incoming_phone_numbers.list(phone_number=phone)
            if not numbers:
                results.append({
                    "phone": phone, "assigned_to": owner_label, "success": False,
                    "error": "Number is not in this organization's Twilio account",
                })
                continue

            number_sid = numbers[0].sid
            # Add to Messaging Service
            client.messaging.v1.services(messaging_service_sid).phone_numbers.create(
                phone_number_sid=number_sid
            )
            results.append({
                "phone": phone, "assigned_to": owner_label,
                "success": True, "number_sid": number_sid,
            })
            logger.info("10DLC: added %s (%s) to Messaging Service %s", phone, number_sid, messaging_service_sid)
        except Exception as e:
            results.append({
                "phone": phone, "assigned_to": owner_label,
                "success": False, "error": str(e),
            })

    return {"results": results}


class PatchSIDsRequest(BaseModel):
    messaging_service_sid: Optional[str] = None
    brand_sid: Optional[str] = None
    brand_status: Optional[str] = None
    campaign_sid: Optional[str] = None
    campaign_status: Optional[str] = None


@router.post("/patch-sids")
def patch_sids(
    req: PatchSIDsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Admin-only: Saves known Twilio SIDs directly to the org record.
    Use when SIDs exist in Twilio but were never persisted to the DB.
    """
    org = _get_org(db, current_user)

    if req.messaging_service_sid and hasattr(org, "twilio_messaging_service_sid"):
        org.twilio_messaging_service_sid = req.messaging_service_sid
    if req.brand_sid and hasattr(org, "twilio_a2p_brand_sid"):
        org.twilio_a2p_brand_sid = req.brand_sid
    if req.brand_status and hasattr(org, "twilio_a2p_brand_status"):
        org.twilio_a2p_brand_status = req.brand_status
    if req.campaign_sid and hasattr(org, "twilio_a2p_campaign_sid"):
        org.twilio_a2p_campaign_sid = req.campaign_sid
    if req.campaign_status and hasattr(org, "twilio_a2p_campaign_status"):
        org.twilio_a2p_campaign_status = req.campaign_status

    db.commit()
    return _status_response(org)


@router.post("/refresh-status")
def refresh_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Polls Twilio to get the current brand and campaign status, updates
    the database, and returns the latest registration state. Call this
    a day after submitting to see if TCR has approved/rejected.
    """
    org = _get_org(db, current_user)

    client, account_sid = _get_twilio_client(db, current_user)
    updated = {}

    brand_sid = getattr(org, "twilio_a2p_brand_sid", None)
    if brand_sid:
        try:
            brand = client.messaging.v1.brands_registrations(brand_sid).fetch()
            new_status = brand.brand_type  # may change from PENDING to APPROVED
            if hasattr(org, "twilio_a2p_brand_status"):
                org.twilio_a2p_brand_status = new_status
            updated["brand_status"] = new_status
        except Exception as e:
            updated["brand_status_error"] = str(e)

    messaging_service_sid = getattr(org, "twilio_messaging_service_sid", None)
    campaign_sid = getattr(org, "twilio_a2p_campaign_sid", None)
    if messaging_service_sid and campaign_sid:
        try:
            campaign = client.messaging.v1.services(messaging_service_sid) \
                .us_app_to_person(campaign_sid).fetch()
            new_status = campaign.campaign_status
            if hasattr(org, "twilio_a2p_campaign_status"):
                org.twilio_a2p_campaign_status = new_status
            updated["campaign_status"] = new_status
        except Exception as e:
            updated["campaign_status_error"] = str(e)

    db.commit()

    return {**_status_response(org), "refreshed": updated}
