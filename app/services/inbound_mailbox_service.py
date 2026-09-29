"""Reading replies from the shared sending mailbox into EvoSys.

See app/models/inbound_mailbox_models.py for why this exists.

CONNECT: a platform owner signs in to Microsoft AS the mailbox (for example
support@evosyspro.live) through the normal OAuth consent screen. Delegated
Mail.Read only - this path never sends mail and never holds a password.

POLL (every run of the email poller cron): read the Inbox from the stored
cursor (first run: the last 3 days, so replies that already arrived are picked
up), newest pages followed with @odata.nextLink.

ROUTE each message, in this order, never guessing across tenants:
  1. Which workspaces could this reply belong to? The mailbox's own workspace,
     plus every workspace whose resolved SENDING identity (from / reply-to) is
     this mailbox address.
  2. Which leads in those workspaces have the sender's email address?
       - exactly one -> that lead;
       - several -> the one we most recently emailed; if none of them was ever
         emailed by us the message is recorded as "ambiguous" and not attached.
  3. Store it the way every other inbound email is stored: a Reply on the lead
     (source "email"), lead status new/sent -> replied, and an in-app
     notification for the lead's advisor.

AUTOMATION: a reply is handed to the AI only when an AI conversation or a
pipeline is ALREADY running for that lead. A reply to a shared mailbox never
starts an automated conversation on its own.
"""
from __future__ import annotations

import html as _html
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage

log = logging.getLogger(__name__)

MAILBOX_SCOPES = "offline_access Mail.Read User.Read"
FIRST_RUN_LOOKBACK = timedelta(days=3)
CURSOR_OVERLAP = timedelta(minutes=10)
MAX_MESSAGES_PER_RUN = 500
GRAPH = "https://graph.microsoft.com/v1.0"


class MailboxAuthError(Exception):
    pass


# ── OAuth ─────────────────────────────────────────────────────────────────────

def authorization_url(state: str) -> str:
    from urllib.parse import urlencode
    from app.services import microsoft_email_service as M
    if not M.MICROSOFT_CLIENT_ID:
        raise RuntimeError("MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET not configured.")
    params = {"client_id": M.MICROSOFT_CLIENT_ID, "response_type": "code",
              "redirect_uri": M.MICROSOFT_REDIRECT_URI, "response_mode": "query",
              "scope": MAILBOX_SCOPES, "state": state,
              # Always show the account picker: the person must choose the
              # SHARED mailbox, not whatever account the browser is signed into.
              "prompt": "select_account"}
    return f"{M.AUTHORITY}/oauth2/v2.0/authorize?{urlencode(params)}"


def connect_from_code(db: Session, *, code: str, connected_by_user_id: str,
                      organization_id: Optional[str] = None) -> InboundMailbox:
    from app.services import microsoft_email_service as M
    from app.utils.crypto import encrypt_value
    r = httpx.post(f"{M.AUTHORITY}/oauth2/v2.0/token", data={
        "client_id": M.MICROSOFT_CLIENT_ID, "client_secret": M.MICROSOFT_CLIENT_SECRET,
        "code": code, "redirect_uri": M.MICROSOFT_REDIRECT_URI,
        "grant_type": "authorization_code", "scope": MAILBOX_SCOPES}, timeout=15)
    r.raise_for_status()
    tok = r.json()
    if not tok.get("refresh_token"):
        raise RuntimeError("Microsoft did not return a refresh token (offline_access not granted).")
    me = httpx.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=15)
    me.raise_for_status()
    prof = me.json()
    address = (prof.get("mail") or prof.get("userPrincipalName") or "").strip().lower()
    if not address:
        raise RuntimeError("Microsoft did not return the mailbox address.")
    box = db.query(InboundMailbox).filter(InboundMailbox.address == address).first()
    if box is None:
        box = InboundMailbox(address=address, organization_id=organization_id)
        db.add(box)
    box.refresh_token_encrypted = encrypt_value(tok["refresh_token"])
    box.is_active = True
    box.connected_by_user_id = connected_by_user_id
    box.connected_at = datetime.utcnow()
    box.last_status, box.last_error = "connected", None
    db.commit()
    return box


