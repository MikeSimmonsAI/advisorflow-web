from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, EmailMessage
from app.services.email_service import send_email_to_lead, send_email_batch

router = APIRouter(prefix="/email", tags=["email"])


class EmailBatchRequest(BaseModel):
    lead_ids: list[str]


class SingleEmailRequest(BaseModel):
    subject: Optional[str] = None
    body: Optional[str] = None
    include_booking_link: bool = True


@router.post("/send/{lead_id}")
def send_single_email(
    lead_id: str,
    req: SingleEmailRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # If custom body provided, use it directly
    if req and req.body:
        body_html = req.body.replace('\n', '<br>')

        # Append booking link if requested
        if req.include_booking_link:
            from app.services.sms_service import create_booking_link
            import os
            booking_link = create_booking_link(db, lead, current_user)
            booking_url = f"{os.environ.get('BOOKING_BASE_URL', 'https://advisorflow-booking.vercel.app')}/book/{booking_link.token}"
            body_html += f'<br><br><a href="{booking_url}">📅 Book an appointment with me</a>'

        subject = req.subject or f"Following up, {lead.first_name or 'there'}"

        # Route through Microsoft 365 if connected — never use SendGrid when advisor has M365
        if current_user.microsoft_365_connected:
            from app.services.microsoft_email_service import send_email_via_microsoft_graph
            result = send_email_via_microsoft_graph(current_user, lead.email, subject, body_html)
        else:
            from app.services.email_service import send_email_via_provider
            result = send_email_via_provider(lead.email, subject, body_html)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error", "Email send failed. Check your Microsoft 365 connection in Settings."))

        from app.models.models import EmailMessage
        from datetime import datetime
        msg = EmailMessage(
            lead_id=lead.id,
            sender_id=current_user.id,
            subject=subject,
            body_html=body_html,
            status="sent",
            provider_message_id=result.get("provider_message_id"),
            sent_at=datetime.utcnow(),
        )
        db.add(msg)
        lead.status = "sent"
        db.commit()
        return {"email_id": msg.id, "status": "sent"}

    # Fallback to template-based send
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
def email_only_queue(
    search: str | None = Query(default=None, description="Optional partial name or email lookup."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Leads routed to email outreach for the logged-in advisor.

    Email-only leads can still have a phone number on file from the raw CRM
    import, so keep `phone` in the response and let the UI display it when
    present. Search is intentionally scoped after org/advisor/channel filters.
    """
    query = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
        Lead.contact_channel == "email_only",
        Lead.status == "new",
    )

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(
            or_(
                Lead.first_name.ilike(term),
                Lead.last_name.ilike(term),
                Lead.email.ilike(term),
            )
        )

    return query.order_by(Lead.created_at.desc(), Lead.last_name.asc(), Lead.first_name.asc()).all()


# ── Email with flyer/attachment ───────────────────────────────────────────────

from fastapi import UploadFile, File, Form
import base64, os

class EmailWithAttachmentRequest(BaseModel):
    lead_id: str
    subject: str
    body_html: str


@router.post("/send-with-attachment/{lead_id}")
async def send_email_with_attachment(
    lead_id: str,
    subject: str = Form(...),
    body_html: str = Form(...),
    file: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Send an email to a lead with an optional flyer/image attachment.
    Accepts multipart form: subject, body_html, and optional file upload.
    """
    from app.services.email_service import send_email_via_provider

    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.email:
        raise HTTPException(status_code=400, detail="Lead has no email address")

    attachments = []
    if file and file.filename:
        file_bytes = await file.read()
        attachments.append({
            "filename": file.filename,
            "content": base64.b64encode(file_bytes).decode(),
            "content_type": file.content_type or "application/octet-stream",
        })

    result = send_email_via_provider(lead.email, subject, body_html, attachments=attachments or None)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Email send failed"))

    # Log it
    from app.models.models import EmailMessage
    from datetime import datetime
    msg = EmailMessage(
        lead_id=lead.id,
        sender_id=current_user.id,
        subject=subject,
        body_html=body_html,
        status="sent",
        provider_message_id=result.get("provider_message_id"),
        sent_at=datetime.utcnow(),
    )
    db.add(msg)
    db.commit()
    return {"email_id": msg.id, "status": "sent", "has_attachment": bool(attachments)}


# ── AI email draft — talking points + 3 options ───────────────────────────────

class EmailDraftRequest(BaseModel):
    tone: str = "warm"
    ai_direction: Optional[str] = None


@router.post("/draft/{lead_id}")
def draft_email(
    lead_id: str,
    req: EmailDraftRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    AI generates talking points + 3 full email draft options for a lead.
    Uses the lead's full context (tier, source year, last action, etc.)
    to personalize — not a generic template.
    """
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    from app.services.draft_reply_service import draft_email_options
    tone = (req.tone if req else "warm")
    ai_direction = (req.ai_direction if req else None)

    return draft_email_options(db, lead, current_user, tone=tone, ai_direction=ai_direction)
