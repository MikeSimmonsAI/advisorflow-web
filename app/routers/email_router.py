import base64
import os
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

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
    appt_label: Optional[str] = None  # appointment type label for booking button text


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
    if lead.status == 'dnc':
        raise HTTPException(status_code=400, detail='Lead is on the Do Not Contact list')

    # If custom body provided, use it directly
    if req and req.body:
        # Strip any raw booking URLs that may have been included in the AI body text.
        # The booking button is appended exactly once below as an HTML element.
        clean_body = re.sub(r'https?://advisorflow-booking\.vercel\.app\S*', '', req.body).strip()
        body_html = clean_body.replace('\n', '<br>')

        # Append a single HTML booking button when requested
        if req.include_booking_link:
            from app.services.sms_service import create_booking_link
            booking_link = create_booking_link(db, lead, current_user)
            booking_url = f"{os.environ.get('BOOKING_BASE_URL', 'https://advisorflow-booking.vercel.app')}/book/{booking_link.token}"
            btn_label = req.appt_label or "Schedule Your Appointment"
            body_html += f"""<br><br>
<table width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr><td align="center" style="padding:20px 0;">
    <a href="{booking_url}" style="background-color:#1a5fa8;color:#ffffff;padding:14px 28px;text-decoration:none;border-radius:6px;font-weight:bold;font-size:15px;display:inline-block;">
      {btn_label}
    </a>
  </td></tr>
</table>"""

        subject = req.subject or f"Following up, {lead.first_name or 'there'}"

        # Route through org-level Resend sender (preferred) — uses org's own verified
        # domain and API key. Falls back to env-var global sender for orgs without one.
        from app.models.models import Organization
        from app.services.email_service import send_email_via_provider
        org = db.query(Organization).filter_by(id=current_user.organization_id).first()
        result = send_email_via_provider(lead.email, subject, body_html, org=org)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result.get("error", "Email send failed. Check your Microsoft 365 connection in Settings."))

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
        Lead.status != "dnc",
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
    if lead.status == 'dnc':
        raise HTTPException(status_code=400, detail='Lead is on the Do Not Contact list')
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

    from app.models.models import Organization as OrgModel
    org = db.query(OrgModel).filter_by(id=current_user.organization_id).first()
    result = send_email_via_provider(lead.email, subject, body_html, attachments=attachments or None, org=org)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Email send failed"))

    # Log it
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
    return {"email_id": msg.id, "status": "sent", "has_attachment": bool(attachments)}


# ── AI email draft — talking points + 3 options ───────────────────────────────

class EmailDraftRequest(BaseModel):
    tone: str = "warm"
    ai_direction: Optional[str] = None
    sample_message: Optional[str] = None  # User-provided sample to use as AI foundation


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
    Respects relationship_type as the primary AI constraint.
    If sample_message is provided, AI uses it as the foundation.
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
    sample_message = (req.sample_message if req else None)

    return draft_email_options(db, lead, current_user, tone=tone, ai_direction=ai_direction, sample_message=sample_message)


