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

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_tenant_user, load_org_in_scope
from app.services.platform_owner import require_tenant_context
from app.models.models import User, Organization, CRMContact, CRMNote, Lead
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter(prefix="/crm-native", tags=["crm-native"])


# ── Industry-specific default stages ──────────────────────────────────────

INDUSTRY_STAGES = {
    "funeral": [
        {"key": "inquiry",           "label": "Inquiry",              "color": "#64748b"},
        {"key": "pre_need",          "label": "Pre-Need",             "color": "#6366f1"},
        {"key": "at_need",           "label": "At-Need",              "color": "#f59e0b"},
        {"key": "arrangements",      "label": "Arrangements",         "color": "#ef4444"},
        {"key": "services_complete", "label": "Services Complete",    "color": "#10b981"},
        {"key": "aftercare",         "label": "Aftercare Follow-up",  "color": "#3b82f6"},
        {"key": "closed",            "label": "Closed",               "color": "#374151"},
    ],
    "fiber": [
        {"key": "new_lead",       "label": "New Lead",         "color": "#64748b"},
        {"key": "contacted",      "label": "Contacted",        "color": "#6366f1"},
        {"key": "quoted",         "label": "Quoted",           "color": "#f59e0b"},
        {"key": "pending_install","label": "Pending Install",  "color": "#f97316"},
        {"key": "installed",      "label": "Installed",        "color": "#10b981"},
        {"key": "active",         "label": "Active",           "color": "#3b82f6"},
        {"key": "churned",        "label": "Churned",          "color": "#374151"},
    ],
    "roofing": [
        {"key": "new_lead",            "label": "New Lead",            "color": "#64748b"},
        {"key": "inspection_scheduled","label": "Inspection Scheduled","color": "#6366f1"},
        {"key": "estimate_sent",       "label": "Estimate Sent",       "color": "#f59e0b"},
        {"key": "contract_signed",     "label": "Contract Signed",     "color": "#f97316"},
        {"key": "job_scheduled",       "label": "Job Scheduled",       "color": "#8b5cf6"},
        {"key": "completed",           "label": "Completed",           "color": "#10b981"},
        {"key": "closed",              "label": "Closed / Lost",       "color": "#374151"},
    ],
    "insurance": [
        {"key": "new_lead",      "label": "New Lead",       "color": "#64748b"},
        {"key": "contacted",     "label": "Contacted",      "color": "#6366f1"},
        {"key": "quoted",        "label": "Quoted",         "color": "#f59e0b"},
        {"key": "application",   "label": "Application",    "color": "#f97316"},
        {"key": "underwriting",  "label": "Underwriting",   "color": "#8b5cf6"},
        {"key": "active_policy", "label": "Active Policy",  "color": "#10b981"},
        {"key": "lapsed",        "label": "Lapsed",         "color": "#374151"},
    ],
    "health_insurance": [
        {"key": "new_lead",      "label": "New Lead",       "color": "#64748b"},
        {"key": "contacted",     "label": "Contacted",      "color": "#6366f1"},
        {"key": "quoted",        "label": "Quoted",         "color": "#f59e0b"},
        {"key": "application",   "label": "Application",    "color": "#f97316"},
        {"key": "enrolled",      "label": "Enrolled",       "color": "#10b981"},
        {"key": "renewal",       "label": "Up for Renewal", "color": "#3b82f6"},
        {"key": "lapsed",        "label": "Lapsed",         "color": "#374151"},
    ],
    "medicare": [
        {"key": "new_lead",      "label": "New Lead",        "color": "#64748b"},
        {"key": "contacted",     "label": "Contacted",       "color": "#6366f1"},
        {"key": "needs_assessed","label": "Needs Assessed",  "color": "#f59e0b"},
        {"key": "plan_selected", "label": "Plan Selected",   "color": "#f97316"},
        {"key": "enrolled",      "label": "Enrolled",        "color": "#10b981"},
        {"key": "renewal",       "label": "Up for Renewal",  "color": "#3b82f6"},
        {"key": "lost",          "label": "Lost",            "color": "#374151"},
    ],
    "real_estate": [
        {"key": "new_lead",       "label": "New Lead",        "color": "#64748b"},
        {"key": "contacted",      "label": "Contacted",       "color": "#6366f1"},
        {"key": "showing",        "label": "Showing",         "color": "#f59e0b"},
        {"key": "offer_made",     "label": "Offer Made",      "color": "#f97316"},
        {"key": "under_contract", "label": "Under Contract",  "color": "#8b5cf6"},
        {"key": "closed",         "label": "Closed",          "color": "#10b981"},
        {"key": "lost",           "label": "Lost",            "color": "#374151"},
    ],
    "auto_repair": [
        {"key": "new_lead",    "label": "New Lead",       "color": "#64748b"},
        {"key": "contacted",   "label": "Contacted",      "color": "#6366f1"},
        {"key": "estimate",    "label": "Estimate Given", "color": "#f59e0b"},
        {"key": "approved",    "label": "Work Approved",  "color": "#f97316"},
        {"key": "in_shop",     "label": "In Shop",        "color": "#8b5cf6"},
        {"key": "completed",   "label": "Completed",      "color": "#10b981"},
        {"key": "closed",      "label": "Closed",         "color": "#374151"},
    ],
    "solar": [
        {"key": "new_lead",      "label": "New Lead",        "color": "#64748b"},
        {"key": "site_survey",   "label": "Site Survey",     "color": "#6366f1"},
        {"key": "proposal",      "label": "Proposal Sent",   "color": "#f59e0b"},
        {"key": "contract",      "label": "Contract Signed", "color": "#f97316"},
        {"key": "permitting",    "label": "Permitting",      "color": "#8b5cf6"},
        {"key": "installation",  "label": "Installation",    "color": "#10b981"},
        {"key": "active",        "label": "Active / Live",   "color": "#3b82f6"},
        {"key": "lost",          "label": "Lost",            "color": "#374151"},
    ],
}

