"""
Outcome Tracker router - the "what does this family have/not have"
feature Mike specifically asked for. After a file review or
appointment, the advisor (or Mike) records what was confirmed, so the
NEXT outreach message can be specific (e.g. "let's talk about your
marker") instead of generic, and so the org-wide sales analytics later
(step 6 of the build plan) can break down real outcomes, not just
reply/booking counts.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, LeadOutcome

router = APIRouter(prefix="/outcomes", tags=["outcomes"])


class RecordOutcomeRequest(BaseModel):
    lead_id: str
    booking_link_id: str | None = None
    appointment_date: datetime | None = None
    has_funeral_arrangement: bool | None = None
    has_cemetery_property: bool | None = None
    has_marker: bool | None = None
    has_memorial: bool | None = None
    has_open_closed_status: str | None = None  # "open" | "closed" | None
    resulted_in_sale: bool = False
    sale_items: str | None = None
    sale_amount: str | None = None
    notes: str | None = None


class OutcomeResponse(BaseModel):
    id: str
    lead_id: str
    recorded_by_id: str
    appointment_date: datetime | None
    has_funeral_arrangement: bool | None
    has_cemetery_property: bool | None
    has_marker: bool | None
    has_memorial: bool | None
    has_open_closed_status: str | None
    resulted_in_sale: bool
    sale_items: str | None
    sale_amount: str | None
    notes: str | None
    created_at: datetime


def _get_lead_or_404(db: Session, lead_id: str, organization_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.post("/", response_model=OutcomeResponse)
def record_outcome(
    req: RecordOutcomeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Records a new outcome entry for a lead - one row per visit/appointment,
    not an overwrite of a single record, so history across multiple
    visits is preserved (see LeadOutcome model docstring for why).
    """
    _get_lead_or_404(db, req.lead_id, current_user.organization_id)

    if req.has_open_closed_status and req.has_open_closed_status not in ("open", "closed"):
        raise HTTPException(status_code=400, detail="has_open_closed_status must be 'open', 'closed', or omitted.")

    outcome = LeadOutcome(
        lead_id=req.lead_id,
        recorded_by_id=current_user.id,
        booking_link_id=req.booking_link_id,
        appointment_date=req.appointment_date,
        has_funeral_arrangement=req.has_funeral_arrangement,
        has_cemetery_property=req.has_cemetery_property,
        has_marker=req.has_marker,
        has_memorial=req.has_memorial,
        has_open_closed_status=req.has_open_closed_status,
        resulted_in_sale=req.resulted_in_sale,
        sale_items=req.sale_items,
        sale_amount=req.sale_amount,
        notes=req.notes,
    )
    db.add(outcome)
    db.commit()
    return outcome


@router.get("/lead/{lead_id}", response_model=list[OutcomeResponse])
def list_outcomes_for_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns every recorded outcome for a lead, most recent first - the
    full visit history an advisor reviews before their next
    conversation, so they know exactly what's already been confirmed
    (e.g. "this family doesn't have a marker yet") without having to
    re-ask or guess.
    """
    _get_lead_or_404(db, lead_id, current_user.organization_id)
    outcomes = (
        db.query(LeadOutcome)
        .filter(LeadOutcome.lead_id == lead_id)
        .order_by(LeadOutcome.created_at.desc())
        .all()
    )
    return outcomes


@router.get("/lead/{lead_id}/latest-gaps")
def get_latest_gaps(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns just the "what's missing" summary from the most recent
    outcome record - the quick-glance version for the Lead Detail page
    and for feeding into smarter follow-up message drafting later (a
    lead missing a marker should get marker-focused follow-up copy,
    not the generic pre-need pitch).
    """
    _get_lead_or_404(db, lead_id, current_user.organization_id)
    latest = (
        db.query(LeadOutcome)
        .filter(LeadOutcome.lead_id == lead_id)
        .order_by(LeadOutcome.created_at.desc())
        .first()
    )
    if not latest:
        return {"has_outcome_data": False}

    gaps = []
    if latest.has_funeral_arrangement is False:
        gaps.append("funeral_arrangement")
    if latest.has_cemetery_property is False:
        gaps.append("cemetery_property")
    if latest.has_marker is False:
        gaps.append("marker")
    if latest.has_memorial is False:
        gaps.append("memorial")

    return {
        "has_outcome_data": True,
        "last_recorded_at": latest.created_at,
        "gaps": gaps,  # things confirmed as NOT had - the actionable follow-up targets
    }
