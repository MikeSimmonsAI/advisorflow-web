from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.models import User, Lead, CadenceState
from app.services.cadence_service import (
    start_cadence, run_due_cadences, get_cadence_summary,
)

router = APIRouter(prefix="/cadence", tags=["cadence"])

VALID_STATUSES = {"active", "paused", "completed", "cancelled"}


class CadenceControlRequest(BaseModel):
    action: str  # "pause" | "resume" | "cancel"


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
    result = run_due_cadences(db, organization_id=current_user.organization_id)
    return result


@router.post("/{cadence_state_id}/control")
def control_cadence(
    cadence_state_id: str,
    req: CadenceControlRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pause, resume, or cancel an individual lead's cadence."""
    state = (
        db.query(CadenceState)
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            CadenceState.id == cadence_state_id,
            Lead.organization_id == current_user.organization_id,
        )
        .first()
    )
    if not state:
        raise HTTPException(status_code=404, detail="Cadence not found")

    if req.action == "pause":
        if state.status != "active":
            raise HTTPException(status_code=400, detail="Only active cadences can be paused")
        state.status = "paused"
    elif req.action == "resume":
        if state.status != "paused":
            raise HTTPException(status_code=400, detail="Only paused cadences can be resumed")
        state.status = "active"
    elif req.action == "cancel":
        if state.status not in ("active", "paused"):
            raise HTTPException(status_code=400, detail="Only active or paused cadences can be cancelled")
        state.status = "cancelled"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    db.commit()
    return {"cadence_state_id": cadence_state_id, "status": state.status, "action": req.action}


@router.get("/summary")
def cadence_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return get_cadence_summary(db, current_user.organization_id)


@router.get("/health-summary")
def cadence_health_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    states = (
        db.query(CadenceState)
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
        )
        .all()
    )

    # CadenceState.status is plain VARCHAR — never call .value on it
    counts = {s: 0 for s in ("active", "paused", "completed", "cancelled")}
    active_count = 0
    healthy_active_count = 0
    overdue_active_count = 0

    for state in states:
        status_key = state.status if state.status else "unknown"
        if status_key in counts:
            counts[status_key] += 1
        if state.status == "active":
            active_count += 1
            if state.next_touch_due_at is None or state.next_touch_due_at >= now:
                healthy_active_count += 1
            else:
                overdue_active_count += 1

    health_score = round((healthy_active_count / active_count) * 100, 2) if active_count else 0

    return {
        "counts": counts,
        "active_count": active_count,
        "healthy_active_count": healthy_active_count,
        "overdue_active_count": overdue_active_count,
        "health_score": health_score,
    }


@router.get("/active")
def list_active_cadences(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    states = (
        db.query(CadenceState)
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            CadenceState.status.in_(["active", "paused"]),
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
            "tier": lead.tier if lead.tier else None,
            "status": state.status,
            "current_touch_number": state.current_touch_number,
            "total_touches": 9,
            "next_touch_due_at": state.next_touch_due_at,
            "cadence_started_at": state.cadence_started_at,
        })
    return results
