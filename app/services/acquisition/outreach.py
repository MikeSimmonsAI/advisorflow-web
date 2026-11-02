"""P3 - outreach email and reply handling for the managed acquisition pilot.

ONE SEND PATH: the platform's own Microsoft 365 mailbox (support@evosyspro.live)
through Microsoft Graph, via inbound_mailbox_service.send_mail. evosyspro.live's
SPF allows Microsoft 365 only, so any other provider would fail authentication.
Replies come back through the same mailbox poller that already runs every five
minutes; `on_reply` is called from there for leads that belong to a pilot.

EVERY SEND IS A HUMAN DECISION. There is no batch sender and no cron: an
operator presses Send on one prospect, and every gate below is checked at that
moment:
  * ACQUISITION_OUTREACH_ENABLED=1 on the server (off by default);
  * practice prospects may only be emailed at an address on
    ACQUISITION_TEST_RECIPIENTS - the owner's own inboxes - so a test can never
    reach a real business;
  * real prospects additionally need: the pilot not paused, a verified contact
    email recorded as evidence with a source, the client's mailing address on
    file (required in every commercial email), the stage at Contact verified /
    Outreach ready, and the daily cap (ACQUISITION_DAILY_SEND_CAP, default 10);
  * nobody on the client's do-not-contact list, nobody who opted out, never the
    same prospect twice in a row without a reply.
The first successful send to a REAL prospect starts the 30-day pilot clock.

OPT-OUT: every email says "reply STOP"; a reply that reads as an opt-out
(harvey_adapted.opt_out_reason) puts the address on the do-not-contact list,
marks the lead dnc and closes the prospect as Not a fit. That list is checked
before every send.
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionEvidence, AcquisitionOpportunity, AcquisitionProfile
from app.models.models import EmailMessage, Lead, Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import harvey_adapted as H

SEND_FROM = "support@evosyspro.live"
SEND_SOURCE = "acquisition_outreach"
DEFAULT_DAILY_CAP = 10
SENDABLE_REAL = (C.CONTACT_VERIFIED, C.OUTREACH_READY)
# Practice prospects are the owner's own inboxes; they may be emailed from any
# early stage so the loop can be tested end to end.
SENDABLE_PRACTICE = (C.DISCOVERED, C.RESEARCHED, C.QUALIFIED, C.CONTACT_VERIFIED, C.OUTREACH_READY)


# ── settings ────────────────────────────────────────────────────────────────
def enabled() -> bool:
    return os.getenv("ACQUISITION_OUTREACH_ENABLED", "").strip() == "1"


def test_recipients() -> List[str]:
    raw = os.getenv("ACQUISITION_TEST_RECIPIENTS", "")
    return [a.strip().lower() for a in raw.replace(";", ",").split(",") if a.strip()]


def daily_cap() -> int:
    try:
        return max(0, int(os.getenv("ACQUISITION_DAILY_SEND_CAP", str(DEFAULT_DAILY_CAP))))
    except ValueError:
        return DEFAULT_DAILY_CAP


# ── the message ─────────────────────────────────────────────────────────────
def _profile(db: Session, org_id: str) -> Optional[AcquisitionProfile]:
    return db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == org_id).first()


def _facts(lead: Lead) -> Dict[str, Any]:
    f = C.jload(lead.custom_fields, {})
    return f if isinstance(f, dict) else {}


def client_name(org: Organization, p: Optional[AcquisitionProfile]) -> str:
    return ((p.business_name if p else None) or org.name or "our cleaning team").strip()


def draft(org: Organization, p: Optional[AcquisitionProfile], lead: Lead, opp: AcquisitionOpportunity,
          subject: Optional[str] = None, body: Optional[str] = None) -> Dict[str, str]:
    """The default first-touch email. Plain, short, no claims we can't back
    (no prices, no insurance claims unless the client verified them)."""
    company = _facts(lead).get("company") or "your office"
    who = client_name(org, p)
    first = (lead.first_name or "").strip()
    subj = (subject or "").strip()[:150] or ("Cleaning for %s" % company)
    if body and body.strip():
        text = body.strip()[:4000]
    else:
        text = ("Hi %s,\n\n"
                "I'm reaching out for %s, a commercial cleaning company here in Dallas-Fort Worth. "
                "We clean offices like %s on a schedule that works around your patients and staff.\n\n"
                "Would you be open to a short walkthrough so we can give you a free quote?\n\n"
                "Thank you,\n%s") % (first or "there", who, company, (p.owner_name if p and p.owner_name else who))
    return {"subject": subj, "body": text}


def footer(org: Organization, p: Optional[AcquisitionProfile]) -> str:
    addr = (p.mailing_address if p else None) or ""
    return ("%s%s\nIf you'd rather not hear from us, just reply STOP and we won't email you again."
            % (client_name(org, p), (" - " + addr) if addr else ""))


def to_html(text: str, foot: str) -> str:
    def para(t):
        return "<br>".join(html.escape(line) for line in t.split("\n"))
    blocks = "".join("<p>%s</p>" % para(b) for b in text.split("\n\n") if b.strip())
    return ('<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">%s'
            '<p style="color:#777;font-size:12px;margin-top:24px">%s</p></div>' % (blocks, para(foot)))


# ── gates ───────────────────────────────────────────────────────────────────
def _verified_email(db: Session, opp: AcquisitionOpportunity, email: str) -> bool:
    rows = (db.query(AcquisitionEvidence)
            .filter(AcquisitionEvidence.opportunity_id == opp.id, AcquisitionEvidence.kind == "contact_email",
                    AcquisitionEvidence.verified.is_(True)).all())
    return any((r.value or "").strip().lower() == email and r.source_url for r in rows)


def sent_today(db: Session, org_id: str, now: Optional[datetime] = None) -> int:
    now = now or datetime.utcnow()
    since = now - timedelta(hours=24)
    return (db.query(EmailMessage).join(Lead, Lead.id == EmailMessage.lead_id)
            .filter(Lead.organization_id == org_id, EmailMessage.send_source == SEND_SOURCE,
                    EmailMessage.status == "sent", EmailMessage.sent_at >= since).count())


def refusals(db: Session, org: Organization, opp: AcquisitionOpportunity, lead: Lead,
             now: Optional[datetime] = None) -> List[str]:
    """Every reason this prospect can't be emailed right now (empty = OK)."""
    out: List[str] = []
    p = _profile(db, org.id)
    email = (lead.email or "").strip().lower()
    if not enabled():
        out.append("Outreach is switched off on the server")
    if not email:
        out.append("No email address for this prospect")
    if lead.status == "dnc":
        out.append("This prospect asked not to be contacted")
    excl = C.jload(p.exclusions, []) if p else []
    if email and H.do_not_contact_match(email, excl):
        out.append("This address is on the do-not-contact list")
    elif opp.domain and H.do_not_contact_domain_match(opp.domain, excl):
        out.append("This business is on the do-not-contact list")
    if opp.is_practice:
        if email and email not in test_recipients():
            out.append("Practice prospects can only be emailed at the owner's test inboxes")
        if opp.stage not in SENDABLE_PRACTICE:
            out.append("Already contacted - wait for a reply")
    else:
        if p is None or p.paused:
            out.append("The pilot is paused")
        if p is None or not (p.mailing_address or "").strip():
            out.append("The client's business mailing address is missing (required in every email)")
        if opp.stage not in SENDABLE_REAL:
            out.append("The prospect must be at Contact verified or Outreach ready")
        if email and not _verified_email(db, opp, email):
            out.append("The contact email has no verified evidence with a source")
        if sent_today(db, org.id, now) >= daily_cap():
            out.append("Today's sending limit (%d) is reached" % daily_cap())
    return out


