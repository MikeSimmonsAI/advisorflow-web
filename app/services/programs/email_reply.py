"""SCI EMAIL REPLIES: answer an inbound family email IN ITS OWN THREAD.

A reply to an email goes back to the person who actually wrote it (the
verified sender on the inbound message - not whoever is named on the family
record), from the mailbox it arrived in, through Microsoft Graph's own
threaded reply (POST /me/messages/{id}/reply). It is never swapped for a
fresh outbound email: when the original message can't be found, the send
fails with that reason.

Every attempt is recorded on EmailMessage - who it went to, from which
mailbox, the body actually sent, the original's Internet Message-ID, the
provider result - and is idempotent on a caller-supplied key, so a double
tap, a retry or a refresh never sends twice. The family's reply is marked
responded only after the provider accepted the send.
"""
from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime
from typing import Dict, Optional

import httpx
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def _strip_html(s: str) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s or "")
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", s)).strip()


def plain_text(body_html: str) -> str:
    return _strip_html(body_html)


def inbound_meta(db: Session, resp) -> Dict:
    """Who really wrote this email reply, its subject, and the message to
    thread on. {} for a reply that is not an email."""
    if resp is None or (resp.channel or "") != "email":
        return {}
    from app.models.inbound_mailbox_models import InboundMailbox, InboundMailboxMessage
    from app.models.models import Reply
    row = None
    if resp.reply_id:
        row = (db.query(InboundMailboxMessage)
               .filter(InboundMailboxMessage.reply_id == resp.reply_id).first())
    out: Dict = {}
    if row is not None:
        box = db.query(InboundMailbox).filter(InboundMailbox.id == row.mailbox_id).first()
        out = {"sender_email": (row.from_address or "").strip().lower() or None,
               "sender_name": (getattr(row, "from_name", None) or "").strip() or None,
               "subject": row.subject, "mailbox_id": row.mailbox_id,
               "mailbox": box.address if box is not None else None,
               "graph_message_id": row.graph_message_id,
               "internet_message_id": row.internet_message_id,
               "threadable": bool(row.graph_message_id and box is not None and box.is_active)}
    elif resp.reply_id:
        rep = db.query(Reply).filter(Reply.id == resp.reply_id).first()
        out = {"internet_message_id": getattr(rep, "source_message_id", None), "threadable": False}
    return out


