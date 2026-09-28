"""
Native CRM Router — master contact records.

CRM contacts are richer, long-lived relationship records that live alongside
leads but are independent of them. A contact can optionally link to a Lead.

This is DISTINCT from crm_router.py which handles external CRM webhook
integrations (GoHighLevel, HubSpot, Zapier). This router handles the
internal native CRM contacts stored in crm_contacts.

Stages are org-configurable (stored as crm_stages JSON on Organization).
If not customized, defaults come from the industry-appropriate stage set.

Custom fields are also org-configurable (crm_custom_fields JSON on Organization).
Values are stored per contact in the custom_data JSON column.
"""

import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import (get_db, get_current_user, require_tenant_user, load_org_in_scope,
                      require_not_observation)
from app.services.platform_owner import (require_tenant_context,  # noqa: F401  (kept for importers)
                                         _NO_CONTEXT_DETAIL, is_platform_pseudo_org)
from app.models.models import User, Organization, CRMContact, CRMNote, Lead
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)
from app.services import lead_scope
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/crm-native", tags=["crm-native"])


# ── Default stages: ONE REGISTRY, NOT A FOURTH MAP ───────────────────
#
# INDUSTRY_STAGES and GENERIC_STAGES were declared here, ninety lines of
# vertical dictionaries looked up with `org.industry or "funeral"`. Two defects
# in one expression:
#
#   * THE LOOKUP WAS RAW. `org.industry` holds whatever a human picked -
#     "Energy & Procurement" - and this was a plain dict.get against
#     lowercase_underscore keys, so a correctly configured energy organization
#     matched nothing.
#   * THE FALLBACK WAS A VERTICAL. An organization with no industry recorded
#     was handed a funeral home's pipeline: Pre-Need, At-Need, Arrangements,
#     Services Complete, Aftercare Follow-up. Nobody chose that.
#
# `app/services/industry_templates.py` is the platform's one industry registry
# and it already normalizes, already has an energy entry, and already refuses
# to fall back to anybody's vertical. The stage table now lives there beside
# the tiers, appointment types and vocabulary it belongs with. The names below
# are kept so nothing that imported them breaks.
from app.services import industry_templates as _industry

INDUSTRY_STAGES = _industry.CRM_STAGE_OBJECTS
GENERIC_STAGES = _industry.GENERIC_CRM_STAGE_OBJECTS


def _industry_default_stages(org: Organization) -> list:
    """This organization's stages before it customized anything."""
    return _industry.crm_stage_objects(getattr(org, "industry", None))


def _get_org_stages(org: Organization) -> list:
    """Return this org's CRM stages — custom if set, else industry default."""
    if org.crm_stages:
        try:
            return json.loads(org.crm_stages)
        except Exception:
            pass
    return _industry_default_stages(org)


def _get_org_custom_fields(org: Organization) -> list:
    """Return this org's CRM custom field schema."""
    if org.crm_custom_fields:
        try:
            return json.loads(org.crm_custom_fields)
        except Exception:
            pass
    return []


def _is_manager(user: User, db: Session = None, request: Optional[Request] = None) -> bool:
    """Manager authority IN THE SELECTED WORKSPACE (same answer as deps.require_admin).

    This was `user.role in ("org_admin", "super_admin", "god_admin")` - the
    account-global role - so an org_admin of A who is only an advisor of B
    passed every manager gate here while acting in B, and a person who is an
    advisor at home but org_admin of B was refused in the workspace they run.
    `is_manager_here` resolves the membership role in the selected workspace
    and falls back to `users.role` when none is selected, so single-workspace
    customers behave exactly as before. Platform operators (super_admin /
    god_admin) keep the platform-role pass they had, as in the leads delete /
    dedup fixes; the org they act on is still the active workspace.
    """
    return (getattr(user, "role", None) in ("super_admin", "god_admin")
            or lead_scope.is_manager_here(user, db, request))


def _org_id(db: Session, user: User, request: Optional[Request] = None) -> Optional[str]:
    """The ACTIVE workspace org - the one `_is_manager` is evaluated in.

    Every route here read `current_user.organization_id`, the HOME column, so a
    person standing in workspace B read and wrote home org A's contacts, stages
    and custom fields - and `/sync-from-leads` / `/contacts/from-lead` copied
    workspace B's leads (authorized_lead_query IS workspace-aware) into A's
    CRM, moving one customer's families into another customer's database.
    Without X-Workspace-Id this is the home column, exactly as before.
    """
    return lead_scope.active_workspace_org_id(user, db, request)


