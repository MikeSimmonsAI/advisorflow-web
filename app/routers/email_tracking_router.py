"""
Email Open/Click Tracking Endpoints

Deliberately UNAUTHENTICATED - these are hit directly by the
recipient's email client or browser, which has no AdvisorFlow login at
all. Each endpoint only needs the email_message_id embedded in the URL
(see email_tracking_service.inject_tracking) to know which row to
update. No sensitive data is exposed by either endpoint - they accept
an ID and either return a 1x1 image or perform a redirect, nothing else.
"""

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone

from app.deps import get_db
from app.models.models import EmailMessage

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
    message = db.query(EmailMessage).filter(EmailMessage.id == email_message_id).first()
    if message:
        message.click_count = (message.click_count or 0) + 1
        message.last_clicked_at = datetime.now(timezone.utc)
        db.commit()

    return RedirectResponse(url=url, status_code=302)
