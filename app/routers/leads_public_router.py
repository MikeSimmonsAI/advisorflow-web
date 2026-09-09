import logging
import os
import shutil
import tempfile
import json as _json
from fastapi import (
    APIRouter, Depends, UploadFile, File, Form, Query, HTTPException, Request, Response,
)
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, require_tenant_user, require_tenant_or_observer
from app.limiter import limiter
from app.services.platform_owner import require_tenant_context
from app.models.models import User, Lead, Reply, ReplyClassification, CadenceState, BookingLink, EngagementTemperature, CRMContact, VoiceCall
from app.services.import_service import import_leads_from_excel
from app.services.import_permissions import require_import_stage, require_import_commit
from app.services.import_staging_service import stage_batch as _stage_batch
from app.services.import_commit_service import commit_batch as _commit_batch_svc
from app.models.import_models import (
    ImportBatch, ImportBatchStatus, ImportStagedRow,
    ImportRowReviewStatus, ImportDuplicateStatus, ImportValidationStatus,
)
from app.models.models import gen_uuid
from app.services.dedup_service import normalize_phone
from app.routers.audit_log_router import log_action
# THE ONE AUTHORIZED LEAD SCOPE. Every list, count, search, export and
# single-record fetch in this file goes through it, so the advisor boundary is
# stated once instead of re-derived per route.
from app.services import lead_scope
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)

router = APIRouter()


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)


class DemoRequestPayload(BaseModel):
    """The UNION of the two schemas this path used to have.

    `first_name` is the only required field. The older handler also required
    `last_name` and `phone`, so widening them to optional cannot break a caller
    that was already sending them — it only stops rejecting callers the other
    handler accepted. `notes`, `source` and `tier` come from that older schema
    and are kept for the same reason.
    """
    first_name: str
    last_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    industry: Optional[str] = None
    message: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = None
    tier: Optional[str] = None
    # Which brand's marketing site this came from. Optional: when absent the
    # destination is resolved from the request Origin, and failing that from the
    # single configured intake destination. See app/services/public_intake.py.
    platform_slug: Optional[str] = None


# A CEILING ON UNAUTHENTICATED WRITES INTO A CUSTOMER'S WORKSPACE.
#
# Both public intake routes create rows in a real organization with no
# credential at all. Without a ceiling, a script can fill a customer's lead
# table faster than anyone notices, and the platform's own rule is that
# production is never contaminated with fabricated prospects. Generous enough
# that a marketing site having a good day is never throttled, and low enough
# that a bulk submitter runs out.
# ONE BUDGET ACROSS BOTH INTAKE ROUTES. `limiter.limit` counts per endpoint, so
# separate decorators would let a bulk submitter take the full allowance twice
# by alternating between them. A named scope makes the ceiling mean what it says.
PUBLIC_INTAKE_LIMIT = "20/minute;100/hour"
PUBLIC_INTAKE_SCOPE = "public-intake"


