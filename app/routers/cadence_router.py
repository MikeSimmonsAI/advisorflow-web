from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_admin
from app.models.models import User, Lead, CadenceState, CadenceStatus
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


@router.get("/health-summary")
def cadence_health_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Advisor-scoped cadence health for the Overview gauge.

    Formula: health_score = healthy_active_count / active_count * 100.
    An active cadence is considered healthy when next_touch_due_at is not set
    (nothing scheduled yet, so nothing can be overdue) OR is not yet due at
    request time. If there are no active cadences, the score is 0 rather than
    a fictional perfect score.
    """
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

    counts = {status.value: 0 for status in CadenceStatus}
    active_count = 0
    healthy_active_count = 0
    overdue_active_count = 0

    for state in states:
        status_key = state.status.value if state.status else "unknown"
        if status_key in counts:
            counts[status_key] += 1
        if state.status == CadenceStatus.ACTIVE:
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
        "formula": "healthy_active_count / active_count * 100; active cadence is healthy when next_touch_due_at is unset or not yet due",
    }


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
            "tier": lead.tier,
            "current_touch_number": state.current_touch_number,
            "total_touches": 9,
            "next_touch_due_at": state.next_touch_due_at,
            "cadence_started_at": state.cadence_started_at,
        })
    return results
