"""Contract templates, the document lifecycle, and signature requests.

THE ONE THING THIS FILE DOES NOT DO
-----------------------------------
It does not draft a contract. There is no clause library here, no assembled
agreement, no "Texas wholesale contract" generated from a model, and no merge
that produces a document a person could mistake for one a lawyer wrote. A
wholesale assignment is a binding real-property agreement whose sufficiency
turns on state law and on the facts of the particular deal, and software that
emits one is emitting a liability.

What it does instead is the part software is actually good at:

  1. HOLD THE CUSTOMER'S OWN FORMS.  They upload the contract their attorney
     gave them, name it, say where it came from and which state it is for, and
     it is there next time. `WholesaleContractTemplate` stores the file and the
     provenance; nothing reads inside it.

  2. HAND OVER THE FACTS.  `GET /deals/{id}/fill-sheet` returns every fact this
     module already holds that a purchase or assignment document typically
     needs — address, parcel, parties, price, earnest money, dates, title
     company — labelled, in transfer order, with the blanks named as blanks.
     It is a checklist of data, not a draft. Every value on it came from a
     person or a document; none is computed for this purpose and none is
     inferred.

  3. TRACK WHAT HAPPENED TO THE DOCUMENT.  The lifecycle in
     `wholesale_esign.py`, enforced on a real transition endpoint, with each
     move recorded as an event naming who made it.

  4. SEND IT FOR SIGNATURE, IF AND ONLY IF SOMETHING CAN.  The signature
     request goes through the provider registry. With no e-signature vendor
     connected, it returns a refusal saying so and the document does not move.
     It never reports a signature that did not happen.

Same three gates as the rest of the module: tenant, feature, and — on every
write — not-an-observer.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, require_not_observation, require_tenant_or_observer,
                      require_tenant_user)
from app.models.models import Lead, User
from app.models.wholesale_models import (
    ACTOR_USER, WholesaleBuyer, WholesaleContractTemplate, WholesaleDeal,
    WholesaleDocument, WholesaleFile, WholesaleProperty, WholesaleSellerProfile,
)
from app.services import wholesale_esign as esign
from app.services import wholesale_files as files
from app.services import wholesale_service as svc
from app.services.entitlements import require_feature

log = logging.getLogger(__name__)

FEATURE = "wholesale_real_estate"

router = APIRouter(prefix="/wholesale", tags=["wholesale"],
                   dependencies=[Depends(require_feature(FEATURE))])

KIND_TEMPLATE = "contract_template"

TEMPLATE_DOC_TYPES = ("purchase_contract", "assignment", "amendment",
                      "disclosure", "addendum", "other")


# ── Contract templates ──────────────────────────────────────────────────────

def template_json(row: WholesaleContractTemplate) -> Dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "doc_type": row.doc_type,
        "jurisdiction": row.jurisdiction,
        "source_note": row.source_note,
        "guidance": row.guidance,
        "file_id": row.file_id,
        "file_name": row.file_name,
        "file_url": ("/wholesale/files/%s" % row.file_id) if row.file_id else None,
        "is_active": bool(row.is_active),
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _own_template(db: Session, org_id: str, template_id: str) -> WholesaleContractTemplate:
    row = (db.query(WholesaleContractTemplate)
           .filter(WholesaleContractTemplate.id == template_id,
                   WholesaleContractTemplate.organization_id == org_id).first())
    if row is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return row


@router.get("/contract-templates")
def list_templates(db: Session = Depends(get_db),
                   user: User = Depends(require_tenant_or_observer),
                   include_archived: bool = False):
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    q = (db.query(WholesaleContractTemplate)
         .filter(WholesaleContractTemplate.organization_id == org_id))
    if not include_archived:
        q = q.filter(WholesaleContractTemplate.is_active.is_(True))
    rows = q.order_by(WholesaleContractTemplate.created_at.desc()).all()
    return {
        "templates": [template_json(r) for r in rows],
        # Said on every read, because a list of "contract templates" is exactly
        # the screen where somebody would otherwise assume the product drafts
        # one for them.
        "notice": ("These are forms your organization supplied. Nothing here "
                   "writes contract language, fills a form in, or checks that a "
                   "form is valid anywhere. Your attorney does that."),
        "doc_types": list(TEMPLATE_DOC_TYPES),
    }


@router.post("/contract-templates")
async def upload_template(
        request: Request,
        file: UploadFile = File(...),
        name: str = Form(...),
        doc_type: str = Form("purchase_contract"),
        jurisdiction: Optional[str] = Form(None),
        source_note: Optional[str] = Form(None),
        guidance: Optional[str] = Form(None),
        db: Session = Depends(get_db),
        user: User = Depends(require_tenant_user),
        _guard: User = Depends(require_not_observation)):
    """Store a form the customer already has.

    The file goes through the same storage path as every other byte in this
    module, so it inherits the same type checks, the same size limit and the
    same authenticated-serve rule. It is stored, not parsed.
    """
    org_id = svc.write_org_id(db, user)
    if doc_type not in TEMPLATE_DOC_TYPES:
        raise HTTPException(status_code=400,
                            detail="doc_type must be one of: %s"
                                   % ", ".join(TEMPLATE_DOC_TYPES))
    if not (name or "").strip():
        raise HTTPException(status_code=400, detail="A template needs a name.")

    data, content_type, original = await files.read_upload(file)
    stored = files.put(org_id, KIND_TEMPLATE, data, content_type)

    blob = WholesaleFile(organization_id=org_id, kind=KIND_TEMPLATE,
                         original_filename=original, uploaded_by_id=user.id,
                         **stored)
    db.add(blob)
    db.flush()

    row = WholesaleContractTemplate(
        organization_id=org_id, name=name.strip(), doc_type=doc_type,
        jurisdiction=(jurisdiction or "").strip() or None,
        source_note=(source_note or "").strip() or None,
        guidance=(guidance or "").strip() or None,
        file_id=blob.id, file_name=original, created_by_id=user.id)
    db.add(row)
    db.flush()
    svc.log_event(db, org_id, "contract_template.added", actor_type=ACTOR_USER,
                  actor_user_id=user.id,
                  summary="Contract template added: %s" % row.name)
    db.commit()
    db.refresh(row)
    return template_json(row)


class TemplatePatch(BaseModel):
    name: Optional[str] = None
    doc_type: Optional[str] = None
    jurisdiction: Optional[str] = None
    source_note: Optional[str] = None
    guidance: Optional[str] = None


@router.patch("/contract-templates/{template_id}")
def edit_template(template_id: str, payload: TemplatePatch, request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(require_tenant_user),
                  _guard: User = Depends(require_not_observation)):
    org_id = svc.write_org_id(db, user)
    row = _own_template(db, org_id, template_id)
    data = payload.model_dump(exclude_unset=True)
    if "doc_type" in data and data["doc_type"] not in TEMPLATE_DOC_TYPES:
        raise HTTPException(status_code=400,
                            detail="doc_type must be one of: %s"
                                   % ", ".join(TEMPLATE_DOC_TYPES))
    for key, value in data.items():
        setattr(row, key, value)
    svc.log_event(db, org_id, "contract_template.updated", actor_type=ACTOR_USER,
                  actor_user_id=user.id,
                  summary="Contract template updated: %s" % row.name)
    db.commit()
    db.refresh(row)
    return template_json(row)


@router.post("/contract-templates/{template_id}/archive")
def archive_template(template_id: str, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(require_tenant_user),
                     _guard: User = Depends(require_not_observation),
                     restore: bool = False):
    """Retire a form, or bring one back. Never a delete.

    A deal that was papered with a template must still be able to say which
    one, and the stored bytes are the only record of what that form actually
    said. Archiving hides it from the picker and changes nothing else.
    """
    org_id = svc.write_org_id(db, user)
    row = _own_template(db, org_id, template_id)
    row.is_active = bool(restore)
    row.archived_at = None if restore else datetime.utcnow()
    svc.log_event(db, org_id,
                  "contract_template.restored" if restore else "contract_template.archived",
                  actor_type=ACTOR_USER, actor_user_id=user.id,
                  summary="Contract template %s: %s"
                          % ("restored" if restore else "archived", row.name))
    db.commit()
    db.refresh(row)
    return template_json(row)


# ── The fill sheet ──────────────────────────────────────────────────────────
#
# Facts, labelled, in the order somebody transcribing them would want. A value
# is either something a person recorded or None — nothing here is derived for
# the purpose of filling a contract, because a number invented to fill a blank
# in a legal document is the worst possible place to invent a number.

def _money(value) -> Optional[str]:
    if value is None:
        return None
    return "%.2f" % float(value)


def _date(value) -> Optional[str]:
    return value.isoformat() if value else None


def _fill_sheet(db: Session, org_id: str, deal: WholesaleDeal) -> List[Dict[str, Any]]:
    prop = (db.query(WholesaleProperty)
            .filter(WholesaleProperty.id == deal.property_id).first())
    # The owner's NAME and contact details live on the Lead, not on the seller
    # profile — the profile carries what they said about selling. Reading them
    # off the profile would have silently produced a contract party of None.
    seller = None
    if deal.seller_profile_id:
        seller = (db.query(WholesaleSellerProfile)
                  .filter(WholesaleSellerProfile.id == deal.seller_profile_id).first())
    lead = None
    if deal.seller_lead_id:
        lead = (db.query(Lead)
                .filter(Lead.id == deal.seller_lead_id,
                        Lead.organization_id == org_id).first())
    buyer = None
    if deal.assigned_buyer_id:
        buyer = (db.query(WholesaleBuyer)
                 .filter(WholesaleBuyer.id == deal.assigned_buyer_id).first())

    def g(obj, attr):
        return getattr(obj, attr, None) if obj is not None else None

    street = g(prop, "street_address")
    unit = g(prop, "unit")
    address = " ".join(x for x in (street, unit) if x) or None

    seller_name = " ".join(
        x for x in (g(lead, "first_name"), g(lead, "last_name")) if x) or None
    seller_name = seller_name or g(prop, "owner_name")

    return [
        {"group": "The property", "fields": [
            {"label": "Street address", "value": address},
            {"label": "City", "value": g(prop, "city")},
            {"label": "State", "value": g(prop, "state")},
            {"label": "ZIP", "value": g(prop, "zip_code")},
            {"label": "County", "value": g(prop, "county")},
            {"label": "Parcel / APN", "value": g(prop, "parcel_apn")},
            {"label": "Legal description", "value": None,
             "note": "Not held here. It comes from the title commitment or the deed."},
        ]},
        {"group": "The parties", "fields": [
            {"label": "Seller of record", "value": seller_name, "review": True,
             "note": "The name on file. Confirm it against the deed before use."},
            {"label": "Seller phone", "value": g(lead, "phone")},
            {"label": "Seller email", "value": g(lead, "email")},
            {"label": "Owner status", "value": g(seller, "owner_status"),
             "note": "Owner of record, heir or agent, as recorded here."},
            {"label": "Ownership type", "value": g(prop, "ownership_type")},
            {"label": "Assignee (end buyer)",
             "value": (g(buyer, "company_name") or g(buyer, "contact_name")) if buyer else None,
             "note": None if buyer else "No buyer has been selected on this deal yet."},
            {"label": "Assignee email", "value": g(buyer, "email")},
        ]},
        {"group": "The money", "fields": [
            {"label": "Purchase price (we pay the seller)",
             "value": _money(deal.contract_price)},
            {"label": "Assignment price (the buyer pays)",
             "value": _money(deal.buyer_price)},
            {"label": "Assignment fee", "value": _money(deal.assignment_fee)},
            {"label": "Earnest money", "value": _money(deal.earnest_money)},
            {"label": "Option fee", "value": _money(deal.option_fee)},
        ]},
        {"group": "The dates", "fields": [
            {"label": "Contract date", "value": _date(deal.contract_date)},
            {"label": "Effective date", "value": _date(deal.effective_date)},
            {"label": "Earnest money due", "value": _date(deal.earnest_money_due)},
            {"label": "Inspection / option deadline",
             "value": _date(deal.inspection_deadline)},
            {"label": "Closing date", "value": _date(deal.closing_date)},
        ]},
        {"group": "Title and closing", "fields": [
            {"label": "Title company", "value": deal.title_company},
            {"label": "Escrow officer", "value": deal.title_escrow_officer},
            {"label": "Title file number", "value": deal.title_file_number},
            {"label": "Closing location", "value": deal.closing_location},
        ]},
    ]


@router.get("/deals/{deal_id}/fill-sheet")
def fill_sheet(deal_id: str, db: Session = Depends(get_db),
               user: User = Depends(require_tenant_or_observer)):
    """This deal's facts, laid out for transcription into the customer's form.

    NOT A DRAFT. It carries no sentences from any agreement, and a blank stays
    blank with a note saying where that fact actually comes from — a blank that
    quietly filled itself is how a wrong legal description reaches a closing
    table.
    """
    org_id = svc.read_org_id(db, user)
    if not org_id:
        raise HTTPException(status_code=409,
                            detail="No customer organization selected.")
    deal = svc.get_deal(db, org_id, deal_id)
    groups = _fill_sheet(db, org_id, deal)

    # ── COMPLETE / NEEDS REVIEW / MISSING ────────────────────────────────
    #
    # Computed here rather than in the browser so the operator screen and any
    # other reader agree about what is ready. Three states, and the middle one
    # earns its place: a seller name that IS on file but has not been checked
    # against the deed is not the same as one that is missing, and it is not
    # the same as one that is done either. Flattening it either way is how a
    # wrong party name reaches a signature page.
    for group in groups:
        counts = {"complete": 0, "needs_review": 0, "missing": 0}
        for field in group["fields"]:
            if not field.get("value"):
                field["status"] = "missing"
            elif field.get("review"):
                field["status"] = "needs_review"
            else:
                field["status"] = "complete"
            counts[field["status"]] += 1
        group["counts"] = counts
        group["ready"] = counts["missing"] == 0 and counts["needs_review"] == 0

    missing = [f["label"] for g in groups for f in g["fields"]
               if f["status"] == "missing"]
    review = [f["label"] for g in groups for f in g["fields"]
              if f["status"] == "needs_review"]
    total = sum(len(g["fields"]) for g in groups)
    templates = (db.query(WholesaleContractTemplate)
                 .filter(WholesaleContractTemplate.organization_id == org_id,
                         WholesaleContractTemplate.is_active.is_(True))
                 .order_by(WholesaleContractTemplate.name).all())
    return {
        "deal_id": deal.id,
        "groups": groups,
        "missing": missing,
        "needs_review": review,
        "summary": {
            "total": total,
            "complete": total - len(missing) - len(review),
            "needs_review": len(review),
            "missing": len(missing),
            # Ready means every field is on file and checked. It is NOT a
            # statement that the resulting document is legally sufficient —
            # nothing in this module makes that claim about anything.
            "ready": not missing and not review,
        },
        "templates": [template_json(t) for t in templates],
        "notice": ("Every value here was entered by a person or read off a "
                   "document. Nothing on this sheet was drafted, completed or "
                   "checked for legal sufficiency, and this module does not "
                   "produce a contract from it."),
    }


# ── The document lifecycle ──────────────────────────────────────────────────

@router.get("/documents/lifecycle")
def document_lifecycle(user: User = Depends(require_tenant_or_observer)):
    """Every state, what it means, and who is allowed to cause it."""
    return {
        "statuses": esign.lifecycle_report(),
        "signature": esign.capability(),
    }


class StatusIn(BaseModel):
    status: str
    note: Optional[str] = None
    # Required when moving to `superseded`: the document that replaces this one.
    superseded_by_id: Optional[str] = None


@router.post("/documents/{document_id}/status")
def set_document_status(document_id: str, payload: StatusIn, request: Request,
                        db: Session = Depends(get_db),
                        user: User = Depends(require_tenant_user),
                        _guard: User = Depends(require_not_observation)):
    """Move a document to a new lifecycle state, or refuse and say why.

    Every move here is caused by the person making the request, and the event
    records who. No status is ever set by elapsed time, by a closing date
    passing, or by anything else inferring that a document has probably moved
    on — see the module docstring in `wholesale_esign.py`.
    """
    org_id = svc.write_org_id(db, user)
    doc = (db.query(WholesaleDocument)
           .filter(WholesaleDocument.id == document_id,
                   WholesaleDocument.organization_id == org_id).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    current = esign.normalise(doc.status)
    try:
        target = esign.transition(current, payload.status)
    except esign.TransitionRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if target == esign.STATUS_SUPERSEDED:
        if not payload.superseded_by_id:
            raise HTTPException(
                status_code=400,
                detail=("Superseding a document means naming the one that "
                        "replaces it. Pass superseded_by_id."))
        replacement = (db.query(WholesaleDocument)
                       .filter(WholesaleDocument.id == payload.superseded_by_id,
                               WholesaleDocument.organization_id == org_id,
                               WholesaleDocument.deal_id == doc.deal_id).first())
        if replacement is None:
            raise HTTPException(status_code=404,
                                detail="The replacing document was not found on this deal.")
        doc.superseded_by_id = replacement.id

    if target == esign.STATUS_SIGNED:
        # `signed` is only honest when there is something signed to point at.
        if not (doc.file_id or doc.file_name or doc.external_ref):
            raise HTTPException(
                status_code=409,
                detail=("Nothing is attached to this document, so there is no "
                        "signed copy to record. Upload the executed file first, "
                        "or connect a signature provider that can report one."))
        doc.executed_at = doc.executed_at or datetime.utcnow()
        doc.signature_status = "signed"

    doc.status = target
    if payload.note:
        doc.notes = payload.note if not doc.notes else "%s\n%s" % (doc.notes, payload.note)

    svc.log_event(db, org_id, "document.status", actor_type=ACTOR_USER,
                  actor_user_id=user.id, deal_id=doc.deal_id,
                  summary="%s: %s → %s"
                          % ((doc.doc_type or "document").replace("_", " "),
                             esign.STATUS_LABEL[current], esign.STATUS_LABEL[target]))
    db.commit()
    db.refresh(doc)
    return {
        "id": doc.id,
        "status": doc.status,
        "status_label": esign.STATUS_LABEL[target],
        "allowed_next": esign.allowed_next(target),
        "superseded_by_id": doc.superseded_by_id,
    }


# ── Signature requests ──────────────────────────────────────────────────────

class SignatureIn(BaseModel):
    # [{"name": ..., "email": ..., "role": ...}]
    parties: Optional[List[Dict[str, Any]]] = None
    message: Optional[str] = None


@router.post("/documents/{document_id}/signature-request")
def request_signature(document_id: str, payload: SignatureIn, request: Request,
                      db: Session = Depends(get_db),
                      user: User = Depends(require_tenant_user),
                      _guard: User = Depends(require_not_observation)):
    """Ask the configured provider to carry this document to its signers.

    With no e-signature provider connected, this returns 200 with
    `sent: false` and the reason — deliberately not an error, because nothing
    went wrong: the operator asked a reasonable question and the honest answer
    is "not from here, sign it the way you already do". The document does NOT
    move to `sent`, no envelope reference is stored, and no signature is
    claimed.
    """
    org_id = svc.write_org_id(db, user)
    doc = (db.query(WholesaleDocument)
           .filter(WholesaleDocument.id == document_id,
                   WholesaleDocument.organization_id == org_id).first())
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    settings = svc.resolve_settings(db, org_id)
    provider = esign.get_provider(getattr(settings, "esign_provider", None))

    parties = payload.parties
    if parties is None and doc.parties:
        try:
            parties = json.loads(doc.parties)
        except (TypeError, ValueError):
            parties = []

    result = provider.send(esign.SignatureRequest(
        document_id=doc.id,
        title=doc.title or (doc.doc_type or "document").replace("_", " "),
        parties=parties or [],
        file_id=doc.file_id,
        file_name=doc.file_name,
        message=payload.message,
    ))

    if result.left_the_building:
        # Only a provider that genuinely reached a signer moves the document.
        doc.status = esign.transition(doc.status, esign.STATUS_SENT)
        doc.signature_status = "out_for_signature"
        doc.signature_provider = result.provider
        doc.external_ref = result.external_ref
        svc.log_event(db, org_id, "document.sent_for_signature",
                      actor_type=ACTOR_USER, actor_user_id=user.id,
                      deal_id=doc.deal_id,
                      summary="Sent for signature via %s" % result.provider)
        db.commit()
        db.refresh(doc)
    else:
        # Recorded as an attempt, so a person can see they asked and what the
        # answer was. Nothing about the document changes.
        svc.log_event(db, org_id, "document.signature_unavailable",
                      actor_type=ACTOR_USER, actor_user_id=user.id,
                      deal_id=doc.deal_id,
                      summary="Signature request not sent: no provider connected")
        db.commit()

    return {
        "sent": result.left_the_building,
        "status": result.status,
        "provider": result.provider,
        "message": result.message,
        "external_ref": result.external_ref,
        "document_status": esign.normalise(doc.status),
        "capability": esign.capability(),
    }
