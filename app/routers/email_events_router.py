"""Resend delivery events -> what actually happened to an email.

    POST /email/events/resend      (configured in the Resend dashboard)

Before this, an email was "sent" and nothing more was ever known: no
delivered, no bounce, no complaint. A send is not a delivery, and a bounced or
complained-about address kept being mailed.

SIGNED OR REFUSED. Resend signs webhooks with Svix: HMAC-SHA256 over
"{svix-id}.{svix-timestamp}.{raw body}" with the base64 secret after "whsec_".
RESEND_WEBHOOK_SECRET unset -> 503 (the endpoint does nothing until someone
configures it); a bad or stale signature -> 401. Nothing here sends anything.

WHAT EACH EVENT DOES (matched on EmailMessage.provider_message_id):
    email.delivered        status -> delivered (never downgrades a bounce)
    email.bounced          status -> bounced; HARD bounce (or unknown type) also
                           flags the lead's address bad_email, so every send
                           path's compliance preflight refuses it
    email.complained       status -> complained; lead.allow_email = False - an
                           explicit opt-out of record
    email.delivery_delayed recorded only
    email.opened           opened_at if not set
Unknown message ids are acknowledged (200) and ignored: Resend retries a
non-2xx, and an id we never sent is not going to start existing.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import EmailMessage, Lead

log = logging.getLogger(__name__)
router = APIRouter(prefix="/email/events", tags=["email-events"])

TOLERANCE_SECONDS = 5 * 60


def _secret() -> bytes:
    raw = (os.environ.get("RESEND_WEBHOOK_SECRET") or "").strip()
    if not raw:
        return b""
    if raw.startswith("whsec_"):
        raw = raw[len("whsec_"):]
    try:
        return base64.b64decode(raw)
    except Exception:
        return b""


def verify(headers, body: bytes, secret: bytes, now: float = None) -> bool:
    sid = headers.get("svix-id") or headers.get("webhook-id")
    ts = headers.get("svix-timestamp") or headers.get("webhook-timestamp")
    sigs = headers.get("svix-signature") or headers.get("webhook-signature") or ""
    if not (sid and ts and sigs and secret):
        return False
    try:
        if abs((now or time.time()) - int(ts)) > TOLERANCE_SECONDS:
            return False
    except ValueError:
        return False
    signed = ("%s.%s." % (sid, ts)).encode() + body
    expected = base64.b64encode(hmac.new(secret, signed, hashlib.sha256).digest()).decode()
    for part in sigs.split():
        _, _, sig = part.partition(",")
        if sig and hmac.compare_digest(sig, expected):
            return True
    return False


@router.post("/resend")
async def resend_event(request: Request, db: Session = Depends(get_db)):
    secret = _secret()
    if not secret:
        raise HTTPException(status_code=503, detail="Email event webhook is not configured.")
    body = await request.body()
    if not verify(request.headers, body, secret):
        raise HTTPException(status_code=401, detail="Invalid signature.")
    try:
        event = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    etype = (event.get("type") or "").strip()
    data = event.get("data") or {}
    msg_id = data.get("email_id") or data.get("id")
    # ONCE PER EVENT. Resend redelivers until it gets a 2xx; the svix-id is
    # the event's identity, so a redelivery is acknowledged and changes nothing.
    from sqlalchemy.exc import IntegrityError
    from app.models.program_models import EmailProviderEvent
    sid = request.headers.get("svix-id") or request.headers.get("webhook-id")
    rec = EmailProviderEvent(provider="resend", event_id=sid, event_type=etype or None,
                             provider_message_id=msg_id)
    db.add(rec)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"ok": True, "duplicate": True}
    if not msg_id:
        rec.action = "ignored: no email id"
        db.commit()
        return {"ok": True, "ignored": "no email id"}
    msg = db.query(EmailMessage).filter(EmailMessage.provider_message_id == msg_id).first()
    if msg is None:
        rec.action = "ignored: unknown email"
        db.commit()
        return {"ok": True, "ignored": "unknown email"}
    rec.email_message_id, rec.lead_id = msg.id, msg.lead_id
    lead = db.query(Lead).filter(Lead.id == msg.lead_id).first()
    action = "recorded"
    if etype == "email.delivered":
        if (msg.status or "") not in ("bounced", "complained"):
            msg.status = "delivered"
            action = "delivered"
    elif etype == "email.bounced":
        msg.status = "bounced"
        btype = ((data.get("bounce") or {}).get("type") or "").lower()
        action = "bounced"
        if lead is not None and btype not in ("transient", "soft") and not lead.manual_flag:
            lead.manual_flag = "bad_email"
            lead.manual_flag_reason = "email bounced (%s)" % (btype or "hard")
            action = "bounced; address flagged bad_email"
    elif etype == "email.complained":
        msg.status = "complained"
        if lead is not None:
            lead.allow_email = False
        action = "complained; email opt-out recorded"
    elif etype == "email.opened":
        if not getattr(msg, "opened_at", None):
            msg.opened_at = datetime.utcnow()
        action = "opened"
    elif etype == "email.delivery_delayed":
        action = "delivery delayed (provider still retrying)"
    if lead is not None:
        rec.organization_id = lead.organization_id
    rec.action = action
    db.commit()
    log.info("resend event %s for message %s: %s", etype, msg.id, action)
    return {"ok": True, "action": action}


# ── one-click unsubscribe (program emails) ──────────────────────────────────

from fastapi.responses import HTMLResponse  # noqa: E402

unsubscribe_router = APIRouter(prefix="/email/unsubscribe", tags=["email-unsubscribe"])

_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Email preferences</title>
<style>body{font-family:system-ui,sans-serif;background:#f4f7f9;color:#1d3b4f;display:grid;place-items:center;min-height:90vh;margin:0}
main{background:#fff;border:1px solid #dfe7ec;border-radius:14px;padding:28px;max-width:420px;margin:16px}
button{background:#1f4f68;color:#fff;border:0;border-radius:10px;padding:10px 16px;font-size:15px;cursor:pointer}</style>
</head><body><main>%s</main></body></html>"""


@unsubscribe_router.get("/{token}", response_class=HTMLResponse)
def unsubscribe_page(token: str):
    """A page with a button - a GET never unsubscribes, so link scanners cannot."""
    from app.services.programs.unsubscribe import read_token
    if not read_token(token):
        return HTMLResponse(_PAGE % "<h1>Link not recognised</h1><p>This unsubscribe link is not valid.</p>", status_code=404)
    return HTMLResponse(_PAGE % ('<h1>Stop these emails?</h1><p>You will not receive further emails from us.</p>'
                                 '<form method="post"><button type="submit">Unsubscribe</button></form>'))


@unsubscribe_router.post("/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, db: Session = Depends(get_db)):
    """Also the RFC 8058 one-click target (List-Unsubscribe-Post)."""
    from app.services.programs.unsubscribe import read_token
    lead_id = read_token(token)
    lead = db.query(Lead).filter(Lead.id == lead_id).first() if lead_id else None
    if lead is None:
        return HTMLResponse(_PAGE % "<h1>Link not recognised</h1>", status_code=404)
    if lead.allow_email is not False:
        lead.allow_email = False
        db.commit()
        log.info("email unsubscribe recorded for lead %s", lead.id)
    return HTMLResponse(_PAGE % "<h1>You're unsubscribed</h1><p>You will not receive further emails from us.</p>")
