"""VOLT Marketing's own email: support@voltmarketing.live through Microsoft 365.

===========================================================================
WHAT THIS IS
===========================================================================

VOLT sends its applicant emails FROM its own support mailbox and staff answer
support mail FROM it, so applicants see one consistent sender, "VOLT Marketing
<support@voltmarketing.live>", and their replies come back to VOLT, not to
EvoSys or to anyone's personal Outlook.

CONNECTION. VOLT staff with write access click "Connect support mailbox" in
VOLT Operations. Microsoft's own sign-in page opens; the person signs in AS
support@voltmarketing.live (we never see a password). The callback stores
only an encrypted refresh token on an InboundMailbox row marked
purpose="volt_support" and organization_id=VOLT. A sign-in as any OTHER
account is refused and nothing is stored, so the mailbox cannot be silently
replaced by whoever happened to be signed in.

  * It does not touch User.microsoft_* (Mike's personal / platform connection)
    or any other workspace's sender.
  * The CRM reply poller never routes this mailbox to leads
    (inbound_mailbox_service.poll_all_mailboxes skips it).

PERMISSIONS (minimum for what is built): offline_access, User.Read (which
mailbox signed in), Mail.Read (read support mail and its attachments),
Mail.Send (transactional email and staff replies via /reply, which keeps the
thread). No Mail.ReadWrite: nothing here moves, flags or deletes mail.

SENDING. Every transactional email is a VoltEmail row with a per-organization
unique `dedupe_key`, written BEFORE the send. A retried request, a status
saved twice or a second poller run finds the row and sends nothing new. A
failed send is retried by the email-poller cron with backoff. A
credential-bearing message (password reset) is sent once, keeps no body and
is not saved to Sent Items.

READING. Each poller run reads support mail received since the cursor (every
folder except Sent / Drafts / Outbox / Deleted), one VoltSupportMessage per
Graph message id (unique, so nothing is imported twice). Bodies are untrusted:
only the plain text Microsoft renders is kept and the UI shows it as text.
Attachments are listed by name and size and are only ever served as forced
downloads to VOLT staff. Nothing in a message is executed or followed.
"""
from __future__ import annotations

import base64
import html as _html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.inbound_mailbox_models import InboundMailbox
from app.models.models import User
from app.models.volt_models import (VoltApplication, VoltAuditEvent, VoltDocument, VoltEmail, VoltProgram,
                                    VoltProject, VoltRep, VoltSupportMessage)
from app.services.volt import common as C

log = logging.getLogger(__name__)

PURPOSE = "volt_support"
SCOPES = "offline_access User.Read Mail.Read Mail.Send"
GRAPH = "https://graph.microsoft.com/v1.0"
FIRST_RUN_LOOKBACK = timedelta(days=3)
CURSOR_OVERLAP = timedelta(minutes=10)
MAX_PER_RUN = 200
MAX_ATTEMPTS = 5
BACKOFF = (timedelta(minutes=2), timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=2))
MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
DEFAULT_SENDER = "VOLT Marketing"

# Swappable in tests; production uses httpx.
_http = httpx


class MailboxAuthError(Exception):
    pass


class WrongMailbox(Exception):
    pass


# ── identity ────────────────────────────────────────────────────────────────

def support_address(prog: VoltProgram) -> str:
    return (prog.support_email or "").strip().lower()


def sender_name(prog: VoltProgram) -> str:
    return (prog.sender_name or DEFAULT_SENDER).strip() or DEFAULT_SENDER


def origin(prog: VoltProgram) -> str:
    o = (prog.public_origin or os.environ.get("FRONTEND_URL") or "").strip().rstrip("/")
    return o


def mailbox(db: Session, org_id: str) -> Optional[InboundMailbox]:
    return (db.query(InboundMailbox)
            .filter(InboundMailbox.purpose == PURPOSE, InboundMailbox.organization_id == org_id,
                    InboundMailbox.is_active.is_(True))
            .order_by(InboundMailbox.connected_at.desc()).first())


# ── connection ──────────────────────────────────────────────────────────────

def _ms():
    from app.services import microsoft_email_service as M
    return M


def authorization_url(state: str, login_hint: str) -> str:
    M = _ms()
    if not M.MICROSOFT_CLIENT_ID:
        raise RuntimeError("Microsoft sign-in is not configured on the server.")
    params = {"client_id": M.MICROSOFT_CLIENT_ID, "response_type": "code",
              "redirect_uri": M.MICROSOFT_REDIRECT_URI, "response_mode": "query",
              "scope": SCOPES, "state": state, "login_hint": login_hint,
              # Always show the account picker: the person must choose the
              # support mailbox, not whatever account the browser holds.
              "prompt": "select_account"}
    return "%s/oauth2/v2.0/authorize?%s" % (M.AUTHORITY, urlencode(params))


