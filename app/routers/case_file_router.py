"""
Appointment Case File Router
-----------------------------
Universal post-appointment case file system. Records everything from an
appointment: outcome, products discussed/sold, policy details, file-review
checklist, advisor notes, case status, and next actions. Also triggers CRM
webhook push to HubSpot, Salesforce, GoHighLevel, or any generic endpoint.

This is industry-agnostic — works for insurance, funeral, real estate, etc.
The existing outcomes_router handles the funeral-specific property checklist;
this router handles the full post-appointment workflow for any org type.

Routes:
  GET  /case-file/lead/{lead_id}          — all case files for lead
  POST /case-file/lead/{lead_id}          — create new case file
  GET  /case-file/{case_file_id}          — single case file detail
  PATCH /case-file/{case_file_id}         — update (partial)
  POST /case-file/{case_file_id}/close    — close the case (won or lost)
  POST /case-file/{case_file_id}/crm-push — push to configured CRM webhook
  GET  /case-file/summary/org             — org-wide pipeline summary
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
import uuid, json, logging

from app.deps import get_db, get_current_user
from app.models.models import User, Lead

router = APIRouter(prefix="/case-file", tags=["case-file"])
logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CASE_STATUSES = [
    "open", "pending_application", "pending_issue", "in_force",
    "closed_won", "closed_lost", "follow_up_needed", "annual_review_due", "rescheduled",
]

OUTCOME_TYPES = [
    "sold", "partial_sale", "no_sale", "needs_followup",
    "rescheduled", "no_show", "lost_to_competitor", "referred_out",
]

NEXT_ACTIONS = [
    "schedule_appointment", "restart_ai_conversation", "add_to_campaign",
    "set_reminder", "refer_to_specialist", "close_case", "none",
]

PRODUCTS = [
    "final_expense", "term_life_10yr", "term_life_20yr", "term_life_30yr",
    "whole_life", "universal_life_iul", "universal_life_vul", "universal_life_gul",
    "annuity_fixed", "annuity_fixed_indexed", "annuity_variable",
    "medicare_supplement", "medicare_advantage", "long_term_care",
    "disability_income", "dental_vision_hearing",
    "burial_preneed", "cemetery_property", "marker_monument", "memorial",
    "funeral_arrangement", "veterans_benefits", "other",
]

# ── Pydantic Schema ───────────────────────────────────────────────────────────

class CaseFileRequest(BaseModel):
    booking_link_id: Optional[str] = None
    appointment_date: Optional[datetime] = None
    appointment_type: Optional[str] = None        # in_person / phone / video / other

    outcome_type: Optional[str] = None            # sold / no_sale / etc.

    products_discussed: Optional[List[str]] = []
    products_sold: Optional[List[str]] = []

    policy_carrier: Optional[str] = None
    policy_number: Optional[str] = None
    coverage_amount: Optional[str] = None
    premium_monthly: Optional[str] = None
    premium_annual: Optional[str] = None
    application_date: Optional[datetime] = None
    issue_date: Optional[datetime] = None

    # File review checklist
    chk_id_verified: Optional[bool] = False
    chk_beneficiary_named: Optional[bool] = False
    chk_app_signed: Optional[bool] = False
    chk_payment_collected: Optional[bool] = False
    chk_illustrations_reviewed: Optional[bool] = False
    chk_medical_history: Optional[bool] = False
    chk_hipaa_signed: Optional[bool] = False
    chk_replacement_form: Optional[bool] = False
    chk_beneficiary_reviewed: Optional[bool] = False
    chk_riders_explained: Optional[bool] = False

    advisor_notes: Optional[str] = None
    objections_raised: Optional[str] = None
    client_concerns: Optional[str] = None
    referral_potential: Optional[bool] = False
    referral_notes: Optional[str] = None

    case_status: Optional[str] = "open"

    next_action: Optional[str] = None
    next_action_date: Optional[datetime] = None
    next_action_notes: Optional[str] = None


class CloseRequest(BaseModel):
    outcome: str   # "closed_won" or "closed_lost"
    close_notes: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require_lead(db: Session, lead_id: str, org_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == org_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def _row_to_dict(row) -> dict:
    d = dict(row._mapping)
    for field in ("products_discussed", "products_sold"):
        if d.get(field):
            try:
                d[field] = json.loads(d[field])
            except Exception:
                d[field] = []
        else:
            d[field] = []
    return d


def _get_case_file(db: Session, case_file_id: str, org_id: str) -> dict:
    row = db.execute(
        text("SELECT * FROM appointment_case_files WHERE id = :id AND organization_id = :org"),
        {"id": case_file_id, "org": org_id}
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Case file not found")
    return _row_to_dict(row)


async def _push_to_crm(db: Session, org_id: str, payload: dict):
    """Push case file data to all active CRM webhooks for this org."""
    import httpx
    connections = db.execute(
        text("""
            SELECT webhook_url, webhook_secret, crm_type, annotation_tag
            FROM crm_connections
            WHERE organization_id = :org AND active = TRUE AND webhook_url IS NOT NULL
        """),
        {"org": org_id}
    ).fetchall()

    results = []
    for conn in connections:
        try:
            headers = {"Content-Type": "application/json"}
            if conn.webhook_secret:
                headers["X-BookaBoost-Secret"] = conn.webhook_secret
            tagged_payload = {**payload, "source": "BookaBoost", "tag": conn.annotation_tag or "BookaBoost"}
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(conn.webhook_url, json=tagged_payload, headers=headers)
            results.append({"crm_type": conn.crm_type, "status": r.status_code, "ok": r.is_success})
        except Exception as e:
            results.append({"crm_type": conn.crm_type, "status": 0, "ok": False, "error": str(e)})
    return results


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/lead/{lead_id}")
def list_case_files(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """All case files for a lead, most recent first."""
    _require_lead(db, lead_id, current_user.organization_id)
    rows = db.execute(
        text("""
            SELECT cf.*, u.full_name AS recorded_by_name
            FROM appointment_case_files cf
            LEFT JOIN users u ON u.id = cf.recorded_by_id
            WHERE cf.lead_id = :lead_id AND cf.organization_id = :org
            ORDER BY cf.created_at DESC
        """),
        {"lead_id": lead_id, "org": current_user.organization_id}
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


@router.post("/lead/{lead_id}")
def create_case_file(
    lead_id: str,
    req: CaseFileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new case file entry for a lead (one per appointment)."""
    lead = _require_lead(db, lead_id, current_user.organization_id)

    if req.outcome_type and req.outcome_type not in OUTCOME_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid outcome_type. Valid: {OUTCOME_TYPES}")
    if req.case_status and req.case_status not in CASE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid case_status. Valid: {CASE_STATUSES}")

    cf_id = str(uuid.uuid4())
    now = datetime.utcnow()

    db.execute(text("""
        INSERT INTO appointment_case_files (
            id, lead_id, organization_id, recorded_by_id, booking_link_id,
            appointment_date, appointment_type, outcome_type,
            products_discussed, products_sold,
            policy_carrier, policy_number, coverage_amount,
            premium_monthly, premium_annual, application_date, issue_date,
            chk_id_verified, chk_beneficiary_named, chk_app_signed,
            chk_payment_collected, chk_illustrations_reviewed, chk_medical_history,
            chk_hipaa_signed, chk_replacement_form, chk_beneficiary_reviewed, chk_riders_explained,
            advisor_notes, objections_raised, client_concerns,
            referral_potential, referral_notes,
            case_status, next_action, next_action_date, next_action_notes,
            created_at, updated_at
        ) VALUES (
            :id, :lead_id, :org, :recorded_by, :booking_link_id,
            :appointment_date, :appointment_type, :outcome_type,
            :products_discussed, :products_sold,
            :policy_carrier, :policy_number, :coverage_amount,
            :premium_monthly, :premium_annual, :application_date, :issue_date,
            :chk_id_verified, :chk_beneficiary_named, :chk_app_signed,
            :chk_payment_collected, :chk_illustrations_reviewed, :chk_medical_history,
            :chk_hipaa_signed, :chk_replacement_form, :chk_beneficiary_reviewed, :chk_riders_explained,
            :advisor_notes, :objections_raised, :client_concerns,
            :referral_potential, :referral_notes,
            :case_status, :next_action, :next_action_date, :next_action_notes,
            :created_at, :updated_at
        )
    """), {
        "id": cf_id, "lead_id": lead_id, "org": current_user.organization_id,
        "recorded_by": current_user.id,
        "booking_link_id": req.booking_link_id,
        "appointment_date": req.appointment_date,
        "appointment_type": req.appointment_type,
        "outcome_type": req.outcome_type,
        "products_discussed": json.dumps(req.products_discussed or []),
        "products_sold": json.dumps(req.products_sold or []),
        "policy_carrier": req.policy_carrier,
        "policy_number": req.policy_number,
        "coverage_amount": req.coverage_amount,
        "premium_monthly": req.premium_monthly,
        "premium_annual": req.premium_annual,
        "application_date": req.application_date,
        "issue_date": req.issue_date,
        "chk_id_verified": req.chk_id_verified,
        "chk_beneficiary_named": req.chk_beneficiary_named,
        "chk_app_signed": req.chk_app_signed,
        "chk_payment_collected": req.chk_payment_collected,
        "chk_illustrations_reviewed": req.chk_illustrations_reviewed,
        "chk_medical_history": req.chk_medical_history,
        "chk_hipaa_signed": req.chk_hipaa_signed,
        "chk_replacement_form": req.chk_replacement_form,
        "chk_beneficiary_reviewed": req.chk_beneficiary_reviewed,
        "chk_riders_explained": req.chk_riders_explained,
        "advisor_notes": req.advisor_notes,
        "objections_raised": req.objections_raised,
        "client_concerns": req.client_concerns,
        "referral_potential": req.referral_potential,
        "referral_notes": req.referral_notes,
        "case_status": req.case_status or "open",
        "next_action": req.next_action,
        "next_action_date": req.next_action_date,
        "next_action_notes": req.next_action_notes,
        "created_at": now, "updated_at": now,
    })

    # Update lead.case_status to match the case file's status
    db.execute(
        text("UPDATE leads SET case_status = :cs WHERE id = :lid"),
        {"cs": req.case_status or "open", "lid": lead_id}
    )
    db.commit()

    logger.info("CaseFile created %s for lead %s by %s", cf_id, lead_id, current_user.id)
    return _get_case_file(db, cf_id, current_user.organization_id)


