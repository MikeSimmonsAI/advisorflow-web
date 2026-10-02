"""
Email Open/Click Tracking Endpoints

Deliberately UNAUTHENTICATED - these are hit directly by the
recipient's email client or browser, which has no AdvisorFlow login at
all. Each endpoint only needs the email_message_id embedded in the URL
(see email_tracking_service.inject_tracking) to know which row to
update. No sensitive data is exposed by either endpoint - they accept
an ID and either return a 1x1 image or perform a redirect, nothing else.
"""

import html
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.deps import get_db
from app.models.models import EmailMessage

import os

# Allowed destination schemes for the click-redirect endpoint.
# Any url that doesn't start with http/https is rejected to prevent
# open-redirect abuse (javascript: / data: / vbscript: etc.).
_ALLOWED_SCHEMES = {"http", "https"}

router = APIRouter(prefix="/email-tracking", tags=["email-tracking"])

# A genuine, valid 1x1 transparent GIF, decoded once at import time -
# this is the actual bytes returned for every open-pixel request,
# regardless of whether the email_message_id matches a real row (see
# open_tracking_pixel below for why a miss still returns this same
# image rather than a 404).
_TRANSPARENT_GIF = bytes.fromhex(
    "47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024c01003b"
)


@router.get("/open/{email_message_id}")
def open_tracking_pixel(email_message_id: str, db: Session = Depends(get_db)):
    """
    Marks an EmailMessage as opened the first time this loads -
    idempotent, only sets opened_at if it isn't already set, so the
    timestamp reflects the FIRST open, not the most recent one (an
    email client may re-fetch images on every view).

    Always returns the same 1x1 transparent GIF regardless of whether
    email_message_id matched a real row - a missing/invalid ID should
    never surface as a broken image or an error to whoever is viewing
    the email, since that's a UX detail entirely outside their control.
    """
    message = db.query(EmailMessage).filter(EmailMessage.id == email_message_id).first()
    if message and message.opened_at is None:
        message.opened_at = datetime.now(timezone.utc)
        db.commit()

    return Response(content=_TRANSPARENT_GIF, media_type="image/gif")


@router.get("/click/{email_message_id}")
def click_tracking_redirect(
    email_message_id: str,
    url: str = Query(...),
    db: Session = Depends(get_db),
):
    """
    Logs a click then redirects to the real original URL - the
    recipient's experience is unaffected, they still land on the
    correct page; the click is just logged on the way through.

    Increments click_count (not a list of individual clicks - see
    EmailMessage model comment for why a simple counter is the right
    level of detail here) and updates last_clicked_at on every click,
    not just the first - unlike opens, repeat clicks across multiple
    links/visits are still meaningful engagement signal worth counting.

    If email_message_id doesn't match a real row, still redirects to
    the original URL rather than erroring - a tracking-data issue on
    our side should never block the recipient from reaching the page
    they actually clicked toward.
    """
    # Reject non-http(s) destinations outright (javascript:, data:, ...).
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid redirect URL.")

    # ONLY A LINK THAT IS IN THE EMAIL IS REDIRECTED. Before, any https URL
    # was 302'd from our own host - an open redirect a phisher could put in
    # front of any page ("advisorflow-backend.onrender.com/... -> evil").
    # The stored body_html is the original, pre-tracking body (see
    # email_tracking_service.inject_tracking), so its hrefs are exactly the
    # destinations we wrote. Anything else - an unknown message id, a URL
    # that is not in that message - gets a plain "you are leaving" page with
    # the link on it: the recipient still reaches the page, nobody can use our
    # host as an invisible hop, and the click counter is only moved by real
    # links in real messages.
    message = db.query(EmailMessage).filter(EmailMessage.id == email_message_id).first()
    if message and _link_in_message(url, message.body_html):
        message.click_count = (message.click_count or 0) + 1
        message.last_clicked_at = datetime.now(timezone.utc)
        db.commit()
        return RedirectResponse(url=url, status_code=302)
    return _leaving_page(url)


_HREF = re.compile(r'href=(["\'])(https?://[^"\']+)\1', re.IGNORECASE)


def _link_in_message(url: str, body_html: str) -> bool:
    """`url` is one of the message's own links (or, for a message sent before
    links were percent-encoded, the part of one that survived the query
    string: same scheme and host, a leading piece of the real link)."""
    host = urlparse(url).netloc.lower()
    for m in _HREF.finditer(body_html or ""):
        link = html.unescape(m.group(2))
        if link == url:
            return True
        if link.startswith(url) and urlparse(link).netloc.lower() == host:
            return True
    return False


def _leaving_page(url: str) -> HTMLResponse:
    host = html.escape(urlparse(url).netloc)
    href = html.escape(url, quote=True)
    body = ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<meta name=\"robots\" content=\"noindex\"><title>Leaving this page</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 16px;color:#1f2937}"
            "a.btn{display:inline-block;margin-top:12px;padding:10px 16px;border-radius:8px;background:#1f2937;color:#fff;text-decoration:none}"
            "code{word-break:break-all}</style></head><body>"
            "<h1 style=\"font-size:20px\">This link goes to another website</h1>"
            "<p>You are about to visit <strong>%s</strong>.</p><p><code>%s</code></p>"
            "<a class=\"btn\" rel=\"noopener noreferrer nofollow\" href=\"%s\">Continue</a>"
            "</body></html>") % (host, href, href)
    return HTMLResponse(body, headers={"Cache-Control": "no-store",
                                       "Referrer-Policy": "no-referrer",
                                       "X-Robots-Tag": "noindex"})
