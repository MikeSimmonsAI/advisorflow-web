from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, EmailMessage
from app.services.email_service import send_email_to_lead, send_email_batch

router = APIRouter(prefix="/email", tags=["email"])


class EmailBatchRequest(BaseModel):
    lead_ids: list[str]


@router.post("/send/{lead_id}")
def send_single_email(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    try:
        msg = send_email_to_lead(db, current_user, lead)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"email_id": msg.id, "status": msg.status}


@router.post("/send-batch")
def send_email_batch_endpoint(req: EmailBatchRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids),
        Lead.organization_id == current_user.organization_id,
        Lead.contact_channel == "email_only",
    ).all()
    result = send_email_batch(db, current_user, leads)
    return result


@router.get("/queue")
def email_only_queue(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Leads with no phone, queued for email outreach, not yet emailed."""
    leads = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
        Lead.contact_channel == "email_only",
        Lead.status == "new",
    ).all()
    return leads
