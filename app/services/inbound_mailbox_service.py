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

# Mail.ReadWrite: after a location-alias reply is fully processed, EvoSys files
# the original into the location's Outlook folder (programs/mailfiling.py).
# A mailbox connected before this asked only for Mail.Read; its refresh falls
# back to MAILBOX_READ_SCOPES, reading continues exactly as before, and filing
# reports "reconnect with Mail.ReadWrite".
MAILBOX_SCOPES = "offline_access Mail.ReadWrite User.Read"
MAILBOX_READ_SCOPES = "offline_access Mail.Read User.Read"
FIRST_RUN_LOOKBACK = timedelta(days=3)
CURSOR_OVERLAP = timedelta(minutes=10)
MAX_MESSAGES_PER_RUN = 500
# A message that fails holds the cursor at itself so the next run reads it
# again - but only for this long after its first failure. A message that fails
# on EVERY run otherwise pins the cursor forever, and with the per-run cap
# (oldest first) a busy mailbox never reads anything newer again. After the
# window it stays in the inbound log as outcome "error" (the dead letter) and
# the cursor moves on.
ERROR_RETRY_WINDOW = timedelta(hours=24)
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


def _refresh(box: InboundMailbox, scope: str):
    from app.services import microsoft_email_service as M
    from app.utils.crypto import decrypt_value
    return httpx.post(f"{M.AUTHORITY}/oauth2/v2.0/token", data={
        "client_id": os.environ.get("MICROSOFT_CLIENT_ID") or M.MICROSOFT_CLIENT_ID,
        "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET") or M.MICROSOFT_CLIENT_SECRET,
        "refresh_token": decrypt_value(box.refresh_token_encrypted),
        "grant_type": "refresh_token", "scope": scope}, timeout=15)


def _access_token_rw(box: InboundMailbox):
    """(access_token, can_write). Asks for Mail.ReadWrite; a sign-in that only
    ever consented to Mail.Read gets a read token instead of an error."""
    if not box.refresh_token_encrypted:
        raise MailboxAuthError("No Microsoft sign-in stored for this mailbox.")
    r = _refresh(box, MAILBOX_SCOPES)
    can_write = True
    if r.status_code in (400, 401):
        first = r.text[:200]
        r = _refresh(box, MAILBOX_READ_SCOPES)
        can_write = False
        if r.status_code in (400, 401):
            raise MailboxAuthError(f"Microsoft refused the stored sign-in ({r.status_code}): {first}")
    r.raise_for_status()
    data = r.json()
    granted = (data.get("scope") or "").lower()
    if granted and "mail.readwrite" not in granted:
        can_write = False
    if data.get("refresh_token"):
        from app.utils.crypto import encrypt_value
        box.refresh_token_encrypted = encrypt_value(data["refresh_token"])
    return data["access_token"], can_write


def _access_token(box: InboundMailbox) -> str:
    return _access_token_rw(box)[0]


# ── Graph read ───────────────────────────────────────────────────────────────

# Folders that never hold a reply FROM a lead. Everything else is read -
# including Junk and any folder a mailbox rule files mail into. (2026-09-29: the
# support@evosyspro.live rules put Joshua's reply in "Careers / Indeed
# Applicants"; an Inbox-only read saw nothing.)
_SKIP_WELL_KNOWN = ("sentitems", "drafts", "outbox", "deleteditems")


def _skip_folder_ids(token: str) -> set:
    out = set()
    for name in _SKIP_WELL_KNOWN:
        r = httpx.get(f"{GRAPH}/me/mailFolders/{name}", headers={"Authorization": f"Bearer {token}"},
                      params={"$select": "id"}, timeout=15)
        if r.status_code == 200 and r.json().get("id"):
            out.add(r.json()["id"])
    return out


