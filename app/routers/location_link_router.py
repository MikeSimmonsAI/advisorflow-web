"""<location>.<LOCATION_LINK_DOMAIN>/<code> -> the branded booking page.

Registered LAST, so every real route matches first, and it answers only on a
location host. The name in front must be the family's own location."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_db

router = APIRouter(tags=["location-links"])


@router.get("/{code}", include_in_schema=False)
def location_booking_redirect(code: str, request: Request, db: Session = Depends(get_db)):
    from app.services.programs import location_links as ll
    key = ll.parse_host(request.headers.get("host") or "")
    if not key:
        raise HTTPException(status_code=404, detail="Not Found")
    from app.models.models import BookingLink, Lead
    from app.services.programs.identity import location_profile_for_lead
    from app.services.public_identity import booking_url
    link = db.query(BookingLink).filter(BookingLink.short_code == code.strip().lower()).first()
    lead = db.query(Lead).filter(Lead.id == link.lead_id).first() if link else None
    prof = location_profile_for_lead(db, lead) if lead else None
    if prof is None or ll.profile_key(prof) != key:
        raise HTTPException(status_code=404, detail="Not Found")
    url = booking_url(db, lead.organization_id, link.token)
    if not url:
        raise HTTPException(status_code=404, detail="Not Found")
    return RedirectResponse(url, status_code=302)