def _access_token(box: InboundMailbox) -> str:
    from app.services import microsoft_email_service as M
    from app.utils.crypto import decrypt_value
    if not box.refresh_token_encrypted:
        raise MailboxAuthError("No Microsoft sign-in stored for this mailbox.")
    r = httpx.post(f"{M.AUTHORITY}/oauth2/v2.0/token", data={
        "client_id": os.environ.get("MICROSOFT_CLIENT_ID") or M.MICROSOFT_CLIENT_ID,
        "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET") or M.MICROSOFT_CLIENT_SECRET,
        "refresh_token": decrypt_value(box.refresh_token_encrypted),
        "grant_type": "refresh_token", "scope": MAILBOX_SCOPES}, timeout=15)
    if r.status_code in (400, 401):
        raise MailboxAuthError(f"Microsoft refused the stored sign-in ({r.status_code}): {r.text[:200]}")
    r.raise_for_status()
    data = r.json()
    if data.get("refresh_token"):
        from app.utils.crypto import encrypt_value
        box.refresh_token_encrypted = encrypt_value(data["refresh_token"])
    return data["access_token"]


# ── Graph read ───────────────────────────────────────────────────────────────

def _fetch(token: str, since: datetime) -> List[dict]:
    """Inbox messages received at/after `since`, oldest first. Raises on failure."""
    iso = since.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{GRAPH}/me/mailFolders/inbox/messages"
    params = {"$filter": f"receivedDateTime ge {iso}",
              "$select": "id,internetMessageId,subject,from,receivedDateTime,body,bodyPreview,conversationId",
              "$orderby": "receivedDateTime asc", "$top": 50}
    out: List[dict] = []
    while url and len(out) < MAX_MESSAGES_PER_RUN:
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}",
                                    "Prefer": 'outlook.body-content-type="text"'},
                      params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Graph inbox read failed {r.status_code}: {r.text[:300]}")
        data = r.json()
        out.extend(data.get("value", []))
        url, params = data.get("@odata.nextLink"), None
    return out


_QUOTE_SPLITS = [
    r'\n[- ]*On .{5,200}?wrote:.*',
    r'\n[- ]*From:.*?\n.*?(Sent|Date):.*',
    r'\n_{5,}.*', r'\n-{5,}\s*Original Message.*', r'\n>.*',
]


def clean_body(content: str) -> str:
    t = content or ""
    if "<" in t and ">" in t:
        t = re.sub(r'(?i)<br\s*/?>|</p>|</div>', '\n', t)
        t = re.sub(r'<[^>]+>', ' ', t)
    t = _html.unescape(t).replace('\r\n', '\n')
    t = re.sub(r'[ \t ]+', ' ', t)
    for pat in _QUOTE_SPLITS:
        t = re.split(pat, t, maxsplit=1, flags=re.DOTALL | re.IGNORECASE)[0]
    t = re.sub(r'\n\s*\n\s*\n+', '\n\n', t).strip()
    return t[:4000] if len(t) > 4000 else t


# ── routing ─────────────────────────────────────────────────────────────────

def candidate_org_ids(db: Session, box: InboundMailbox) -> List[str]:
    """Workspaces whose replies can land in this mailbox."""
    from app.models.models import Organization
    from app.services.public_identity import sending_identity_for_org
    out = []
    if box.organization_id:
        out.append(box.organization_id)
    addr = box.address.lower()
    for (oid,) in db.query(Organization.id).filter(Organization.is_active.isnot(False)).all():
        if oid in out:
            continue
        try:
            ident = sending_identity_for_org(db, oid)
        except Exception:  # noqa: BLE001
            continue
        for v in (getattr(ident, "from_email", None), getattr(ident, "reply_to_email", None)):
            if v and v.strip().lower() == addr:
                out.append(oid)
                break
    return out


def route(db: Session, org_ids: List[str], sender: str):
    """(lead, outcome, detail)."""
    from sqlalchemy import func
    from app.models.models import EmailMessage, Lead
    if not org_ids:
        return None, "no_lead", "No workspace sends from this mailbox."
    leads = (db.query(Lead).filter(Lead.organization_id.in_(org_ids),
                                   func.lower(Lead.email) == sender).all())
    if not leads:
        return None, "no_lead", f"No lead with {sender} in the workspaces that send from this mailbox."
    if len(leads) == 1:
        return leads[0], "matched", None
    last = {}
    for lead_id, sent_at in (db.query(EmailMessage.lead_id, func.max(EmailMessage.sent_at))
                             .filter(EmailMessage.lead_id.in_([l.id for l in leads]))
                             .group_by(EmailMessage.lead_id).all()):
        if sent_at:
            last[lead_id] = sent_at
    emailed = [l for l in leads if l.id in last]
    if not emailed:
        return None, "ambiguous", (f"{len(leads)} leads have {sender} and none was emailed by us; "
                                   "not attached to any of them.")
    emailed.sort(key=lambda l: last[l.id], reverse=True)
    return emailed[0], "matched", f"{len(leads)} leads share this address; attached to the one emailed most recently."