def connect_from_code(db: Session, *, code: str, org_id: str, user_id: str) -> InboundMailbox:
    """Exchange the code, confirm WHICH mailbox signed in, then store it.
    Raises WrongMailbox (nothing stored) when it is not VOLT's support address."""
    from app.utils.crypto import encrypt_value
    M = _ms()
    prog = db.get(VoltProgram, org_id)
    if prog is None or not support_address(prog):
        raise RuntimeError("VOLT's support address is not configured.")
    expected = support_address(prog)
    r = _http.post("%s/oauth2/v2.0/token" % M.AUTHORITY, data={
        "client_id": M.MICROSOFT_CLIENT_ID, "client_secret": M.MICROSOFT_CLIENT_SECRET,
        "code": code, "redirect_uri": M.MICROSOFT_REDIRECT_URI,
        "grant_type": "authorization_code", "scope": SCOPES}, timeout=15)
    if r.status_code != 200:
        raise RuntimeError("Microsoft refused the sign-in (%s)." % r.status_code)
    tok = r.json()
    if not tok.get("refresh_token"):
        raise RuntimeError("Microsoft did not return a refresh token (offline access not granted).")
    me = _http.get("%s/me" % GRAPH, headers={"Authorization": "Bearer %s" % tok["access_token"]},
                   params={"$select": "mail,userPrincipalName,displayName"}, timeout=15)
    if me.status_code != 200:
        raise RuntimeError("Microsoft did not say which mailbox signed in (%s)." % me.status_code)
    prof = me.json()
    signed_in = {(prof.get("mail") or "").strip().lower(), (prof.get("userPrincipalName") or "").strip().lower()}
    if expected not in signed_in:
        C.audit(db, org_id, db.get(User, user_id), "mailbox", expected, "mailbox_connect_refused",
                reason="Signed in as a different account; nothing was stored.")
        db.commit()
        raise WrongMailbox(next(iter(a for a in signed_in if a), "another account"))
    other = (db.query(InboundMailbox).filter(InboundMailbox.address == expected).first())
    if other is not None and (other.purpose != PURPOSE or other.organization_id != org_id):
        raise RuntimeError("%s is already connected for another purpose. Disconnect it there first." % expected)
    box = other or InboundMailbox(address=expected, organization_id=org_id, purpose=PURPOSE)
    if other is None:
        db.add(box)
    box.refresh_token_encrypted = encrypt_value(tok["refresh_token"])
    box.is_active = True
    box.connected_by_user_id = user_id
    box.connected_at = datetime.utcnow()
    box.last_status, box.last_error = "connected", None
    C.audit(db, org_id, db.get(User, user_id), "mailbox", expected, "mailbox_connected",
            new=(prof.get("displayName") or "")[:120])
    db.commit()
    return box


def _token(db: Session, box: InboundMailbox) -> str:
    """A fresh access token, rotating the stored refresh token when Microsoft
    issues a new one. A revoked / expired sign-in marks the mailbox
    auth_error (shown to staff as "Reconnect") and raises."""
    from app.utils.crypto import decrypt_value, encrypt_value
    M = _ms()
    if not box.refresh_token_encrypted:
        raise MailboxAuthError("No Microsoft sign-in is stored for the support mailbox.")
    r = _http.post("%s/oauth2/v2.0/token" % M.AUTHORITY, data={
        "client_id": os.environ.get("MICROSOFT_CLIENT_ID") or M.MICROSOFT_CLIENT_ID,
        "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET") or M.MICROSOFT_CLIENT_SECRET,
        "refresh_token": decrypt_value(box.refresh_token_encrypted),
        "grant_type": "refresh_token", "scope": SCOPES}, timeout=15)
    if r.status_code in (400, 401):
        try:
            code = (r.json() or {}).get("error") or "refused"
        except Exception:  # noqa: BLE001
            code = "refused"
        box.last_status = "auth_error"
        box.last_error = ("Microsoft refused the stored sign-in (%s). Reconnect the support mailbox." % code)[:300]
        db.commit()
        raise MailboxAuthError(box.last_error)
    if r.status_code != 200:
        raise RuntimeError("Microsoft token service returned %s." % r.status_code)
    data = r.json()
    if data.get("refresh_token"):
        box.refresh_token_encrypted = encrypt_value(data["refresh_token"])
    return data["access_token"]


_URL = re.compile(r"https?://\S+")


def _safe_error(text: str) -> str:
    """Provider errors can quote what they rejected; never keep a link."""
    return _URL.sub("[link]", (text or ""))[:500]


def status(db: Session, prog: VoltProgram) -> Dict[str, Any]:
    box = mailbox(db, prog.organization_id)
    since = datetime.utcnow() - timedelta(days=7)
    counts: Dict[str, int] = {}
    for st, n in (db.query(VoltEmail.state, func.count(VoltEmail.id))
                  .filter(VoltEmail.organization_id == prog.organization_id, VoltEmail.created_at >= since)
                  .group_by(VoltEmail.state).all()):
        counts[st] = n
    recent = (db.query(VoltEmail).filter(VoltEmail.organization_id == prog.organization_id)
              .order_by(VoltEmail.created_at.desc()).limit(25).all())
    display = None
    connected_by = None
    if box is not None:
        ev = (db.query(VoltAuditEvent).filter(VoltAuditEvent.organization_id == prog.organization_id,
                                              VoltAuditEvent.action == "mailbox_connected")
              .order_by(VoltAuditEvent.at.desc()).first())
        display = ev.new_value if ev else None
        u = db.get(User, box.connected_by_user_id) if box.connected_by_user_id else None
        connected_by = (u.full_name or u.email) if u else None
    state = "not_connected" if box is None else ("reconnect" if box.last_status == "auth_error" else "connected")
    return {
        "state": state,
        "expected_address": support_address(prog),
        "sender_name": sender_name(prog),
        "address": box.address if box else None,
        "microsoft_display_name": display,
        "connected_at": C.iso(box.connected_at) if box else None,
        "connected_by": connected_by,
        "last_polled_at": C.iso(box.last_polled_at) if box else None,
        "last_status": box.last_status if box else None,
        "last_error": box.last_error if box else None,
        "permissions": SCOPES.split(),
        "sent_last_7_days": counts,
        "recent": [{"id": e.id, "kind": e.kind, "to": e.to_email, "subject": e.subject, "state": e.state,
                    "attempts": e.attempts, "error": e.last_error, "is_test": e.is_test,
                    "created_at": C.iso(e.created_at), "sent_at": C.iso(e.sent_at)} for e in recent],
    }