@router.post("/demo-request", status_code=201)
@limiter.shared_limit(PUBLIC_INTAKE_LIMIT, scope=PUBLIC_INTAKE_SCOPE)
def demo_request(payload: DemoRequestPayload,
                 request: Request,
                 db: Session = Depends(get_db)):
    """Public demo request from a brand's marketing site. No auth.

    THE DESTINATION IS CONFIGURED, NEVER GUESSED. This used to resolve its
    organization with `Organization.name.ilike('%bookaboost%')` and, failing
    that, `db.query(Organization).first()` — so a rename silently redirected
    public leads and the fallback wrote a stranger's name, phone number and
    email into whichever paying customer's workspace came back first. It now
    resolves an explicitly configured `Platform.public_intake_organization_id`
    and REFUSES when there is none. A refusal is a 503 the operator can see and
    fix; the alternative was a lead quietly landing in the wrong company.

    CORS IS THE APP'S, NOT THIS ROUTE'S. The dead second copy of this handler
    set `Access-Control-Allow-Origin: *` by hand while CORSMiddleware was
    already setting the real origin — two values for one header, which browsers
    reject outright. Both marketing domains are in ALLOWED_ORIGINS in main.py,
    which is the one place that decides this.
    """
    import uuid as _uuid
    from app.services import public_intake

    origin = request.headers.get("origin") or request.headers.get("referer")
    try:
        platform, org = public_intake.resolve_public_intake(
            db, platform_slug=payload.platform_slug, origin=origin)
    except public_intake.IntakeDestinationError as exc:
        # The operator gets the reason in the log; the public caller gets a
        # neutral message. Nothing about the platform's configuration, its
        # organizations or their names is disclosed to an anonymous poster.
        logging.getLogger(__name__).error(
            "demo-request refused: %s (origin=%r, slug=%r)",
            exc.reason, origin, payload.platform_slug)
        raise HTTPException(
            status_code=503,
            detail="We can't accept demo requests right now. Please email us.")

    notes = payload.notes or ""
    detail_lines = [
        "Company: %s" % (payload.company or "n/a"),
        "Industry: %s" % (payload.industry or "n/a"),
        "Message: %s" % (payload.message or "n/a"),
    ]
    composed_notes = "[Demo Request]\n" + "\n".join(detail_lines)
    if notes:
        composed_notes = composed_notes + "\n" + notes

    # DEDUPE, kept from the handler that was live. A prospect who fills the form
    # twice is one prospect; the second submission appends to the first rather
    # than creating a duplicate record for someone to call twice.
    existing = None
    if payload.phone:
        existing = db.query(Lead).filter(
            Lead.organization_id == org.id,
            Lead.phone == payload.phone,
        ).first()
    if existing is None and payload.email:
        existing = db.query(Lead).filter(
            Lead.organization_id == org.id,
            Lead.email == payload.email,
        ).first()

    if existing is not None:
        stamp = datetime.utcnow().strftime("%Y-%m-%d")
        existing.notes = ("%s\n[New demo request %s]\n%s"
                          % (existing.notes or "", stamp, composed_notes)).strip()
        db.commit()
        _notify_demo_request(payload, platform)
        return {
            # BOTH RESPONSE SHAPES. One handler returned {"status": ...} and the
            # other {"success": ...}; a caller checking either keeps working.
            "success": True,
            "status": "updated",
            "message": "Demo request received. We'll be in touch soon!",
            "id": str(existing.id),
        }

    lead = Lead(
        id=str(_uuid.uuid4()),
        organization_id=org.id,
        first_name=(payload.first_name or "").strip(),
        last_name=((payload.last_name or "").strip() or None),
        email=(payload.email.strip() if payload.email else None),
        phone=(payload.phone.strip() if payload.phone else None),
        status="new",
        tier=payload.tier or "web_lead",
        source_file=payload.source or "demo_request",
        message_track="new_inquiry_intro",
        notes=composed_notes,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    # PLAN CAPACITY - HELD, NEVER DROPPED. Public and unauthenticated: external
    # arrival by every definition, and the prospect is real.
    from app.services import lead_capacity
    lead_capacity.hold_if_over_capacity(db, lead, org)

    db.add(lead)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="We can't accept demo requests right now. Please email us.")

    _notify_demo_request(payload, platform)
    return {
        "success": True,
        "status": "created",
        "message": "Demo request received. We'll be in touch soon!",
        "id": str(lead.id),
    }


