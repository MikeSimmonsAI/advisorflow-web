"""EvoSys Sales: a lead's emailed reply stops their follow-up by itself.

HOW A REPLY FINDS ITS LEAD (no AI, no guessing)
  Every follow-up email carries a Reply-To unique to the lead:
      info+evs-<code>@evosyspro.live
  (the team's reply address with a "+evs-<code>" tag; Microsoft 365 delivers a
  plus-tagged address to the plain mailbox). The reply comes back addressed to
  that tag, so the tag names the lead. This is the thread identifier we control:
  our emails go out through Resend, so the reply shares no Outlook conversation
  with them, and the Graph read does not carry In-Reply-To.

  Without a tag (the person wrote a fresh email, or a client dropped the
  Reply-To) the sender's address may still name the lead: only if exactly ONE
  EvoSys Sales lead has that address AND was emailed by the follow-up. Two or
  more such leads is recorded as ambiguous and attached to nobody.

WHAT IS IGNORED
  Out-of-office and other automatic replies (Auto-Submitted, X-Autoreply,
  Precedence: auto_reply/bulk/junk headers, or an automatic-reply subject):
  logged on the inbound log, never stop the follow-up. The same message read
  twice (same Internet Message-ID) is recorded once.

WHERE IT HOOKS IN
  inbound_mailbox_service.poll_mailbox calls handle() for every message of a
  connected mailbox, before its own routing for tagged replies and after it
  (only when nothing else claimed the message) for the sender fallback.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.evosys_sales_models import EvoSysOutreachMessage, EvoSysOutreachState
from app.models.sales_models import Opportunity, OpportunityEvent

log = logging.getLogger(__name__)

TAG_RE = re.compile(r"^[^@+\s]+\+evs-([a-z0-9]{6,16})@", re.I)
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

AUTO_SUBJECT = re.compile(
    r"^\s*(automatic reply|auto(matic)?[- ]?(reply|response)|autoreply|out of (the )?office|"
    r"away from (the |my )?office|on vacation|vacation reply|i am out of|i'm out of|"
    r"delivery status notification|undeliverable|mail delivery (failed|subsystem))\b", re.I)


# ── the tag ───────────────────────────────────────────────────────────────────

def ensure_code(db: Session, st: EvoSysOutreachState) -> str:
    if not st.reply_code:
        for _ in range(5):
            code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(10))
            if not db.query(EvoSysOutreachState.opportunity_id).filter(
                    EvoSysOutreachState.reply_code == code).first():
                st.reply_code = code
                db.flush()
                break
    return st.reply_code


def tagged_address(reply_to: str, code: Optional[str]) -> str:
    """info@evosyspro.live + abc -> info+evs-abc@evosyspro.live."""
    reply_to = (reply_to or "").strip()
    if not code or "@" not in reply_to:
        return reply_to
    local, domain = reply_to.split("@", 1)
    local = local.split("+", 1)[0]
    return "%s+evs-%s@%s" % (local, code, domain)


def code_in(recipients: List[str]) -> Optional[str]:
    for a in recipients or []:
        m = TAG_RE.match((a or "").strip())
        if m:
            return m.group(1).lower()
    return None


# ── auto-replies ──────────────────────────────────────────────────────────────

def is_auto_reply(subject: Optional[str], headers: Optional[List[dict]]) -> Optional[str]:
    """Why this is an automatic reply, or None. Headers win; the subject is the
    fallback when the headers could not be read."""
    for h in headers or []:
        name = str((h or {}).get("name") or "").strip().lower()
        val = str((h or {}).get("value") or "").strip().lower()
        if name == "auto-submitted" and val and val != "no":
            return "Auto-Submitted: %s" % val
        if name in ("x-autoreply", "x-autorespond", "x-autoresponse"):
            return "%s header" % name
        if name == "precedence" and val in ("auto_reply", "bulk", "junk", "list"):
            return "Precedence: %s" % val
        if name == "x-auto-response-suppress":
            continue
    if subject and AUTO_SUBJECT.search(subject):
        return "automatic-reply subject"
    return None


# ── matching ──────────────────────────────────────────────────────────────────

def match(db: Session, recipients: List[str], sender: str,
          allow_sender: bool = True) -> Tuple[Optional[Opportunity], str, str]:
    """(lead or None, method, detail). method: reply_address | sender |
    ambiguous | unknown_tag | none."""
    code = code_in(recipients)
    if code:
        st = db.query(EvoSysOutreachState).filter(EvoSysOutreachState.reply_code == code).first()
        if st is None:
            return None, "unknown_tag", "Reply tag evs-%s matches no EvoSys Sales lead." % code
        opp = db.get(Opportunity, st.opportunity_id)
        return opp, "reply_address", "Matched by the lead's reply address (evs-%s)." % code
    if not allow_sender or not sender:
        return None, "none", ""
    emailed = (db.query(EvoSysOutreachMessage.opportunity_id)
               .filter(EvoSysOutreachMessage.direction == "out",
                       EvoSysOutreachMessage.channel == "email",
                       EvoSysOutreachMessage.status == "sent",
                       func.lower(EvoSysOutreachMessage.to_address) == sender.strip().lower())
               .distinct().all())
    ids = [r[0] for r in emailed]
    if len(ids) == 1:
        return db.get(Opportunity, ids[0]), "sender", "Matched by sender: the only EvoSys Sales lead emailed at this address."
    if len(ids) > 1:
        return None, "ambiguous", "%d EvoSys Sales leads were emailed at %s; attached to none." % (len(ids), sender)
    return None, "none", ""


def already_recorded(db: Session, opp_id: str, internet_message_id: Optional[str]) -> bool:
    if not internet_message_id:
        return False
    return db.query(EvoSysOutreachMessage.id).filter(
        EvoSysOutreachMessage.opportunity_id == opp_id,
        EvoSysOutreachMessage.direction == "in",
        EvoSysOutreachMessage.provider_message_id == internet_message_id).first() is not None


def record(db: Session, opp: Opportunity, *, subject: Optional[str], body: str, received_at: datetime,
           internet_message_id: Optional[str], method: str, sender: str) -> Tuple[EvoSysOutreachMessage, bool]:
    """Store the reply on the lead and stop their follow-up. Returns (row, created)."""
    if internet_message_id:
        prior = db.query(EvoSysOutreachMessage).filter(
            EvoSysOutreachMessage.opportunity_id == opp.id, EvoSysOutreachMessage.direction == "in",
            EvoSysOutreachMessage.provider_message_id == internet_message_id).first()
        if prior is not None:
            return prior, False
    from app.services.evosys_sales import outreach as O
    m = EvoSysOutreachMessage(brand_sales_org_id=opp.brand_sales_org_id, opportunity_id=opp.id,
                              direction="in", channel="email", to_address=sender, subject=(subject or "")[:300],
                              body=(body or "(Replied with no text.)")[:5000], status="received",
                              provider_message_id=internet_message_id, match_method=method,
                              created_at=received_at)
    db.add(m)
    O.stop(db, opp.id, "replied", received_at)
    db.add(OpportunityEvent(opportunity_id=opp.id, event_type="outreach_reply",
                            summary="Replied by email - automatic follow-up stopped",
                            detail=(body or "")[:2000], occurred_at=received_at))
    db.flush()
    return m, True


def handle(db: Session, msg: dict, *, sender: str, received_at: datetime, body: str,
           allow_sender: bool, headers_for: Optional[Callable[[], Optional[List[dict]]]] = None
           ) -> Optional[Tuple[str, str, Optional[str]]]:
    """Process one mailbox message. Returns (outcome, detail, lead_id) when it
    belongs to EvoSys Sales, else None (the mailbox's own routing carries on)."""
    from app.services.inbound_mailbox_service import recipients_of
    opp, method, detail = match(db, recipients_of(msg), sender, allow_sender=allow_sender)
    if method in ("none",):
        return None
    if opp is None:
        return ("evs_ambiguous" if method == "ambiguous" else "evs_unknown_tag"), detail, None
    headers = None
    if headers_for is not None:
        try:
            headers = headers_for()
        except Exception:  # noqa: BLE001 - the subject check still runs
            log.exception("reading headers for an EvoSys Sales reply failed")
    why = is_auto_reply(msg.get("subject"), headers)
    if why:
        return "evs_auto_reply", "Automatic reply ignored (%s). %s" % (why, detail), opp.id
    _, created = record(db, opp, subject=msg.get("subject"), body=body, received_at=received_at,
                        internet_message_id=msg.get("internetMessageId"), method=method, sender=sender)
    return ("evs_reply" if created else "evs_duplicate"), (detail if created else "Already on the lead. " + detail), opp.id