# ── sending ─────────────────────────────────────────────────────────────────

def deliverable_test_address(addr: str) -> bool:
    """A test applicant is emailed only at a deliverable test inbox Mike owns
    (a "+volt-test" plus-address), never at the undeliverable @example.test."""
    return "+volt-test" in (addr or "").lower()


def queue(db: Session, prog: VoltProgram, *, kind: str, key: str, to: Optional[str], subject: str,
          html: str, rep_serial: Optional[int] = None, application_id: Optional[str] = None,
          sensitive: bool = False, is_test: bool = False) -> Optional[VoltEmail]:
    """Record one email to send, once. Returns the row (new or the existing
    one for this key), or None when there is no address."""
    to = (to or "").strip()
    if not to:
        return None
    found = (db.query(VoltEmail).filter(VoltEmail.organization_id == prog.organization_id,
                                        VoltEmail.dedupe_key == key).first())
    if found is not None:
        return found
    row = VoltEmail(organization_id=prog.organization_id, kind=kind, dedupe_key=key, rep_serial=rep_serial,
                    application_id=application_id, to_email=to, subject=subject[:250],
                    body_html=None if sensitive else html, sensitive=bool(sensitive), is_test=bool(is_test),
                    state="queued")
    if is_test and not deliverable_test_address(to):
        row.state, row.last_error = "skipped", "Test applicant at a test address; not sent."
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return (db.query(VoltEmail).filter(VoltEmail.organization_id == prog.organization_id,
                                           VoltEmail.dedupe_key == key).first())
    return row


def _send_graph(token: str, prog: VoltProgram, to: str, subject: str, html: str, save: bool):
    reply_to = support_address(prog)
    msg = {"subject": subject, "body": {"contentType": "HTML", "content": html},
           "toRecipients": [{"emailAddress": {"address": to}}],
           "from": {"emailAddress": {"address": reply_to, "name": sender_name(prog)}},
           "replyTo": [{"emailAddress": {"address": reply_to, "name": sender_name(prog)}}]}
    return _http.post("%s/me/sendMail" % GRAPH, headers={"Authorization": "Bearer %s" % token},
                      json={"message": msg, "saveToSentItems": bool(save)}, timeout=30)


def deliver(db: Session, row: VoltEmail, html: Optional[str] = None) -> VoltEmail:
    """Try to send a queued row now. Never raises; the row records the result."""
    if row.state in ("sent", "skipped"):
        return row
    body = html if html is not None else row.body_html
    prog = db.get(VoltProgram, row.organization_id)
    box = mailbox(db, row.organization_id)
    if prog is None or box is None or not body:
        row.state = "failed"
        row.last_error = ("The VOLT support mailbox is not connected." if box is None
                          else "Nothing to send.")
        row.next_attempt_at = None if row.sensitive or not body else datetime.utcnow() + timedelta(minutes=15)
        db.commit()
        return row
    row.attempts = (row.attempts or 0) + 1
    try:
        token = _token(db, box)
        r = _send_graph(token, prog, row.to_email, row.subject, body, save=not row.sensitive)
        if r.status_code == 202:
            row.state, row.sent_at, row.last_error, row.next_attempt_at = "sent", datetime.utcnow(), None, None
        else:
            row.state = "failed"
            row.last_error = _safe_error("Microsoft 365 refused the send (%s): %s" % (r.status_code, r.text[:300]))
    except MailboxAuthError as exc:
        row.state, row.last_error = "failed", _safe_error(str(exc))
    except Exception as exc:  # noqa: BLE001 - a failed send is recorded, never a crash
        row.state, row.last_error = "failed", "Microsoft 365 could not be reached (%s)." % type(exc).__name__
    if row.state == "failed":
        if row.sensitive or row.attempts >= MAX_ATTEMPTS:
            row.next_attempt_at = None
        else:
            row.next_attempt_at = datetime.utcnow() + BACKOFF[min(row.attempts - 1, len(BACKOFF) - 1)]
    db.commit()
    return row


def flush(db: Session, ids: List[str]) -> None:
    for i in ids or []:
        row = db.get(VoltEmail, i)
        if row is not None and row.state == "queued":
            deliver(db, row)


def flush_in_background(ids: List[str]) -> None:
    """For FastAPI BackgroundTasks: its own session, after the response."""
    if not ids:
        return
    from app.deps import SessionLocal
    db = SessionLocal()
    try:
        flush(db, ids)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("VOLT email flush failed")
    finally:
        db.close()