def _notify_demo_request(payload: "DemoRequestPayload", platform) -> None:
    """Email the team. Kept from the handler that could never run — it was the
    only one that did this, and losing it is how a demo request goes unanswered.

    Never raises: a notification that fails must not lose a lead that was
    already stored.
    """
    # Fire notification emails
    notify_raw = os.environ.get("LEAD_NOTIFY_EMAILS", "")
    notify_addrs = [e.strip() for e in notify_raw.split(",") if e.strip()]
    # The brand the request actually came to, not a hardcoded one. With more
    # than one marketing site posting here, "New Demo Request — BookaBoost" on
    # an EvoSys enquiry is a small lie that costs someone a reply.
    _brand = getattr(platform, "name", None) or "the platform"
    _site = getattr(platform, "website_url", None) or "the marketing site"
    if notify_addrs:
        try:
            import resend
            api_key = os.environ.get("RESEND_API_KEY", "")
            from_addr = os.environ.get("EMAIL_FROM_ADDRESS", "noreply@bookaboost.com")
            if api_key:
                resend.api_key = api_key
                html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;padding:24px;color:#222;">
  <h2 style="color:#1565c0;margin-bottom:16px;">New Demo Request — {_brand}</h2>
  <table style="width:100%;border-collapse:collapse;font-size:14px;">
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;width:120px;">Name</td>
        <td>{payload.first_name} {payload.last_name or ''}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Email</td>
        <td>{payload.email or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Phone</td>
        <td>{payload.phone or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Company</td>
        <td>{payload.company or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Industry</td>
        <td>{payload.industry or '—'}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:700;color:#555;">Message</td>
        <td>{payload.message or '—'}</td></tr>
  </table>
  <p style="margin-top:20px;font-size:12px;color:#888;">
    Sent from the {_site} demo request form.
  </p>
</div>"""
                resend.Emails.send({
                    "from": from_addr,
                    "to": notify_addrs,
                    "subject": f"New Demo Request: {payload.first_name} {payload.last_name or ''} ({payload.company or payload.email or 'unknown'})",
                    "html": html_body,
                })
        except Exception as exc:
            import logging as _log
            # The exception text only. A payload dump here would put a
            # prospect's contact details in the log for a delivery failure.
            _log.getLogger(__name__).error("demo_request notify email failed: %s", exc)


# ── PUBLIC: SMS opt-in form submission (no auth required) ─────────────────────
# Called by advisorflow-booking.vercel.app/optin when a lead submits the
# SMS consent form. Required for Twilio A2P 10DLC carrier verification.

class SmsOptinRequest(BaseModel):
    first_name: str
    last_name: Optional[str] = None
    phone: str
    consent: bool
    source: Optional[str] = "optin_page"
    optin_url: Optional[str] = None
    optin_timestamp: Optional[str] = None
    # Which brand's opt-in page this came from. Optional for the same reason it
    # is optional on the demo request: with one configured destination there is
    # nothing to choose between, and with two the caller has to say.
    platform_slug: Optional[str] = None


@router.post("/sms-optin", status_code=201)
@limiter.shared_limit(PUBLIC_INTAKE_LIMIT, scope=PUBLIC_INTAKE_SCOPE)
def sms_optin(
    payload: SmsOptinRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Public endpoint — no auth required.
    Records SMS consent from the /optin page on the Vercel booking app.
    Checks suppression list before creating lead record.
    Used as evidence of opt-in for Twilio A2P 10DLC campaign verification.

    THE DESTINATION IS CONFIGURED, NOT "THE FIRST ACTIVE ORGANIZATION".

    This route had the same defect as the demo-request one directly above, and
    a worse consequence. It read:

        # Route to the first active organization (Restland).
        org = db.query(Organization).filter(Organization.is_active == True).first()

    What this endpoint writes is a CONSENT RECORD - the evidence a carrier and
    the TCPA rely on to show that a specific person agreed to be texted by a
    specific business. Filing that under whichever organization happened to come
    back first does not merely misplace a lead; it puts one company's name on
    another company's consent, and every SMS later sent on the strength of it
    inherits the mistake. It resolves the same configured destination as every
    other public intake path now, and refuses when there is none.
    """
    import uuid
    from app.services.dedup_service import normalize_phone
    from app.services import public_intake

    if not payload.consent:
        raise HTTPException(status_code=400, detail="SMS consent is required.")

    phone_normalized = normalize_phone(payload.phone or "")
    if not phone_normalized:
        raise HTTPException(status_code=400, detail="A valid phone number is required.")

    try:
        _platform, org = public_intake.resolve_public_intake(
            db, platform_slug=payload.platform_slug, origin=None)
    except public_intake.IntakeDestinationError as exc:
        logging.getLogger(__name__).error(
            "sms-optin refused: %s (slug=%r)", exc.reason, payload.platform_slug)
        raise HTTPException(
            status_code=503,
            detail="We can't record opt-ins right now. Please try again later.")

    # Check suppression / DNC list before creating record
    try:
        from app.services.compliance_service import is_phone_suppressed
        if is_phone_suppressed(db, org.id, phone_normalized):
            raise HTTPException(
                status_code=409,
                detail="This phone number is on the do-not-contact list and cannot be added.",
            )
    except HTTPException:
        raise
    except Exception:
        pass  # If suppression check fails, proceed — don't block opt-in

    # Deduplicate: if lead already exists for this phone, update notes
    existing = db.query(Lead).filter(
        Lead.organization_id == org.id,
        Lead.phone == phone_normalized,
    ).first()

    if existing:
        note_entry = (
            f"\n[SMS Opt-In {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC] "
            f"Re-confirmed consent via {payload.source or 'optin_page'}. "
            f"URL: {payload.optin_url or 'n/a'}"
        )
        existing.notes = (existing.notes or "") + note_entry
        db.commit()
        return {"success": True, "lead_id": existing.id, "action": "updated"}

    lead = Lead(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        first_name=payload.first_name.strip(),
        last_name=(payload.last_name or "").strip() or None,
        phone=phone_normalized,
        phone_raw=payload.phone,
        contact_channel="sms",
        status="new",
        source_file="optin_page",
        tier="web_lead",
        notes=(
            f"[SMS Opt-In {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC] "
            f"Consent given via {payload.source or 'optin_page'}. "
            f"URL: {payload.optin_url or 'n/a'}. "
            f"Timestamp: {payload.optin_timestamp or 'n/a'}"
        ),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    # PLAN CAPACITY - HELD, NEVER DROPPED.
    #
    # Somebody typed their number into an opt-in page and consented to be
    # texted. Throwing that away over a billing ceiling would discard a
    # written consent record, which is the one artefact this platform can
    # least afford to lose.
    from app.services import lead_capacity
    lead_capacity.hold_if_over_capacity(db, lead, org)

    db.add(lead)
    db.commit()
    db.refresh(lead)

    return {"success": True, "lead_id": lead.id, "action": "created"}


# ── Resend booking link ───────────────────────────────────────────────────────

@router.post("/{lead_id}/resend-booking-link")
def resend_booking_link(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_tenant_user),
):
    """
    Generate a fresh booking link for a lead and email it to them.

    Expires any existing pending booking links so there's always exactly
    one active link per lead. Works regardless of the current AI conversation
    state — advisors can resend manually at any time.
    """
    import uuid as _uuid
    from app.models.models import EmailMessage, Organization
    from app.services.sms_service import create_booking_link, BOOKING_BASE_URL
    from app.services.email_service import send_email_via_provider
    from app.services.ai_conversation_service import _build_email_html

    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if lead.status == "dnc":
        raise HTTPException(status_code=400, detail="Lead is on the Do Not Contact list")
    if not lead.email:
        raise HTTPException(status_code=400, detail="Lead has no email address on file")

    # Expire any stale pending links so there's only one active at a time
    stale = db.query(BookingLink).filter(
        BookingLink.lead_id == lead.id,
        BookingLink.status == "pending",
    ).all()
    for s in stale:
        s.status = "expired"
    db.flush()

    # Create fresh link — owned by the lead's ADVISOR, not by whoever pressed
    # the button. A resend issued while the platform owner had the lead open
    # would otherwise send the family to the OWNER's calendar. Same helper as
    # the composer and the email sender, so the three cannot drift apart.
    from app.routers.compose_router import acting_advisor
    _advisor = acting_advisor(db, lead, current_user)
    link = create_booking_link(db, lead, _advisor)
    from app.services.public_identity import booking_url as public_booking_url
    booking_url = public_booking_url(db, lead.organization_id, link.token)

    # Build the email
    org = db.query(Organization).filter_by(id=current_user.organization_id).first()
    org_name = org.name if org else "our organization"
    advisor_name = _advisor.full_name or "Your Advisor"
    first_name = lead.first_name or "there"

    body_text = (
        f"Hi {first_name}, I wanted to make sure you have a convenient way to schedule "
        f"your appointment with us. Use the button below to pick a time that works for you — "
        f"it only takes a minute."
    )

    booking_btn = (
        f'<br><br>'
        f'<a href="{booking_url}" '
        f'style="display:inline-block;background:#1a5fa8;color:#ffffff;padding:12px 28px;'
        f'border-radius:6px;text-decoration:none;font-weight:700;font-size:15px;">'
        f'Schedule a Time &rarr;</a>'
    )

    html_body = _build_email_html(body_text, advisor_name, org_name, extra_html=booking_btn)
    subject = f"Your scheduling link, {first_name}"

    result = send_email_via_provider(lead.email, subject, html_body, org=org)
    if not result["success"]:
        raise HTTPException(
            status_code=500,
            detail=f"Email send failed: {result.get('error', 'unknown error')}",
        )

    # Log in email history
    msg = EmailMessage(
        id=str(_uuid.uuid4()),
        lead_id=lead.id,
        sender_id=current_user.id,
        subject=subject,
        body_html=html_body,
        status="sent",
        provider_message_id=result.get("provider_message_id"),
        sent_at=datetime.utcnow(),
    )
    db.add(msg)

    # Bump lead status to "sent" if still "new"
    if lead.status == "new":
        lead.status = "sent"
    lead.last_messaged_at = datetime.utcnow()

    db.commit()

    return {
        "success": True,
        "booking_url": booking_url,
        "email_sent_to": lead.email,
        "link_id": link.id,
    }