@router.get("/summary/org")
def org_pipeline_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Org-wide case file pipeline summary for reporting."""
    rows = db.execute(text("""
        SELECT case_status, outcome_type, COUNT(*) as count
        FROM appointment_case_files
        WHERE organization_id = :org
        GROUP BY case_status, outcome_type
        ORDER BY count DESC
    """), {"org": current_user.organization_id}).fetchall()

    total = db.execute(text(
        "SELECT COUNT(*) FROM appointment_case_files WHERE organization_id = :org"
    ), {"org": current_user.organization_id}).scalar()

    sold = db.execute(text(
        "SELECT COUNT(*) FROM appointment_case_files WHERE organization_id = :org AND outcome_type = 'sold'"
    ), {"org": current_user.organization_id}).scalar()

    return {
        "total_case_files": total or 0,
        "sold_count": sold or 0,
        "conversion_rate": round((sold / total * 100) if total else 0, 1),
        "by_status": [dict(r._mapping) for r in rows],
    }


@router.get("/{case_file_id}")
def get_case_file(
    case_file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_case_file(db, case_file_id, current_user.organization_id)


@router.patch("/{case_file_id}")
def update_case_file(
    case_file_id: str,
    req: CaseFileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update any fields on an existing case file."""
    existing = _get_case_file(db, case_file_id, current_user.organization_id)

    if req.outcome_type and req.outcome_type not in OUTCOME_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid outcome_type.")
    if req.case_status and req.case_status not in CASE_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid case_status.")

    db.execute(text("""
        UPDATE appointment_case_files SET
            appointment_date = :appointment_date,
            appointment_type = :appointment_type,
            outcome_type = :outcome_type,
            products_discussed = :products_discussed,
            products_sold = :products_sold,
            policy_carrier = :policy_carrier,
            policy_number = :policy_number,
            coverage_amount = :coverage_amount,
            premium_monthly = :premium_monthly,
            premium_annual = :premium_annual,
            application_date = :application_date,
            issue_date = :issue_date,
            chk_id_verified = :chk_id_verified,
            chk_beneficiary_named = :chk_beneficiary_named,
            chk_app_signed = :chk_app_signed,
            chk_payment_collected = :chk_payment_collected,
            chk_illustrations_reviewed = :chk_illustrations_reviewed,
            chk_medical_history = :chk_medical_history,
            chk_hipaa_signed = :chk_hipaa_signed,
            chk_replacement_form = :chk_replacement_form,
            chk_beneficiary_reviewed = :chk_beneficiary_reviewed,
            chk_riders_explained = :chk_riders_explained,
            advisor_notes = :advisor_notes,
            objections_raised = :objections_raised,
            client_concerns = :client_concerns,
            referral_potential = :referral_potential,
            referral_notes = :referral_notes,
            case_status = :case_status,
            next_action = :next_action,
            next_action_date = :next_action_date,
            next_action_notes = :next_action_notes,
            updated_at = :updated_at
        WHERE id = :id AND organization_id = :org
    """), {
        "id": case_file_id, "org": current_user.organization_id,
        "appointment_date": req.appointment_date,
        "appointment_type": req.appointment_type,
        "outcome_type": req.outcome_type,
        "products_discussed": json.dumps(req.products_discussed or []),
        "products_sold": json.dumps(req.products_sold or []),
        "policy_carrier": req.policy_carrier,
        "policy_number": req.policy_number,
        "coverage_amount": req.coverage_amount,
        "premium_monthly": req.premium_monthly,
        "premium_annual": req.premium_annual,
        "application_date": req.application_date,
        "issue_date": req.issue_date,
        "chk_id_verified": req.chk_id_verified,
        "chk_beneficiary_named": req.chk_beneficiary_named,
        "chk_app_signed": req.chk_app_signed,
        "chk_payment_collected": req.chk_payment_collected,
        "chk_illustrations_reviewed": req.chk_illustrations_reviewed,
        "chk_medical_history": req.chk_medical_history,
        "chk_hipaa_signed": req.chk_hipaa_signed,
        "chk_replacement_form": req.chk_replacement_form,
        "chk_beneficiary_reviewed": req.chk_beneficiary_reviewed,
        "chk_riders_explained": req.chk_riders_explained,
        "advisor_notes": req.advisor_notes,
        "objections_raised": req.objections_raised,
        "client_concerns": req.client_concerns,
        "referral_potential": req.referral_potential,
        "referral_notes": req.referral_notes,
        "case_status": req.case_status or existing["case_status"],
        "next_action": req.next_action,
        "next_action_date": req.next_action_date,
        "next_action_notes": req.next_action_notes,
        "updated_at": datetime.utcnow(),
    })

    # Sync lead.case_status
    if req.case_status:
        db.execute(
            text("UPDATE leads SET case_status = :cs WHERE id = :lid AND organization_id = :org"),
            {"cs": req.case_status, "lid": existing["lead_id"], "org": current_user.organization_id}
        )
    db.commit()
    return _get_case_file(db, case_file_id, current_user.organization_id)