def retry_due(db: Session, limit: int = 50) -> int:
    now = datetime.utcnow()
    rows = (db.query(VoltEmail)
            .filter(VoltEmail.state.in_(("queued", "failed")), VoltEmail.sensitive.is_(False),
                    VoltEmail.body_html.isnot(None), VoltEmail.attempts < MAX_ATTEMPTS)
            .filter((VoltEmail.next_attempt_at.is_(None) & (VoltEmail.state == "queued"))
                    | (VoltEmail.next_attempt_at <= now))
            .order_by(VoltEmail.created_at).limit(limit).all())
    for row in rows:
        deliver(db, row)
    return len(rows)


# ── templates (accurate, transactional, no recruiting promotion) ────────────

def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _page(prog: VoltProgram, heading: str, paras: List[str], cta: Optional[tuple] = None) -> str:
    sender = _e(sender_name(prog))
    support = _e(support_address(prog))
    body = "".join('<p style="margin:0 0 14px;line-height:1.5">%s</p>' % p for p in paras)
    button = ""
    if cta:
        button = ('<p style="margin:22px 0"><a href="%s" style="background:#c8102e;color:#ffffff;'
                  'text-decoration:none;font-weight:700;padding:12px 20px;border-radius:6px;display:inline-block">'
                  '%s</a></p>' % (_e(cta[1]), _e(cta[0])))
    return ('<div style="font-family:Arial,Helvetica,sans-serif;color:#111113;max-width:560px">'
            '<div style="background:#0b0b0c;color:#ffffff;padding:14px 18px;font-weight:900;font-size:20px;'
            'border-bottom:4px solid #c8102e">&#9889; VOLT <span style="font-weight:500;font-size:14px">Marketing</span></div>'
            '<div style="padding:20px 18px">'
            '<h1 style="font-size:22px;margin:0 0 14px">%s</h1>%s%s'
            '<p style="margin:22px 0 0;color:#555;font-size:13px;line-height:1.5">Questions? Reply to this email or write '
            'to <a href="mailto:%s">%s</a>.<br>%s &middot; You are receiving this because of your VOLT Marketing '
            'account or application.</p></div></div>'
            % (_e(heading), body, button, support, support, sender))


def _recipient(db: Session, rep: VoltRep):
    u = db.get(User, rep.user_id) if rep else None
    return (u.email if u else None), u


def _project(db: Session, app: VoltApplication) -> str:
    p = db.get(VoltProject, app.project_id) if app else None
    return p.name if p else "VOLT Marketing"


def _app_link(prog: VoltProgram, app: VoltApplication) -> str:
    return "%s/volt/me/applications/%s" % (origin(prog), quote(app.id))


def _status_events(db: Session, app: VoltApplication) -> int:
    return (db.query(VoltAuditEvent).filter(VoltAuditEvent.entity_type == "application",
                                            VoltAuditEvent.entity_id == str(app.id),
                                            VoltAuditEvent.action == "status").count())


def on_registered(db: Session, prog: VoltProgram, rep: VoltRep, app: Optional[VoltApplication]) -> Optional[VoltEmail]:
    to, _u = _recipient(db, rep)
    proj = _project(db, app) if app else None
    # An applicant is not a rep before approval/start: no rep ID here.
    paras = ["Hi %s, your VOLT Marketing account is ready." % _e(rep.first_name)]
    if app:
        paras.append("Your application reference is <b>%s</b>." % _e(C.app_ref(app)))
    if proj:
        paras.append("Your %s application is saved as a draft. You can finish it any time; we review it once you submit."
                     % _e(proj))
    cta = ("Continue your application", _app_link(prog, app)) if app else ("Open your dashboard", "%s/volt/me" % origin(prog))
    return queue(db, prog, kind="registered", key="registered:%s" % rep.serial, to=to,
                 subject="Your VOLT Marketing account is ready",
                 html=_page(prog, "Welcome to VOLT Marketing", paras, cta),
                 rep_serial=rep.serial, application_id=app.id if app else None, is_test=rep.is_test)


def on_submitted(db: Session, prog: VoltProgram, rep: VoltRep, app: VoltApplication) -> Optional[VoltEmail]:
    if app.status != "submitted":
        return None
    db.flush()
    to, _u = _recipient(db, rep)
    proj = _project(db, app)
    n = _status_events(db, app)
    return queue(db, prog, kind="receipt", key="receipt:%s:%s" % (app.id, n), to=to,
                 subject="We received your %s application" % proj,
                 html=_page(prog, "Application received",
                            ["Hi %s, we received your application for <b>%s</b>." % (_e(rep.first_name), _e(proj)),
                             "Our team reviews every application and will contact you about next steps. "
                             "You can check your status any time."],
                            ("View your application", _app_link(prog, app))),
                 rep_serial=rep.serial, application_id=app.id, is_test=rep.is_test)


