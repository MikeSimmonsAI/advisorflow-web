"""An investor's own buy box, from the public Wholesale site, into ONE org.

    wholesale.evosyspro.live/buyers.html -> intake.php (the site's server)
        -> POST /site-intake/wholesale/{intake_key}/buyer-inquiry

The same key and the same rules as the seller form (wholesale_seller_intake):
the organization comes ONLY from the key the site's server holds; the browser
never sees it. Any organization field in the payload is ignored.

WHAT IT CREATES - THE CANONICAL BUYER RECORDS
---------------------------------------------
A `WholesaleBuyer` (source "website") and one `WholesaleBuyBox` with what the
investor actually chose: their markets as typed, the strategy, the budget as a
price range. The buyer is linked to the shared contact database as a PARTNER
(never a Lead), exactly like a buyer added by hand.

NO UNCONTROLLED DUPLICATES
--------------------------
  * the same `submission_id` twice returns the first result and writes nothing;
  * an existing buyer in this org with the same email or the same phone is
    NOT changed by a public form - anyone can type someone else's email. The
    form's criteria are kept as an INACTIVE buy box on that buyer, labelled
    unverified, with a dated note, for a person to confirm with the buyer.
    Nothing on the buyer (email, phone, name, do-not-contact, active) is
    touched, and an inactive box never matches a deal.

PERMISSION
----------
The form's "you may contact me about this inquiry" is required and recorded.
It is NOT SMS marketing consent: the buyer's preferred channel is email and
nothing here sends anything. Staff get an in-app notification only.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Organization
from app.models.wholesale_models import (ACTOR_API, WholesaleBuyBox, WholesaleBuyer,
                                         WholesaleEvent)
from app.services import wholesale_service as svc

log = logging.getLogger(__name__)

ACTOR_LABEL = "Public buyer form"
EVENT_RECEIVED = "buyer_inquiry.received"

# What the site offers -> the buy box vocabulary (wholesale_buyers_router.STRATEGIES).
# "Multiple Strategies" names no restriction, so the box carries none.
STRATEGIES: Dict[str, Tuple[List[str], List[str]]] = {
    "Fix & Flip": (["flip"], []),
    "Buy & Hold": (["rental"], []),
    "Land": (["land"], ["land"]),
    "Multifamily": (["rental"], ["multi_family"]),
    "Multiple Strategies": ([], []),
}
# Budget answer -> (min_price, max_price). "Flexible" names no range.
BUDGETS: Dict[str, Tuple[Optional[int], Optional[int]]] = {
    "Under $100,000": (None, 100000),
    "$100,000–$250,000": (100000, 250000),
    "$250,000–$500,000": (250000, 500000),
    "$500,000–$1 million": (500000, 1000000),
    "Over $1 million": (1000000, None),
    "Flexible / Deal Dependent": (None, None),
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _clean(v: Any, n: int) -> Optional[str]:
    if v is None:
        return None
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(v))
    v = re.sub(r"[ \t]+", " ", v).strip()
    return v[:n] or None


def _budget_key(v: Optional[str]) -> Optional[str]:
    """Hyphen or en dash, either is the same answer."""
    if not v:
        return None
    norm = v.replace("-", "–").replace("—", "–")
    for k in BUDGETS:
        if k.replace("-", "–") == norm:
            return k
    return None


def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() in ("yes", "true", "1", "on")


def phone_digits(v: Optional[str]) -> Optional[str]:
    d = re.sub(r"\D", "", v or "")
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else None


def markets_list(text: Optional[str]) -> List[str]:
    """'Dallas, Tarrant County; Houston' -> ['Dallas', 'Tarrant County', 'Houston'].
    Kept as typed; the matcher normalizes spelling and never invents geography."""
    out: List[str] = []
    for part in re.split(r"[,;\n/]+", text or ""):
        p = part.strip(" .")
        if p and p.lower() not in (x.lower() for x in out):
            out.append(p[:80])
    return out[:20]


def validate(payload: Dict[str, Any]) -> Dict[str, Any]:
    d = {
        "full_name": _clean(payload.get("full_name"), 120),
        "company": _clean(payload.get("company"), 120),
        "email": (_clean(payload.get("email"), 160) or "").lower() or None,
        "phone": _clean(payload.get("phone"), 35),
        "markets": _clean(payload.get("markets"), 250),
        "strategy": _clean(payload.get("strategy"), 60),
        "budget": _budget_key(_clean(payload.get("budget"), 70)),
        "notes": _clean(payload.get("notes"), 1200),
        "contact_permission": _truthy(payload.get("contact_permission")),
        "submission_id": re.sub(r"[^A-Za-z0-9_-]", "", str(payload.get("submission_id") or ""))[:64] or None,
        "source_url": _clean(payload.get("source_url"), 300),
        "form_version": _clean(payload.get("form_version"), 40),
    }
    errors: Dict[str, str] = {}
    if not d["full_name"] or len(d["full_name"]) < 2:
        errors["full_name"] = "Please enter your name."
    if not d["email"] or not _EMAIL_RE.match(d["email"]):
        errors["email"] = "Please enter a valid email address."
    if not phone_digits(d["phone"]):
        errors["phone"] = "Please enter a 10-digit phone number."
    if not d["markets"] or len(d["markets"]) < 2:
        errors["markets"] = "Please enter your target markets."
    if d["strategy"] not in STRATEGIES:
        errors["strategy"] = "Please select a buying strategy."
    if not d["budget"]:
        errors["budget"] = "Please select a budget range."
    if not d["contact_permission"]:
        errors["contact_permission"] = "Please confirm you agree to be contacted about this inquiry."
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    return d


def _prior_submission(db: Session, org_id: str, submission_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not submission_id:
        return None
    for ev in (db.query(WholesaleEvent)
               .filter(WholesaleEvent.organization_id == org_id,
                       WholesaleEvent.action == EVENT_RECEIVED,
                       WholesaleEvent.details.contains(submission_id))
               .order_by(WholesaleEvent.created_at.desc()).limit(5).all()):
        try:
            det = json.loads(ev.details) if ev.details else {}
        except (TypeError, ValueError):
            det = {}
        if det.get("submission_id") == submission_id:
            return det
    return None


def existing_buyer(db: Session, org_id: str, email: Optional[str], phone: Optional[str]) -> Optional[WholesaleBuyer]:
    """Same email (any case) or same 10 phone digits, in this org, not a test record."""
    base = db.query(WholesaleBuyer).filter(WholesaleBuyer.organization_id == org_id,
                                           WholesaleBuyer.is_test.isnot(True))
    if email:
        hit = base.filter(func.lower(WholesaleBuyer.email) == email.lower()).first()
        if hit is not None:
            return hit
    digits = phone_digits(phone)
    if digits:
        for b in base.filter(WholesaleBuyer.phone.isnot(None)).all():
            if phone_digits(b.phone) == digits:
                return b
    return None


def _reference() -> str:
    import secrets
    return "BUY-%s-%s" % (datetime.utcnow().strftime("%Y%m%d"), secrets.token_hex(4).upper())


def _note_line(d: Dict[str, Any], reference: str) -> str:
    bits = ["Website buyer form %s (%s)" % (datetime.utcnow().strftime("%Y-%m-%d"), reference),
            "markets: %s" % d["markets"], "strategy: %s" % d["strategy"], "budget: %s" % d["budget"]]
    if d.get("company"):
        bits.append("company: %s" % d["company"])
    line = "; ".join(bits)
    if d.get("notes"):
        line += "\nTheir criteria: %s" % d["notes"]
    return line + "\nContact permission: yes (about this inquiry). Not SMS marketing consent."


def _append(existing: Optional[str], line: str, limit: int = 8000) -> str:
    if existing and len(existing) >= limit:
        return existing
    return ((existing.rstrip() + "\n\n") if existing else "") + line


def submit(db: Session, org: Organization, d: Dict[str, Any]) -> Dict[str, Any]:
    """Create or reuse the buyer, add the buy box, record, notify. Commits."""
    from app.models.models import NotificationType
    from app.services import wholesale_buyer_contacts as BC
    from app.services import wholesale_notify as WN

    org_id = str(org.id)
    prior = _prior_submission(db, org_id, d.get("submission_id"))
    if prior:
        return {"reference": prior.get("reference"), "action": "duplicate",
                "buyer_id": prior.get("buyer_id")}

    from app.services import wholesale_sms as ws
    reference = _reference()
    note = _note_line(d, reference)
    buyer = existing_buyer(db, org_id, d["email"], d["phone"])
    action = "matched_existing" if buyer is not None else "created"
    if buyer is None:
        buyer = WholesaleBuyer(
            organization_id=org_id,
            entity_type="company" if d.get("company") else "person",
            company_name=d.get("company"), contact_name=d["full_name"],
            phone=ws.normalize_e164(d["phone"]) or d["phone"], email=d["email"],
            preferred_channel="email",
            source="website", source_detail="Public buyer form %s" % reference,
            notes=note, is_test=False)
        db.add(buyer)
    else:
        # A public form cannot prove who sent it: the buyer's own fields stay
        # exactly as a person entered them; only a dated, unverified note.
        buyer.notes = _append(buyer.notes, "UNVERIFIED - a website form used this buyer's %s "
                              "(name given: %s). Confirm with the buyer before using the criteria.\n%s"
                              % ("email" if (buyer.email or "").lower() == d["email"] else "phone",
                                 d["full_name"], note))
    db.flush()

    strategies, types = STRATEGIES[d["strategy"]]
    lo, hi = BUDGETS[d["budget"]]
    box = WholesaleBuyBox(
        organization_id=org_id, buyer_id=buyer.id,
        label="Website %s%s" % (datetime.utcnow().strftime("%Y-%m-%d"),
                                " (unverified - confirm with buyer)" if action != "created" else ""),
        is_active=(action == "created"),
        markets=json.dumps(markets_list(d["markets"])),
        strategies=json.dumps(strategies) if strategies else None,
        property_types=json.dumps(types) if types else None,
        min_price=lo, max_price=hi, notes=d.get("notes"))
    db.add(box)
    db.flush()

    svc.log_event(
        db, org_id, EVENT_RECEIVED, actor_type=ACTOR_API, actor_label=ACTOR_LABEL,
        summary="Buyer inquiry received (%s): %s" % (action, d.get("company") or d["full_name"]),
        details={"submission_id": d.get("submission_id"), "reference": reference,
                 "buyer_id": buyer.id, "buy_box_id": box.id, "action": action,
                 "source_url": d.get("source_url"), "form_version": d.get("form_version"),
                 "contact_permission": True, "sms_marketing_consent": False})
    WN.notify(db, org_id, kind=NotificationType.WHOLESALE_INQUIRY,
              message=("New buyer from the website: %s - %s, %s. Ref %s" % (
                  d.get("company") or d["full_name"], d["strategy"], d["budget"], reference))
              if action == "created" else
              ("Website buyer form used an existing buyer's contact details (%s). Criteria saved "
               "as an inactive box - confirm with the buyer. Ref %s" % (
                   buyer.company_name or buyer.contact_name or "buyer", reference)),
              link="/wholesale/buyers")
    db.commit()
    buyer_id = buyer.id
    if action == "created":
        BC.link_buyer(db, buyer, None)       # never fails the write; commits
    return {"reference": reference, "action": action, "buyer_id": buyer_id}