# ── send ────────────────────────────────────────────────────────────────────
def preview(db: Session, org: Organization, opp: AcquisitionOpportunity, data: Dict[str, Any]) -> Dict[str, Any]:
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == org.id).first()
    p = _profile(db, org.id)
    d = draft(org, p, lead, opp, data.get("subject"), data.get("body"))
    return {"from": SEND_FROM, "to": lead.email, "subject": d["subject"], "body": d["body"],
            "footer": footer(org, p), "refused_because": refusals(db, org, opp, lead),
            "sent_last_24h": sent_today(db, org.id), "daily_cap": daily_cap()}


def send(db: Session, org: Organization, opp: AcquisitionOpportunity, actor: User,
         data: Dict[str, Any], mailer=None) -> Dict[str, Any]:
    from app.services.acquisition import engine as E
    lead = db.query(Lead).filter(Lead.id == opp.lead_id, Lead.organization_id == org.id).first()
    if lead is None:
        raise HTTPException(404, "Prospect not found")
    why = refusals(db, org, opp, lead)
    if why:
        raise HTTPException(409, "Not sent: " + "; ".join(why))
    p = _profile(db, org.id)
    d = draft(org, p, lead, opp, data.get("subject"), data.get("body"))
    body_html = to_html(d["body"], footer(org, p))
    if mailer is None:
        from app.services import inbound_mailbox_service as _ims
        box = _ims.sending_mailbox(db, SEND_FROM)
        if box is None:
            raise HTTPException(409, "Not sent: the %s mailbox is not connected" % SEND_FROM)
        res = _ims.send_mail(db, box, to_email=lead.email, subject=d["subject"], body_html=body_html)
    else:
        res = mailer(to_email=lead.email, subject=d["subject"], body_html=body_html)
    now = datetime.utcnow()
    ok = bool(res.get("success"))
    msg = EmailMessage(lead_id=lead.id, sender_id=actor.id, sent_by_user_id=actor.id, send_source=SEND_SOURCE,
                       subject=d["subject"], body_html=body_html, to_email=lead.email,
                       from_email=res.get("from_email") or SEND_FROM, provider_message_id=res.get("provider_message_id"),
                       status="sent" if ok else "failed", error=None if ok else (res.get("error") or "")[:2000],
                       sent_at=now if ok else None)
    db.add(msg)
    if not ok:
        E._event(db, org.id, opp.id, "outreach_failed", actor.id, note=(res.get("error") or "")[:300])
        db.commit()
        raise HTTPException(502, "Not sent: %s" % (res.get("error") or "Microsoft 365 refused the send"))
    E._move(db, opp, C.CONTACTED, actor.id, "Email sent from %s: %s" % (SEND_FROM, d["subject"]))
    if lead.status in (None, "", "new"):
        lead.status = "sent"
    lead.last_contact_date = now
    started = False
    if not opp.is_practice and p is not None and p.pilot_started_at is None:
        p.pilot_started_at = now
        started = True
        E._event(db, org.id, opp.id, "pilot_started", actor.id, note="First real outreach email sent")
    db.commit()
    return {"id": msg.id, "stage": opp.stage, "pilot_started": started}