def on_status(db: Session, prog: VoltProgram, app: VoltApplication) -> Optional[VoltEmail]:
    """After a staff stage change. One email per real change: the key counts
    the status changes recorded for this application, and a repeated save
    records none."""
    if app.status not in ("needs_update", "approved", "started"):
        return None
    db.flush()
    rep = db.get(VoltRep, app.rep_serial)
    to, _u = _recipient(db, rep)
    proj = _project(db, app)
    n = _status_events(db, app)
    if app.status == "needs_update":
        subject = "Action needed on your %s application" % proj
        html = _page(prog, "Please update your application",
                     ["Hi %s, our team reviewed your <b>%s</b> application and asked for a change:" % (_e(rep.first_name), _e(proj)),
                      '<span style="display:block;border-left:4px solid #c8102e;padding:8px 12px;background:#f7f2f3">%s</span>'
                      % _e(app.return_reason).replace("\n", "<br>"),
                      "Make the change and submit your application again."],
                     ("Update your application", _app_link(prog, app)))
    elif app.status == "approved":
        subject = "Your %s application was approved" % proj
        html = _page(prog, "Application approved",
                     ["Hi %s, your application for <b>%s</b> was approved." % (_e(rep.first_name), _e(proj)),
                      "Our team will contact you about next steps."],
                     ("View your application", _app_link(prog, app)))
    else:
        subject = "Your %s status is now Started" % proj
        html = _page(prog, "You're marked as started",
                     ["Hi %s, your status for <b>%s</b> is now <b>Started</b>." % (_e(rep.first_name), _e(proj)),
                      "If anything about your start looks wrong, reply to this email."],
                     ("Open your dashboard", "%s/volt/me" % origin(prog)))
    return queue(db, prog, kind=app.status, key="status:%s:%s" % (app.id, n), to=to, subject=subject, html=html,
                 rep_serial=rep.serial, application_id=app.id, is_test=rep.is_test)


def _when(sess) -> str:
    """'Thursday, November 12, 2026 at 10:00 AM CST (America/Chicago)' - DST-correct."""
    from datetime import timezone as _tz
    from zoneinfo import ZoneInfo
    try:
        loc = sess.starts_at.replace(tzinfo=_tz.utc).astimezone(ZoneInfo(sess.timezone))
    except Exception:  # pragma: no cover
        return C.iso(sess.starts_at)
    return "%s at %s %s (%s)" % (loc.strftime("%A, %B %-d, %Y"), loc.strftime("%-I:%M %p"), loc.tzname(), sess.timezone)


def on_booking(db: Session, prog: VoltProgram, rep: VoltRep, app: VoltApplication, sess, row,
               event: str) -> Optional[VoltEmail]:
    """Zoom booking notices: confirmed | rescheduled | canceled. One email per
    real event (the key carries the event's own timestamp). The join button
    lives on the applicant's signed-in application page, never in the email."""
    to, _u = _recipient(db, rep)
    kind = "interview" if sess.kind == "interview" else "orientation"
    proj = _project(db, app)
    when = _e(_when(sess))
    if event == "confirmed":
        stamp, subject = C.iso(row.booked_at), "Your VOLT Zoom %s is scheduled" % kind
        paras = ["Hi %s, your Zoom %s for <b>%s</b> is confirmed." % (_e(rep.first_name), kind, _e(proj)),
                 "<b>%s</b> &middot; %d minutes" % (when, sess.duration_minutes),
                 "Your application stays active. Use the Join button on your application page at the scheduled time."]
        heading = "Zoom scheduled"
    elif event == "rescheduled":
        stamp, subject = C.iso(sess.rescheduled_at), "Your VOLT Zoom %s has a new time" % kind
        paras = ["Hi %s, our team moved your Zoom %s for <b>%s</b>." % (_e(rep.first_name), kind, _e(proj)),
                 "New time: <b>%s</b> &middot; %d minutes" % (when, sess.duration_minutes),
                 "Open your application page to see the details and join at the new time."]
        heading = "New Zoom time"
    else:
        stamp = C.iso(sess.canceled_at if sess.status == "canceled" else row.canceled_at)
        subject = "Your VOLT Zoom %s was canceled" % kind
        paras = ["Hi %s, the Zoom %s for <b>%s</b> on %s was canceled." % (_e(rep.first_name), kind, _e(proj), when),
                 "Your application is still active. Open your application page to see your next step, or reply to "
                 "this email if you need help."]
        heading = "Zoom canceled"
    return queue(db, prog, kind="zoom_" + event, key="zoom_%s:%s:%s:%s" % (event, sess.id, rep.serial, stamp),
                 to=to, subject=subject,
                 html=_page(prog, heading, paras, ("Open My Application", "%s/volt/me?application=%s" % (origin(prog), quote(app.id)))),
                 rep_serial=rep.serial, application_id=app.id, is_test=rep.is_test)


def on_doc_replace(db: Session, prog: VoltProgram, doc: VoltDocument) -> Optional[VoltEmail]:
    if doc.status != "replace":
        return None
    rep = db.get(VoltRep, doc.rep_serial)
    to, _u = _recipient(db, rep)
    label = getattr(doc, "label", None) or getattr(doc, "doc_key", None) or "a document"
    link = "%s/volt/me" % origin(prog)
    if getattr(doc, "application_id", None):
        a = db.get(VoltApplication, doc.application_id)
        if a is not None:
            link = _app_link(prog, a)
    return queue(db, prog, kind="doc_replace", key="doc_replace:%s" % doc.id, to=to,
                 subject="Please replace a document for VOLT Marketing",
                 html=_page(prog, "A document needs to be replaced",
                            ["Hi %s, our team asked you to replace <b>%s</b>:" % (_e(rep.first_name), _e(label)),
                             '<span style="display:block;border-left:4px solid #c8102e;padding:8px 12px;background:#f7f2f3">%s</span>'
                             % _e(doc.reason).replace("\n", "<br>")],
                            ("Upload a new version", link)),
                 rep_serial=rep.serial, application_id=getattr(doc, "application_id", None), is_test=rep.is_test)