def _text_to_html(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return "".join("<p>%s</p>" % _html.escape(p).replace("\n", "<br>") for p in paras)


def signature(db: Session, lead) -> str:
    """The same identity the family sees on texts: "Kerry Allan, <Location>"."""
    try:
        from app.services.programs import identity
        prog = identity.program_for_org(db, lead.organization_id)
        prof = identity.location_profile_for_lead(db, lead)
        if prog is not None and prof is not None:
            return identity.sms_signoff(prog, prof).lstrip("- ").strip()
    except Exception:                                    # noqa: BLE001
        pass
    return ""


def _blocked(db: Session, lead, to_addr: Optional[str]) -> Optional[str]:
    from app.models.models import Lead
    if lead is None:
        return "no family record"
    from app.services.sci_staging_demo import is_demo_lead
    from app.services.test_records import owner_live_test_email_allowed
    if is_demo_lead(lead):
        return "a staging demo family is never emailed"
    if getattr(lead, "is_test", False) and not owner_live_test_email_allowed(db, lead, to_addr):
        return "a test contact is never emailed (only the owner's own live test address)"
    if (lead.status or "") == "dnc":
        return "this family is Do Not Contact"
    if getattr(lead, "manual_flag", None) in ("remove_all", "bad_email"):
        return "this record is flagged: no email"
    if getattr(lead, "allow_email", None) is False:
        return "this family opted out of email"
    if not to_addr or "@" not in to_addr:
        return "the original sender's address is not known"
    other = (db.query(Lead.id).filter(Lead.organization_id == lead.organization_id, Lead.status == "dnc",
                                      Lead.email == to_addr).first())
    if other is not None:
        return "this address is Do Not Contact"
    from app.services import outbound_brake
    if outbound_brake.engaged():
        return "outbound messaging is paused platform-wide"
    return None


def _lock(db: Session, key: str) -> None:
    try:
        if db.bind.dialect.name == "postgresql":
            from sqlalchemy import text
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": "email-reply:" + key})
    except Exception:                                    # noqa: BLE001
        pass


def _row_json(m) -> Dict:
    return {"email_id": m.id, "status": m.status, "to": getattr(m, "to_email", None),
            "from": getattr(m, "from_email", None), "subject": m.subject,
            "in_reply_to": getattr(m, "in_reply_to", None), "error": getattr(m, "error", None),
            "sent_at": m.sent_at.isoformat() + "Z" if m.sent_at else None}


def send_reply(db: Session, prog, lead, resp, text: str, *, send_source: str, idempotency_key: str,
               actor_id: Optional[str] = None) -> Dict:
    """Reply to the family email `resp` in its own thread. Returns
    {"ok", "duplicate"?, "error"?, "email": {...}}. Never raises."""
    from app.models.models import EmailMessage
    from app.services.programs import appointments as A
    key = (idempotency_key or "").strip()[:120]
    if not key:
        return {"ok": False, "error": "missing send key"}
    _lock(db, key)
    prior = db.query(EmailMessage).filter(EmailMessage.idempotency_key == key).first()
    if prior is not None:
        return {"ok": prior.status == "sent", "duplicate": True, "email": _row_json(prior),
                "error": getattr(prior, "error", None) if prior.status != "sent" else None}
    meta = inbound_meta(db, resp)
    to_addr = meta.get("sender_email")
    why = _blocked(db, lead, to_addr)
    if why is None and not meta.get("threadable"):
        why = "the original email is not in a connected mailbox, so it cannot be answered in its thread"
    body_text = (text or "").strip()
    if why is None and not body_text:
        why = "the reply is empty"
    sig = signature(db, lead)
    full = body_text + (("\n\n" + sig) if sig and sig not in body_text else "")
    body_html = _text_to_html(full)
    subj = meta.get("subject") or ""
    subject = subj if re.match(r"(?i)^re:", subj) else ("Re: " + subj if subj else "Re:")
    advisor = A.program_advisor(db, prog) if prog is not None else None
    row = EmailMessage(lead_id=lead.id, sender_id=(advisor.id if advisor is not None else actor_id),
                       subject=subject[:250], body_html=body_html, status="sending", send_source=send_source,
                       sent_by_user_id=actor_id)
    row.idempotency_key = key
    row.to_email = to_addr
    row.from_email = meta.get("mailbox")
    row.in_reply_to = meta.get("internet_message_id")
    row.response_id = getattr(resp, "id", None)
    if row.sender_id is None:
        return {"ok": False, "error": "no sender for this program"}
    db.add(row)
    db.flush()
    if why is not None:
        row.status, row.error = "failed", why[:500]
        db.commit()
        return {"ok": False, "error": why, "email": _row_json(row)}
    from app.models.inbound_mailbox_models import InboundMailbox
    from app.services import inbound_mailbox_service as ims
    box = db.query(InboundMailbox).filter(InboundMailbox.id == meta["mailbox_id"]).first()
    try:
        token = ims._access_token_send(box)
        r = httpx.post("%s/me/messages/%s/reply" % (ims.GRAPH, meta["graph_message_id"]),
                       headers={"Authorization": "Bearer %s" % token,
                                "Prefer": 'IdType="ImmutableId"'},
                       json={"comment": body_html}, timeout=30)
        ok = r.status_code == 202
        err = None if ok else "Microsoft 365 refused the reply (%s): %s" % (r.status_code, r.text[:300])
    except Exception as exc:                             # noqa: BLE001 - a refusal is a failed send
        ok, err = False, "%s: %s" % (type(exc).__name__, str(exc)[:300])
    row.status = "sent" if ok else "failed"
    row.error = err
    row.provider_message_id = ("graph-reply:%s" % meta["graph_message_id"])[:250] if ok else None
    if ok and resp is not None:
        now = datetime.utcnow()
        resp.responded_at = resp.responded_at or now
        if (resp.handling_status or "new") in ("new", "opened"):
            resp.handling_status = "responded"
    db.commit()
    if ok:
        try:
            from app.services.programs import identity
            identity.record_on_behalf(db, lead, channel="email", actor_user_id=actor_id,
                                      sender_user_id=row.sender_id, message_id=row.id)
        except Exception:                                # noqa: BLE001
            pass
    return {"ok": ok, "error": err, "email": _row_json(row)}