@router.post("/poll-inbox")
def poll_inbox(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Poll Microsoft 365 inbox for new replies from leads.
    Matches by sender email, saves as Reply, triggers AI pipeline.
    Call this every 2 minutes via cron or manually.
    """
    from app.services.email_poller_service import poll_inbox_for_replies
    result = poll_inbox_for_replies(db, current_user.id)
    return result


@router.post("/poll-inbox/all")
def poll_inbox_all(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Poll inbox for all M365-connected advisors across all orgs. Super admin only."""
    if current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin only")
    from app.services.email_poller_service import poll_all_orgs
    result = poll_all_orgs(db)
    return result


# ── Sent email log ─────────────────────────────────────────────────────────────

@router.get("/sent-log")
def sent_email_log(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the last 300 emails sent by this advisor, joined with the lead's
    email address so the Email Queue can cross-reference and badge any lead
    that's already been emailed this session or previously.
    """
    rows = (
        db.query(EmailMessage, Lead.email.label("lead_email"))
        .join(Lead, Lead.id == EmailMessage.lead_id)
        .filter(EmailMessage.sender_id == current_user.id)
        .order_by(EmailMessage.sent_at.desc())
        .limit(300)
        .all()
    )
    return [
        {
            "id": msg.id,
            "lead_id": msg.lead_id,
            "lead_email": lead_email,
            "subject": msg.subject,
            "status": msg.status,
            "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
        }
        for msg, lead_email in rows
    ]


# ── Email system diagnostic ────────────────────────────────────────────────────

@router.post("/system-check")
def email_system_check(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Runs a live diagnostic over every layer of the email stack for the
    logged-in advisor's org. Returns a list of checks, each with:
      name, ok (bool), detail (what was found), fix (what to do if not ok)
    The UI renders this as a pass/fail panel so the advisor can pinpoint
    exactly why emails aren't going out without guessing.
    """
    import httpx
    from app.models.models import Organization
    from app.services.email_service import send_email_via_provider, RESEND_API_KEY, FROM_EMAIL

    checks = []

    # 1. Resolve org-level sender settings
    org = db.query(Organization).filter_by(id=current_user.organization_id).first()
    org_api_key = getattr(org, "resend_api_key", None) if org else None
    org_from_email = getattr(org, "from_email", None) if org else None

    effective_api_key = org_api_key or RESEND_API_KEY
    effective_from = org_from_email or FROM_EMAIL

    # Check: API key exists
    if effective_api_key:
        source = "org settings" if org_api_key else "RESEND_API_KEY env var"
        checks.append({"name": "Resend API key", "ok": True, "detail": f"Key present (from {source})", "fix": None})
    else:
        checks.append({
            "name": "Resend API key",
            "ok": False,
            "detail": "No Resend API key found in org settings or RESEND_API_KEY env var.",
            "fix": "Add your Resend API key in Settings → Email Sender, or set RESEND_API_KEY on Render.",
        })

    # Check: From address
    if effective_from and "@" in effective_from:
        checks.append({"name": "From email address", "ok": True, "detail": f"Sending from: {effective_from}", "fix": None})
    else:
        checks.append({
            "name": "From email address",
            "ok": False,
            "detail": f"From address looks wrong: '{effective_from}'",
            "fix": "Set a valid from_email in Settings → Email Sender (e.g. support@bookaboost.live).",
        })

    # Check: Domain verified in Resend
    if effective_api_key:
        try:
            r = httpx.get(
                "https://api.resend.com/domains",
                headers={"Authorization": f"Bearer {effective_api_key}"},
                timeout=10,
            )
            if r.status_code == 200:
                domains = r.json().get("data", [])
                from_domain = effective_from.split("@")[-1] if effective_from and "@" in effective_from else ""
                matched = next((d for d in domains if d.get("name", "").lower() == from_domain.lower()), None)
                if matched and matched.get("status") == "verified":
                    checks.append({"name": "Resend domain verified", "ok": True, "detail": f"{from_domain} is verified in Resend.", "fix": None})
                elif matched:
                    checks.append({
                        "name": "Resend domain verified",
                        "ok": False,
                        "detail": f"{from_domain} is in Resend but status is '{matched.get('status')}' — DNS records may not have propagated yet.",
                        "fix": "In Resend → Domains, check that all DNS records (SPF, DKIM, DMARC) show green. DNS can take up to 48 hours.",
                    })
                else:
                    checks.append({
                        "name": "Resend domain verified",
                        "ok": False,
                        "detail": f"{from_domain} is NOT in your Resend account.",
                        "fix": "Add the domain in Resend → Domains → Add Domain, then add the DNS records in GoDaddy.",
                    })
            else:
                checks.append({
                    "name": "Resend domain verified",
                    "ok": False,
                    "detail": f"Resend API returned {r.status_code} when checking domains.",
                    "fix": "The API key may be invalid or revoked. Generate a new one at resend.com/api-keys.",
                })
        except Exception as e:
            checks.append({"name": "Resend domain verified", "ok": False, "detail": f"Could not reach Resend API: {e}", "fix": "Check internet connectivity from the backend server."})

    # Check: Send a real test email to the advisor
    if effective_api_key and effective_from and "@" in effective_from:
        test_to = current_user.email or current_user.microsoft_email_address
        if test_to:
            test_result = send_email_via_provider(
                to_email=test_to,
                subject="BookaBoost email system check",
                body_html="<p>This is an automated test from the BookaBoost email diagnostic. If you received this, outbound email is working correctly.</p>",
                org=org,
            )
            if test_result["success"]:
                checks.append({"name": "Live test email", "ok": True, "detail": f"Test email sent to {test_to} successfully.", "fix": None})
            else:
                checks.append({
                    "name": "Live test email",
                    "ok": False,
                    "detail": f"Send failed: {test_result.get('error', 'unknown error')}",
                    "fix": "Check the error above — it usually contains the exact reason (domain not verified, API key invalid, etc.).",
                })
        else:
            checks.append({"name": "Live test email", "ok": False, "detail": "No email address on file for your account to send the test to.", "fix": "Ensure your user account has an email address set."})

    all_ok = all(c["ok"] for c in checks)
    return {"all_ok": all_ok, "checks": checks}