def password_reset(db: Session, user: User, raw_token: str, reset_id: str) -> bool:
    """A VOLT applicant's reset link: VOLT domain, VOLT sender, opens the VOLT
    sign-in afterwards. Returns False when the user is not a VOLT applicant or
    VOLT's mailbox cannot send, so the caller keeps its existing path."""
    rep = db.query(VoltRep).filter(VoltRep.user_id == user.id).first()
    if rep is None:
        return False
    prog = db.get(VoltProgram, rep.organization_id)
    if prog is None or mailbox(db, prog.organization_id) is None:
        return False
    link = "%s/activate?token=%s&next=%s" % (origin(prog), quote(raw_token), quote("/volt/sign-in"))
    html = _page(prog, "Reset your password",
                 ["Someone asked to reset the password for your VOLT Marketing account.",
                  "This link works once, for one hour. If you didn't ask for this, ignore this email; "
                  "your password stays the same."],
                 ("Choose a new password", link))
    row = queue(db, prog, kind="password_reset", key="password_reset:%s" % reset_id, to=user.email,
                subject="Reset your VOLT Marketing password", html=html, rep_serial=rep.serial,
                sensitive=True, is_test=rep.is_test)
    db.commit()
    if row is None:
        return False
    if row.state == "queued":
        deliver(db, row, html=html)
    return row.state in ("sent", "skipped")


def reset_link_for(db: Session, user: User) -> Optional[Dict[str, str]]:
    """Where a VOLT applicant's reset link goes when VOLT's mailbox cannot send
    it: the VOLT origin and sign-in, used by the existing provider path."""
    rep = db.query(VoltRep).filter(VoltRep.user_id == user.id).first()
    if rep is None:
        return None
    prog = db.get(VoltProgram, rep.organization_id)
    if prog is None:
        return None
    return {"origin": origin(prog), "next": "/volt/sign-in", "brand": sender_name(prog)}


# ── reading the support inbox ───────────────────────────────────────────────

_SKIP_WELL_KNOWN = ("sentitems", "drafts", "outbox", "deleteditems")
_AUTO = re.compile(r"(?i)^(automatic reply|auto(matic)?[- ]?reply|out of (the )?office)")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _skip_ids(token: str) -> set:
    out = set()
    for name in _SKIP_WELL_KNOWN:
        r = _http.get("%s/me/mailFolders/%s" % (GRAPH, name), headers={"Authorization": "Bearer %s" % token},
                      params={"$select": "id"}, timeout=15)
        if r.status_code == 200 and r.json().get("id"):
            out.add(r.json()["id"])
    return out


