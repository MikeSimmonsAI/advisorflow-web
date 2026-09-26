"""Promotion: a discovered opportunity becomes an EXISTING Wholesale deal.

Human only (the route requires a signed-in user; there is no automation path
to this function). Idempotent: promoting twice returns the same deal.

What it does, all through existing Wholesale services:
    wholesale_service.create_property   -> WholesaleProperty + WholesaleDeal
    wholesale_service.attach_seller     -> the SAME Lead EvoSense worked (no
                                           duplicate person, no second seat)
    seller profile fields               <- SELLER STATED facts, with source
    wholesale_service.log_event         -> "evosense.promoted" carrying the
                                           discovery history, scores and spend

The EvoSense record is kept (never deleted) and points at the deal, so
"which strategy found this, what did it cost, what did the seller say" stays
answerable after the deal closes.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException
from sqlalchemy import func

from app.models.evosense_models import (EvoSenseCostEntry, EvoSenseEngagement, EvoSenseEvent,
                                        EvoSenseFact, EvoSenseHandoff, EvoSensePerson)
from app.models.wholesale_models import VALUE_IMPORTED, VALUE_MANUAL
from app.services import wholesale_service as WS
from app.services.evosense import common as C
from app.services.evosense import contacts as CT
from app.services.evosense import signals as SIG


def promote(db, org_id: str, prop, user, *, note: str = None) -> Dict[str, Any]:
    if user is None:
        raise HTTPException(status_code=403, detail="Only a person can promote an opportunity.")
    if prop.promoted_deal_id:
        return {"already": True, "deal_id": prop.promoted_deal_id,
                "property_id": prop.promoted_property_id}
    if prop.identity_status == "review":
        raise HTTPException(status_code=409, detail="Confirm the property identity first.")
    owner = CT.primary_owner(db, prop)
    from app.services.evosense import evaluate as EV
    from app.services.evosense import valuation as VAL
    stacked = [s for s in EV.stacked_signals(db, prop) if s["freshness"] != SIG.STALE]
    # An appraisal district TAX value never becomes the deal's "estimated
    # value" (and so can never drift into an ARV). It travels as a labelled
    # note; only a market estimate fills the value field.
    vals = VAL.view(prop)
    market = vals["market_value"]
    appraisal = vals["appraisal"]
    tax_note = ("%s: $%s — a property-tax value, not a market value and not an ARV. "
                % (appraisal["label"], format(appraisal["value"], ","))) if appraisal else ""
    data = {
        "street_address": prop.street_address, "unit": prop.unit, "city": prop.city,
        "state": prop.state, "zip_code": prop.zip_code, "county": prop.county,
        "parcel_apn": prop.parcel_apn, "property_type": prop.property_type,
        "bedrooms": prop.bedrooms, "bathrooms": prop.bathrooms, "square_feet": prop.square_feet,
        "year_built": prop.year_built, "estimated_value": market,
        "estimated_value_source": VALUE_IMPORTED if market is not None else None,
        "mortgage_balance": prop.mortgage_balance,
        "mortgage_source": VALUE_IMPORTED if prop.mortgage_balance is not None else None,
        "ownership_type": owner.owner_type if owner else None,
        "owner_name": owner.display_name if owner else None,
        "owner_mailing_street": getattr(owner, "mailing_street", None),
        "owner_mailing_city": getattr(owner, "mailing_city", None),
        "owner_mailing_state": getattr(owner, "mailing_state", None),
        "owner_mailing_zip": getattr(owner, "mailing_zip", None),
        "occupancy_status": prop.occupancy,
        "acquisition_source": "evosense",
        "source_detail": "EvoSense discovery %s" % prop.id,
        "notes": "Found by EvoSense. Signals: %s. %s%s" % (
            ", ".join(s["label"] for s in stacked) or "none", tax_note,
            "SANDBOX DATA — not live." if prop.is_test else ""),
        "is_test": bool(prop.is_test),
        "test_note": "EvoSense sandbox promotion" if prop.is_test else None,
    }
    wprop = WS.create_property(db, org_id, user, data)
    deal = WS.deal_for_property(db, org_id, wprop.id)

    eng = (db.query(EvoSenseEngagement)
           .filter(EvoSenseEngagement.organization_id == org_id,
                   EvoSenseEngagement.property_id == prop.id)
           .order_by(EvoSenseEngagement.updated_at.desc()).first())
    person = None
    if eng is not None and eng.person_id:
        person = db.query(EvoSensePerson).filter(EvoSensePerson.id == eng.person_id).first()
    facts = {f.fact_type: f for f in (db.query(EvoSenseFact)
                                      .filter(EvoSenseFact.organization_id == org_id,
                                              EvoSenseFact.property_id == prop.id,
                                              EvoSenseFact.superseded.is_(False)).all())}
    seller = {}
    if person is not None and person.lead_id:
        seller["lead_id"] = person.lead_id
        seller["owner_status"] = {"owner": "owner_of_record", "co_owner": "owner_of_record",
                                  "heir": "heir", "executor": "heir"}.get(person.role, "unknown")
        if "willing_to_sell" in facts:
            seller["considering_selling"] = True
        if "asking_price" in facts:
            seller["asking_price"] = float(facts["asking_price"].value)
        if "wants_quick_close" in facts:
            seller["timeline"] = "asap"
        if "condition" in facts:
            seller["property_condition"] = facts["condition"].value
        if "occupancy" in facts:
            seller["occupancy"] = facts["occupancy"].value
        if "estate_context" in facts:
            seller["reason_for_selling"] = "Inherited property"
            seller["motivation"] = facts["estate_context"].quote
        if "decision_makers" in facts:
            seller["decision_makers"] = facts["decision_makers"].value
        seller["relationship_note"] = "Worked by EvoSense before promotion"
        profile = WS.attach_seller(db, org_id, user, wprop, seller)
        if "asking_price" in facts:
            # The value came from the seller's own words, which is a person's
            # statement — MANUAL in the Wholesale vocabulary, with the quote.
            profile.asking_price_source = VALUE_MANUAL
        if eng is not None and eng.status in ("responded", "handed_off"):
            WS._set_stage_unchecked(db, deal, "seller_engaged", actor_type="user",
                                    actor_user_id=user.id)

    spend = int(db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0))
                .filter(EvoSenseCostEntry.organization_id == org_id,
                        EvoSenseCostEntry.property_id == prop.id,
                        EvoSenseCostEntry.status == "charged").scalar() or 0)
    history = [{"at": e.created_at.isoformat() + "Z" if e.created_at else None, "action": e.action,
                "actor": e.actor_type, "summary": e.summary}
               for e in (db.query(EvoSenseEvent)
                         .filter(EvoSenseEvent.organization_id == org_id,
                                 EvoSenseEvent.property_id == prop.id)
                         .order_by(EvoSenseEvent.created_at.asc()).all())]
    WS.log_event(db, org_id, "evosense.promoted", actor_type="user", actor_user_id=user.id,
                 deal_id=deal.id, property_id=wprop.id,
                 summary="Promoted from EvoSense discovery (%s)" % (prop.street_address or prop.id),
                 details={"evosense_property_id": prop.id, "strategy_id": prop.best_strategy_id,
                          "property_opportunity": prop.opportunity_score,
                          "contact_confidence": prop.contact_confidence,
                          "seller_intent": prop.seller_intent,
                          "acquisition_cost_cents": spend,
                          "signals": [s["signal_type"] for s in stacked],
                          "seller_facts": {k: {"value": f.value, "quote": f.quote}
                                           for k, f in facts.items()},
                          "history": history, "note": note, "sandbox": bool(prop.is_test)})
    prop.promoted_property_id = wprop.id
    prop.promoted_deal_id = deal.id
    prop.promoted_at = C.now()
    if eng is not None:
        eng.status = "promoted"
    for h in (db.query(EvoSenseHandoff)
              .filter(EvoSenseHandoff.organization_id == org_id, EvoSenseHandoff.property_id == prop.id,
                      EvoSenseHandoff.status.in_(("open", "acknowledged"))).all()):
        h.status = "promoted"
        h.resolved_by_id = user.id
        h.resolved_at = C.now()
    C.log_event(db, org_id, "promoted", property_id=prop.id, user=user, actor_type=C.ACTOR_USER,
                is_test=prop.is_test, summary="Promoted into Wholesale deal",
                details={"deal_id": deal.id, "wholesale_property_id": wprop.id})
    EV.refresh_status(db, prop)
    return {"already": False, "deal_id": deal.id, "property_id": wprop.id,
            "acquisition_cost_cents": spend}
