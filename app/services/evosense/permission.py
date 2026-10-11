"""Permission links: how a caller gets an owner's WRITTEN permission for
follow-up, so the AI may take over the conversation.

    caller (VA) on the phone -> "may Mike's office follow up by text?"
      -> creates a personal link for that property + phone number
      -> sends it (email, or one text from her own phone)
    owner opens the link -> reads the exact wording -> ticks the box ITSELF
      -> wholesale_sms.record_consent (the one consent writer): wording,
         server time, IP, browser, property
    from then on the seller-SMS gate finds a consent of record for that
    number, and only that number.

Nothing here can create permission without the owner's own tick. A link that
is never opened, declined or expired leaves the number exactly as it was:
not permitted.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.models.evosense_models import EvoSensePermissionLink, EvoSenseProperty

LINK_DAYS = 14
DISCLOSURE_VERSION = "permission-link-v1"
FORM_ID = "evosense_permission_link"
CONSENT_METHOD = "personal_link_checkbox"
STATUSES = ("sent", "confirmed", "declined", "expired")


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def last4(e164: Optional[str]) -> str:
    return (e164 or "")[-4:]


def disclosure(business: str, street: Optional[str], e164: str) -> str:
    """The exact wording the owner agrees to. Stored verbatim with the consent."""
    about = " about selling my property at %s" % street if street else " about selling my property"
    return ("By checking this box, I agree that %s may contact me%s by text message and phone call "
            "at the number ending in %s, including messages and calls made with automated technology "
            "and AI assistants. My consent is not a condition of any purchase or sale. Message "
            "frequency varies. Message and data rates may apply. Reply STOP to opt out of texts at any "
            "time, or HELP for help. I can ask to stop calls at any time."
            % (business, about, last4(e164)))


def business_name(db, org_id: str) -> str:
    """The name owners see: Wholesale Settings' seller-facing name, else the
    organization's own name."""
    from app.services import wholesale_sms as WS
    s = WS.settings_row(db, org_id)
    if s is not None and (getattr(s, "public_business_name", None) or "").strip():
        return s.public_business_name.strip()
    from app.models.models import Organization
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return (getattr(org, "name", None) or "our office").strip()