# Generic fallback for any industry not listed above
GENERIC_STAGES = [
    {"key": "new_lead",   "label": "New Lead",   "color": "#64748b"},
    {"key": "contacted",  "label": "Contacted",  "color": "#6366f1"},
    {"key": "qualified",  "label": "Qualified",  "color": "#f59e0b"},
    {"key": "proposal",   "label": "Proposal",   "color": "#f97316"},
    {"key": "negotiating","label": "Negotiating","color": "#8b5cf6"},
    {"key": "won",        "label": "Won",         "color": "#10b981"},
    {"key": "lost",       "label": "Lost",        "color": "#374151"},
]


def _get_org_stages(org: Organization) -> list:
    """Return this org's CRM stages — custom if set, else industry default."""
    if org.crm_stages:
        try:
            return json.loads(org.crm_stages)
        except Exception:
            pass
    return INDUSTRY_STAGES.get(org.industry or "funeral", GENERIC_STAGES)


def _get_org_custom_fields(org: Organization) -> list:
    """Return this org's CRM custom field schema."""
    if org.crm_custom_fields:
        try:
            return json.loads(org.crm_custom_fields)
        except Exception:
            pass
    return []


def _is_manager(user: User) -> bool:
    return user.role in ("org_admin", "super_admin", "god_admin")


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


def _base_query(db: Session, user: User):
    q = db.query(CRMContact).filter(
        CRMContact.organization_id == user.organization_id,
        CRMContact.is_archived == False,
    )
    if not _is_manager(user):
        q = q.filter(CRMContact.assigned_to_id == user.id)
    return q


# ── Stages endpoints ───────────────────────────────────────────────────────

