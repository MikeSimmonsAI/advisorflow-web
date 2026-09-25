"""A property owner's own inquiry, from a public seller page, into ONE org.

WHERE IT LANDS
--------------
The organization is resolved from `WholesaleSettings.public_intake_key`, a
value that lives in the website's SERVER config and in this organization's
settings - never in a browser, never an organization id, never a display name.
An unknown key, an inactive organization, or an organization without the
Wholesale module all get the same neutral refusal.

WHAT IT CREATES - THE CANONICAL WHOLESALE RECORDS, NOT A SIDE TABLE
-------------------------------------------------------------------
`wholesale_service.create_property` (property + its deal) and
`wholesale_service.attach_seller` (the seller Lead + the seller profile). The
same two calls an operator's "add property" makes, so the inquiry appears in
the operator's normal EvoSys Wholesale pipeline with every existing guard.

NO UNCONTROLLED DUPLICATES
--------------------------
  * the same `submission_id` twice (a double click, a refresh, a retry) returns
    the first result and writes nothing;
  * the same property (street + ZIP) in this org is reused, not re-created;
  * the same phone number in this org reuses that Lead, not a second person.
A repeat inquiry updates the seller profile's answers and is logged as a repeat.

SMS CONSENT IS SEPARATE AND OPTIONAL
------------------------------------
The inquiry is complete without it. Only an explicit "yes" from the unticked
checkbox writes a consent record (`wholesale_sms.record_consent`), with the
server's clock. An unticked box writes nothing and implies nothing.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Lead, Organization
from app.models.wholesale_models import (ACTOR_API, WholesaleEvent, WholesaleProperty,
                                         WholesaleSettings)
from app.services import wholesale_service as svc
from app.services import wholesale_sms as ws

log = logging.getLogger(__name__)

FEATURE = "wholesale_real_estate"
ACTOR_LABEL = "Public seller inquiry form"
EVENT_RECEIVED = "seller_inquiry.received"

CONDITIONS = ("excellent", "good", "fair", "poor", "distressed")
TIMELINES = ("asap", "30_days", "90_days", "6_months", "no_rush")
CONTACT_METHODS = ("phone", "sms", "email")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


class IntakeRefused(Exception):
    """No destination. Operator-facing reason; the public caller never sees it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def resolve_destination(db: Session, intake_key: str):
    """(organization, settings) for a configured, active Wholesale org, or raise."""
    key = (intake_key or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,64}", key):
        raise IntakeRefused("malformed intake key")
    s = (db.query(WholesaleSettings)
         .filter(WholesaleSettings.public_intake_key == key).first())
    if s is None:
        raise IntakeRefused("no organization has this intake key")
    org = db.query(Organization).filter(Organization.id == s.organization_id).first()
    if org is None:
        raise IntakeRefused("intake key names a missing organization")
    if getattr(org, "is_active", True) is False:
        raise IntakeRefused("intake organization %s is not active" % org.id)
    from app.services import entitlements
    if not entitlements.org_has_feature(org, FEATURE):
        raise IntakeRefused("intake organization %s does not have Wholesale" % org.id)
    return org, s


def _clean(v: Optional[str], n: int) -> Optional[str]:
    v = re.sub(r"\s+", " ", (v or "").strip())
    return v[:n] or None


