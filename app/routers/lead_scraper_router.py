"""
Lead Scraper -- TIER 3
----------------------
Search for local businesses by ZIP code + type using Google Places API,
validate phone numbers via Twilio Lookup, then import as leads.

Required env vars:
  GOOGLE_PLACES_API_KEY   -- from Google Cloud Console (Places API enabled)

Twilio Lookup uses the org's own Twilio creds (falls back to global).

Endpoints:
  POST /scraper/search     -- search Google Places, returns raw results
  POST /scraper/validate   -- run Twilio Lookup on a batch of phones
  POST /scraper/import     -- create Lead records from validated results
"""

import logging
import os
import uuid
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user
from app.models.models import Lead, Organization, User
from app.utils.crypto import decrypt_value

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scraper", tags=["lead-scraper"])


# -- Request / Response Models ------------------------------------------------

class ScrapeSearchRequest(BaseModel):
    zip_code: str = Field(..., min_length=5, max_length=10)
    business_type: str = Field(..., min_length=2, max_length=100)
    radius_miles: int = Field(default=10, ge=1, le=50)
    max_results: int = Field(default=20, ge=1, le=60)


class ScrapedBusiness(BaseModel):
    place_id: str
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    website: Optional[str] = None
    rating: Optional[float] = None
    phone_type: Optional[str] = None  # "mobile" | "landline" | "voip" | "unknown"
    channel: Optional[str] = None    # "sms" | "email" | "voice"


class ValidateRequest(BaseModel):
    businesses: List[ScrapedBusiness]


class ImportRequest(BaseModel):
    businesses: List[ScrapedBusiness]
    list_name: Optional[str] = None


class ScrapeSearchResponse(BaseModel):
    results: List[ScrapedBusiness]
    total: int
    query: str


# -- Helpers ------------------------------------------------------------------

def _miles_to_meters(miles: int) -> int:
    return int(miles * 1609.34)


async def _google_places_search(
    query: str, zip_code: str, radius_m: int, max_results: int, api_key: str
) -> List[dict]:
    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
    params = {"query": f"{query} near {zip_code}", "radius": radius_m, "key": api_key}
    results = []
    next_page_token = None

    async with httpx.AsyncClient(timeout=15) as client:
        while len(results) < max_results:
            if next_page_token:
                import asyncio
                await asyncio.sleep(2)
                params = {"pagetoken": next_page_token, "key": api_key}
            resp = await client.get(url, params=params)
            data = resp.json()
            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                logger.warning("Google Places error: %s", data.get("status"))
                break
            for place in data.get("results", []):
                results.append(place)
                if len(results) >= max_results:
                    break
            next_page_token = data.get("next_page_token")
            if not next_page_token:
                break
    return results



async def _google_place_details(place_id: str, api_key: str) -> dict:
    url = "https://maps.googleapis.com/maps/api/place/details/json"
    params = {"place_id": place_id, "fields": "formatted_phone_number,website", "key": api_key}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        return resp.json().get("result", {})


def _twilio_lookup(phone: str, account_sid: str, auth_token: str) -> str:
    import requests as _req
    url = f"https://lookups.twilio.com/v2/PhoneNumbers/{phone}?Fields=line_type_intelligence"
    try:
        r = _req.get(url, auth=(account_sid, auth_token), timeout=8)
        if r.status_code == 200:
            lti = r.json().get("line_type_intelligence") or {}
            t = (lti.get("type") or "unknown").lower()
            if "mobile" in t or "cellular" in t:
                return "mobile"
            if "landline" in t or "fixed" in t:
                return "landline"
            if "voip" in t:
                return "voip"
    except Exception as exc:
        logger.warning("Twilio Lookup error for %s: %s", phone, exc)
    return "unknown"


def _channel_for_type(phone_type: str, has_email: bool) -> str:
    if phone_type == "mobile":
        return "sms"
    if has_email:
        return "email"
    return "voice"


