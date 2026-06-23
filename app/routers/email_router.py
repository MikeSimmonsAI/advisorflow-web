from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, EmailMessage, MessageTrack
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


class EmailPreviewItem(BaseModel):
    lead_id: str
    lead_name: str
    email: str | None
    tier: str | None
    message_track: str | None
    draft_subject: str
    draft_body_html: str
    skip_reason: str | None = None


class EmailPreviewRequest(BaseModel):
    lead_ids: list[str]


@router.post("/preview-batch", response_model=list[EmailPreviewItem])
def preview_email_batch(
    req: EmailPreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Review-before-send for email, matching the same pattern
    /leads/preview-messages already gives SMS - see MessageReview.jsx.
    Previously /email/send-batch sent immediately with zero preview at
    all; this is the actual review step, drafting the real subject/body
    per lead WITHOUT sending anything, so the advisor sees exactly what
    will go out and can still send via /email/confirm-send-batch below
    once they've reviewed it.
    """
    from app.services.email_service import render_email
    from app.services.sms_service import create_booking_link, BOOKING_BASE_URL

    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids), Lead.organization_id == current_user.organization_id
    ).all()
    found_by_id = {l.id: l for l in leads}

    results = []
    for lead_id in req.lead_ids:
        lead = found_by_id.get(lead_id)
        if not lead:
            continue

        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "(no name)"
        skip_reason = None
        subject, body_html = "", ""

        if not lead.email:
            skip_reason = "No email address on file"
        else:
            # Same placeholder-link approach as the SMS preview - a real
            # booking link isn't created until actual send, so an edited
            # or skipped preview doesn't leave a dead link behind.
            placeholder_booking_url = f"{BOOKING_BASE_URL}/book/preview"
            track = lead.message_track or MessageTrack.EMAIL_ONLY_NURTURE
            rendered = render_email(db, track, lead, current_user, placeholder_booking_url)
            subject, body_html = rendered["subject"], rendered["body_html"]

        results.append(EmailPreviewItem(
            lead_id=lead.id, lead_name=lead_name, email=lead.email,
            tier=lead.tier.value if lead.tier else None,
            message_track=lead.message_track.value if lead.message_track else None,
            draft_subject=subject, draft_body_html=body_html, skip_reason=skip_reason,
        ))

    return results


class ConfirmEmailItem(BaseModel):
    lead_id: str
    subject: str  # the (possibly edited) final subject for this lead
    body_html: str  # the (possibly edited) final body for this lead


class ConfirmEmailBatchRequest(BaseModel):
    items: list[ConfirmEmailItem]


@router.post("/confirm-send-batch")
def confirm_email_send_batch(
    req: ConfirmEmailBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The actual send step, after the advisor has reviewed (and possibly
    edited) the drafted subject/body from /email/preview-batch. Each item
    carries its own final subject+body, since the advisor may have
    edited individual ones. Mirrors /leads/confirm-send-batch for SMS.
    """
    from app.services.sms_service import create_booking_link
    from app.services.email_service import send_email_via_provider
    from app.models.models import EmailMessage
    import os as _os

    sent_ids, failed_ids, skipped_ids = [], [], []

    for item in req.items:
        lead = db.query(Lead).filter(
            Lead.id == item.lead_id, Lead.organization_id == current_user.organization_id
        ).first()
        if not lead or not lead.email:
            skipped_ids.append(item.lead_id)
            continue

        booking = create_booking_link(db, lead, current_user)
        booking_url = f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/{booking.token}"
        # The advisor may have left the preview's placeholder link in the
        # edited text untouched - swap it for the real one at send time,
        # same as the SMS confirm-send-batch does for {booking_link}.
        final_subject = item.subject.replace(f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/preview", booking_url)
        final_body = item.body_html.replace(f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/preview", booking_url)

        if current_user.microsoft_365_connected:
            from app.services.microsoft_email_service import send_email_via_microsoft_graph
            result = send_email_via_microsoft_graph(current_user, lead.email, final_subject, final_body)
        else:
            result = send_email_via_provider(lead.email, final_subject, final_body)

        email_msg = EmailMessage(
            lead_id=lead.id, sender_id=current_user.id,
            subject=final_subject, body_html=final_body,
            provider_message_id=result.get("provider_message_id"),
            status="sent" if result["success"] else "failed",
        )
        db.add(email_msg)

        if result["success"]:
            lead.status = "sent"
            sent_ids.append(lead.id)
        else:
            failed_ids.append(lead.id)

    db.commit()
    return {"sent_count": len(sent_ids), "failed_count": len(failed_ids), "skipped_count": len(skipped_ids)}


@router.get("/sent")
def email_sent_history(
    search: str | None = Query(default=None, description="Optional partial name or email lookup."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Leads this advisor has already emailed - previously these just
    vanished from /email/queue the moment they got sent (queue filters
    Lead.status == 'new'), with no way to look back at who'd already
    been contacted. Joins EmailMessage so the most recent send per lead
    is visible alongside the lead info.
    """
    query = (
        db.query(Lead, EmailMessage)
        .join(EmailMessage, EmailMessage.lead_id == Lead.id)
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            EmailMessage.sender_id == current_user.id,
        )
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

    rows = query.order_by(EmailMessage.sent_at.desc()).limit(200).all()

    # One row per EmailMessage, not deduplicated to one-per-lead - if a
    # lead's been emailed multiple times, each send is its own entry in
    # the sent history, same as how the SMS Replies inbox shows every
    # individual message rather than collapsing to one row per lead.
    return [
        {
            "lead_id": lead.id,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "email": lead.email,
            "phone": lead.phone,
            "subject": email_msg.subject,
            "status": email_msg.status,
            "sent_at": email_msg.sent_at,
        }
        for lead, email_msg in rows
    ]


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