def _street_key(street: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9 ]", "", re.sub(r"\s+", " ", (street or "").lower())).strip()


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Server-side validation. Returns cleaned data or raises 422 with field errors."""
    from app.services.contact_hours import STATE_ZONES
    errors: Dict[str, str] = {}
    d: Dict[str, Any] = {}
    d["full_name"] = _clean(payload.get("full_name"), 120)
    if not d["full_name"] or len(d["full_name"]) < 2:
        errors["full_name"] = "Please enter your full name."
    d["phone_raw"] = _clean(payload.get("phone"), 40)
    d["phone"] = ws.normalize_e164(d["phone_raw"])
    if not d["phone"]:
        errors["phone"] = "Please enter a valid 10-digit US mobile number."
    d["email"] = (_clean(payload.get("email"), 180) or "").lower() or None
    if d["email"] and not _EMAIL_RE.match(d["email"]):
        errors["email"] = "Please enter a valid email address, or leave it blank."
    d["street_address"] = _clean(payload.get("street_address"), 200)
    if not d["street_address"] or len(d["street_address"]) < 3:
        errors["street_address"] = "Please enter the property's street address."
    d["city"] = _clean(payload.get("city"), 100)
    if not d["city"]:
        errors["city"] = "Please enter the city."
    d["state"] = (_clean(payload.get("state"), 2) or "").upper() or None
    if d["state"] not in STATE_ZONES:
        errors["state"] = "Please choose the state."
    d["zip_code"] = _clean(payload.get("zip_code"), 10)
    if not d["zip_code"] or not _ZIP_RE.match(d["zip_code"]):
        errors["zip_code"] = "Please enter a 5-digit ZIP code."
    for key, allowed, msg in (("property_condition", CONDITIONS, "Please choose a condition."),
                              ("timeline", TIMELINES, "Please choose a timeline."),
                              ("preferred_contact_method", CONTACT_METHODS,
                               "Please choose how we should contact you.")):
        v = _clean(payload.get(key), 40)
        if v and v not in allowed:
            errors[key] = msg
        d[key] = v
    d["reason_for_selling"] = _clean(payload.get("reason_for_selling"), 500)
    d["notes"] = (payload.get("notes") or "").strip()[:2000] or None
    d["sms_consent"] = ws.truthy(payload.get("sms_consent"))
    d["preferred_contact_method"] = d["preferred_contact_method"] or "phone"
    if d["preferred_contact_method"] == "sms" and not d["sms_consent"]:
        errors["preferred_contact_method"] = ("To be contacted by text, check the SMS consent "
                                              "box - or choose phone or email.")
    if d["preferred_contact_method"] == "email" and not d["email"]:
        errors["email"] = "Please enter your email address, or choose another contact method."
    d["disclosure_text"] = (payload.get("disclosure_text") or "").strip()[:4000] or None
    d["disclosure_version"] = _clean(payload.get("disclosure_version"), 64)
    d["form_version"] = _clean(payload.get("form_version"), 64)
    if d["sms_consent"]:
        t = (d["disclosure_text"] or "").upper()
        if not (d["disclosure_version"] and "STOP" in t and "HELP" in t and "RATES" in t):
            # The page must send the exact wording it showed. Without it there is
            # no evidence, so the consent is refused rather than recorded bare.
            errors["sms_consent"] = "We could not record SMS consent. Please try again."
    d["source_url"] = _clean(payload.get("source_url"), 500)
    d["submission_id"] = _clean(payload.get("submission_id"), 64)
    d["ip"] = _clean(payload.get("ip"), 64)
    d["user_agent"] = _clean(payload.get("user_agent"), 400)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    return d


def _prior_submission(db: Session, org_id: str, submission_id: Optional[str]):
    if not submission_id:
        return None
    for ev in (db.query(WholesaleEvent)
               .filter(WholesaleEvent.organization_id == org_id,
                       WholesaleEvent.action == EVENT_RECEIVED,
                       WholesaleEvent.details.contains(submission_id))
               .order_by(WholesaleEvent.created_at.desc()).limit(5).all()):
        det = svc_json(ev.details)
        if det.get("submission_id") == submission_id:
            return det
    return None


def svc_json(raw) -> Dict[str, Any]:
    import json
    try:
        v = json.loads(raw) if raw else {}
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def _existing_property(db: Session, org_id: str, street: str, zip_code: str):
    zip5 = (zip_code or "")[:5]
    key = _street_key(street)
    for prop in (db.query(WholesaleProperty)
                 .filter(WholesaleProperty.organization_id == org_id,
                         WholesaleProperty.zip_code.like(zip5 + "%"),
                         WholesaleProperty.is_test.is_(False)).all()):
        if _street_key(prop.street_address) == key:
            return prop
    return None


def _existing_lead(db: Session, org_id: str, e164: str):
    forms = [e164, e164[1:], e164[2:]]
    return (db.query(Lead)
            .filter(Lead.organization_id == org_id, Lead.phone.in_(forms),
                    Lead.is_test.is_(False))
            .order_by(Lead.updated_at.desc()).first())


def _reference(deal_id: Optional[str]) -> str:
    return "SI-" + (deal_id or "").replace("-", "")[:8].upper()


def submit(db: Session, org: Organization, settings: WholesaleSettings,
           d: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update the Wholesale records for one inquiry. Commits."""
    org_id = str(org.id)
    prior = _prior_submission(db, org_id, d.get("submission_id"))
    if prior:
        return {"reference": prior.get("reference"), "action": "duplicate",
                "sms_consent_recorded": bool(prior.get("sms_consent"))}

    first, _, last = (d["full_name"] or "").partition(" ")
    host = urlparse(d.get("source_url") or "").hostname or None
    prop = _existing_property(db, org_id, d["street_address"], d["zip_code"])
    lead = _existing_lead(db, org_id, d["phone"])
    repeat = prop is not None and lead is not None

    try:
        if prop is None:
            prop = svc.create_property(db, org_id, None, {
                "street_address": d["street_address"], "city": d["city"],
                "state": d["state"], "zip_code": d["zip_code"],
                "owner_name": d["full_name"], "acquisition_source": "seller_inquiry",
                "source_detail": ("%s/sell" % host) if host else "public seller form",
            }, actor_type=ACTOR_API, actor_label=ACTOR_LABEL)
        seller_data = {
            "first_name": first, "last_name": last.strip() or None,
            "phone": d["phone"], "email": d["email"],
            "source_category": ws.SOURCE_CATEGORY,
            "preferred_contact_method": d["preferred_contact_method"],
            "property_condition": d["property_condition"], "timeline": d["timeline"],
            "reason_for_selling": d["reason_for_selling"], "considering_selling": True,
            "notes": ("Seller inquiry via %s.%s" % (
                host or "public seller form",
                (" Seller's notes: " + d["notes"]) if d.get("notes") else ""))[:2400],
        }
        if lead is not None:
            seller_data["lead_id"] = lead.id
        profile = svc.attach_seller(db, org_id, None, prop, seller_data, actor_type=ACTOR_API)
    except HTTPException as exc:
        # Plan capacity or another refusal from the canonical path. The public
        # caller gets the neutral refusal; the operator gets the reason.
        db.rollback()
        raise IntakeRefused("canonical wholesale path refused: %s %s"
                            % (exc.status_code, exc.detail))

    lead = db.query(Lead).filter(Lead.id == profile.lead_id,
                                 Lead.organization_id == org_id).first()
    if not lead.email and d["email"]:
        lead.email = d["email"]
    deal = svc.deal_for_property(db, org_id, prop.id)
    reference = _reference(getattr(deal, "id", None) or prop.id)

    consent = None
    if d["sms_consent"]:
        consent = ws.record_consent(
            db, org_id, phone_raw=d["phone_raw"], disclosure_text=d["disclosure_text"],
            disclosure_version=d["disclosure_version"], form_version=d["form_version"],
            source_url=d["source_url"], ip=d["ip"], user_agent=d["user_agent"],
            lead=lead, profile_id=profile.id, property_id=prop.id,
            deal_id=getattr(deal, "id", None))
        # The registered opt-in confirmation. Through the gate like everything
        # else: until the program is on and a Messaging Service is configured
        # it is refused, and the reason is recorded on the consent itself.
        try:
            names = ws.program_brand(db, org_id)
            body = ws.OPT_IN_CONFIRMATION.format(brand=names["brand"])
            result = ws.send_program_sms(db, lead, body,
                                         sender_user_id=ws.attribution_user_id(db, lead),
                                         send_source="wholesale_program")
            consent.confirmation_status = ("sent" if result.get("sent")
                                           else ",".join(result.get("reasons") or ["NOT_SENT"]))
        except Exception as exc:                            # noqa: BLE001
            log.exception("wholesale intake: confirmation SMS failed for consent %s", consent.id)
            consent.confirmation_status = "ERROR"

    svc.log_event(
        db, org_id, EVENT_RECEIVED, actor_type=ACTOR_API, actor_label=ACTOR_LABEL,
        property_id=prop.id, deal_id=getattr(deal, "id", None),
        summary="Seller inquiry received%s - SMS consent: %s" % (
            " (repeat)" if repeat else "", "YES" if consent else "NO"),
        details={"submission_id": d.get("submission_id"), "reference": reference,
                 "lead_id": lead.id, "seller_profile_id": profile.id,
                 "sms_consent": bool(consent), "consent_id": getattr(consent, "id", None),
                 "consented_at": consent.consented_at.isoformat() + "Z" if consent else None,
                 "consent_source": d.get("source_url") if consent else None,
                 "preferred_contact_method": d["preferred_contact_method"],
                 "source_url": d.get("source_url"), "repeat": repeat,
                 "notes": d.get("notes")})
    db.commit()
    return {"reference": reference, "action": "updated" if repeat else "created",
            "sms_consent_recorded": consent is not None}