@router.post("/{case_file_id}/close")
def close_case(
    case_file_id: str,
    req: CloseRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Close the case. Sets case_status to closed_won or closed_lost on both
    the case file AND the lead record. Stops all future AI outreach from
    targeting this lead (closed cases are excluded from campaign blasts).
    """
    if req.outcome not in ("closed_won", "closed_lost"):
        raise HTTPException(status_code=400, detail="outcome must be 'closed_won' or 'closed_lost'")

    existing = _get_case_file(db, case_file_id, current_user.organization_id)
    lead_id = existing["lead_id"]

    db.execute(text("""
        UPDATE appointment_case_files
        SET case_status = :status, advisor_notes = COALESCE(advisor_notes || E'\n\n[CLOSED] ' || :note, advisor_notes),
            updated_at = :now
        WHERE id = :id AND organization_id = :org
    """), {
        "status": req.outcome, "note": req.close_notes or "",
        "now": datetime.utcnow(), "id": case_file_id, "org": current_user.organization_id
    })

    db.execute(
        text("UPDATE leads SET case_status = :cs WHERE id = :lid AND organization_id = :org"),
        {"cs": req.outcome, "lid": lead_id, "org": current_user.organization_id}
    )
    db.commit()
    logger.info("Case %s closed as %s for lead %s", case_file_id, req.outcome, lead_id)
    return {"success": True, "case_status": req.outcome, "lead_id": lead_id}


@router.post("/{case_file_id}/crm-push")
async def crm_push(
    case_file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually push this case file to all configured CRM webhooks for this org.
    Supports HubSpot, Salesforce, GoHighLevel, or any generic webhook endpoint
    configured in Settings → CRM Integrations.
    """
    cf = _get_case_file(db, case_file_id, current_user.organization_id)

    # Enrich with lead info
    lead = db.query(Lead).filter(Lead.id == cf["lead_id"]).first()
    payload = {
        **cf,
        "lead_first_name": lead.first_name if lead else None,
        "lead_last_name": lead.last_name if lead else None,
        "lead_phone": lead.phone if lead else None,
        "lead_email": lead.email if lead else None,
        "lead_tier": lead.tier if lead else None,
        "pushed_at": datetime.utcnow().isoformat(),
    }

    results = await _push_to_crm(db, current_user.organization_id, payload)

    # Log push results
    if any(r.get("ok") for r in results):
        db.execute(text("""
            UPDATE appointment_case_files
            SET crm_synced_at = :now, crm_sync_status = 'success', updated_at = :now
            WHERE id = :id
        """), {"now": datetime.utcnow(), "id": case_file_id})
        db.commit()

    return {"results": results, "pushed_fields": list(payload.keys())}


@router.get("/constants/all")
def get_constants(current_user: User = Depends(get_current_user)):
    """Returns all valid enum values for the frontend dropdowns."""
    return {
        "case_statuses": CASE_STATUSES,
        "outcome_types": OUTCOME_TYPES,
        "next_actions": NEXT_ACTIONS,
        "products": PRODUCTS,
    }