# ── replies (called by the mailbox poller) ──────────────────────────────────
def on_reply(db: Session, lead: Lead, body: str) -> Optional[str]:
    """A reply arrived for `lead`. Returns what happened (for logs), or None
    when the lead isn't a pilot prospect. Never raises into the poller."""
    opp = db.query(AcquisitionOpportunity).filter(AcquisitionOpportunity.lead_id == lead.id).first()
    if opp is None:
        return None
    from app.services.acquisition import engine as E
    text = body or ""
    opt = H.opt_out_reason(text)
    if opt:
        p = _profile(db, opp.organization_id)
        email = (lead.email or "").strip().lower()
        if p is not None and email:
            excl = C.jload(p.exclusions, [])
            if not H.do_not_contact_match(email, excl):
                excl.append(email)
                p.exclusions = C.jdump(excl)
        lead.status = "dnc"
        if opp.stage != C.REPLIED:
            E._move(db, opp, C.REPLIED, None, "Replied: " + text[:200])
        opp.disqualified_reason = "Asked not to be contacted"
        E._move(db, opp, C.DISQUALIFIED, None, "Opted out - added to the do-not-contact list")
        return "opted_out"
    note = text[:300]
    esc = H.escalation_reason(text)
    if opp.stage in (C.CONTACTED, C.OUTREACH_READY, C.CONTACT_VERIFIED):
        E._move(db, opp, C.REPLIED, None, ("NEEDS A HUMAN (%s): " % esc if esc else "Replied: ") + note)
    else:
        E._event(db, opp.organization_id, opp.id, "reply", None, note=("NEEDS A HUMAN (%s): " % esc if esc else "") + note)
    return "escalated" if esc else "replied"
