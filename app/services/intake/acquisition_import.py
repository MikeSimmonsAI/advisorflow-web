"""Destination: a customer's Acquisition pipeline (#470 Stage B).

Commercial businesses that might BUY cleaning from ONE cleaning customer (for
example Divine Shine) go straight into that customer's pipeline as
Discovered prospects - the same path as the owner's hand entry, so the
existing dedupe, junk-business filter, client exclusion list and geography all
still apply (`acquisition/engine.add_prospect`, shared with Issue #452; not
modified here).

  * Who: God, or an operator the owner granted for THAT customer. Re-checked
    on every call. The customer is chosen explicitly when the import starts.
  * Never an EvoSys Sales prospect (a business EvoSys sells software to, like
    the Facebook cleaning companies): a row that matches one is refused.
  * Nothing is emailed, assigned elsewhere or started. Notes and intro drafts
    become evidence notes on the prospect.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionEvent, AcquisitionEvidence, AcquisitionOpportunity
from app.models.models import Lead, Organization
from app.services.intake import business_import as BI

PIPELINE = "acquisition_import"
DESTINATION = "customer_acquisition"


def can_import(db: Session, user, org_id: str) -> bool:
    from app.services.acquisition import common as C
    from app.services.acquisition import engine as E
    org = db.query(Organization).filter(Organization.id == org_id).first() if org_id else None
    if org is None or not C.acquisition_identity(org):
        return False
    return getattr(user, "role", None) == "god_admin" or E.is_operator(db, user, org_id)


def targets(db: Session, user) -> List[Dict[str, Any]]:
    from app.services.acquisition import engine as E
    god = getattr(user, "role", None) == "god_admin"
    allowed = None if god else set(E.operator_org_ids(db, user))
    out = []
    for o in E.enabled_orgs(db):
        if allowed is not None and o.id not in allowed:
            continue
        if can_import(db, user, o.id):
            out.append({"id": o.id, "name": o.name})
    return out


def _org(db: Session, b) -> Organization:
    org = db.query(Organization).filter(Organization.id == BI.target_id(b)).first()
    if org is None:
        raise BI.BusinessImportError("Customer not found")
    return org


def find_existing(db: Session, b, n: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    from app.services.acquisition import harvey_adapted as H
    org_id = BI.target_id(b)
    key = H.dedupe_key(n.get("business_name") or "", n.get("website") or "", n.get("phone_raw") or "")
    if key:
        hit = (db.query(AcquisitionOpportunity.id)
               .filter(AcquisitionOpportunity.organization_id == org_id, AcquisitionOpportunity.dedupe_key == key)
               .first())
        if hit:
            return hit[0], "already in this customer's pipeline"
    if n.get("domain"):
        hit = (db.query(AcquisitionOpportunity.id)
               .filter(AcquisitionOpportunity.organization_id == org_id, AcquisitionOpportunity.domain == n["domain"])
               .first())
        if hit:
            return hit[0], "same website in this customer's pipeline"
    if n.get("phone10"):
        for oid, phone in (db.query(AcquisitionOpportunity.id, Lead.phone)
                           .join(Lead, Lead.id == AcquisitionOpportunity.lead_id)
                           .filter(AcquisitionOpportunity.organization_id == org_id,
                                   Lead.phone.ilike("%" + n["phone10"][-4:] + "%")).limit(200).all()):
            if BI.digits10(phone) == n["phone10"]:
                return oid, "same phone in this customer's pipeline"
    return None


def conflicts(db: Session, b, n: Dict[str, Any]) -> List[str]:
    from app.models.sales_models import Opportunity
    q = db.query(Opportunity)
    sid = n.get("source_record_id")
    if sid and q.filter(func.lower(Opportunity.source_record_id) == sid.lower()).first():
        return ["This is an EvoSys Sales prospect (a company EvoSys sells to), not a customer for a cleaning company"]
    if sid:
        for o in q.filter(Opportunity.source.ilike("%" + sid + "%")).limit(20).all():
            if BI.id_in_text(sid, o.source):
                return ["This is an EvoSys Sales prospect (a company EvoSys sells to), not a customer for a cleaning company"]
    if n.get("phone10"):
        for (phone,) in q.with_entities(Opportunity.phone).filter(
                Opportunity.phone.ilike("%" + n["phone10"][-4:] + "%")).limit(200).all():
            if BI.digits10(phone) == n["phone10"]:
                return ["Same phone as an EvoSys Sales prospect - not a customer for a cleaning company"]
    return []


def write(db: Session, b, user, n: Dict[str, Any], row) -> str:
    from app.services.acquisition import common as C
    from app.services.acquisition import engine as E
    from app.services.intake.platform_pool import segment_for
    org = _org(db, b)
    seg = segment_for(n.get("industry"), n.get("business_name"))
    first, _, last = (n.get("contact_name") or "").partition(" ")
    data = {"business_name": n["business_name"], "segment": seg if seg in C.SEGMENT_KEYS else None,
            "website": n.get("website"), "phone": n.get("phone_raw"), "email": n.get("email"),
            "street_address": n.get("address"), "city": n.get("city"), "state": n.get("state"),
            "country": n.get("country"), "zip_code": n.get("zip_code"), "contact_first_name": first or None,
            "contact_last_name": last or None, "source": (b.source_system or "import")[:60],
            "source_url": n.get("source_url") if (n.get("source_url") or "").lower().startswith(("http://", "https://")) else None,
            "is_practice": False}
    try:
        opp = E.add_prospect(db, org, user, data)          # commits
    except HTTPException as exc:
        raise BI.BusinessImportError(str(exc.detail))
    notes = [x for x in (("Source record ID: " + n["source_record_id"]) if n.get("source_record_id") else None,
                         ("Imported in " + b.batch_code), n.get("notes"),
                         ("Contact preference: " + n["contact_preference_label"]) if n.get("contact_preference_label") else None,
                         "Phone line type: %s (not verified as mobile; no texting permission)" % n.get("phone_line_type"),
                         ("Email not verified" if n.get("email") and not n.get("email_verified") else None)) if x]
    E.add_evidence(db, org.id, opp, user, {"kind": "note", "value": "\n".join(notes)}, commit=False)
    if n.get("intro_draft"):
        E.add_evidence(db, org.id, opp, user, {"kind": "note", "value": "Intro message DRAFT - not sent:\n" + n["intro_draft"]},
                       commit=False)
    db.commit()
    return opp.id


def undo(db: Session, b, record_id: str) -> bool:
    """Remove a prospect this import created while it is still untouched:
    stage Discovered and nothing but its creation and evidence notes."""
    from app.services.acquisition import common as C
    opp = (db.query(AcquisitionOpportunity)
           .filter(AcquisitionOpportunity.id == record_id, AcquisitionOpportunity.organization_id == BI.target_id(b))
           .first())
    if opp is None or opp.stage != C.DISCOVERED:
        return False
    worked = (db.query(AcquisitionEvent.id).filter(AcquisitionEvent.opportunity_id == opp.id,
                                                  AcquisitionEvent.kind != "stage").first())
    if worked or db.query(AcquisitionEvent.id).filter(AcquisitionEvent.opportunity_id == opp.id).count() > 1:
        return False
    lead_id = opp.lead_id
    db.query(AcquisitionEvidence).filter(AcquisitionEvidence.opportunity_id == opp.id).delete(synchronize_session=False)
    db.query(AcquisitionEvent).filter(AcquisitionEvent.opportunity_id == opp.id).delete(synchronize_session=False)
    db.delete(opp)
    db.flush()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if lead is not None:
        db.delete(lead)
    return True


ADAPTER = BI.Adapter(pipeline=PIPELINE, destination=DESTINATION, label="Customer Acquisition",
                     find_existing=find_existing, write=write, conflicts=conflicts, undo=undo,
                     commits_per_row=True)
