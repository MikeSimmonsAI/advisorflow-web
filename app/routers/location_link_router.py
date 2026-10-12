"""<location>.<LOCATION_LINK_DOMAIN>/<code> -> the branded booking page.

Registered LAST, so every real route matches first, and it answers only on a
location host. The name in front must be the family's own location. A family
who opens an old or mistyped link, or the bare location address, gets a plain
page with the location's name and phone - never a raw error."""
import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.deps import get_db

router = APIRouter(tags=["location-links"])


def _profile_for_key(db: Session, key: str):
    from app.models.program_models import LocationProfile
    from app.services.programs import location_links as ll
    for p in db.query(LocationProfile).filter(LocationProfile.is_review_bucket.is_(False)).all():
        if ll.profile_key(p) == key:
            return p
    return None


def _fmt_phone(raw) -> str:
    d = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(d) == 11 and d[0] == "1":
        d = d[1:]
    return "(%s) %s-%s" % (d[:3], d[3:6], d[6:]) if len(d) == 10 else ""


def _page(prof, headline: str, line: str, status: int) -> HTMLResponse:
    name = html.escape(prof.official_name if prof else "")
    phone = _fmt_phone(getattr(prof, "facility_phone", None)) if prof else ""
    call = ('<a class="call" href="tel:%s">Call %s</a>' % (html.escape(prof.facility_phone), phone)) if phone else ""
    body = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>%(title)s</title>
<style>
body{margin:0;min-height:100vh;background:#f5f0e6;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;color:#1d2a2c;display:flex;align-items:flex-start;justify-content:center;padding:32px 16px;box-sizing:border-box}
.card{max-width:480px;width:100%%;background:#fff;border-radius:18px;overflow:hidden;box-shadow:0 18px 48px rgba(31,59,63,.12);border:1px solid #e6dfd2}
.head{background:linear-gradient(135deg,#1f3b3f,#2d5257);color:#fff;text-align:center;padding:26px 22px;border-bottom:3px solid #c79a3a;font-family:Georgia,serif;font-size:23px;line-height:1.25}
.body{padding:26px 24px 30px;text-align:center}
h1{font-family:Georgia,serif;font-size:24px;margin:0 0 10px}
p{font-size:16px;line-height:1.6;color:#3f4644;margin:0 0 20px}
.call{display:inline-block;background:#1f3b3f;color:#fff;text-decoration:none;font-weight:650;padding:14px 22px;border-radius:12px}
</style></head><body><div class="card">%(head)s<div class="body"><h1>%(headline)s</h1><p>%(line)s</p>%(call)s</div></div></body></html>""" % {
        "title": name or "Not found", "head": ('<div class="head">%s</div>' % name) if name else "",
        "headline": html.escape(headline, quote=False), "line": html.escape(line, quote=False), "call": call}
    return HTMLResponse(body, status_code=status)


@router.get("/", include_in_schema=False)
def location_home(request: Request, db: Session = Depends(get_db)):
    from app.services.programs import location_links as ll
    key = ll.parse_host(request.headers.get("host") or "")
    if not key:
        raise HTTPException(status_code=404, detail="Not Found")
    prof = _profile_for_key(db, key)
    if prof is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return _page(prof, "We're here to help",
                 "To set up a time, use the link in the message we sent you, or give us a call.", 200)


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
    url = booking_url(db, lead.organization_id, link.token) if (prof is not None and ll.profile_key(prof) == key) else ""
    if not url:
        return _page(_profile_for_key(db, key), "This link isn't active",
                     "It may have expired or been typed in wrong. We'd be glad to help you find a time.", 404)
    return RedirectResponse(url, status_code=302)