def create(db, org_id: str, prop: EvoSenseProperty, phone: str, *, owner_name: Optional[str] = None,
           user_id: Optional[str] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """A new link for (property, number). Earlier unanswered links for the same
    property and number are expired, so only the newest one works."""
    from app.services import wholesale_sms as WS
    e164 = WS.normalize_e164(phone)
    if not e164:
        raise ValueError("Enter a 10-digit US mobile number.")
    now = now or datetime.utcnow()
    for old in (db.query(EvoSensePermissionLink)
                .filter(EvoSensePermissionLink.organization_id == org_id,
                        EvoSensePermissionLink.property_id == prop.id,
                        EvoSensePermissionLink.phone_normalized == e164,
                        EvoSensePermissionLink.status == "sent").all()):
        old.status = "expired"
    token = secrets.token_urlsafe(24)
    link = EvoSensePermissionLink(
        organization_id=org_id, property_id=prop.id, token_hash=token_hash(token),
        phone_raw=(phone or "")[:40], phone_normalized=e164,
        owner_name=(owner_name or "").strip()[:120] or None,
        street=(prop.street_address or "").strip()[:200] or None,
        business_name=business_name(db, org_id)[:200],
        status="sent", created_by_user_id=user_id, created_at=now,
        expires_at=now + timedelta(days=LINK_DAYS))
    db.add(link)
    db.flush()
    return {"link": link, "token": token}


def find(db, token: str, *, now: Optional[datetime] = None) -> Optional[EvoSensePermissionLink]:
    link = (db.query(EvoSensePermissionLink)
            .filter(EvoSensePermissionLink.token_hash == token_hash(token)).first())
    if link is not None and link.status == "sent" and (now or datetime.utcnow()) > link.expires_at:
        link.status = "expired"
    return link


def confirm(db, link: EvoSensePermissionLink, *, ip: Optional[str], user_agent: Optional[str],
            page_url: str, now: Optional[datetime] = None):
    """The owner ticked the box. Writes the consent of record."""
    from app.services import wholesale_sms as WS
    if link.status != "sent":
        raise ValueError("This link is no longer active.")
    text = disclosure(link.business_name or "our office", link.street, link.phone_normalized)
    rec = WS.record_consent(
        db, link.organization_id, phone_raw=link.phone_normalized, disclosure_text=text,
        disclosure_version=DISCLOSURE_VERSION, form_version="1", source_url=page_url[:500],
        ip=ip, user_agent=user_agent, property_id=link.property_id,
        form_id=FORM_ID, consent_method=CONSENT_METHOD)
    link.status = "confirmed"
    link.answered_at = now or datetime.utcnow()
    link.consent_record_id = rec.id
    db.flush()
    return rec


def decline(db, link: EvoSensePermissionLink, *, now: Optional[datetime] = None) -> None:
    if link.status == "sent":
        link.status = "declined"
        link.answered_at = now or datetime.utcnow()


def link_json(link: EvoSensePermissionLink) -> Dict[str, Any]:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {"id": link.id, "property_id": link.property_id, "status": link.status,
            "phone_last4": last4(link.phone_normalized), "owner_name": link.owner_name,
            "street": link.street, "created_at": iso(link.created_at), "expires_at": iso(link.expires_at),
            "viewed_at": iso(link.viewed_at), "answered_at": iso(link.answered_at)}


def status_for(db, org_id: str, prop: EvoSenseProperty, phones: List[str]) -> Dict[str, Any]:
    """One answer for the property page: on_file | opted_out | link_sent | not_yet."""
    from app.services import wholesale_sms as WS
    numbers = []
    for p in phones:
        e = WS.normalize_e164(p)
        if e and e not in numbers:
            numbers.append(e)
    links = (db.query(EvoSensePermissionLink)
             .filter(EvoSensePermissionLink.organization_id == org_id,
                     EvoSensePermissionLink.property_id == prop.id)
             .order_by(EvoSensePermissionLink.created_at.desc()).all())
    for link in links:
        if link.phone_normalized not in numbers:
            numbers.append(link.phone_normalized)
    per = []
    for e in numbers:
        rec = WS.latest_consent(db, org_id, e)
        if rec is not None and (rec.status != "opted_in" or rec.opted_out_at):
            state = "opted_out"
        elif rec is not None:
            state = "on_file"
        elif any(l.phone_normalized == e and l.status == "sent" for l in links):
            state = "link_sent"
        else:
            state = "not_yet"
        per.append({"phone_last4": last4(e), "state": state,
                    "consented_at": rec.consented_at.isoformat() + "Z" if rec is not None and rec.consented_at else None})
    order = ("on_file", "link_sent", "opted_out", "not_yet")
    overall = next((s for s in order if any(x["state"] == s for x in per)), "not_yet")
    return {"state": overall, "phones": per, "links": [link_json(l) for l in links[:10]]}


def pending(db, org_id: str, limit: int = 200) -> List[Dict[str, Any]]:
    rows = (db.query(EvoSensePermissionLink)
            .filter(EvoSensePermissionLink.organization_id == org_id,
                    EvoSensePermissionLink.status == "sent")
            .order_by(EvoSensePermissionLink.created_at.asc()).limit(limit).all())
    now = datetime.utcnow()
    out = []
    for r in rows:
        if now > r.expires_at:
            r.status = "expired"
            continue
        out.append(link_json(r))
    return out


def message_text(business: str, owner_name: Optional[str], url: str) -> Dict[str, str]:
    """Ready-to-send wording for the caller (email or her own phone)."""
    hi = "Hi %s," % owner_name.split()[0].title() if owner_name else "Hi,"
    body = ("%s thanks for speaking with us today. As promised, here is the link to confirm it's okay "
            "for %s to follow up with you by text and phone about your property. It takes 10 seconds: "
            "%s" % (hi, business, url))
    return {"text": body, "email_subject": "Confirm follow-up about your property",
            "email_body": body + "\n\nIf you'd rather we didn't, you can choose \"No thanks\" on the same page."}
