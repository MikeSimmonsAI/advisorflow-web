"""EvoSys Sales: Mike's one booking link for anybody (services/evosys_sales/open_booking.py).

GET  /evosys-sales-public/meet   the "who are you" step (no login)
POST /evosys-sales-public/meet   find or add the lead, return their own booking page
GET  /sales/evosys/open-booking  the link itself, for the EvoSys Pro team

GET  /evosys-sales-public/walkthrough   free cleaning walkthrough request page (no login)
POST /evosys-sales-public/walkthrough   save the request, alert Mike
GET  /sales/evosys/walkthrough-requests the requests, for EvoSys sales managers
"""
# NO `from __future__ import annotations` IN A ROUTER MODULE - see the note in
# god_access_router.py. Under a `@limiter.limit` wrapper FastAPI cannot resolve
# a stringified body annotation and the app refuses to import.
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db
from app.limiter import limiter
from app.routers.evosys_sales_router import Ctx, ctx
from app.services.evosys_sales import open_booking as OB
from app.services.evosys_sales import walkthrough_request as WR

public_router = APIRouter(prefix="/evosys-sales-public", tags=["evosys-sales-public"])
# Short link for cards and texts: links.evosyspro.live/walkthrough
short_router = APIRouter(tags=["evosys-sales-public"])
router = APIRouter(prefix="/sales/evosys", tags=["evosys-sales"])

_HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}


@public_router.get("/meet", response_class=HTMLResponse)
def meet_page():
    return HTMLResponse(OB.render(), headers=_HEADERS)


class MeetIn(BaseModel):
    name: Optional[str] = ""
    company: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    topic: Optional[str] = ""
    website: Optional[str] = ""          # honeypot: people never fill it


@public_router.post("/meet")
@limiter.limit("6/minute")
def meet_start(request: Request, body: MeetIn, db: Session = Depends(get_db)):
    if (body.website or "").strip():
        return {"status": "error", "message": "Something went wrong. Please try again."}
    data = OB.validate(body.model_dump())
    if data["errors"]:
        raise HTTPException(422, detail={"errors": data["errors"]})
    return OB.start(db, data)


@router.get("/open-booking")
def open_booking_link(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    host = OB.owner(db)
    return {"url": OB.link(), "calendar_of": host.email if host else None, "ready": host is not None}


# ── free commercial-cleaning walkthrough requests (owner request 2026-11-22) ──

@public_router.get("/walkthrough", response_class=HTMLResponse)
def walkthrough_page():
    return HTMLResponse(WR.render(), headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


class WalkthroughIn(BaseModel):
    name: Optional[str] = ""
    title: Optional[str] = ""
    company: Optional[str] = ""
    email: Optional[str] = ""
    phone: Optional[str] = ""
    address: Optional[str] = ""
    building_type: Optional[str] = ""
    square_feet: Optional[str] = ""
    current_service: Optional[str] = ""
    timing: Optional[str] = ""
    preferred_times: Optional[str] = ""
    notes: Optional[str] = ""
    src: Optional[str] = ""
    website: Optional[str] = ""          # honeypot: people never fill it


@public_router.post("/walkthrough")
@limiter.limit("6/minute")
def walkthrough_submit(request: Request, body: WalkthroughIn, db: Session = Depends(get_db)):
    if (body.website or "").strip():
        return {"status": "ok"}          # a bot: look done, save nothing
    data = WR.validate(body.model_dump())
    if data["errors"]:
        raise HTTPException(422, detail={"errors": data["errors"]})
    return WR.save(db, data)


@router.get("/walkthrough-requests")
def walkthrough_requests(c: Ctx = Depends(ctx), db: Session = Depends(get_db)):
    if not c.manager:
        raise HTTPException(403, "Only an EvoSys sales manager can see walkthrough requests.")
    return {"url": OB.link().replace(OB.PATH, WR.PATH), "requests": WR.listing(db)}


@short_router.get("/walkthrough", include_in_schema=False)
def walkthrough_short(src: Optional[str] = None):
    """links.evosyspro.live/walkthrough -> the request page (owner request 2026-11-22)."""
    import re as _re
    tag = _re.sub(r"[^a-z0-9_-]", "", (src or "").lower())[:60]
    return RedirectResponse(WR.PATH + ("?src=" + tag if tag else ""), status_code=302)