def _fetch(token: str, since: datetime) -> List[dict]:
    """Messages in ANY folder except Sent/Drafts/Outbox/Deleted, received at or
    after `since`, oldest first. Raises on failure."""
    iso = since.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    skip = _skip_folder_ids(token)
    url = f"{GRAPH}/me/messages"
    params = {"$filter": f"receivedDateTime ge {iso}",
              "$select": "id,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,bodyPreview,conversationId,parentFolderId",
              "$orderby": "receivedDateTime asc", "$top": 50}
    out: List[dict] = []
    while url and len(out) < MAX_MESSAGES_PER_RUN:
        # ImmutableId: a message keeps its id when a rule or a person moves it
        # between folders (ids changed when Joshua's reply was re-filed).
        r = httpx.get(url, headers={"Authorization": f"Bearer {token}",
                                    "Prefer": 'outlook.body-content-type="text", IdType="ImmutableId"'},
                      params=params, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"Graph inbox read failed {r.status_code}: {r.text[:300]}")
        data = r.json()
        out.extend(m for m in data.get("value", []) if m.get("parentFolderId") not in skip)
        url, params = data.get("@odata.nextLink"), None
    return out


_QUOTE_SPLITS = [
    r'\n?-{5,}\s*Forwarded message\s*-{5,}.*',
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
    # Signature / inline images arrive in the text body as "[https://...]" or
    # "[image: name]" - never the person's words.
    t = re.sub(r'\[(?:image:[^\]]*|https?://[^\]\s]+)\]', '', t)
    t = re.sub(r'<https?://[^>\s]+>', '', t)
    t = re.sub(r'[ \t ]+', ' ', t)
    for pat in _QUOTE_SPLITS:
        t = re.split(pat, t, maxsplit=1, flags=re.DOTALL | re.IGNORECASE)[0]
    t = re.sub(r'\n\s*\n\s*\n+', '\n\n', t).strip()
    return t[:4000] if len(t) > 4000 else t


# ── routing ─────────────────────────────────────────────────────────────────

def org_sending_addresses(db: Session) -> Dict[str, set]:
    """org id -> the lower-cased From / Reply-To addresses it sends under, for
    every active org, in the organizations query order.

    PERFORMANCE (S18, 2026-10-01). candidate_org_ids used to call
    sending_identity_for_org() for EVERY active org, for EVERY mailbox, every
    five minutes - ~8 queries per org (org, platform, brand-config, then the
    org and platform again for the audit BCC and platform id, neither of which
    routing reads). Only From and Reply-To matter here, and they resolve as
    public_identity.identity_for_org documents:
      * Reply-To: the organization's own column, no inheritance;
      * From: the organization's own from_email, else a value that depends
        ONLY on its platform (platform support_email, then the brand registry
        for the platform slug, else unresolved).
    So the orgs are read in one query and identity_for_org runs once per
    distinct platform (for an org with no from_email of its own) instead of
    once per org. tests/test_perf_oct1.py pins this map against
    sending_identity_for_org for every org shape. Callers that route several
    mailboxes in one run compute it once and pass it in.
    """
    from app.models.models import Organization
    from app.services.public_identity import identity_for_org
    rows = (db.query(Organization.id, Organization.from_email, Organization.reply_to_email,
                     Organization.platform_id)
            .filter(Organization.is_active.isnot(False)).all())
    inherited: Dict[Optional[str], Optional[str]] = {}
    out: Dict[str, set] = {}
    for oid, from_email, reply_to, platform_id in rows:
        if from_email:
            frm = from_email
        else:
            if platform_id not in inherited:
                try:
                    inherited[platform_id] = identity_for_org(db, oid).from_email
                except Exception:  # noqa: BLE001
                    inherited[platform_id] = None
            frm = inherited[platform_id]
        out[oid] = {v.strip().lower() for v in (frm, reply_to) if v and v.strip()}
    return out


def candidate_org_ids(db: Session, box: InboundMailbox,
                      addresses: Optional[Dict[str, set]] = None) -> List[str]:
    """Workspaces whose replies can land in this mailbox. `addresses` is
    org_sending_addresses(db), passed by callers that route several mailboxes."""
    out = []
    if box.organization_id:
        out.append(box.organization_id)
    addr = box.address.lower()
    if addresses is None:
        addresses = org_sending_addresses(db)
    for oid, sends_as in addresses.items():
        if oid not in out and addr in sends_as:
            out.append(oid)
    return out


_SUBJ_PREFIX = re.compile(r'^\s*((re|fw|fwd|aw|sv)\s*(\[\d+\])?\s*:\s*)+', re.I)


def _norm_subject(subject: Optional[str]) -> str:
    return " ".join(_SUBJ_PREFIX.sub("", subject or "").lower().split())


def route(db: Session, org_ids: List[str], sender: str, subject: Optional[str] = None):
    """(lead, outcome, detail).

    A SHARED mailbox (several workspaces send from it) never attaches a reply to
    a lead no workspace emailed, and never picks between two TENANTS on recency
    alone: the reply's subject must point at one of them, or it is logged as
    ambiguous and attached to nobody. (Review 2026-10-01: tenant A emails Jane on
    Monday, tenant B on Wednesday; Jane answers A's email on Thursday - recency
    alone put her reply on B's lead.)
    """
    from sqlalchemy import func
    from app.models.models import EmailMessage, Lead
    if not org_ids:
        return None, "no_lead", "No workspace sends from this mailbox."
    leads = (db.query(Lead).filter(Lead.organization_id.in_(org_ids),
                                   func.lower(Lead.email) == sender).all())
    if not leads:
        return None, "no_lead", f"No lead with {sender} in the workspaces that send from this mailbox."
    shared = len(set(org_ids)) > 1
    last, subjects = {}, {}
    for lead_id, sent_at, subj in (db.query(EmailMessage.lead_id, EmailMessage.sent_at, EmailMessage.subject)
                                   .filter(EmailMessage.lead_id.in_([l.id for l in leads])).all()):
        if sent_at and (lead_id not in last or sent_at > last[lead_id]):
            last[lead_id] = sent_at
        subjects.setdefault(lead_id, set()).add(_norm_subject(subj))
    if not shared:
        if len(leads) == 1:
            return leads[0], "matched", None
        emailed = [l for l in leads if l.id in last]
        if not emailed:
            return None, "ambiguous", (f"{len(leads)} leads have {sender} and none was emailed by us; "
                                       "not attached to any of them.")
        emailed.sort(key=lambda l: last[l.id], reverse=True)
        return emailed[0], "matched", f"{len(leads)} leads share this address; attached to the one emailed most recently."

    emailed = [l for l in leads if l.id in last]
    if not emailed:
        return None, "no_lead", (f"{sender} is a lead, but no workspace sharing this mailbox ever emailed "
                                 "them, so the reply was not attached.")
    want = _norm_subject(subject)
    by_subject = [l for l in emailed if want and want in subjects.get(l.id, set())]
    pool = by_subject or emailed
    orgs = {l.organization_id for l in pool}
    if len(orgs) > 1:
        return None, "ambiguous", (f"{len(pool)} leads in {len(orgs)} workspaces were emailed at {sender}"
                                   + (" with this subject" if by_subject else "")
                                   + "; not attached to any of them.")
    pool.sort(key=lambda l: last[l.id], reverse=True)
    detail = None
    if len(pool) > 1:
        detail = f"{len(pool)} leads share this address; attached to the one emailed most recently."
    elif not by_subject and want:
        detail = "Subject did not match a sent email; attached to the only lead emailed at this address."
    return pool[0], "matched", detail


def recipients_of(m: dict) -> List[str]:
    """Every To/Cc address on a Graph message, lower-cased."""
    out = []
    for key in ("toRecipients", "ccRecipients"):
        for r in m.get(key) or []:
            a = ((r or {}).get("emailAddress") or {}).get("address")
            if a and a.strip():
                out.append(a.strip().lower())
    return out


def _alias_location(db: Session, m: dict):
    """LOCATION OUTREACH PROGRAMS: the location profile whose alias this
    message was addressed to (easterngategardens@...), or None. An alias names
    exactly one workspace and location, so it - not the shared-mailbox sender
    list - decides where the reply is routed."""
    try:
        from app.services.programs import aliases as _aliases
        return _aliases.resolve(db, recipients_of(m))
    except Exception:  # noqa: BLE001 - routing falls back to the sender, as before
        log.exception("alias resolution failed")
        return None


def _file_processed(db: Session, box: InboundMailbox, row, gid: str, prof, mover) -> None:
    try:
        from app.services.programs import identity as _identity
        from app.services.programs.mailfiling import file_message
        prog = _identity.program_for_org(db, prof.organization_id)
        if prog is None:
            return
        f = file_message(db, mover, box_id=box.id, mailbox_message_id=row.id, graph_id=gid,
                         prog=prog, prof=prof)
        if f is not None:
            row.detail = ((row.detail + " ") if row.detail else "") + (
                "Filed in %s." % f.folder_path if f.status == "filed"
                else "Not filed yet (%s)." % (f.last_error or f.status))
        db.commit()
    except Exception:  # noqa: BLE001 - a filing problem never undoes processing
        db.rollback()
        log.exception("outlook filing failed for message %s", gid)


def _store_reply(db: Session, lead, body: str, received_at: datetime, message_id: Optional[str] = None):
    from app.models.models import Notification, Reply
    # Same message (same body, received within minutes), not merely the same
    # words: a second "Yes" days later is a second reply. See reply_dedupe.
    from app.services.reply_dedupe import find_duplicate_email_reply, insert_email_reply
    message_id = (message_id or "").strip()[:500] or None
    existing = find_duplicate_email_reply(db, lead.id, body, received_at, message_id)
    if existing:
        return existing, False
    reply = Reply(lead_id=lead.id, body=body, source="email", received_at=received_at,
                  classification="neutral", is_hot=False, source_message_id=message_id)
    if not insert_email_reply(db, reply):
        # A concurrent reader / run stored this Message-ID first.
        return find_duplicate_email_reply(db, lead.id, body, received_at, message_id), False
    if lead.status in (None, "", "new", "sent"):
        lead.status = "replied"
    if lead.assigned_to_id:
        name = " ".join(p for p in (lead.first_name, lead.last_name) if p) or "A lead"
        from app.models.models import NotificationType
        notif = Notification(user_id=lead.assigned_to_id, type=NotificationType.REPLY_RECEIVED,
                             message=f"{name} replied by email: {body[:140]}", lead_id=lead.id)
        db.add(notif)
        # Web push outbox row (generic title, no content); never raises.
        from app.services.web_push_service import enqueue_for_notification
        enqueue_for_notification(db, notif, organization_id=lead.organization_id)
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


def _reconnect_alert(db: Session, box: InboundMailbox, error: str) -> None:
    """The mailbox just lost its Microsoft sign-in: tell the people who depend
    on it, once per outage (not every five minutes)."""
    try:
        from app.services.programs import responses as _pr
        _pr.mailbox_reconnect_alert(db, box.address, error)
    except Exception:  # noqa: BLE001 - alerting must never break polling
        log.exception("mailbox reconnect alert failed")


def poll_mailbox(db: Session, box: InboundMailbox, *, fetch=None, now: Optional[datetime] = None,
                 addresses: Optional[Dict[str, set]] = None, mover=None) -> Dict:
    now = now or datetime.utcnow()
    box.last_polled_at = now
    was_auth_error = box.last_status == "auth_error"
    try:
        token = None
        if fetch is None:
            token, can_write = _access_token_rw(box)
            if mover is None:
                from app.services.programs.mailfiling import GraphMover
                mover = GraphMover(token, can_write)
        since = (box.cursor_received_at - CURSOR_OVERLAP) if box.cursor_received_at else now - FIRST_RUN_LOOKBACK
        messages = (fetch or (lambda s: _fetch(token, s)))(since)
    except MailboxAuthError as e:
        box.last_status, box.last_error = "auth_error", str(e)[:1000]
        db.commit()
        if not was_auth_error:
            _reconnect_alert(db, box, str(e))
        log.error("inbound mailbox %s: %s", box.address, e)
        return {"mailbox": box.address, "checked": 0, "matched": 0, "errors": 1, "auth_error": True}
    except Exception as e:  # noqa: BLE001
        box.last_status, box.last_error = "error", str(e)[:1000]
        db.commit()
        log.exception("inbound mailbox %s read failed", box.address)
        return {"mailbox": box.address, "checked": 0, "matched": 0, "errors": 1}

    org_ids = candidate_org_ids(db, box, addresses)
    seen = {r[0]: r[1] for r in db.query(InboundMailboxMessage.graph_message_id, InboundMailboxMessage.outcome)
            .filter(InboundMailboxMessage.mailbox_id == box.id,
                    InboundMailboxMessage.graph_message_id.in_([m.get("id") for m in messages if m.get("id")] or ["-"]))
            .all()}
    matched = errors = 0
    newest = box.cursor_received_at
    hold = None   # oldest received time of a failed message still being retried
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
        imid = m.get("internetMessageId")
        if imid and db.query(InboundMailboxMessage.id).filter(
                InboundMailboxMessage.mailbox_id == box.id,
                InboundMailboxMessage.internet_message_id == imid,
                InboundMailboxMessage.graph_message_id != gid,
                InboundMailboxMessage.outcome.in_(("matched", "own_mail"))).first():
            continue  # the same message under an older id, already handled
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
                alias_prof = _alias_location(db, m)
                if alias_prof is not None:
                    from app.services.programs import aliases as _aliases
                    _aliases.mark_verified(alias_prof, now)
                    lead, outcome, detail = route(db, [alias_prof.organization_id], sender, m.get("subject"))
                    detail = ("Sent to %s (%s). " % (alias_prof.email_alias, alias_prof.official_name)
                              + (detail or "")).strip()
                else:
                    lead, outcome, detail = route(db, org_ids, sender, m.get("subject"))
                row.outcome, row.detail = outcome, detail
                if lead is None and alias_prof is not None:
                    # Addressed to a location but no contact matches: kept,
                    # alerted and queued in the program - never left unread.
                    row.organization_id = alias_prof.organization_id
                    db.add(row)
                    db.flush()
                    from app.services.programs import responses as _program_responses
                    _program_responses.record_unmatched(
                        db, alias_prof, alias=alias_prof.email_alias, sender=sender,
                        subject=m.get("subject"),
                        body=clean_body((m.get("body") or {}).get("content") or m.get("bodyPreview") or ""),
                        received_at=received, mailbox_message_id=row.id, reason=outcome)
                    db.commit()
                    continue
                if lead is not None:
                    body = clean_body((m.get("body") or {}).get("content") or m.get("bodyPreview") or "")
                    if not body:
                        subj = (m.get("subject") or "").strip()
                        body = ("(Forwarded the email with no message of their own.)"
                                if re.match(r'(?i)^(fwd?|fw):', subj) else "(Replied with no text.)")
                    reply, created = _store_reply(db, lead, body, received, m.get("internetMessageId"))
                    row.organization_id, row.lead_id, row.reply_id = lead.organization_id, lead.id, reply.id
                    if not created:
                        row.detail = ((row.detail + " ") if row.detail else "") + "Already on the lead."
                    db.add(row)
                    db.commit()
                    if created:
                        # Location outreach programs: pause, classify, alert, SLA.
                        from app.services.programs import responses as _program_responses
                        _program_responses.safe_on_inbound(
                            db, lead, body, "email", reply_id=reply.id,
                            reply_to_alias=alias_prof.email_alias if alias_prof is not None else None,
                            alias_location_id=alias_prof.location_id if alias_prof is not None else None)
                        _maybe_hand_to_ai(db, lead, reply)
                        db.commit()
                    if alias_prof is not None:
                        # Processed first, filed second: only now is the
                        # original moved to its location folder.
                        _file_processed(db, box, row, gid, alias_prof, mover)
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
                first_failed = fail.created_at or now
            except Exception:  # noqa: BLE001
                db.rollback()
                first_failed = now
            if now - first_failed < ERROR_RETRY_WINDOW:
                hold = received if hold is None else min(hold, received)
    if mover is not None:
        try:
            from app.services.programs.mailfiling import retry_pending
            retry_pending(db, mover, box.id)
        except Exception:  # noqa: BLE001
            db.rollback()
            log.exception("outlook filing retry failed for %s", box.address)
    # Advance the cursor, but never past a failed message that is still inside
    # its retry window, so it is read again next run (the fetch is `ge`, and the
    # next run starts CURSOR_OVERLAP earlier still).
    if newest is not None:
        box.cursor_received_at = newest if hold is None else min(newest, hold)
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
    boxes = db.query(InboundMailbox).filter(InboundMailbox.is_active.is_(True)).all()
    # Resolved once per run, not once per mailbox (see org_sending_addresses).
    addresses = org_sending_addresses(db) if boxes else None
    for box in boxes:
        res = poll_mailbox(db, box, addresses=addresses)
        totals["mailboxes_polled"] += 1
        totals["mailbox_checked"] += res.get("checked", 0)
        totals["mailbox_matched"] += res.get("matched", 0)
        totals["mailbox_errors"] += res.get("errors", 0)
        totals["mailbox_results"].append(res)
    return totals