def _write_org_id(db: Session, user: User, request: Request) -> str:
    """The workspace a new contact belongs to, or the platform's 409.

    Replaces `require_tenant_context` on the create routes: that guard read the
    home column only, so it refused a member working in a workspace they hold
    a membership in, and (with the home column) attributed the row to the
    wrong tenant. A neutral god_admin still gets the same 409 as before.
    """
    org_id = _org_id(db, user, request)
    if not org_id or is_platform_pseudo_org(org_id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_NO_CONTEXT_DETAIL)
    return str(org_id)


def _load_org(db: Session, user: User, request: Optional[Request] = None) -> Optional[Organization]:
    org_id = _org_id(db, user, request)
    if not org_id:
        return None
    return db.query(Organization).filter(Organization.id == org_id).first()


def _assert_assignee_in_workspace(db: Session, org_id: str, user_id: str) -> None:
    """An assignee must belong to THIS workspace (home column or membership).

    A guessed user id from another tenant would otherwise be written onto the
    contact, pointing one customer's record at another customer's staff. 404,
    not 403, so the refusal does not confirm the user exists.
    """
    from app.models.sales_models import Membership
    from app.services.workspace_access import SCOPE_CUSTOMER_ORG
    target = db.query(User).filter(User.id == user_id).first()
    if target is not None:
        if str(getattr(target, "organization_id", None) or "") == str(org_id):
            return
        mem = (db.query(Membership)
               .filter(Membership.user_id == target.id,
                       Membership.scope_type == SCOPE_CUSTOMER_ORG,
                       Membership.scope_id == str(org_id),
                       Membership.is_active.is_(True))
               .first())
        if mem is not None:
            return
    raise HTTPException(status_code=404, detail="User not found")


def _assert_lead_in_scope(db: Session, user: User, lead_id: str,
                          request: Optional[Request] = None) -> None:
    """A linked lead must be one this caller may see in this workspace, else 404."""
    if authorized_lead_query(db, user, request=request).filter(Lead.id == lead_id).first() is None:
        raise HTTPException(status_code=404, detail="Lead not found")


def _contact_dict(c: CRMContact) -> dict:
    custom = {}
    if c.custom_data:
        try:
            custom = json.loads(c.custom_data)
        except Exception:
            pass
    return {
        "id": c.id,
        "organization_id": c.organization_id,
        "first_name": c.first_name,
        "last_name": c.last_name,
        "full_name": f"{c.first_name or ''} {c.last_name or ''}".strip() or "—",
        "phone": c.phone,
        "email": c.email,
        "address_street": c.address_street,
        "address_city": c.address_city,
        "address_state": c.address_state,
        "address_zip": c.address_zip,
        "stage": c.stage,
        "notes": c.notes,
        "tags": c.tags,
        "lead_id": c.lead_id,
        "assigned_to_id": c.assigned_to_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        "last_contacted_at": c.last_contacted_at.isoformat() if c.last_contacted_at else None,
        "is_archived": c.is_archived,
        "custom_data": custom,
    }


def _base_query(db: Session, user: User, request: Optional[Request] = None):
    """Contacts in the ACTIVE workspace; a non-manager sees only their own.

    A contact in any other tenant is simply absent, so every route built on this
    answers a guessed id with 404 and changes nothing.
    """
    org_id = _org_id(db, user, request)
    q = db.query(CRMContact).filter(
        CRMContact.organization_id == org_id,
        CRMContact.is_archived == False,
    )
    if not org_id:
        # `== None` renders IS NULL and organization_id is NOT NULL, so this is
        # already empty - said explicitly rather than left to the schema.
        return q.filter(False)
    if not _is_manager(user, db, request):
        q = q.filter(CRMContact.assigned_to_id == user.id)
    return q


# ── Stages endpoints ───────────────────────────────────────────────────────