def _store_reply(db: Session, lead, body: str, received_at: datetime):
    from app.models.models import Notification, Reply
    existing = db.query(Reply).filter(Reply.lead_id == lead.id, Reply.body == body).first()
    if existing:
        return existing, False
    reply = Reply(lead_id=lead.id, body=body, source="email", received_at=received_at,
                  classification="neutral", is_hot=False)
    db.add(reply)
    db.flush()
    if lead.status in (None, "", "new", "sent"):
        lead.status = "replied"
    if lead.assigned_to_id:
        name = " ".join(p for p in (lead.first_name, lead.last_name) if p) or "A lead"
        db.add(Notification(user_id=lead.assigned_to_id, type="email_reply",
                            message=f"{name} replied by email: {body[:140]}", lead_id=lead.id))
    return reply, True


def _maybe_hand_to_ai(db: Session, lead, reply) -> None:
    """Only when an AI conversation / pipeline is ALREADY running for this lead."""
    from app.models.models import PipelineConversation, User
    advisor = db.query(User).filter(User.id == lead.assigned_to_id).first() if lead.assigned_to_id else None
    if advisor is None:
        return
    try:
        from app.services.ai_conversation_service import handle_inbound_reply as ai_handle
        res = ai_handle(db, lead, advisor, reply.body)
        if res.get("action") != "no_active_conversation":
            return
    except Exception:  # noqa: BLE001
        log.exception("AI conversation handler failed for lead %s", lead.id)
        return
    active = db.query(PipelineConversation).filter(
        PipelineConversation.lead_id == lead.id,
        PipelineConversation.stage.notin_(["stopped", "dnc", "sale", "kept"])).first()
    if active is not None:
        try:
            from app.services.pipeline_service import process_inbound_reply
            process_inbound_reply(db, lead, advisor, reply)
        except Exception:  # noqa: BLE001
            log.exception("pipeline failed for lead %s", lead.id)


