"""THE PURCHASE AGREEMENT FROM THE KITCHEN TABLE (build list #15).

A rep on a visit can prepare and send the seller the purchase agreement
(with the Texas Section 1101.0045 notice it already carries) for e-signing,
with three guard rails:

  * The price is the rep's own recorded offer for this visit, and only one
    that stands - inside their limit, or approved by an admin. They cannot
    type a different price here.
  * The company's signer is an ADMIN (the one who approved the offer, else
    the person who booked the visit if an admin, else the first admin of the
    workspace) - never the rep. A canvasser cannot bind the company.
  * The rep sees the document and what is still missing - not the deal's
    other numbers (assignment fee, investor figures).

Sending moves the property into Deal Operations first (the same promotion a
person does from the property page), then uses the existing contract send,
so the document, its e-sign envelope and its status live where the office
already looks for them.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.wholesale_ops_models import WholesaleFieldOffer
from app.services import wholesale_appointments as A
from app.services import wholesale_contract_docs as docs
from app.services import wholesale_team as T

KIND = "purchase_agreement"
REP_FIELDS = ("seller_name", "seller_email", "earnest_money", "option_days", "title_company", "closing_date")


def _standing_offer(db: Session, org_id: str, appt_id: str) -> Optional[WholesaleFieldOffer]:
    return (db.query(WholesaleFieldOffer)
            .filter(WholesaleFieldOffer.organization_id == org_id, WholesaleFieldOffer.appointment_id == appt_id,
                    WholesaleFieldOffer.status.in_(("within", "approved")))
            .order_by(WholesaleFieldOffer.created_at.desc()).first())


def _signer(db: Session, org_id: str, appt, offer) -> Optional[User]:
    for uid in (getattr(offer, "decided_by_id", None), appt.created_by_id):
        if uid:
            u = db.query(User).filter(User.id == uid, User.organization_id == org_id).first()
            if u is not None and getattr(u, "role", None) == "org_admin":
                return u
    return (db.query(User).filter(User.organization_id == org_id, User.role == "org_admin",
                                  User.is_active.isnot(False)).order_by(User.created_at.asc()).first())


def _evo_prop(db, org_id, appt):
    if not appt.evosense_property_id:
        return None
    from app.models.evosense_models import EvoSenseProperty
    return db.query(EvoSenseProperty).filter(EvoSenseProperty.id == appt.evosense_property_id,
                                             EvoSenseProperty.organization_id == org_id).first()


def _deal_id(db, org_id, appt, prop) -> Optional[str]:
    return appt.deal_id or getattr(prop, "promoted_deal_id", None)


def _values(db, org_id, appt, offer, signer, data, deal=None, wprop=None, prop=None) -> Dict[str, Any]:
    over = {k: data.get(k) for k in REP_FIELDS if data.get(k) not in (None, "")}
    over["price"] = offer.offer
    if signer is not None:
        over["buyer_signer"] = signer.full_name or signer.email
        over["buyer_email"] = signer.email
    org = db.query(Organization).filter(Organization.id == org_id).first()
    return docs.build_values(deal=deal, prop=wprop or prop, seller_name=None, seller_email=None,
                             org_name=getattr(org, "name", None), user=signer, overrides=over)


def handle(db: Session, org_id: str, user: User, appt_id: str, data: Dict[str, Any], request=None) -> Dict[str, Any]:
    appt = A.get_visible(db, org_id, user, appt_id)
    if T.is_field(db, user) and appt.assigned_to_id != user.id:
        raise HTTPException(404, "Appointment not found")
    offer = _standing_offer(db, org_id, appt.id)
    if offer is None:
        raise HTTPException(409, "Record an offer first (Offer tab). It has to be inside your limit or approved.")
    prop = _evo_prop(db, org_id, appt)
    if prop is None and not appt.deal_id:
        raise HTTPException(409, "This visit is not linked to a property, so there is nothing to put on a contract.")
    signer = _signer(db, org_id, appt, offer)
    if signer is None:
        raise HTTPException(409, "No admin in this workspace to sign for the company.")
    values = _values(db, org_id, appt, offer, signer, data, prop=prop)
    miss = docs.missing(KIND, values) + docs.signer_problems(KIND, values)
    base = {"html": docs.render(KIND, values), "missing": miss, "price": offer.offer,
            "company_signer": signer.full_name or signer.email, "notice": docs.starter_notice_for(db, org_id)}
    if data.get("action") != "send":
        return {**base, "sent": False}
    if miss:
        raise HTTPException(409, "Fill these in first: %s." % ", ".join(miss))
    deal_id = _deal_id(db, org_id, appt, prop)
    if deal_id is None:
        from app.services.evosense import promotion as PR
        out = PR.promote(db, org_id, prop, user, note="Seller signing at a field visit")
        deal_id = out.get("deal_id")
        db.flush()
    appt.deal_id = appt.deal_id or deal_id
    from app.routers.wholesale_contracts_router import ContractIn, contract_send
    over = {k: values.get(k) for k in docs.OVERRIDABLE if values.get(k) not in (None, "")}
    over["price"] = offer.offer                      # unformatted, the send path formats it once
    for k in ("closing_date",):
        if data.get(k):
            over[k] = data[k]
    result = contract_send(deal_id, KIND, ContractIn(values=over, message=data.get("message")),
                           request, db, user, user)
    db.commit()
    sent = bool(result.get("sent", True)) if isinstance(result, dict) else True
    return {**base, "sent": sent, "deal_id": deal_id,
            "message": (result.get("message") if isinstance(result, dict) else None)
            or ("Sent for signature. The seller gets an email to sign; %s signs for the company."
                % (signer.full_name or signer.email))}
