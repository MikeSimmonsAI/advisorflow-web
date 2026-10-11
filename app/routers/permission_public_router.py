"""The OWNER's side of a permission link. Public: the link token IS the
authorization, and it only ever shows the owner their own street and the last
four digits of the number. Permission is written only when the owner ticks
the (unticked) box and presses Confirm. "No thanks" records nothing but the
answer. See app/services/evosense/permission.py."""
from __future__ import annotations

import html
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.services.evosense import permission as PM

router = APIRouter(prefix="/permission", tags=["permission-public"])

_STYLE = """
body{margin:0;font-family:Arial,Helvetica,sans-serif;background:#f4f6f9;color:#1d2433}
.card{max-width:520px;margin:32px auto;background:#fff;border-radius:12px;padding:28px 24px;
box-shadow:0 2px 12px rgba(0,0,0,.08)}
h1{font-size:22px;margin:0 0 12px}p{line-height:1.5;font-size:16px}
.box{display:flex;gap:12px;align-items:flex-start;background:#f8fafc;border:1px solid #d6dde8;
border-radius:8px;padding:14px;margin:18px 0;font-size:15px;line-height:1.5}
.box input{width:22px;height:22px;flex:0 0 auto;margin-top:2px}
button{font-size:17px;padding:12px 18px;border-radius:8px;border:0;cursor:pointer;width:100%;margin-top:8px}
.yes{background:#1f3864;color:#fff}.no{background:#e9edf3;color:#1d2433}
.small{font-size:13px;color:#5b6577}
@media (max-width:560px){.card{margin:0;border-radius:0;min-height:100vh}}
"""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
           "<meta name='viewport' content='width=device-width,initial-scale=1'>"
           "<meta name='robots' content='noindex,nofollow'><title>%s</title><style>%s</style></head>"
           "<body><main class='card'>%s</main></body></html>" % (html.escape(title), _STYLE, body))
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store",
                                                          "Referrer-Policy": "no-referrer"})


def _closed(link) -> HTMLResponse:
    if link is None:
        return _page("Link not found", "<h1>This link isn't valid</h1><p>Please contact the person "
                                       "who sent it to you.</p>", 404)
    msg = {"confirmed": "Thank you - you've already confirmed. We'll be in touch.",
           "declined": "Got it - you chose not to receive follow-up. Nothing else is needed.",
           "expired": "This link has expired. If you'd still like us to follow up, just let us know."}
    return _page("Follow-up", "<h1>All set</h1><p>%s</p>" % html.escape(msg.get(link.status, "")))


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for") or ""
    return (fwd.split(",")[0].strip() or (request.client.host if request.client else "")) or ""


@router.get("/{token}", response_class=HTMLResponse)
def show(token: str, db: Session = Depends(get_db)):
    link = PM.find(db, token)
    if link is None or link.status != "sent":
        db.commit()
        return _closed(link)
    if link.viewed_at is None:
        link.viewed_at = datetime.utcnow()
    db.commit()
    biz = html.escape(link.business_name or "our office")
    name = html.escape((link.owner_name or "").split()[0].title()) if link.owner_name else ""
    text = html.escape(PM.disclosure(link.business_name or "our office", link.street, link.phone_normalized))
    body = (
        "<h1>%s</h1>"
        "<p>%s would like to follow up with you about your property%s. Please confirm whether that's okay.</p>"
        "<form method='post'>"
        "<label class='box'><input type='checkbox' name='agree' value='yes' required>"
        "<span>%s</span></label>"
        "<button class='yes' type='submit' name='answer' value='confirm'>Confirm</button>"
        "</form>"
        "<form method='post'><input type='hidden' name='answer' value='decline'>"
        "<button class='no' type='submit'>No thanks</button></form>"
        "<p class='small'>Confirming is optional. Number on file ends in %s.</p>"
        % ("Hi %s," % name if name else "Hello,", biz,
           (" at " + html.escape(link.street)) if link.street else "", text, PM.last4(link.phone_normalized)))
    return _page("Confirm follow-up", body)


@router.post("/{token}", response_class=HTMLResponse)
def answer(token: str, request: Request, answer: str = Form(...), agree: str = Form(None),
           db: Session = Depends(get_db)):
    link = PM.find(db, token)
    if link is None or link.status != "sent":
        db.commit()
        return _closed(link)
    if answer == "decline":
        PM.decline(db, link)
        db.commit()
        return _closed(link)
    if answer != "confirm" or agree != "yes":
        # The box is the consent. Without the owner's own tick, nothing is recorded.
        return _page("Confirm follow-up", "<h1>One more step</h1><p>Please go back and tick the box "
                                          "if you'd like us to follow up.</p>", 400)
    # The consent's source is the page, named by the link's id - never by the
    # token, which stays a secret even after it is used.
    page_url = str(request.base_url).rstrip("/") + "/permission/link-" + link.id
    PM.confirm(db, link, ip=_client_ip(request)[:64], user_agent=(request.headers.get("user-agent") or "")[:400],
               page_url=page_url)
    db.commit()
    return _page("Thank you", "<h1>Thank you!</h1><p>You're all set. %s will follow up with you soon. "
                              "You can reply STOP to any text to opt out.</p>"
                 % html.escape(link.business_name or "We"))
