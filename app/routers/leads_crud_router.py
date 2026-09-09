import logging
import os
import shutil
import tempfile
import json as _json
from fastapi import (
    APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, Request, Response,
)
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, require_tenant_user, require_tenant_or_observer
from app.limiter import limiter
from app.services.platform_owner import require_tenant_context
from app.models.models import User, Lead, Reply, ReplyClassification, CadenceState, BookingLink, EngagementTemperature, CRMContact, VoiceCall
from app.services.import_service import import_leads_from_excel
from app.services.import_permissions import require_import_stage, require_import_commit
from app.services.import_staging_service import stage_batch as _stage_batch
from app.services.import_commit_service import commit_batch as _commit_batch_svc
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportStagedRow,
    ImportRowReviewStatus, ImportDuplicateStatus, ImportValidationStatus,
)
from app.models.models import gen_uuid
from app.services.dedup_service import normalize_phone
from app.routers.audit_log_router import log_action
# THE ONE AUTHORIZED LEAD SCOPE. Every list, count, search, export and
# single-record fetch in this file goes through it, so the advisor boundary is
# stated once instead of re-derived per route.
from app.services import lead_scope
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter()


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)


class ManualLeadCreate(BaseModel):
    first_name: str
    last_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    tier: Optional[str] = "pre_need"
    source_year: Optional[int] = None
    notes: Optional[str] = None


