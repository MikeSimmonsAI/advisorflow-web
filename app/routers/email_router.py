from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timezone

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
            track = lead.message_track or "email_only_nurture"
            rendered = render_email(db, track, lead, current_user, placeholder_booking_url)
            subject, body_html = rendered["subject"], rendered["body_html"]

        results.append(EmailPreviewItem(
            lead_id=lead.id, lead_name=lead_name, email=lead.email,
            tier=lead.tier,
            message_track=lead.message_track,
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
    from app.services.email_tracking_service import inject_tracking
    from app.services.compliance_service import check_compliance_preflight
    from app.models.models import EmailMessage
    import os as _os

    sent_ids, failed_ids, skipped_ids, blocked_ids = [], [], [], []

    for item in req.items:
        lead = db.query(Lead).filter(
            Lead.id == item.lead_id, Lead.organization_id == current_user.organization_id
        ).first()
        if not lead or not lead.email:
            skipped_ids.append(item.lead_id)
            continue

        # Single, shared Compliance Preflight gate - the real, confirmed
        # gap this closes: this manual review-and-send path had NO
        # compliance check at all before this, the same gap as
        # send_email_to_lead. A DNC/suppressed lead is tracked
        # separately (blocked_ids) from a lead simply missing an email
        # address (skipped_ids) - these are different situations and an
        # advisor reviewing the batch result deserves to know which one
        # actually happened.
        try:
            check_compliance_preflight(db, lead)
        except ValueError:
            blocked_ids.append(item.lead_id)
            continue

        booking = create_booking_link(db, lead, current_user)
        booking_url = f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/{booking.token}"
        # The advisor may have left the preview's placeholder link in the
        # edited text untouched - swap it for the real one at send time,
        # same as the SMS confirm-send-batch does for {booking_link}.
        final_subject = item.subject.replace(f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/preview", booking_url)
        final_body = item.body_html.replace(f"{_os.environ.get('BOOKING_BASE_URL', '')}/book/preview", booking_url)

        # Same ordering as send_email_to_lead: create the row first to
        # get a real id before tracking can reference it, store the
        # ORIGINAL (edited, untracked) body_html, and inject tracking
        # only into a separate copy used for the actual provider send
        # call - see email_tracking_service.py for the full reasoning.
        email_msg = EmailMessage(
            lead_id=lead.id, sender_id=current_user.id,
            subject=final_subject, body_html=final_body,
            status="queued",
        )
        db.add(email_msg)
        db.commit()
        db.refresh(email_msg)

        tracked_body = inject_tracking(final_body, email_msg.id)

        if current_user.microsoft_365_connected:
            from app.services.microsoft_email_service import send_email_via_microsoft_graph
            result = send_email_via_microsoft_graph(current_user, lead.email, final_subject, tracked_body)
        else:
            result = send_email_via_provider(lead.email, final_subject, tracked_body)

        email_msg.provider_message_id = result.get("provider_message_id")
        email_msg.status = "sent" if result["success"] else "failed"

        if result["success"]:
            lead.status = "sent"
            sent_ids.append(lead.id)
        else:
            failed_ids.append(lead.id)

    db.commit()
    return {"sent_count": len(sent_ids), "failed_count": len(failed_ids), "skipped_count": len(skipped_ids), "blocked_count": len(blocked_ids)}


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
            "opened_at": email_msg.opened_at,
            "click_count": email_msg.click_count or 0,
        }
        for lead, email_msg in rows
    ]


@router.get("/counts")
def email_counts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Real scorecard numbers for the Email Queue action center - per
    Mike's direct feedback that the page "looks way too simple," same
    fix already proven on the Replies page (see sms_router.py's
    reply_counts). A SEPARATE endpoint from /queue and /sent, not
    derived from either's result, so the numbers reflect true totals
    regardless of search filters or the 200-row cap on /sent.

    open_rate is computed from messages sent in the last 30 days only -
    a true all-time rate would be skewed by very old sends an advisor
    no longer cares about, and 30 days is long enough to be a stable,
    meaningful signal without diluting it with months of stale history.
    """
    from datetime import timedelta

    base_email_filter = EmailMessage.sender_id == current_user.id

    queued_count = (
        db.query(func.count(Lead.id))
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            Lead.email.isnot(None),
            Lead.email != "",
            Lead.status == "new",
        )
        .scalar()
        or 0
    )

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    sent_today_count = (
        db.query(func.count(EmailMessage.id))
        .filter(base_email_filter, EmailMessage.sent_at >= today_start)
        .scalar()
        or 0
    )

    thirty_days_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
    recent_sent = (
        db.query(func.count(EmailMessage.id))
        .filter(base_email_filter, EmailMessage.status == "sent", EmailMessage.sent_at >= thirty_days_ago)
        .scalar()
        or 0
    )
    recent_opened = (
        db.query(func.count(EmailMessage.id))
        .filter(base_email_filter, EmailMessage.status == "sent", EmailMessage.sent_at >= thirty_days_ago, EmailMessage.opened_at.isnot(None))
        .scalar()
        or 0
    )
    open_rate_pct = round((recent_opened / recent_sent) * 100) if recent_sent > 0 else None

    total_clicks = (
        db.query(func.sum(EmailMessage.click_count))
        .filter(base_email_filter, EmailMessage.sent_at >= thirty_days_ago)
        .scalar()
        or 0
    )

    return {
        "queued": queued_count,
        "sent_today": sent_today_count,
        "open_rate_pct": open_rate_pct,  # None if no sends in the last 30 days, not 0 - "no data" must read differently than "0% open rate"
        "total_clicks_30d": total_clicks,
    }


@router.get("/queue")
def email_queue(
    search: str | None = Query(default=None, description="Optional partial name or email lookup."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Leads reachable by email for the logged-in advisor.

    BROADENED per Mike's explicit, direct complaint: this was
    previously filtered to ONLY Lead.contact_channel == "email_only"
    (people with no phone number at all) - a real, confirmed gap. A
    lead with BOTH a phone and an email was invisible here entirely,
    even though email is genuinely useful for them too (Mike's own
    words: promos and visual content perform better by email than a
    one-line text, and some households are landline-only and never see
    a text but do read email). Now any lead with a real email address
    on file shows up here, regardless of whether they also have a
    phone - contact_channel is no longer the gate, Lead.email being
    present is.

    Still scoped to leads NOT already on an active email-sending path
    elsewhere (status == "new") - this is the manual/one-off queue, not
    a duplicate of leads already being worked through the mixed-channel
    cadence sequence.
    """
    query = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
        Lead.email.isnot(None),
        Lead.email != "",
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