def _get_twilio_creds(current_user: User, db: Session) -> tuple:
    if current_user.twilio_account_sid and current_user.twilio_auth_token_encrypted:
        return (current_user.twilio_account_sid, decrypt_value(current_user.twilio_auth_token_encrypted))
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if org and getattr(org, "twilio_account_sid", None) and getattr(org, "twilio_auth_token_encrypted", None):
        return (org.twilio_account_sid, decrypt_value(org.twilio_auth_token_encrypted))
    return (os.getenv("TWILIO_ACCOUNT_SID", ""), os.getenv("TWILIO_AUTH_TOKEN", ""))


# -- Endpoints ----------------------------------------------------------------

@router.post("/search", response_model=ScrapeSearchResponse)
async def scrape_search(
    req: ScrapeSearchRequest,
    current_user: User = Depends(get_current_user),
):
    """Search Google Places for businesses matching type + ZIP."""
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="Google Places API not configured. Set GOOGLE_PLACES_API_KEY.")

    radius_m = _miles_to_meters(req.radius_miles)
    places = await _google_places_search(req.business_type.strip(), req.zip_code, radius_m, req.max_results, api_key)

    businesses = []
    for place in places:
        place_id = place.get("place_id", "")
        name = place.get("name", "Unknown")
        address = place.get("formatted_address") or place.get("vicinity") or ""
        rating = place.get("rating")
        details = await _google_place_details(place_id, api_key) if place_id else {}
        businesses.append(ScrapedBusiness(
            place_id=place_id,
            name=name,
            phone=details.get("formatted_phone_number"),
            address=address,
            website=details.get("website"),
            rating=rating,
        ))

    return ScrapeSearchResponse(
        results=businesses,
        total=len(businesses),
        query=f"{req.business_type.strip()} near {req.zip_code}",
    )


@router.post("/validate")
def scrape_validate(
    req: ValidateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run Twilio Lookup on each business phone. Returns updated list with phone_type and channel."""
    account_sid, auth_token = _get_twilio_creds(current_user, db)
    if not account_sid or not auth_token:
        raise HTTPException(status_code=503, detail="Twilio not configured -- cannot validate phone types.")

    updated = []
    for biz in req.businesses:
        phone_type = "unknown"
        if biz.phone:
            phone_type = _twilio_lookup(biz.phone, account_sid, auth_token)
        channel = _channel_for_type(phone_type, bool(biz.website))
        updated.append(biz.model_copy(update={"phone_type": phone_type, "channel": channel}))

    return {"results": [b.model_dump() for b in updated], "total": len(updated)}


@router.post("/import", status_code=201)
def scrape_import(
    req: ImportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Import validated businesses as leads. Deduplicates by phone within the org."""
    org_id = current_user.organization_id
    list_name = req.list_name or "Scraper Import"
    created_count = 0
    skipped_count = 0

    for biz in req.businesses:
        if biz.phone:
            existing = db.query(Lead).filter(
                Lead.organization_id == org_id,
                Lead.phone == biz.phone,
            ).first()
            if existing:
                skipped_count += 1
                continue

        name_parts = (biz.name or "Unknown").split(" ", 2)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        channel = biz.channel or "sms"
        source_map = {"sms": "google_places_sms", "email": "google_places_email", "voice": "google_places_voice"}
        source = source_map.get(channel, "google_places")

        lead = Lead(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            user_id=current_user.id,
            first_name=first_name,
            last_name=last_name,
            phone=biz.phone,
            email=None,
            source=source,
            status="new",
            tier="new_inquiry",
            message_track="new_inquiry_intro",
            import_list_name=list_name,
            notes=f"Business: {biz.name}\nAddress: {biz.address or 'N/A'}\nWebsite: {biz.website or 'N/A'}\nPhone type: {biz.phone_type or 'unknown'}",
        )
        db.add(lead)
        created_count += 1

    db.commit()
    logger.info("scraper_import: org=%s list='%s' created=%d skipped=%d", org_id, list_name, created_count, skipped_count)
    return {"success": True, "created": created_count, "skipped_duplicates": skipped_count, "list_name": list_name}