def _fetch(token: str, since: datetime) -> List[dict]:
    iso = since.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    skip = _skip_ids(token)
    url = "%s/me/messages" % GRAPH
    params = {"$filter": "receivedDateTime ge %s" % iso,
              "$select": "id,internetMessageId,conversationId,subject,from,toRecipients,ccRecipients,"
                         "receivedDateTime,body,hasAttachments,parentFolderId",
              "$orderby": "receivedDateTime asc", "$top": 50}
    out: List[dict] = []
    while url and len(out) < MAX_PER_RUN:
        r = _http.get(url, headers={"Authorization": "Bearer %s" % token,
                                    # Plain text only: the HTML of an incoming message is never kept.
                                    "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'},
                      params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError("Reading the support mailbox failed (%s)." % r.status_code)
        data = r.json()
        out.extend(m for m in data.get("value", []) if m.get("parentFolderId") not in skip)
        url, params = data.get("@odata.nextLink"), None
    return out


def _attachments(token: str, gid: str) -> List[Dict[str, Any]]:
    r = _http.get("%s/me/messages/%s/attachments" % (GRAPH, quote(gid, safe="")),
                  headers={"Authorization": "Bearer %s" % token, "Prefer": 'IdType="ImmutableId"'},
                  params={"$select": "id,name,size,contentType,isInline"}, timeout=20)
    if r.status_code != 200:
        return []
    return [{"id": a.get("id"), "name": (a.get("name") or "attachment")[:200], "size": a.get("size") or 0,
             "content_type": (a.get("contentType") or "application/octet-stream")[:100]}
            for a in r.json().get("value", []) if not a.get("isInline")]


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None


def _rep_by_email(db: Session, org_id: str, addr: str) -> Optional[VoltRep]:
    from sqlalchemy import func
    if not addr:
        return None
    return (db.query(VoltRep).join(User, User.id == VoltRep.user_id)
            .filter(VoltRep.organization_id == org_id, func.lower(User.email) == addr.lower()).first())


def poll(db: Session, box: InboundMailbox, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    res = {"mailbox": box.address, "checked": 0, "imported": 0, "errors": 0}
    try:
        token = _token(db, box)
    except MailboxAuthError as exc:
        res["error"] = str(exc)
        box.last_polled_at = now
        db.commit()
        return res
    since = (box.cursor_received_at - CURSOR_OVERLAP) if box.cursor_received_at else (now - FIRST_RUN_LOOKBACK)
    try:
        msgs = _fetch(token, since)
    except Exception as exc:  # noqa: BLE001
        box.last_status, box.last_error, box.last_polled_at = "error", _safe_error(str(exc)), now
        db.commit()
        res["error"] = box.last_error
        return res
    seen = {r[0] for r in db.query(VoltSupportMessage.graph_message_id)
            .filter(VoltSupportMessage.mailbox_id == box.id).all()}
    newest = box.cursor_received_at
    from app.services.inbound_mailbox_service import bounce_addresses, recipients_of
    for m in msgs:
        res["checked"] += 1
        gid = m.get("id")
        received = _parse_dt(m.get("receivedDateTime"))
        if received and (newest is None or received > newest):
            newest = received
        if not gid or gid in seen:
            continue
        sender = ((m.get("from") or {}).get("emailAddress") or {})
        addr = (sender.get("address") or "").strip().lower()
        if addr == box.address:
            continue                                    # the mailbox's own mail
        try:
            kind = "message"
            bounced = bounce_addresses(m, {box.address})
            if bounced is not None:
                kind = "bounce"
                for a in bounced:
                    for e in (db.query(VoltEmail).filter(VoltEmail.organization_id == box.organization_id,
                                                         VoltEmail.to_email.ilike(a), VoltEmail.state == "sent")
                              .order_by(VoltEmail.sent_at.desc()).limit(1).all()):
                        e.state, e.last_error = "failed", "Bounced: the receiving server refused it."
            elif _AUTO.match((m.get("subject") or "").strip()):
                kind = "auto_reply"
            body = _CTRL.sub("", ((m.get("body") or {}).get("content") or ""))[:20000]
            rep = _rep_by_email(db, box.organization_id, addr)
            atts = _attachments(token, gid) if m.get("hasAttachments") else []
            with db.begin_nested():
                db.add(VoltSupportMessage(
                    organization_id=box.organization_id, mailbox_id=box.id, graph_message_id=gid,
                    internet_message_id=(m.get("internetMessageId") or "")[:500] or None,
                    conversation_id=m.get("conversationId") or gid, direction="in",
                    from_address=addr or None, from_name=(sender.get("name") or "")[:200] or None,
                    to_addresses=", ".join(recipients_of(m))[:2000] or None,
                    subject=(m.get("subject") or "")[:500], body_text=body, received_at=received,
                    rep_serial=rep.serial if rep else None,
                    attachments=json.dumps(atts) if atts else None, kind=kind))
            seen.add(gid)
            res["imported"] += 1
        except IntegrityError:
            seen.add(gid)
        except Exception:  # noqa: BLE001
            res["errors"] += 1
            log.exception("VOLT support message import failed")
    box.cursor_received_at = newest
    box.last_polled_at = now
    box.last_checked, box.last_matched = res["checked"], res["imported"]
    box.last_status, box.last_error = ("ok" if not res["errors"] else "error"), (
        None if not res["errors"] else "%d message(s) could not be imported." % res["errors"])
    db.commit()
    return res


def run_all(db: Session) -> Dict[str, Any]:
    """Every VOLT support mailbox: read new mail, then retry due sends."""
    out = {"volt_mailboxes": 0, "volt_imported": 0, "volt_retried": 0}
    for box in (db.query(InboundMailbox).filter(InboundMailbox.purpose == PURPOSE,
                                                InboundMailbox.is_active.is_(True)).all()):
        try:
            r = poll(db, box)
            out["volt_mailboxes"] += 1
            out["volt_imported"] += r.get("imported", 0)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("VOLT mailbox poll failed")
    try:
        out["volt_retried"] = retry_due(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("VOLT email retry failed")
    return out


# ── staff inbox ─────────────────────────────────────────────────────────────

def _msg_dict(m: VoltSupportMessage, names: Dict[str, str]) -> Dict[str, Any]:
    return {"id": m.id, "direction": m.direction, "kind": m.kind, "from": m.from_address, "from_name": m.from_name,
            "to": m.to_addresses, "subject": m.subject, "body_text": m.body_text,
            "received_at": C.iso(m.received_at), "rep_code": C.rep_code(m.rep_serial),
            "attachments": C.loads(m.attachments, []),
            "staff": names.get(m.staff_user_id) if m.staff_user_id else None}


def threads(db: Session, org_id: str, show: str = "open") -> List[Dict[str, Any]]:
    rows = (db.query(VoltSupportMessage).filter(VoltSupportMessage.organization_id == org_id)
            .order_by(VoltSupportMessage.received_at.asc()).all())
    by: Dict[str, List[VoltSupportMessage]] = {}
    for r in rows:
        by.setdefault(r.conversation_id or r.id, []).append(r)
    out = []
    for conv, msgs in by.items():
        ins = [m for m in msgs if m.direction == "in"]
        last = msgs[-1]
        open_ = any(m.direction == "in" and m.handled_at is None for m in msgs)
        if show == "open" and not open_:
            continue
        first_in = ins[0] if ins else msgs[0]
        out.append({"conversation_id": conv, "subject": first_in.subject, "from": first_in.from_address,
                    "from_name": first_in.from_name, "rep_code": C.rep_code(first_in.rep_serial),
                    "kind": first_in.kind, "count": len(msgs), "open": open_,
                    "last_at": C.iso(last.received_at), "last_direction": last.direction,
                    "has_attachments": any(m.attachments for m in msgs)})
    out.sort(key=lambda t: t["last_at"] or "", reverse=True)
    return out


def thread(db: Session, org_id: str, conv: str) -> Dict[str, Any]:
    msgs = (db.query(VoltSupportMessage).filter(VoltSupportMessage.organization_id == org_id,
                                                VoltSupportMessage.conversation_id == conv)
            .order_by(VoltSupportMessage.received_at.asc()).all())
    if not msgs:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    ids = {m.staff_user_id for m in msgs if m.staff_user_id}
    names = {u.id: (u.full_name or u.email) for u in db.query(User).filter(User.id.in_(ids or {"-"})).all()}
    return {"conversation_id": conv, "messages": [_msg_dict(m, names) for m in msgs],
            "open": any(m.direction == "in" and m.handled_at is None for m in msgs)}


def mark_handled(db: Session, org_id: str, conv: str, user: User, handled: bool = True) -> None:
    rows = (db.query(VoltSupportMessage).filter(VoltSupportMessage.organization_id == org_id,
                                                VoltSupportMessage.conversation_id == conv,
                                                VoltSupportMessage.direction == "in").all())
    if not rows:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    for r in rows:
        r.handled_at = datetime.utcnow() if handled else None
        r.handled_by = user.id if handled else None


def reply(db: Session, prog: VoltProgram, conv: str, user: User, text: str) -> Dict[str, Any]:
    """Answer the newest incoming message of a conversation with Graph /reply,
    which keeps In-Reply-To / References, so it stays in the same thread."""
    text = (text or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write a reply first.")
    if len(text) > 10000:
        raise HTTPException(status_code=422, detail="That reply is too long.")
    last_in = (db.query(VoltSupportMessage).filter(VoltSupportMessage.organization_id == prog.organization_id,
                                                   VoltSupportMessage.conversation_id == conv,
                                                   VoltSupportMessage.direction == "in")
               .order_by(VoltSupportMessage.received_at.desc()).first())
    if last_in is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if last_in.kind == "bounce":
        raise HTTPException(status_code=409, detail="This is a delivery-failure notice; there is no one to reply to.")
    box = mailbox(db, prog.organization_id)
    if box is None or box.id != last_in.mailbox_id:
        raise HTTPException(status_code=409, detail="The support mailbox is not connected.")
    try:
        token = _token(db, box)
    except MailboxAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    comment = _e(text).replace("\n", "<br>")
    r = _http.post("%s/me/messages/%s/reply" % (GRAPH, quote(last_in.graph_message_id, safe="")),
                   headers={"Authorization": "Bearer %s" % token, "Prefer": 'IdType="ImmutableId"'},
                   json={"comment": comment}, timeout=30)
    if r.status_code != 202:
        raise HTTPException(status_code=502, detail=_safe_error("Microsoft 365 did not send the reply (%s)." % r.status_code))
    import uuid
    subj = last_in.subject or ""
    out = VoltSupportMessage(organization_id=prog.organization_id, mailbox_id=box.id,
                             graph_message_id="sent:%s" % uuid.uuid4(), conversation_id=conv, direction="out",
                             from_address=box.address, from_name=sender_name(prog),
                             to_addresses=last_in.from_address,
                             subject=subj if subj.lower().startswith("re:") else "RE: %s" % subj,
                             body_text=text, received_at=datetime.utcnow(), rep_serial=last_in.rep_serial,
                             staff_user_id=user.id)
    db.add(out)
    mark_handled(db, prog.organization_id, conv, user, True)
    C.audit(db, prog.organization_id, user, "support_message", last_in.id, "support_reply")
    db.commit()
    return thread(db, prog.organization_id, conv)


_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def attachment(db: Session, org_id: str, message_id: str, att_id: str):
    """(bytes, safe filename) of one attachment, for a forced download."""
    m = db.get(VoltSupportMessage, message_id)
    if m is None or m.organization_id != org_id or m.direction != "in":
        raise HTTPException(status_code=404, detail="Message not found.")
    meta = next((a for a in C.loads(m.attachments, []) if a.get("id") == att_id), None)
    if meta is None:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    if (meta.get("size") or 0) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="This attachment is too large to download here. Open it in Outlook.")
    box = db.get(InboundMailbox, m.mailbox_id)
    if box is None or box.purpose != PURPOSE:
        raise HTTPException(status_code=409, detail="The support mailbox is not connected.")
    try:
        token = _token(db, box)
    except MailboxAuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    r = _http.get("%s/me/messages/%s/attachments/%s" % (GRAPH, quote(m.graph_message_id, safe=""), quote(att_id, safe="")),
                  headers={"Authorization": "Bearer %s" % token, "Prefer": 'IdType="ImmutableId"'}, timeout=30)
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail="Microsoft 365 did not return the attachment.")
    data = r.json()
    if data.get("@odata.type") != "#microsoft.graph.fileAttachment" or not data.get("contentBytes"):
        raise HTTPException(status_code=415, detail="Only file attachments can be downloaded here. Open it in Outlook.")
    raw = base64.b64decode(data["contentBytes"])
    if len(raw) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="This attachment is too large to download here.")
    name = _FILENAME.sub("_", meta.get("name") or "attachment").strip() or "attachment"
    return raw, name[:150]