def poll_mailbox(db: Session, box: InboundMailbox, *, fetch=None, now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    box.last_polled_at = now
    try:
        token = _access_token(box) if fetch is None else None
        since = (box.cursor_received_at - CURSOR_OVERLAP) if box.cursor_received_at else now - FIRST_RUN_LOOKBACK
        messages = (fetch or (lambda s: _fetch(token, s)))(since)
    except MailboxAuthError as e:
        box.last_status, box.last_error = "auth_error", str(e)[:1000]
        db.commit()
        log.error("inbound mailbox %s: %s", box.address, e)
        return {"mailbox": box.address, "checked": 0, "matched": 0, "errors": 1, "auth_error": True}
    except Exception as e:  # noqa: BLE001
        box.last_status, box.last_error = "error", str(e)[:1000]
        db.commit()
        log.exception("inbound mailbox %s read failed", box.address)
        return {"mailbox": box.address, "checked": 0, "matched": 0, "errors": 1}

    org_ids = candidate_org_ids(db, box)
    seen = {r[0]: r[1] for r in db.query(InboundMailboxMessage.graph_message_id, InboundMailboxMessage.outcome)
            .filter(InboundMailboxMessage.mailbox_id == box.id,
                    InboundMailboxMessage.graph_message_id.in_([m.get("id") for m in messages if m.get("id")] or ["-"]))
            .all()}
    matched = errors = 0
    newest = box.cursor_received_at
    for m in messages:
        gid = m.get("id")
        if not gid:
            continue
        try:
            received = datetime.fromisoformat((m.get("receivedDateTime") or "").replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:  # noqa: BLE001
            received = now
        if newest is None or received > newest:
            newest = received
        if gid in seen and seen[gid] != "error":
            continue
        sender = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").strip().lower()
        row = (db.query(InboundMailboxMessage).filter(InboundMailboxMessage.mailbox_id == box.id,
                                                      InboundMailboxMessage.graph_message_id == gid).first()
               or InboundMailboxMessage(mailbox_id=box.id, graph_message_id=gid))
        row.internet_message_id = m.get("internetMessageId")
        row.from_address, row.subject, row.received_at = sender or None, (m.get("subject") or "")[:500], received
        try:
            if not sender or sender == box.address:
                row.outcome, row.detail = "own_mail", "Sent by the mailbox itself."
            else:
                lead, outcome, detail = route(db, org_ids, sender)
                row.outcome, row.detail = outcome, detail
                if lead is not None:
                    body = clean_body((m.get("body") or {}).get("content") or m.get("bodyPreview") or "")
                    if not body:
                        body = (m.get("bodyPreview") or m.get("subject") or "(empty reply)")[:800]
                    reply, created = _store_reply(db, lead, body, received)
                    row.organization_id, row.lead_id, row.reply_id = lead.organization_id, lead.id, reply.id
                    if not created:
                        row.detail = ((row.detail + " ") if row.detail else "") + "Already on the lead."
                    db.add(row)
                    db.commit()
                    if created:
                        _maybe_hand_to_ai(db, lead, reply)
                        db.commit()
                    matched += 1
                    continue
            db.add(row)
            db.commit()
        except Exception as e:  # noqa: BLE001 - one message must not stop the rest
            db.rollback()
            errors += 1
            log.exception("inbound mailbox %s message %s failed", box.address, gid)
            try:
                fail = (db.query(InboundMailboxMessage).filter(InboundMailboxMessage.mailbox_id == box.id,
                                                               InboundMailboxMessage.graph_message_id == gid).first()
                        or InboundMailboxMessage(mailbox_id=box.id, graph_message_id=gid))
                fail.outcome, fail.detail = "error", f"{type(e).__name__}: {e}"[:1000]
                fail.from_address, fail.received_at = sender or None, received
                db.add(fail)
                db.commit()
            except Exception:  # noqa: BLE001
                db.rollback()
    # Advance the cursor only past messages that did not error, so a failed one
    # is read again next run.
    if errors == 0 and newest is not None:
        box.cursor_received_at = newest
    box.last_status = "ok" if errors == 0 else "error"
    box.last_error = None if errors == 0 else f"{errors} message(s) failed; see the inbound log."
    box.last_checked, box.last_matched = len(messages), matched
    db.commit()
    return {"mailbox": box.address, "checked": len(messages), "matched": matched, "errors": errors}


def probe(db: Session, box: InboundMailbox) -> Dict:
    """Read-only look at the mailbox: its folders and the 15 newest messages in
    ANY folder, so "why was nothing read?" can be answered (moved to another
    folder, Junk, a different account signed in...)."""
    token = _access_token(box)
    db.commit()
    h = {"Authorization": f"Bearer {token}"}
    me = httpx.get(f"{GRAPH}/me", headers=h, params={"$select": "mail,userPrincipalName,displayName"}, timeout=15)
    folders = httpx.get(f"{GRAPH}/me/mailFolders", headers=h,
                        params={"$select": "id,displayName,totalItemCount,unreadItemCount", "$top": 50}, timeout=15)
    fmap = {f["id"]: f["displayName"] for f in (folders.json().get("value", []) if folders.status_code == 200 else [])}
    msgs = httpx.get(f"{GRAPH}/me/messages", headers=h,
                     params={"$select": "subject,from,receivedDateTime,parentFolderId",
                             "$orderby": "receivedDateTime desc", "$top": 15}, timeout=20)
    return {
        "account": me.json() if me.status_code == 200 else {"error": me.status_code, "body": me.text[:300]},
        "folders": ([{"name": f["displayName"], "total": f.get("totalItemCount"), "unread": f.get("unreadItemCount")}
                     for f in folders.json().get("value", [])] if folders.status_code == 200
                    else {"error": folders.status_code, "body": folders.text[:300]}),
        "newest": ([{"received": m.get("receivedDateTime"), "subject": m.get("subject"),
                     "from": ((m.get("from") or {}).get("emailAddress") or {}).get("address"),
                     "folder": fmap.get(m.get("parentFolderId"), m.get("parentFolderId"))}
                    for m in msgs.json().get("value", [])] if msgs.status_code == 200
                   else {"error": msgs.status_code, "body": msgs.text[:300]}),
    }


def poll_all_mailboxes(db: Session) -> Dict:
    totals = {"mailboxes_polled": 0, "mailbox_checked": 0, "mailbox_matched": 0,
              "mailbox_errors": 0, "mailbox_results": []}
    for box in db.query(InboundMailbox).filter(InboundMailbox.is_active.is_(True)).all():
        res = poll_mailbox(db, box)
        totals["mailboxes_polled"] += 1
        totals["mailbox_checked"] += res.get("checked", 0)
        totals["mailbox_matched"] += res.get("matched", 0)
        totals["mailbox_errors"] += res.get("errors", 0)
        totals["mailbox_results"].append(res)
    return totals