@router.get("/stages")
def get_stages(
    request: Request,
    org_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Return this org's CRM stages. Custom if configured, else industry default.
    Super admin / god admin can pass ?org_id= to inspect any org's stages.
    """
    # Third copy of the same pattern. Read-only, but it still leaked another
    # brand's CRM stage configuration and industry to a super_admin who guessed
    # an org id. Same guard as the other two.
    if org_id and current_user.role in ("super_admin", "god_admin"):
        org = load_org_in_scope(db, current_user, org_id)
    else:
        org = _load_org(db, current_user, request)
    if not org:
        return GENERIC_STAGES
    stages = _get_org_stages(org)
    industry_default = _industry_default_stages(org)
    return {
        "stages": stages,
        "is_custom": bool(org.crm_stages),
        # THE RESOLVED KEY, not the raw string and not a vertical default.
        "industry": _industry.normalize(getattr(org, "industry", None)),
        "industry_raw": getattr(org, "industry", None),
        "industry_default": industry_default,
    }


class StagesUpdate(BaseModel):
    stages: list  # list of {key, label, color}


@router.put("/stages", dependencies=[Depends(require_not_observation)])
def update_stages(
    req: StagesUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Save custom pipeline stages for this org. org_admin+ only."""
    if not _is_manager(current_user, db, request):
        raise HTTPException(status_code=403, detail="Admin access required")
    if not req.stages:
        raise HTTPException(status_code=400, detail="Must have at least one stage")
    for s in req.stages:
        if not s.get("key") or not s.get("label"):
            raise HTTPException(status_code=400, detail="Each stage needs a key and label")
    org = _load_org(db, current_user, request)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    previous = org.crm_stages
    org.crm_stages = json.dumps(req.stages)
    # Audited under the workspace org acted on (same as /stages/reset), in the
    # same transaction as the write it records.
    log_action(db, org.id, current_user.id,
               action="crm_native.stages_updated", target_type="organization",
               target_id=str(org.id), before={"crm_stages": previous},
               after={"crm_stages": org.crm_stages}, commit=False)
    db.commit()
    return {"saved": True, "stages": req.stages}


@router.delete("/stages/reset", dependencies=[Depends(require_not_observation)])
def reset_stages(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Reset stages back to the industry default."""
    if not _is_manager(current_user, db, request):
        raise HTTPException(status_code=403, detail="Admin access required")
    org = _load_org(db, current_user, request)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    previous = org.crm_stages
    org.crm_stages = None
    log_action(db, org.id, current_user.id,
               action="crm_native.stages_reset", target_type="organization",
               target_id=str(org.id), before={"crm_stages": previous},
               commit=False)
    db.commit()
    default = _industry_default_stages(org)
    return {"reset": True, "stages": default}


# ── Custom fields endpoints ────────────────────────────────────────────────

@router.get("/custom-fields")
def get_custom_fields(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Return this org's custom field schema."""
    org = _load_org(db, current_user, request)
    if not org:
        return []
    return _get_org_custom_fields(org)


class CustomFieldsUpdate(BaseModel):
    fields: list  # list of {key, label, type, options?}


@router.put("/custom-fields", dependencies=[Depends(require_not_observation)])
def update_custom_fields(
    req: CustomFieldsUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Save custom field schema for this org. org_admin+ only."""
    if not _is_manager(current_user, db, request):
        raise HTTPException(status_code=403, detail="Admin access required")
    for f in req.fields:
        if not f.get("key") or not f.get("label") or not f.get("type"):
            raise HTTPException(status_code=400, detail="Each field needs key, label, and type")
        if f["type"] not in ("text", "number", "dropdown", "date"):
            raise HTTPException(status_code=400, detail=f"Invalid field type: {f['type']}")
    org = _load_org(db, current_user, request)
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    previous = org.crm_custom_fields
    org.crm_custom_fields = json.dumps(req.fields)
    log_action(db, org.id, current_user.id,
               action="crm_native.custom_fields_updated", target_type="organization",
               target_id=str(org.id), before={"crm_custom_fields": previous},
               after={"crm_custom_fields": org.crm_custom_fields}, commit=False)
    db.commit()
    return {"saved": True, "fields": req.fields}


# ── Contact CRUD ───────────────────────────────────────────────────────────

@router.get("/contacts")
def list_contacts(
    request: Request,
    stage: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    q = _base_query(db, current_user, request)
    if stage:
        q = q.filter(CRMContact.stage == stage)
    if search:
        like = f"%{search.lower()}%"
        from sqlalchemy import or_, func
        q = q.filter(or_(
            func.lower(CRMContact.first_name).like(like),
            func.lower(CRMContact.last_name).like(like),
            CRMContact.phone.like(like),
            func.lower(CRMContact.email).like(like),
        ))
    total = q.count()
    contacts = q.order_by(CRMContact.updated_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "page": page, "page_size": page_size, "items": [_contact_dict(c) for c in contacts]}


@router.get("/contacts/{contact_id}")
def get_contact(
    contact_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user, request).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    result = _contact_dict(contact)
    if contact.lead_id:
        lead = authorized_lead_query(db, current_user, request=request).filter(Lead.id == contact.lead_id).first()
        if lead:
            result["linked_lead"] = {"id": lead.id, "status": lead.status, "tier": lead.tier}
    return result


class ContactCreate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    stage: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    lead_id: Optional[str] = None
    assigned_to_id: Optional[str] = None
    custom_data: Optional[dict] = None


@router.post("/contacts", dependencies=[Depends(require_not_observation)])
def create_contact(
    req: ContactCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _write_org_id(db, current_user, request)
    # Ids from the body are checked against THIS workspace: a guessed lead or
    # user id from another tenant is refused with 404 and nothing is written.
    if req.lead_id:
        _assert_lead_in_scope(db, current_user, req.lead_id, request)
    if req.assigned_to_id and req.assigned_to_id != current_user.id:
        _assert_assignee_in_workspace(db, org_id, req.assigned_to_id)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    stages = _get_org_stages(org) if org else GENERIC_STAGES
    default_stage = stages[0]["key"] if stages else "new_lead"
    contact = CRMContact(
        organization_id=org_id,
        first_name=req.first_name,
        last_name=req.last_name,
        phone=req.phone,
        email=req.email,
        address_street=req.address_street,
        address_city=req.address_city,
        address_state=req.address_state,
        address_zip=req.address_zip,
        stage=req.stage or default_stage,
        notes=req.notes,
        tags=req.tags,
        lead_id=req.lead_id,
        assigned_to_id=req.assigned_to_id or current_user.id,
        custom_data=json.dumps(req.custom_data) if req.custom_data else None,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return _contact_dict(contact)


class ContactUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    stage: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[str] = None
    lead_id: Optional[str] = None
    assigned_to_id: Optional[str] = None
    custom_data: Optional[dict] = None


@router.patch("/contacts/{contact_id}", dependencies=[Depends(require_not_observation)])
def update_contact(
    contact_id: str,
    req: ContactUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user, request).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    # The UI PATCHes the whole record back, so only a CHANGED link is checked:
    # re-sending the current values is an ordinary edit.
    if req.lead_id is not None and req.lead_id and req.lead_id != contact.lead_id:
        _assert_lead_in_scope(db, current_user, req.lead_id, request)
    if (req.assigned_to_id is not None and req.assigned_to_id
            and req.assigned_to_id != contact.assigned_to_id
            and req.assigned_to_id != current_user.id):
        _assert_assignee_in_workspace(db, contact.organization_id, req.assigned_to_id)
    for field in ["first_name", "last_name", "phone", "email",
                  "address_street", "address_city", "address_state", "address_zip",
                  "stage", "notes", "tags", "lead_id", "assigned_to_id"]:
        val = getattr(req, field)
        if val is not None:
            setattr(contact, field, val)
    if req.custom_data is not None:
        # Merge with existing custom data so other fields aren't wiped
        existing = {}
        if contact.custom_data:
            try:
                existing = json.loads(contact.custom_data)
            except Exception:
                pass
        existing.update(req.custom_data)
        contact.custom_data = json.dumps(existing)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(contact)
    return _contact_dict(contact)


@router.delete("/contacts/{contact_id}", dependencies=[Depends(require_not_observation)])
def archive_contact(
    contact_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user, request).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.is_archived = True
    contact.updated_at = datetime.utcnow()
    # Recorded against the tenant the contact belongs to, with the operator.
    log_action(db, contact.organization_id, current_user.id,
               action="crm_native.contact_archived", target_type="crm_contact",
               target_id=str(contact.id),
               before={"first_name": contact.first_name, "last_name": contact.last_name,
                       "stage": contact.stage, "assigned_to_id": contact.assigned_to_id},
               commit=False)
    db.commit()
    return {"archived": True}


# ── Notes ─────────────────────────────────────────────────────────────────

@router.get("/contacts/{contact_id}/notes")
def get_notes(
    contact_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user, request).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    notes = db.query(CRMNote).filter(CRMNote.contact_id == contact_id).order_by(CRMNote.created_at.desc()).all()
    return [{"id": n.id, "content": n.content, "created_at": n.created_at.isoformat() if n.created_at else None, "author_id": n.author_id} for n in notes]


class NoteCreate(BaseModel):
    content: str


@router.post("/contacts/{contact_id}/notes", dependencies=[Depends(require_not_observation)])
def add_note(
    contact_id: str,
    req: NoteCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user, request).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    note = CRMNote(contact_id=contact_id, author_id=current_user.id, content=req.content.strip())
    db.add(note)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return {"id": note.id, "content": note.content, "created_at": note.created_at.isoformat() if note.created_at else None, "author_id": note.author_id}


# ── Lead sync ─────────────────────────────────────────────────────────────

@router.post("/contacts/from-lead/{lead_id}", dependencies=[Depends(require_not_observation)])
def create_from_lead(
    lead_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    org_id = _write_org_id(db, current_user, request)
    lead = authorized_lead_query(db, current_user, request=request).filter(Lead.id == lead_id).first()
    if not lead or str(lead.organization_id) != org_id:
        raise HTTPException(status_code=404, detail="Lead not found")
    existing = db.query(CRMContact).filter(
        CRMContact.lead_id == lead_id,
        CRMContact.organization_id == org_id,
        CRMContact.is_archived == False,
    ).first()
    if existing:
        return {**_contact_dict(existing), "already_existed": True}
    org = db.query(Organization).filter(Organization.id == org_id).first()
    stages = _get_org_stages(org) if org else GENERIC_STAGES
    default_stage = stages[0]["key"] if stages else "new_lead"
    contact = CRMContact(
        # THE LEAD'S WORKSPACE. This was the caller's home column while the
        # lead came from the selected workspace - one tenant's family copied
        # into another tenant's CRM.
        organization_id=org_id,
        first_name=lead.first_name,
        last_name=lead.last_name,
        phone=lead.phone,
        email=lead.email,
        stage=default_stage,
        lead_id=lead.id,
        assigned_to_id=lead.assigned_to_id or current_user.id,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return {**_contact_dict(contact), "already_existed": False}


@router.post("/sync-from-leads", dependencies=[Depends(require_not_observation)])
def sync_leads_to_crm(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bulk-sync all org leads into the CRM. Safe to call multiple times.

    Contacts are written into the ACTIVE workspace - the same one the lead
    query below is scoped to. This used the home column for the contacts while
    the leads came from the selected workspace, so syncing inside B copied B's
    families (name, phone, email) into home org A's CRM.
    """
    org_id = _write_org_id(db, current_user, request)
    existing_lead_ids = {
        row.lead_id
        for row in db.query(CRMContact.lead_id).filter(
            CRMContact.organization_id == org_id,
            CRMContact.lead_id.isnot(None),
            CRMContact.is_archived == False,
        ).all()
        if row.lead_id
    }
    org = db.query(Organization).filter(Organization.id == org_id).first()
    stages = _get_org_stages(org) if org else GENERIC_STAGES
    default_stage = stages[0]["key"] if stages else "new_lead"

    # THE SIDE DOOR. This read every lead in the organization and copied each
    # one into crm_contacts - name, phone, email - which an advisor could then
    # read back through the CRM screens. Nothing here was labelled a lead
    # endpoint, so it survived a lead-by-lead audit while quietly materialising
    # the whole book for whoever pressed Sync. Starting from the authorized
    # query means an advisor syncs their own families and a manager syncs the
    # team's, which is what the button was always meant to do.
    leads = (
        lead_scope.authorized_lead_query(db, current_user, request=request)
        .filter(Lead.is_duplicate == False, Lead.organization_id == org_id)
        .all()
    )

    created = 0
    for lead in leads:
        if lead.id in existing_lead_ids:
            continue
        contact = CRMContact(
            organization_id=org_id,
            first_name=lead.first_name,
            last_name=lead.last_name,
            phone=lead.phone,
            email=lead.email,
            stage=default_stage,
            lead_id=lead.id,
            assigned_to_id=lead.assigned_to_id or current_user.id,
        )
        db.add(contact)
        created += 1

    if created:
        db.commit()

    return {
        "synced": created,
        "already_existed": len(leads) - created,
        "total_leads": len(leads),
    }
