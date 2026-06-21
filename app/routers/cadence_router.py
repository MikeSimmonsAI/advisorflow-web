from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.models import User, Lead, CadenceState
from app.services.cadence_service import (
    start_cadence, run_due_cadences, get_cadence_summary,
)

router = APIRouter(prefix="/cadence", tags=["cadence"])


@router.post("/start/{lead_id}")
def start_lead_cadence(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    state = start_cadence(db, lead)
    if not state:
        return {"started": False, "reason": "Lead is excluded from cadence (DNC, duplicate, needs review, or email-only)"}
    return {"started": True, "next_touch_due_at": state.next_touch_due_at}


@router.post("/start-batch")
def start_batch_cadence(lead_ids: list[str], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    leads = db.query(Lead).filter(
        Lead.id.in_(lead_ids), Lead.organization_id == current_user.organization_id
    ).all()
    started, skipped = 0, 0
    for lead in leads:
        state = start_cadence(db, lead)
        if state:
            started += 1
        else:
            skipped += 1
    return {"started": started, "skipped": skipped}


@router.post("/run-due")
def run_due(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Manually triggers the cadence job for the current org. In production
    this should run on a schedule (Render cron job hitting this endpoint,
    or a background worker loop) rather than being click-triggered every
    time - exposed here for testing/manual runs during the proof of concept.
    """
    result = run_due_cadences(db, organization_id=current_user.organization_id)
    return result


@router.get("/summary")
def cadence_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_cadence_summary(db, current_user.organization_id)


@router.get("/active")
def list_active_cadences(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Returns every lead currently in an active cadence for the current
    advisor, with their touch progress and next-due date - the detail
    view behind the summary counts shown on the Cadence dashboard, so
    an advisor can actually see WHO is queued up, not just how many.
    """
    from app.models.models import CadenceState, CadenceStatus, Lead

    states = (
        db.query(CadenceState)
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            CadenceState.status == CadenceStatus.ACTIVE,
        )
        .order_by(CadenceState.next_touch_due_at.asc())
        .all()
    )

    results = []
    for state in states:
        lead = state.lead
        results.append({
            "cadence_state_id": state.id,
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name or ''} {lead.last_name or ''}".strip(),
            "phone": lead.phone,
            "tier": lead.tier.value if lead.tier else None,
            "current_touch_number": state.current_touch_number,
            "total_touches": 9,
            "next_touch_due_at": state.next_touch_due_at,
            "cadence_started_at": state.cadence_started_at,
        })
    return results