@router.post("/create", status_code=201)
def create_lead_manually(
    payload: ManualLeadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_context),
):
    """
    Create a single lead manually from the Leads page UI.
    Runs through dedup check against existing org leads.
    """
    import uuid
    from app.services.dedup_service import normalize_phone, normalize_last_name

    phone_normalized = normalize_phone(payload.phone or "")
    last_name_normalized = normalize_last_name(payload.last_name or "")  # was: return value discarded

    # DEDUP ON PHONE **AND LAST NAME**, not on phone alone.
    #
    # This matched on phone only. dedup_service is explicit that phone-only
    # matching is wrong - "a phone number can represent two different real
    # people in the same household (e.g. father and son sharing a landline)" -
    # and the registry it owns keys on phone + last name for exactly that
    # reason. This endpoint quietly did the opposite.
    #
    # The result: every manually added lead on a number the org had ever used
    # was flagged a duplicate of an unrelated person. Ashton Jamon was flagged
    # against Jennifer Breeder purely because they share a phone number, and
    # any new test lead on a previously-texted number was unusable on creation.
    #
    # A lead a human has already resolved with "keep separate" is not re-matched.
    is_dup = False
    dup_of = None
    if phone_normalized and last_name_normalized:
        for existing in db.query(Lead).filter(
            Lead.organization_id == lead_scope.active_workspace_org_id(current_user, db),
            Lead.phone == phone_normalized,
            Lead.is_duplicate == False,
            Lead.duplicate_resolved_at.is_(None),
        ).all():
            if normalize_last_name(existing.last_name or "") == last_name_normalized:
                is_dup = True
                dup_of = existing.id
                break

    # PLAN CAPACITY - USER-INITIATED, SO REFUSED CLEANLY.
    #
    # Somebody is sitting at the Leads screen and clicked Add. They are
    # present, they can be told, and they can act on it - so this returns a
    # structured PLAN_CAPACITY_REACHED naming the resource, the current count
    # and the limit, rather than silently holding a lead they think they just
    # created. Holding is for arrivals nobody is watching.
    from app.models.models import Organization
    from app.services import lead_capacity
    _org = (db.query(Organization)
            .filter(Organization.id == current_user.organization_id).first())
    lead_capacity.require_capacity_user_initiated(db, _org, adding=1)

    lead = Lead(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        assigned_to_id=current_user.id,
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        phone=phone_normalized or payload.phone,
        phone_raw=payload.phone,
        email=payload.email,
        tier=payload.tier,
        status="new",
        contact_channel="sms" if payload.phone else "email_only",
        source_year=payload.source_year,
        source_file="manual",
        is_duplicate=is_dup,
        duplicate_of_lead_id=dup_of,
        duplicate_reason="manual_add_phone_last_name" if is_dup else None,
        duplicate_match_field="phone+last_name" if is_dup else None,
        duplicate_match_value=phone_normalized if is_dup else None,
        notes=payload.notes,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    log_action(db, current_user.organization_id, current_user.id, action="lead.create_manual", target_type="lead", target_id=lead.id)

    # Auto-create a CRM contact so this lead shows up in the CRM immediately.
    # Silently skip if one already exists (shouldn't happen for a brand-new lead, but defensive).
    try:
        already_in_crm = db.query(CRMContact).filter(
            CRMContact.lead_id == lead.id,
            CRMContact.organization_id == current_user.organization_id,
            CRMContact.is_archived == False,
        ).first()
        if not already_in_crm:
            crm_contact = CRMContact(
                organization_id=current_user.organization_id,
                first_name=lead.first_name,
                last_name=lead.last_name,
                phone=lead.phone,
                email=lead.email,
                stage="inquiry",
                lead_id=lead.id,
                assigned_to_id=lead.assigned_to_id or current_user.id,
            )
            db.add(crm_contact)
            db.commit()
    except Exception:
        pass  # CRM creation is best-effort; lead was already committed

    return {
        "id": lead.id,
        "name": f"{lead.first_name} {lead.last_name}",
        "is_duplicate": is_dup,
        "status": "created",
    }


# ── Edit basic lead fields ────────────────────────────────────────────────────

class LeadFieldUpdate(BaseModel):
    # EXTRAS ARE ACCEPTED SO THEY CAN BE REFUSED.
    #
    # By default pydantic DISCARDS a field the model does not declare, which
    # meant `{"assigned_to_id": "<other advisor>"}` was silently dropped: the
    # lead did not move, but nothing was reported either. Silent success is the
    # wrong answer to an attempt to reassign somebody else's lead - it looks
    # identical to a normal edit in the logs, and it leaves the caller believing
    # the field is simply not implemented yet rather than forbidden.
    #
    # Allowing extras puts them in `model_fields_set`, which is exactly what
    # `reject_ownership_fields` inspects. Undeclared fields that are NOT
    # ownership fields keep their old behaviour: ignored.
    model_config = {"extra": "allow"}

    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    tier: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    relationship_type: Optional[str] = None  # AI familiarity guardrail


@router.patch("/{lead_id}")
def update_lead_fields(
    lead_id: str,
    payload: LeadFieldUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Edit basic contact fields on a lead. Advisors can edit their own; admins can edit any."""
    # OWNERSHIP IS NOT AN EDITABLE FIELD. Checked BEFORE the lead is loaded, so
    # an advisor probing another advisor's id with a reassignment payload gets
    # the same answer whether or not that lead exists.
    reject_ownership_fields(current_user, payload, request)

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # ALLOWLIST, not a denylist. This was `role == "advisor"`, which meant any
    # role outside the ladder (e.g. the grantable-but-unguarded "viewer", or any
    # future role) silently skipped the ownership check and could edit every
    # lead in the org. Name the roles allowed to bypass; everyone else is owner-only.
    if current_user.role not in ("org_admin", "super_admin", "god_admin") \
            and lead.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only edit your own leads")

    if payload.first_name is not None:
        lead.first_name = payload.first_name.strip() or lead.first_name
    if payload.last_name is not None:
        lead.last_name = payload.last_name.strip() or lead.last_name
    if payload.phone is not None:
        normalized = normalize_phone(payload.phone.strip())
        lead.phone = normalized or payload.phone.strip() or None
        try:
            lead.phone_raw = payload.phone.strip() or None
        except Exception:
            pass
        try:
            lead.contact_channel = "sms" if lead.phone else ("email_only" if lead.email else "unknown")
        except Exception:
            pass
    if payload.email is not None:
        lead.email = payload.email.strip() or None
    if payload.notes is not None:
        lead.notes = payload.notes
    if payload.tier is not None:
        lead.tier = payload.tier
    if payload.street_address is not None:
        lead.street_address = payload.street_address.strip() or None
    if payload.city is not None:
        lead.city = payload.city.strip() or None
    if payload.state is not None:
        lead.state = payload.state.strip() or None
    if payload.zip_code is not None:
        lead.zip_code = payload.zip_code.strip() or None
    if payload.relationship_type is not None:
        valid_rel_types = {"cold_lead", "warm_lead", "re_engagement", "previous_prospect", "past_customer", "existing_customer"}
        if payload.relationship_type in valid_rel_types:
            lead.relationship_type = payload.relationship_type

    try:
        lead.updated_at = datetime.utcnow()
    except Exception:
        pass

    db.commit()
    db.refresh(lead)

    try:
        log_action(db, current_user.organization_id, current_user.id,
                   action="lead.update", target_type="lead", target_id=lead_id)
    except Exception:
        pass

    return {
        "id": lead.id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "notes": getattr(lead, "notes", None),
        "tier": lead.tier,
        "street_address": getattr(lead, "street_address", None),
        "city": getattr(lead, "city", None),
        "state": getattr(lead, "state", None),
        "zip_code": getattr(lead, "zip_code", None),
        "relationship_type": getattr(lead, "relationship_type", "cold_lead"),
    }


# ── Delete a single lead ──────────────────────────────────────────────────────

@router.delete("/{lead_id}")
def delete_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Permanently delete a single lead. Advisors can delete their own leads; admins can delete any."""
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # ALLOWLIST, not a denylist — same reasoning as the edit guard above.
    if current_user.role not in ("org_admin", "super_admin", "god_admin") \
            and lead.assigned_to_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own leads")

    log_action(db, current_user.organization_id, current_user.id, action="lead.delete", target_type="lead", target_id=lead_id)
    db.delete(lead)
    db.commit()
    return {"deleted": True, "id": lead_id}


# ── Update lead type / AI direction ──────────────────────────────────────────

class LeadTypeUpdate(BaseModel):
    lead_type: Optional[str] = None   # file_check, code_lead, new_inquiry, referral, web_lead, etc.
    ai_direction: Optional[str] = None  # free-text instruction for AI messaging this lead

@router.patch("/{lead_id}/lead-type")
def update_lead_type(
    lead_id: str,
    payload: LeadTypeUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """Set the lead type and/or AI direction override for a lead."""
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if payload.lead_type is not None:
        lead.message_track = payload.lead_type
    if payload.ai_direction is not None:
        lead.notes = (lead.notes or "") + f"\n[AI Direction]: {payload.ai_direction}"
    lead.updated_at = datetime.utcnow()
    db.commit()
    log_action(db, current_user.organization_id, current_user.id, action="lead.update_type", target_type="lead", target_id=lead_id)
    return {"updated": True}


class FlagLeadRequest(BaseModel):
    flag_type: Optional[str] = None   # "bad_email" | "remove_all" | null (unflag)
    reason: Optional[str] = None      # optional note from advisor


@router.patch("/{lead_id}/flag")
def flag_lead(
    lead_id: str,
    payload: FlagLeadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Any advisor in any org can manually flag a lead the auto-detection missed.
    flag_type = "bad_email"   → hide from email queue + email campaigns, SMS still ok
    flag_type = "remove_all"  → hide from all outreach lists everywhere
    flag_type = None          → unflag, fully restore to all lists
    """
    # Advisors can only flag leads in their own org for security
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    valid_flags = {None, "bad_email", "remove_all"}
    if payload.flag_type not in valid_flags:
        raise HTTPException(status_code=400, detail=f"flag_type must be one of: bad_email, remove_all, or null to unflag")

    lead.manual_flag = payload.flag_type
    lead.manual_flag_reason = payload.reason if payload.flag_type else None
    lead.updated_at = datetime.utcnow()
    db.commit()

    action = "lead.unflag" if not payload.flag_type else f"lead.flag.{payload.flag_type}"
    log_action(db, current_user.organization_id, current_user.id, action=action, target_type="lead", target_id=lead_id)
    return {"flagged": bool(payload.flag_type), "flag_type": payload.flag_type}


# ── PUBLIC: Demo request from bookaboost.live (no auth required) ─────────────
# Called by the "Request a Demo" form on the bookaboost.live marketing site.
# Stores a lead record in the default org and fires a notification email to
# every address in the LEAD_NOTIFY_EMAILS env var (comma-separated, set in Render).

