"""
Social Media Lead Capture
-------------------------
Receives incoming leads from:
  - Meta Lead Ads (Facebook + Instagram)
  - TikTok Lead Generation

Each org gets a unique webhook URL containing their social_webhook_token.
Org admins copy this URL from Settings and paste it into their Meta/TikTok
lead form configuration in the platform's developer/business console.

Meta webhook flow:
  GET  /webhooks/meta  — hub challenge verification (Meta pings this once on setup)
  POST /webhooks/meta?org_token=<token>  — lead notification received

  Meta lead notifications contain a leadgen_id. We call the Graph API
  (using the org's stored meta_page_access_token) to fetch the actual
  lead fields (name, phone, email, etc.), then create a Lead record.

TikTok webhook flow:
  GET  /webhooks/tiktok?org_token=<token>  — challenge verification
  POST /webhooks/tiktok?org_token=<token>  — lead notification received

  TikTok sends lead data directly in the webhook body.

Org settings needed (all stored in organizations table):
  social_webhook_token      — unique URL token, auto-generated on first access
  meta_page_access_token    — Facebook Page access token for Graph API calls
  meta_webhook_verify_token — the verify token you set in Meta Dev Console
  tiktok_webhook_secret     — TikTok webhook secret for signature verification
"""

import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.models import Lead, Organization, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["social-webhooks"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_org_by_token(db: Session, org_token: str) -> Organization:
    """Resolve the org_token param to an Organization. Raises 403 if invalid."""
    if not org_token:
        raise HTTPException(status_code=403, detail="Missing org_token")
    org = db.query(Organization).filter(Organization.social_webhook_token == org_token).first()
    if not org:
        raise HTTPException(status_code=403, detail="Invalid org_token")
    return org


def _upsert_social_lead(
    db: Session,
    org: Organization,
    first_name: str,
    last_name: str,
    phone: Optional[str],
    email: Optional[str],
    source: str,  # "facebook" | "instagram" | "tiktok"
    source_ref: Optional[str] = None,  # leadgen_id or tiktok form id
) -> Lead:
    """
    Create or update a lead from a social platform. Deduplicates by phone/email
    within the org. Returns the lead record (created or existing).
    """
    # Dedup: phone first, then email
    existing = None
    if phone:
        existing = db.query(Lead).filter(
            Lead.organization_id == org.id,
            Lead.phone == phone,
        ).first()
    if not existing and email:
        existing = db.query(Lead).filter(
            Lead.organization_id == org.id,
            Lead.email == email,
        ).first()

    if existing:
        logger.info("social_webhook: deduped %s lead to existing lead %s", source, existing.id)
        return existing

    # Find the first active advisor in the org to assign the lead to
    row = db.execute(
        text(
            "SELECT id FROM users "
            "WHERE organization_id = :org_id AND role IN ('advisor','admin') "
            "ORDER BY created_at ASC LIMIT 1"
        ),
        {"org_id": org.id},
    ).fetchone()
    assigned_user_id = row[0] if row else None

    lead = Lead(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        user_id=assigned_user_id,
        first_name=first_name or "Unknown",
        last_name=last_name or "",
        phone=phone,
        email=email,
        source=source,
        status="new",
        tier="new_inquiry",
        message_track="new_inquiry_intro",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    logger.info(
        "social_webhook: created lead %s from %s (org=%s, ref=%s)",
        lead.id, source, org.id, source_ref,
    )
    return lead



# ── Meta / Facebook / Instagram Endpoints ─────────────────────────────────────

@router.get("/meta")
def meta_webhook_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    org_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Meta webhook verification endpoint. Meta GETs this URL when you first
    register the webhook in Meta's developer console. We validate the
    verify_token against the org's stored meta_webhook_verify_token and
    respond with the hub.challenge to confirm ownership.
    """
    if hub_mode != "subscribe":
        raise HTTPException(status_code=400, detail="hub.mode must be 'subscribe'")

    if not hub_verify_token:
        raise HTTPException(status_code=400, detail="Missing hub.verify_token")

    # Look up org by token
    org = _get_org_by_token(db, org_token)

    stored_token = getattr(org, "meta_webhook_verify_token", None)
    if not stored_token or stored_token != hub_verify_token:
        raise HTTPException(status_code=403, detail="verify_token mismatch")

    return Response(content=hub_challenge or "", media_type="text/plain")


@router.post("/meta")
async def meta_webhook_receive(
    request: Request,
    org_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Receives Meta Lead Ads notifications (Facebook + Instagram).
    Each notification contains a leadgen_id. We call the Graph API to
    fetch the actual field values, then create a Lead.

    Meta signs every request with X-Hub-Signature-256 using the app secret.
    We verify this before processing so only genuine Meta payloads are accepted.
    """
    org = _get_org_by_token(db, org_token)

    body = await request.body()

    # HMAC signature verification using the org's stored Meta app secret.
    # Meta sends:  X-Hub-Signature-256: sha256=<hex>
    meta_app_secret = getattr(org, "meta_app_secret", None)
    if meta_app_secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            meta_app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            logger.warning("Meta webhook HMAC mismatch for org %s", org.id)
            raise HTTPException(status_code=403, detail="Signature mismatch")
    else:
        logger.warning(
            "Meta webhook received for org %s with no meta_app_secret configured — "
            "payload accepted but not verified. Set meta_app_secret to enable HMAC.",
            org.id,
        )

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Meta sends an array of entries
    entries = payload.get("entry", [])
    created_count = 0

    meta_token = getattr(org, "meta_page_access_token", None)

    for entry in entries:
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue
            value = change.get("value", {})
            leadgen_id = value.get("leadgen_id")
            ad_id = value.get("ad_id")
            source = "instagram" if value.get("is_organic") else "facebook"

            first_name, last_name, phone, email = "", "", None, None

            if meta_token and leadgen_id:
                # Fetch full lead data from Meta Graph API
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(
                            f"https://graph.facebook.com/v19.0/{leadgen_id}",
                            params={"access_token": meta_token, "fields": "field_data"},
                        )
                    if resp.status_code == 200:
                        data = resp.json()
                        for field in data.get("field_data", []):
                            fname = field.get("name", "").lower()
                            val = (field.get("values") or [""])[0]
                            if fname in ("full_name", "name"):
                                parts = val.split(" ", 1)
                                first_name = parts[0]
                                last_name = parts[1] if len(parts) > 1 else ""
                            elif fname == "first_name":
                                first_name = val
                            elif fname == "last_name":
                                last_name = val
                            elif fname in ("phone_number", "phone"):
                                phone = val
                            elif fname == "email":
                                email = val
                    else:
                        logger.warning("Meta Graph API returned %s for leadgen %s", resp.status_code, leadgen_id)
                except Exception as exc:
                    logger.error("Meta Graph API fetch failed: %s", exc)
            else:
                # No access token — pull whatever Meta included inline (older format)
                for field in value.get("field_data", []):
                    fname = field.get("name", "").lower()
                    val = (field.get("values") or [""])[0]
                    if fname in ("full_name", "name"):
                        parts = val.split(" ", 1)
                        first_name = parts[0]
                        last_name = parts[1] if len(parts) > 1 else ""
                    elif fname == "first_name":
                        first_name = val
                    elif fname == "last_name":
                        last_name = val
                    elif fname in ("phone_number", "phone"):
                        phone = val
                    elif fname == "email":
                        email = val

            _upsert_social_lead(
                db, org, first_name, last_name, phone, email,
                source=source, source_ref=leadgen_id or ad_id,
            )
            created_count += 1

    return {"received": True, "processed": created_count}



# ── TikTok Lead Gen Endpoints ─────────────────────────────────────────────────

@router.get("/tiktok")
async def tiktok_webhook_verify(
    request: Request,
    org_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    TikTok webhook verification. TikTok GETs this URL and expects us to
    echo back a specific parameter to confirm webhook ownership.
    """
    _get_org_by_token(db, org_token)
    # TikTok sends a 'challenge' query param we must echo back
    challenge = request.query_params.get("challenge", "")
    return Response(content=challenge, media_type="text/plain")


@router.post("/tiktok")
async def tiktok_webhook_receive(
    request: Request,
    org_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Receives TikTok Lead Generation form submissions. TikTok sends the
    lead fields directly in the webhook payload.
    """
    org = _get_org_by_token(db, org_token)

    body = await request.body()

    # HMAC signature check — reject the request if no secret is configured.
    # Silently accepting unsigned webhooks lets any caller who knows the
    # org_token inject fake leads into the CRM.
    secret = getattr(org, "tiktok_webhook_secret", None)
    if not secret:
        logger.error(
            "TikTok webhook received for org %s but tiktok_webhook_secret is not set. "
            "Request rejected — configure the secret in org settings.",
            org.id,
        )
        raise HTTPException(
            status_code=403,
            detail="TikTok webhook secret not configured for this org.",
        )
    sig_header = request.headers.get("x-tiktok-signature", "")
    expected = hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig_header, expected):
        raise HTTPException(status_code=403, detail="Invalid TikTok signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    first_name, last_name, phone, email = "", "", None, None

    # TikTok sends fields as a list under "lead_data" or "answers"
    lead_data = payload.get("lead_data") or payload.get("answers") or []
    for field in lead_data:
        key = (field.get("name") or field.get("field_id") or "").lower()
        val = field.get("value") or field.get("answer") or ""
        if key in ("full_name", "name"):
            parts = str(val).split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
        elif key == "first_name":
            first_name = str(val)
        elif key == "last_name":
            last_name = str(val)
        elif key in ("phone_number", "phone"):
            phone = str(val)
        elif key == "email":
            email = str(val)

    # Fallback: top-level fields
    if not first_name:
        first_name = payload.get("first_name") or payload.get("name", "")
    if not last_name:
        last_name = payload.get("last_name", "")
    if not phone:
        phone = payload.get("phone") or payload.get("phone_number")
    if not email:
        email = payload.get("email")

    lead = _upsert_social_lead(
        db, org, first_name, last_name, phone, email,
        source="tiktok",
        source_ref=payload.get("form_id") or payload.get("ad_id"),
    )
    return {"received": True, "lead_id": lead.id}


# ── Google Ads Lead Form Extension Endpoints ──────────────────────────────────

@router.get("/google")
def google_ads_webhook_verify(
    google_key: str = Query(None),
    org_token: Optional[str] = Query(None),
):
    """
    Google Ads Lead Form webhook verification. Google GETs this URL after
    you register it in the Google Ads Lead Form Extension settings.
    It expects a 200 OK response (with the google_key echoed back).
    """
    return {"google_key": google_key}


@router.post("/google")
async def google_ads_webhook_receive(
    request: Request,
    org_token: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """
    Receives Google Ads Lead Form Extension submissions.
    Google sends lead data directly in the request body as JSON.

    Standard fields Google sends:
      lead_id, form_id, campaign_id, google_key,
      user_column_data (list of {column_name, string_value})
    """
    org = _get_org_by_token(db, org_token)

    try:
        body = await request.body()
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info("google_ads_webhook: org=%s payload=%s", org.id, payload)

    first_name, last_name, phone, email = "", "", None, None

    # Extract from user_column_data list (primary Google Ads format)
    for col in payload.get("user_column_data") or []:
        col_name = (col.get("column_name") or col.get("column_id") or "").lower()
        col_val = col.get("string_value") or col.get("value") or ""
        if col_name in ("full_name", "name"):
            parts = str(col_val).split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""
        elif col_name in ("first_name", "given_name"):
            first_name = str(col_val)
        elif col_name in ("last_name", "family_name"):
            last_name = str(col_val)
        elif col_name in ("phone_number", "phone"):
            phone = str(col_val)
        elif col_name == "email":
            email = str(col_val)

    # Fallback: top-level fields
    if not first_name:
        first_name = payload.get("first_name") or ""
    if not last_name:
        last_name = payload.get("last_name") or ""
    if not phone:
        phone = payload.get("phone") or payload.get("phone_number")
    if not email:
        email = payload.get("email")

    lead = _upsert_social_lead(
        db, org, first_name, last_name, phone, email,
        source="google_ads",
        source_ref=str(payload.get("lead_id") or payload.get("form_id") or ""),
    )
    return {"received": True, "lead_id": lead.id}


# ── Utility: generate/return webhook URLs for org settings UI ─────────────────

@router.get("/token")
def get_or_create_webhook_token(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Returns the org's unique social webhook token (auto-generates if missing).
    Used by the OrgSettings UI to display the webhook URLs to copy.
    Requires org_admin or super_admin.
    """

    org = db.query(Organization).filter(
        Organization.id == current_user.organization_id
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")

    if not getattr(org, "social_webhook_token", None):
        org.social_webhook_token = str(uuid.uuid4()).replace("-", "")
        db.commit()
        db.refresh(org)

    base_url = os.getenv("BOOKING_BASE_URL", "https://advisorflow-backend.onrender.com")
    token = org.social_webhook_token
    return {
        "social_webhook_token": token,
        "meta_webhook_url": f"{base_url}/webhooks/meta?org_token={token}",
        "tiktok_webhook_url": f"{base_url}/webhooks/tiktok?org_token={token}",
        "google_webhook_url": f"{base_url}/webhooks/google?org_token={token}",
    }