@router.get("/stages")
def get_stages(
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
        org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        return GENERIC_STAGES
    stages = _get_org_stages(org)
    industry_default = INDUSTRY_STAGES.get(org.industry or "funeral", GENERIC_STAGES)
    return {
        "stages": stages,
        "is_custom": bool(org.crm_stages),
        "industry": org.industry or "funeral",
        "industry_default": industry_default,
    }


class StagesUpdate(BaseModel):
    stages: list  # list of {key, label, color}


@router.put("/stages")
def update_stages(
    req: StagesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Save custom pipeline stages for this org. org_admin+ only."""
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    if not req.stages:
        raise HTTPException(status_code=400, detail="Must have at least one stage")
    for s in req.stages:
        if not s.get("key") or not s.get("label"):
            raise HTTPException(status_code=400, detail="Each stage needs a key and label")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    org.crm_stages = json.dumps(req.stages)
    db.commit()
    return {"saved": True, "stages": req.stages}


@router.delete("/stages/reset")
def reset_stages(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Reset stages back to the industry default."""
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    org.crm_stages = None
    db.commit()
    default = INDUSTRY_STAGES.get(org.industry or "funeral", GENERIC_STAGES)
    return {"reset": True, "stages": default}


# ── Custom fields endpoints ────────────────────────────────────────────────

@router.get("/custom-fields")
def get_custom_fields(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Return this org's custom field schema."""
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        return []
    return _get_org_custom_fields(org)


class CustomFieldsUpdate(BaseModel):
    fields: list  # list of {key, label, type, options?}


@router.put("/custom-fields")
def update_custom_fields(
    req: CustomFieldsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Save custom field schema for this org. org_admin+ only."""
    if not _is_manager(current_user):
        raise HTTPException(status_code=403, detail="Admin access required")
    for f in req.fields:
        if not f.get("key") or not f.get("label") or not f.get("type"):
            raise HTTPException(status_code=400, detail="Each field needs key, label, and type")
        if f["type"] not in ("text", "number", "dropdown", "date"):
            raise HTTPException(status_code=400, detail=f"Invalid field type: {f['type']}")
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Org not found")
    org.crm_custom_fields = json.dumps(req.fields)
    db.commit()
    return {"saved": True, "fields": req.fields}


# ── Contact CRUD ───────────────────────────────────────────────────────────

@router.get("/contacts")
def list_contacts(
    stage: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    q = _base_query(db, current_user)
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
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    result = _contact_dict(contact)
    if contact.lead_id:
        lead = authorized_lead_query(db, current_user).filter(Lead.id == contact.lead_id).first()
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


@router.post("/contacts")
def create_contact(
    req: ContactCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    stages = _get_org_stages(org) if org else GENERIC_STAGES
    default_stage = stages[0]["key"] if stages else "new_lead"
    contact = CRMContact(
        organization_id=current_user.organization_id,
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


@router.patch("/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    req: ContactUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
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


@router.delete("/contacts/{contact_id}")
def archive_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact.is_archived = True
    contact.updated_at = datetime.utcnow()
    db.commit()
    return {"archived": True}


# ── Notes ─────────────────────────────────────────────────────────────────

@router.get("/contacts/{contact_id}/notes")
def get_notes(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    notes = db.query(CRMNote).filter(CRMNote.contact_id == contact_id).order_by(CRMNote.created_at.desc()).all()
    return [{"id": n.id, "content": n.content, "created_at": n.created_at.isoformat() if n.created_at else None, "author_id": n.author_id} for n in notes]


class NoteCreate(BaseModel):
    content: str


@router.post("/contacts/{contact_id}/notes")
def add_note(
    contact_id: str,
    req: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    contact = _base_query(db, current_user).filter(CRMContact.id == contact_id).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    note = CRMNote(contact_id=contact_id, author_id=current_user.id, content=req.content.strip())
    db.add(note)
    contact.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(note)
    return {"id": note.id, "content": note.content, "created_at": note.created_at.isoformat() if note.created_at else None, "author_id": note.author_id}


# ── Lead sync ─────────────────────────────────────────────────────────────

@router.post("/contacts/from-lead/{lead_id}")
def create_from_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    existing = db.query(CRMContact).filter(
        CRMContact.lead_id == lead_id,
        CRMContact.organization_id == current_user.organization_id,
        CRMContact.is_archived == False,
    ).first()
    if existing:
        return {**_contact_dict(existing), "already_existed": True}
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    stages = _get_org_stages(org) if org else GENERIC_STAGES
    default_stage = stages[0]["key"] if stages else "new_lead"
    contact = CRMContact(
        organization_id=current_user.organization_id,
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


@router.post("/sync-from-leads")
def sync_leads_to_crm(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """
    Bulk-sync all org leads into the CRM. Safe to call multiple times.
    """
    existing_lead_ids = {
        row.lead_id
        for row in db.query(CRMContact.lead_id).filter(
            CRMContact.organization_id == current_user.organization_id,
            CRMContact.lead_id.isnot(None),
            CRMContact.is_archived == False,
        ).all()
        if row.lead_id
    }
    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    stages = _get_org_stages(org) if org else GENERIC_STAGES
    default_stage = stages[0]["key"] if stages else "new_lead"

    leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.is_duplicate == False,
        )
        .all()
    )

    created = 0
    for lead in leads:
        if lead.id in existing_lead_ids:
            continue
        contact = CRMContact(
            organization_id=current_user.organization_id,
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
