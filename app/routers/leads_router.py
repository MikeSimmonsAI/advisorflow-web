import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, time, timezone

from app.deps import get_db, get_current_user
from app.models.models import User, Lead, LeadStatus, Reply, ReplyClassification, CadenceState, CadenceStatus, BookingLink, EngagementTemperature
from app.services.import_service import import_leads_from_excel, parse_excel_file
from app.services.dedup_service import bulk_dedup_check
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/leads", tags=["leads"])


def _is_suppressed(db: Session, lead: Lead) -> bool:
    """Lazy import to avoid a circular import (compliance_service -> compliance_router -> ... )."""
    from app.services.compliance_service import is_phone_suppressed
    return is_phone_suppressed(db, lead.organization_id, lead.phone)


def _require_import_access(current_user: User) -> None:
    """
    Lead import (Excel upload) is admin-only by default per Mike's
    explicit request, but with a per-advisor override
    (User.can_import_leads) an admin can grant individually - not
    all-or-nothing. Checked first, before any file is even written to
    disk, so a rejected request doesn't waste effort on temp-file I/O.
    """
    is_admin = current_user.role in ("org_admin", "super_admin")
    if not is_admin and not current_user.can_import_leads:
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to import leads. Ask an admin to grant you access in Users.",
        )


@router.post("/upload/preview")
def preview_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = Form(None),
    force_new_inquiry: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 1: advisor uploads an Excel file, we run the REAL import logic
    (tier routing, dedup, compliance flags) in dry_run mode so the preview
    numbers always match what confirm_upload will actually do.

    Admin-only by default, with a per-advisor override - see
    _require_import_access above.

    source_year and force_new_inquiry are explicitly marked as Form(...)
    fields, not bare params - without that marker FastAPI treats them as
    query parameters when mixed with a File(...) upload, which silently
    ignored the frontend's multipart form value for source_year (a real,
    pre-existing bug found and fixed while wiring up force_new_inquiry,
    which would have had the exact same problem).

    force_new_inquiry: manual override for batches of brand-new web/cold
    leads - tags every row as New Inquiry regardless of auto-detection
    from a source column. See import_service.import_leads_from_excel for
    the full reasoning.
    """
    _require_import_access(current_user)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        summary = import_leads_from_excel(
            db,
            file_path=tmp_path,
            organization_id=current_user.organization_id,
            uploading_user_id=current_user.id,
            source_year=source_year,
            source_filename=file.filename,
            dry_run=True,
            force_new_inquiry=force_new_inquiry,
        )
    finally:
        os.unlink(tmp_path)

    return summary


@router.post("/upload/confirm")
def confirm_upload(
    file: UploadFile = File(...),
    source_year: Optional[int] = Form(None),
    force_new_inquiry: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Step 2: advisor confirms - actually import and persist the leads.
    Admin-only by default, with a per-advisor override - see
    _require_import_access above. See preview_upload above for why
    source_year/force_new_inquiry use Form(...).
    """
    _require_import_access(current_user)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        result = import_leads_from_excel(
            db,
            file_path=tmp_path,
            organization_id=current_user.organization_id,
            uploading_user_id=current_user.id,
            source_year=source_year,
            source_filename=file.filename,
            force_new_inquiry=force_new_inquiry,
        )
    finally:
        os.unlink(tmp_path)

    return result


def _create_lead_core(
    db: Session,
    organization_id: str,
    current_user: User,
    first_name: str,
    last_name: str,
    phone: str | None,
    email: str | None,
    tier: str,
    notes: str | None,
    assigned_to_id: str | None,
    source_file: str,
) -> Lead:
    """
    The shared core of manual lead creation - extracted so
    create_lead_manually and the referral endpoint
    (create_referral_lead, see below) both go through the EXACT same
    dedup registry check and tier-to-track mapping, rather than one of
    them reimplementing this logic separately and risking drift between
    the two. source_file distinguishes the two callers in the audit
    trail/lead history ("manual_entry" vs "referral") without changing
    any of the actual creation logic itself.

    Does NOT call log_action, sync to Google Contacts, or do the final
    db.refresh() - those stay in each caller, since create_lead_manually
    logs a different action name than the referral endpoint will, and
    the referral endpoint needs to create the LeadReferral link row
    before its own final refresh/return.
    """
    if not first_name.strip() or not last_name.strip():
        raise HTTPException(status_code=400, detail="First and last name are required.")
    if not phone and not email:
        raise HTTPException(status_code=400, detail="A phone number or email address is required.")

    from app.models.models import LeadTier, MessageTrack
    from app.services.import_service import TIER_TO_TRACK
    from app.services.dedup_service import check_and_register, normalize_phone

    manual_entry_tiers = {"pre_need", "at_need", "imminent", "contract_sold", "new_inquiry"}
    if tier not in manual_entry_tiers:
        raise HTTPException(
            status_code=400,
            detail=f"tier must be one of: {', '.join(sorted(manual_entry_tiers))}",
        )
    tier_enum = LeadTier(tier)

    resolved_assigned_to_id = assigned_to_id or current_user.id
    if resolved_assigned_to_id != current_user.id:
        target_advisor = db.query(User).filter(
            User.id == resolved_assigned_to_id, User.organization_id == organization_id
        ).first()
        if not target_advisor:
            raise HTTPException(status_code=404, detail="The advisor you're assigning this lead to was not found.")

    norm_phone = normalize_phone(phone) if phone else None
    contact_channel = "sms" if norm_phone else "email_only"

    is_duplicate = False
    duplicate_of_lead_id = None
    if norm_phone:
        is_duplicate, registry_entry = check_and_register(
            db, organization_id, phone, last_name,
            lead_id=None,
            user_id=current_user.id,
        )
        if is_duplicate and registry_entry:
            duplicate_of_lead_id = registry_entry.first_seen_lead_id

    lead = Lead(
        organization_id=organization_id,
        assigned_to_id=resolved_assigned_to_id,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=norm_phone,
        phone_raw=phone,
        email=email.strip() if email else None,
        tier=tier_enum,
        message_track=TIER_TO_TRACK.get(tier_enum, MessageTrack.NEEDS_REVIEW),
        contact_channel=contact_channel,
        status=LeadStatus.NEW,
        notes=notes,
        is_duplicate=is_duplicate,
        duplicate_of_lead_id=duplicate_of_lead_id,
        source_file=source_file,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    if norm_phone and not is_duplicate:
        from app.models.models import ContactRegistry
        fresh_entry = (
            db.query(ContactRegistry)
            .filter(
                ContactRegistry.organization_id == organization_id,
                ContactRegistry.normalized_phone == norm_phone,
                ContactRegistry.first_seen_lead_id.is_(None),
            )
            .order_by(ContactRegistry.id.desc())
            .first()
        )
        if fresh_entry:
            fresh_entry.first_seen_lead_id = lead.id
            db.commit()

    return lead


class CreateLeadRequest(BaseModel):
    first_name: str
    last_name: str
    phone: str | None = None
    email: str | None = None
    tier: str = "pre_need"
    notes: str | None = None
    assigned_to_id: str | None = None  # defaults to the creating advisor if omitted


@router.post("/manual")
def create_lead_manually(
    req: CreateLeadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manual single-lead entry - per Mike's explicit request: "if I get one
    person's information, I need to be able to enter that person directly
    into the system without uploading a spreadsheet." Previously the only
    way a lead entered AdvisorFlow at all was an Excel upload, which makes
    no sense for a single walk-in or phone call.

    Core creation logic lives in _create_lead_core above, shared with
    the referral endpoint below - see that function's docstring for why.

    tier is a plain string here (not every LeadTier value) restricted to
    the ones an advisor would actually choose by hand for someone they
    know personally - EMAIL_ONLY/ADDR_ONLY/PARTIAL exist as auto-detected
    OUTCOMES of import based on what data is present, not something an
    advisor manually picks; that distinction matters even though those
    enum values still technically exist on the Lead model.
    """
    lead = _create_lead_core(
        db, current_user.organization_id, current_user,
        req.first_name, req.last_name, req.phone, req.email, req.tier, req.notes,
        req.assigned_to_id, source_file="manual_entry",
    )

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.create_manual", target_type="lead", target_id=lead.id,
        details={"tier": req.tier, "is_duplicate": lead.is_duplicate},
    )

    # Automatic Google Contacts sync - same as the Excel import path,
    # per Mike's explicit request that this happen automatically, no
    # separate review step. Wrapped defensively even though the sync
    # function itself never raises internally - see import_service.py's
    # equivalent hook for the full reasoning.
    try:
        from app.services.google_contacts_service import sync_lead_to_google_contacts
        sync_lead_to_google_contacts(db, lead)
    except Exception:
        pass

    # IMPORTANT: db.refresh() must be the LAST thing before return, after
    # every commit in this function - not just after the insert commit
    # earlier. SQLAlchemy's session default (expire_on_commit=True) marks
    # every loaded attribute on a tracked object as stale after ANY
    # commit, and FastAPI's jsonable_encoder does not trigger SQLAlchemy's
    # lazy-reload itself when it walks an expired object - it silently
    # serializes to {}, with no error anywhere. This was a real, confirmed
    # bug (found via jsonable_encoder(lead) directly returning {} after a
    # commit, isolated and reproduced outside any FastAPI routing at all)
    # that ALSO affects existing set_lead_tier and mark_lead_dnc, which
    # commit and then return the same object with no final refresh - their
    # existing tests never caught it because they assert against a
    # separately re-queried/refreshed db_session.refresh(lead) object,
    # never against response.json() itself.
    db.refresh(lead)
    return lead


class CreateReferralRequest(BaseModel):
    first_name: str
    last_name: str
    phone: str | None = None
    email: str | None = None
    relationship_type: str
    tier: str = "pre_need"
    notes: str | None = None
    assigned_to_id: str | None = None  # defaults to the creating advisor if omitted


@router.post("/{lead_id}/referrals")
def create_referral_lead(
    lead_id: str,
    req: CreateReferralRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Creates a REAL, separate Lead record for someone referred by an
    existing lead - per Mike's explicit, concrete scenario: "I'm
    dealing with Deborah Brown and... she's now given me Lisa and Tom
    [via a permission-to-access form]... I need to be able to send out
    some messages to Lisa and Tom... I need to get them in for a
    pre-need [conversation]."

    This is deliberately NOT a notes field or a sub-record attached to
    Deborah's lead - Lisa gets her own full Lead row, eligible for the
    exact same cadence, replies, and outcome tracking as any other
    lead, going through the exact same dedup check and tier-to-track
    mapping (_create_lead_core, shared with create_lead_manually
    above). The LeadReferral row created here is purely the link
    remembering WHO referred Lisa and HOW they're related - it never
    duplicates contact info that already lives on Lisa's own Lead
    record.

    The source lead (Deborah) must belong to the current user's org;
    no further ownership check beyond that - any advisor in the org can
    record a referral from any lead, the same scope as set_lead_tier,
    since this is fundamentally a data-entry action, not a sensitive
    edit to someone else's personal contact info.
    """
    from app.models.models import RelationshipType, LeadReferral

    source_lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not source_lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    try:
        relationship_enum = RelationshipType(req.relationship_type)
    except ValueError:
        valid = ", ".join(r.value for r in RelationshipType)
        raise HTTPException(status_code=400, detail=f"relationship_type must be one of: {valid}")

    referred_lead = _create_lead_core(
        db, current_user.organization_id, current_user,
        req.first_name, req.last_name, req.phone, req.email, req.tier, req.notes,
        req.assigned_to_id, source_file="referral",
    )

    referral = LeadReferral(
        source_lead_id=source_lead.id,
        referred_lead_id=referred_lead.id,
        relationship_type=relationship_enum,
        created_by_id=current_user.id,
        notes=req.notes,
    )
    db.add(referral)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.create_referral", target_type="lead", target_id=referred_lead.id,
        details={
            "source_lead_id": source_lead.id,
            "source_lead_name": f"{source_lead.first_name} {source_lead.last_name}",
            "relationship_type": req.relationship_type,
            "is_duplicate": referred_lead.is_duplicate,
        },
    )

    try:
        from app.services.google_contacts_service import sync_lead_to_google_contacts
        sync_lead_to_google_contacts(db, referred_lead)
    except Exception:
        pass

    # Same root-cause fix as every other bare-object return in this
    # file: db.refresh() must be the genuinely last thing before return,
    # after the LeadReferral commit above too, not just the lead
    # creation's own internal commits.
    db.refresh(referred_lead)
    return referred_lead


@router.get("/{lead_id}/referrals")
def list_referrals_for_lead(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns both directions of referral relationships for a lead -
    people THIS lead referred (source_lead_id == lead_id), and who
    referred THIS lead, if anyone (referred_lead_id == lead_id). A
    lead detail page needs both: Deborah's page shows "Referred: Lisa,
    Tom"; Lisa's page shows "Referred by: Deborah" - same underlying
    LeadReferral rows, just queried from each side.
    """
    from app.models.models import LeadReferral

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    referred_by_this_lead = (
        db.query(LeadReferral, Lead)
        .join(Lead, LeadReferral.referred_lead_id == Lead.id)
        .filter(LeadReferral.source_lead_id == lead_id)
        .all()
    )
    referred_from = (
        db.query(LeadReferral, Lead)
        .join(Lead, LeadReferral.source_lead_id == Lead.id)
        .filter(LeadReferral.referred_lead_id == lead_id)
        .first()
    )

    return {
        "referred": [
            {
                "referral_id": referral.id,
                "lead_id": referred_lead.id,
                "first_name": referred_lead.first_name,
                "last_name": referred_lead.last_name,
                "relationship_type": referral.relationship_type.value,
                "phone": referred_lead.phone,
                "email": referred_lead.email,
                "status": referred_lead.status.value if referred_lead.status else None,
            }
            for referral, referred_lead in referred_by_this_lead
        ],
        "referred_by": (
            {
                "referral_id": referred_from[0].id,
                "lead_id": referred_from[1].id,
                "first_name": referred_from[1].first_name,
                "last_name": referred_from[1].last_name,
                "relationship_type": referred_from[0].relationship_type.value,
            }
            if referred_from else None
        ),
    }


@router.get("/{lead_id}/certification")
def get_lead_certification(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns this lead's position in the Certified Appointment pipeline
    - Solicited -> Contacted -> Booked -> Confirmed -> Waiting. Per
    Mike's explicit, direct definition (see certification_service.py
    for the full reasoning) - this is a real, auditable sequence of
    events, not an AI-judged score.
    """
    from app.services.certification_service import get_certification_status

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    return get_certification_status(db, lead)


@router.post("/{lead_id}/certification/confirm")
def confirm_lead_appointment(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Marks this lead's currently-booked appointment as confirmed - the
    deliberate, separate action Mike described, not something inferred
    automatically from booking alone.
    """
    from app.services.certification_service import confirm_appointment

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found.")

    booking = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead.id, BookingLink.status == "booked")
        .order_by(BookingLink.booked_time.desc())
        .first()
    )
    if not booking:
        raise HTTPException(status_code=400, detail="This lead has no booked appointment to confirm.")

    confirm_appointment(db, booking)

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.confirm_appointment", target_type="lead", target_id=lead.id,
        details={"booking_link_id": booking.id},
    )

    from app.services.certification_service import get_certification_status
    return get_certification_status(db, lead)


@router.get("/")
def list_leads(
    status_filter: Optional[str] = Query(None, alias="status"),
    tier: Optional[str] = Query(None),
    message_track: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Advisors see only their own leads. org_admin/super_admin (Mike) can
    use /admin/leads instead for the cross-advisor view.
    Filter by tier or message_track to work one queue at a time, e.g.
    ?message_track=upsell_existing to pull just the Contract Sold upsell list.
    """
    query = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
    )
    if status_filter:
        query = query.filter(Lead.status == status_filter)
    if tier:
        query = query.filter(Lead.tier == tier)
    if message_track:
        query = query.filter(Lead.message_track == message_track)
    leads = query.order_by(Lead.created_at.desc()).limit(500).all()
    return leads


@router.get("/needs-review")
def leads_needing_tier_review(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Leads imported with no Lead Type set in the source file (untyped/blank).
    These are held out of any SMS queue until a real tier is assigned -
    they are NOT defaulted to Pre-Need.
    """
    leads = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
        Lead.status == LeadStatus.NEEDS_TIER_REVIEW,
    ).order_by(Lead.created_at.desc()).all()
    return leads


@router.patch("/{lead_id}/tier")
def set_lead_tier(
    lead_id: str,
    new_tier: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Manually assign a tier to a needs-review lead, which also sets its
    message_track and unlocks it for the SMS queue.

    Scope note: intentionally org-wide rather than restricted to leads
    assigned to current_user, unlike GET /needs-review above which only
    lists the calling advisor's own needs-review leads. Re-tiering is a
    reversible data-correction action (similar to the Lead Cleanup
    contact-info fixes), and any advisor noticing a teammate's
    obviously-mistagged lead should be able to fix it rather than waiting
    on that specific advisor. Logged below so there's still a clear trail
    of who changed what.
    """
    from app.models.models import LeadTier
    from app.services.import_service import TIER_TO_TRACK

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    try:
        tier_enum = LeadTier(new_tier)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid tier: {new_tier}")

    previous_tier = lead.tier.value if lead.tier else None

    lead.tier = tier_enum
    lead.message_track = TIER_TO_TRACK.get(tier_enum)
    lead.status = LeadStatus.NEW
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.set_tier", target_type="lead", target_id=lead.id,
        details={"from": previous_tier, "to": tier_enum.value, "lead_assigned_to_id": lead.assigned_to_id},
    )

    # See create_lead_manually's comment for the full explanation: a bare
    # object returned with no response_model must be refreshed AFTER the
    # last commit in the function (log_action above commits internally),
    # or FastAPI's jsonable_encoder silently serializes it to {} with no
    # error - a real, confirmed bug this endpoint had until now, never
    # caught because its existing test asserts against a separately
    # re-queried object, not against response.json() itself.
    db.refresh(lead)
    return lead


class MarkDNCRequest(BaseModel):
    reason: str | None = None  # optional - e.g. a snippet of the reply that prompted this


@router.patch("/{lead_id}/mark-dnc")
def mark_lead_dnc(
    lead_id: str,
    req: MarkDNCRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Quick-action DNC flag for when an advisor reads a reply themselves
    and spots STOP/do-not-contact language the automatic keyword/AI
    classification missed. Per Mike's explicit request: any advisor
    should be able to act on this immediately, not just an admin, since
    a missed STOP only gets worse the longer it sits unactioned.

    Deliberately mirrors the automatic DNC path in sms_router.py's
    inbound webhook exactly - same three things happen, in the same
    order, for the same reason: a manually-flagged DNC must behave
    IDENTICALLY to an automatically-detected one, or this lead would
    still get cadence touches sent after being marked DNC, which would
    defeat the entire point of this button.
      1. lead.status = "dnc"
      2. stop_cadence_for_lead(..., CadenceStatus.STOPPED_DNC) - so no
         further scheduled touches go out
      3. added to the org-wide suppression list (source=ADVISOR_FLAGGED,
         distinguishable from an admin's manual Compliance Center entry
         and from the automatic keyword match) - so this phone number is
         blocked from ANY future send, not just this one lead's cadence

    Restricted to leads that HAVE a phone - a lead with no phone can't
    be added to a phone-keyed suppression list; still flips lead.status
    to "dnc" in that case, just skips the suppression-list step.
    """
    from app.services.cadence_service import stop_cadence_for_lead
    from app.models.models import CadenceStatus

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    already_dnc = lead.status == LeadStatus.DNC

    lead.status = LeadStatus.DNC
    stop_cadence_for_lead(db, lead.id, CadenceStatus.STOPPED_DNC)

    suppression_entry_id = None
    if lead.phone:
        from app.services.compliance_service import add_suppression_entry_from_reply
        from app.models.models import SuppressionSource
        reason = req.reason.strip() if req and req.reason and req.reason.strip() else f"Flagged DNC by {current_user.full_name} from lead detail"
        entry = add_suppression_entry_from_reply(
            db, lead.organization_id, lead.phone, reason=reason, source=SuppressionSource.ADVISOR_FLAGGED,
        )
        suppression_entry_id = entry.id

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="lead.mark_dnc", target_type="lead", target_id=lead.id,
        details={
            "was_already_dnc": already_dnc,
            "phone": lead.phone,
            "reason": req.reason if req else None,
            "suppression_entry_id": suppression_entry_id,
        },
    )

    # Same root-cause fix as create_lead_manually and set_lead_tier - see
    # those for the full explanation. This was a real, confirmed bug:
    # log_action's internal commit expires lead's loaded attributes, and
    # returning it bare with no response_model and no final refresh
    # silently serializes to {}.
    db.refresh(lead)
    return lead


class UpdateLeadDetailsRequest(BaseModel):
    phone: str | None = None
    email: str | None = None
    notes: str | None = None
    first_name: str | None = None
    last_name: str | None = None


@router.patch("/{lead_id}/details")
def update_lead_details(
    lead_id: str,
    req: UpdateLeadDetailsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Advisor-facing contact info / notes editing - per Mike's explicit
    complaint that Lead Detail let him VIEW phone/email but never edit
    them, with "no clear save button in some areas."

    Deliberately ADVISOR-SCOPED, not org-wide: an advisor may only edit
    leads assigned to THEM. This is a different scope rule than
    set_lead_tier (intentionally org-wide, since retiering is a
    low-stakes shared correction) - contact info is more personal/
    sensitive, and Mike's explicit call was that editing should be
    limited to the advisor's own leads, with admins able to edit any
    lead via the existing admin_router.py fix_lead_contact_info
    endpoint (org-wide, require_admin, used by Lead Cleanup).

    Reuses the SAME registry-resync helper
    (_apply_contact_registry_after_contact_fix) that fix_lead_contact_info
    already uses and has tests for - not a second, divergent
    implementation of "what happens to dedup tracking when phone/name
    changes." notes is new here; fix_lead_contact_info has no notes
    field at all, since Lead Cleanup never needed one.
    """
    from app.routers.admin_router import _apply_contact_registry_after_contact_fix, _lead_summary
    from app.services.dedup_service import normalize_phone

    if all(v is None for v in (req.phone, req.email, req.notes, req.first_name, req.last_name)):
        raise HTTPException(status_code=400, detail="Provide at least one field to update.")

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found in this organization.")

    is_admin = current_user.role in ("org_admin", "super_admin")
    if not is_admin and lead.assigned_to_id != current_user.id:
        raise HTTPException(
            status_code=403,
            detail="You can only edit leads assigned to you. Ask an admin to edit this one, or reassign it to yourself first.",
        )

    before = {"first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone, "email": lead.email, "notes": lead.notes}
    registry_needs_resync = False

    if req.phone is not None:
        normalized = normalize_phone(req.phone)
        if not normalized:
            raise HTTPException(status_code=400, detail="Phone could not be normalized.")
        lead.phone_raw = req.phone
        lead.phone = normalized
        registry_needs_resync = True

    if req.email is not None:
        lead.email = req.email.strip() or None

    if req.notes is not None:
        lead.notes = req.notes.strip() or None

    if req.first_name is not None:
        cleaned_first = req.first_name.strip()
        lead.first_name = cleaned_first or None

    if req.last_name is not None:
        cleaned_last = req.last_name.strip()
        if not cleaned_last:
            raise HTTPException(status_code=400, detail="last_name cannot be blank.")
        lead.last_name = cleaned_last
        registry_needs_resync = True

    if registry_needs_resync:
        _apply_contact_registry_after_contact_fix(db, lead)

    db.commit()
    db.refresh(lead)

    after = {"first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone, "email": lead.email, "notes": lead.notes}
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.update_details", target_type="lead", target_id=lead.id,
            details=changed,
        )

    # _lead_summary builds a plain dict by reading each attribute
    # individually, which safely triggers SQLAlchemy's lazy-reload on an
    # expired object even after log_action's internal commit above - see
    # the extensive comment on create_lead_manually for the full
    # background on why bare-object returns are unsafe but this pattern
    # isn't. Extended here with notes, which the admin version doesn't include.
    summary = _lead_summary(lead)
    summary["notes"] = lead.notes
    return summary


@router.get("/daily-briefing")
def daily_briefing(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Advisor-scoped daily briefing data for the Overview page.

    This deliberately mirrors the existing needs_attention behavior from
    GET /sms/replies?needs_attention=true: Interested + Callback replies on
    leads owned by the logged-in advisor. It does not introduce a separate
    definition that could drift from the Replies inbox.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start_24h = now - timedelta(hours=24)
    end_of_today = datetime.combine(now.date(), time.max)
    start_7d = now - timedelta(days=7)

    base_lead_filters = (
        Lead.organization_id == current_user.organization_id,
        Lead.assigned_to_id == current_user.id,
    )

    replies_needing_attention = (
        db.query(func.count(Reply.id))
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            Reply.classification.in_([ReplyClassification.INTERESTED, ReplyClassification.CALLBACK]),
        )
        .scalar()
        or 0
    )

    cadence_touches_due_today = (
        db.query(func.count(CadenceState.id))
        .join(Lead, CadenceState.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            CadenceState.status == CadenceStatus.ACTIVE,
            CadenceState.next_touch_due_at.isnot(None),
            CadenceState.next_touch_due_at <= end_of_today,
        )
        .scalar()
        or 0
    )

    leads_imported_last_24h = (
        db.query(func.count(Lead.id))
        .filter(
            *base_lead_filters,
            Lead.created_at >= start_24h,
        )
        .scalar()
        or 0
    )

    bookings_last_7_days = (
        db.query(func.count(distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            BookingLink.status == "booked",
            BookingLink.booked_time.isnot(None),
            BookingLink.booked_time >= start_7d,
        )
        .scalar()
        or 0
    )

    # Certified appointments waiting - per Mike's exact definition
    # (Solicited -> Contacted -> Booked -> Confirmed -> Waiting). A
    # genuinely DIFFERENT count from bookings_last_7_days above: a
    # booking can exist without confirmed_at set yet (booked but not
    # yet confirmed is a real, distinct state in the pipeline) - this
    # counts only bookings that have actually reached the final,
    # certified state, regardless of how long ago they were booked.
    certified_appointments_waiting = (
        db.query(func.count(distinct(BookingLink.lead_id)))
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            *base_lead_filters,
            BookingLink.status == "booked",
            BookingLink.confirmed_at.isnot(None),
        )
        .scalar()
        or 0
    )

    return {
        "replies_needing_attention": replies_needing_attention,
        "cadence_touches_due_today": cadence_touches_due_today,
        "leads_imported_last_24h": leads_imported_last_24h,
        "bookings_last_7_days": bookings_last_7_days,
        "certified_appointments_waiting": certified_appointments_waiting,
    }


@router.get("/engagement-breakdown")
def engagement_breakdown(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Advisor-scoped engagement temperature counts for the Overview chart.
    Uses the real Lead.engagement_temperature field; no client-side guesses.
    """
    rows = (
        db.query(Lead.engagement_temperature, func.count(Lead.id))
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
        )
        .group_by(Lead.engagement_temperature)
        .all()
    )
    counts = {temperature.value: 0 for temperature in EngagementTemperature}
    for temperature, count in rows:
        key = temperature.value if temperature else EngagementTemperature.UNKNOWN.value
        counts[key] = int(count or 0)
    return counts


@router.get("/status-funnel")
def status_funnel(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Advisor-scoped real lead status funnel for Overview.
    Only returns the stages displayed in the dashboard funnel.
    """
    stages = [
        LeadStatus.NEW,
        LeadStatus.SENT,
        LeadStatus.REPLIED,
        LeadStatus.HOT,
        LeadStatus.BOOKED,
    ]
    rows = (
        db.query(Lead.status, func.count(Lead.id))
        .filter(
            Lead.organization_id == current_user.organization_id,
            Lead.assigned_to_id == current_user.id,
            Lead.status.in_(stages),
        )
        .group_by(Lead.status)
        .all()
    )
    counts = {stage.value: 0 for stage in stages}
    for status, count in rows:
        if status:
            counts[status.value] = int(count or 0)
    return [
        {"status": stage.value, "label": stage.value.replace("_", " ").title(), "count": counts[stage.value]}
        for stage in stages
    ]

@router.get("/{lead_id}")
def get_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Returns full contact-card detail for a single lead."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.get("/{lead_id}/timeline")
def get_lead_timeline(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Returns the full conversation thread for one lead: every outbound
    message and every inbound reply, merged into one chronological feed,
    plus the AI lead-quality note if one exists, plus their most recent
    booking link status. Built for the lead detail page so an advisor
    can see everything about one person in one place instead of hunting
    across the Leads and Replies screens separately.

    Booking info was a real gap: the BookingLink table (whether a lead
    booked, what time, whether a Google Calendar event was created) was
    tracked on the backend the whole time but never surfaced anywhere in
    the UI - an advisor had no way to see if someone actually booked.
    """
    from app.models.models import Message, Reply, BookingLink, EmailMessage
    import json as _json

    lead = db.query(Lead).filter(
        Lead.id == lead_id, Lead.organization_id == current_user.organization_id
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    messages = db.query(Message).filter(Message.lead_id == lead_id).all()
    replies = db.query(Reply).filter(Reply.lead_id == lead_id).all()
    # Real, genuine gap found and fixed: outbound emails (EmailMessage)
    # were never included in this timeline at all, even though they're
    # a real, persisted part of the conversation - an advisor emailing a
    # lead three times had zero visibility into that here, only the SMS
    # side ever showed. There is currently no INBOUND email reply model
    # at all (that's the separately-tracked, not-yet-built "inbound
    # email reply handling" item, blocked on a Gmail-forwarding
    # decision) - so this fixes the outbound half of the gap, which is
    # real, existing, queryable data that simply wasn't being shown.
    email_messages = db.query(EmailMessage).filter(EmailMessage.lead_id == lead_id).all()

    events = []
    for m in messages:
        events.append({
            "type": "outbound",
            "channel": "sms",
            "body": m.body,
            "timestamp": m.sent_at,
            "status": m.twilio_status,
        })
    for r in replies:
        events.append({
            "type": "inbound",
            "channel": "sms",
            "body": r.body,
            "timestamp": r.received_at,
            "is_hot": r.is_hot,
        })
    for e in email_messages:
        events.append({
            "type": "outbound",
            "channel": "email",
            "subject": e.subject,
            "body": e.body_html,
            "timestamp": e.sent_at,
            "status": e.status,
        })
    events.sort(key=lambda e: e["timestamp"] or "")

    ai_note = None
    if lead.ai_lead_quality_note:
        try:
            ai_note = _json.loads(lead.ai_lead_quality_note)
        except Exception:
            ai_note = {"raw": lead.ai_lead_quality_note}

    # NOTE: created_at has only second-level precision on some databases
    # (confirmed during testing - two BookingLinks created in the same
    # second get identical timestamps). For the real-world case (one
    # booking link per lead, occasionally resent days/weeks apart) this
    # is a non-issue. It only matters if two links are created for the
    # same lead within the same second, which doesn't happen in normal
    # usage (a human re-sending a link takes longer than that). Not
    # adding a UUID tie-breaker since UUID4 ordering has no relationship
    # to creation order and would be actively misleading.
    latest_booking = (
        db.query(BookingLink)
        .filter(BookingLink.lead_id == lead_id)
        .order_by(BookingLink.created_at.desc())
        .first()
    )
    booking_info = None
    if latest_booking:
        booking_info = {
            "id": latest_booking.id,
            "status": latest_booking.status,
            "booked_time": latest_booking.booked_time,
            "calendar_event_id": latest_booking.calendar_event_id,
            "created_at": latest_booking.created_at,
            "expires_at": latest_booking.expires_at,
        }

    return {
        "lead": lead,
        "events": events,
        "ai_quality": ai_note,
        "booking": booking_info,
    }


# ---------------------------------------------------------------------------
# Message review/confirm flow - the "AI drafts, I confirm, then it sends"
# workflow Mike specifically asked for. Reuses the EXACT SAME template
# resolution logic the real cadence engine uses (render_cadence_message),
# so what's shown in this preview is genuinely what would be sent, not an
# approximation that could drift out of sync with the real send path.
# ---------------------------------------------------------------------------

class MessagePreviewRequest(BaseModel):
    lead_ids: list[str]


class MessagePreviewItem(BaseModel):
    lead_id: str
    lead_name: str
    phone: str | None
    tier: str | None
    message_track: str | None
    draft_message: str
    skip_reason: str | None = None  # set if this lead can't actually be sent to (DNC, no phone, etc.)


@router.post("/preview-messages", response_model=list[MessagePreviewItem])
def preview_messages_for_leads(
    req: MessagePreviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Given a batch of lead IDs (e.g. everything just created by an
    import), returns the actual AI/template-drafted first message for
    each one - WITHOUT sending anything. This is the review step: the
    advisor sees exactly what would go out, per lead, and can edit or
    skip individual ones before calling /leads/confirm-send-batch below.
    """
    from app.services.cadence_service import render_cadence_message
    from app.services.sms_service import BOOKING_BASE_URL

    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids), Lead.organization_id == current_user.organization_id
    ).all()
    found_by_id = {l.id: l for l in leads}

    results = []
    for lead_id in req.lead_ids:
        lead = found_by_id.get(lead_id)
        if not lead:
            continue  # silently skip IDs that don't belong to this org - same pattern as reassign_leads

        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or "(no name)"
        skip_reason = None
        draft = ""

        if lead.status == LeadStatus.DNC:
            skip_reason = "DNC - excluded from outreach"
        elif lead.is_duplicate:
            skip_reason = "Duplicate - already owned by another lead record"
        elif lead.contact_channel == "email_only":
            skip_reason = "Email-only lead - not part of the SMS preview"
        elif not lead.phone:
            skip_reason = "No phone number on file"
        elif _is_suppressed(db, lead):
            # REAL GAP CLOSED HERE: this preview previously only checked
            # Lead.status/is_duplicate, never the actual suppression
            # list - confirmed by testing that a manually suppressed
            # number still came back with skip_reason=None and a full
            # draft message ready to send.
            skip_reason = "Phone number is on the suppression list"
        else:
            # Booking link URL isn't actually created yet at preview time
            # (that only happens on real send, to avoid generating dead
            # links for messages that get edited or skipped) - use a
            # placeholder so the draft still reads naturally.
            placeholder_booking_url = f"{BOOKING_BASE_URL}/book/preview"
            draft = render_cadence_message(db, lead, current_user, touch_number=1, booking_url=placeholder_booking_url)

        results.append(MessagePreviewItem(
            lead_id=lead.id, lead_name=lead_name, phone=lead.phone,
            tier=lead.tier.value if lead.tier else None,
            message_track=lead.message_track.value if lead.message_track else None,
            draft_message=draft, skip_reason=skip_reason,
        ))

    return results


class ConfirmSendItem(BaseModel):
    lead_id: str
    message: str  # the (possibly edited) final message text for this lead


class ConfirmSendBatchRequest(BaseModel):
    items: list[ConfirmSendItem]
    include_booking_link: bool = True


@router.post("/confirm-send-batch")
def confirm_send_batch(
    req: ConfirmSendBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The actual send step, AFTER the advisor has reviewed (and possibly
    edited) the drafted messages from /preview-messages. Each item
    carries its own final message text, since the advisor may have
    edited individual ones rather than accepting every AI draft as-is.
    """
    from app.services.sms_service import send_sms

    sent_ids = []
    skipped = []
    for item in req.items:
        lead = db.query(Lead).filter(
            Lead.id == item.lead_id, Lead.organization_id == current_user.organization_id
        ).first()
        if not lead:
            skipped.append({"lead_id": item.lead_id, "reason": "not_found"})
            continue
        try:
            msg = send_sms(db, current_user, lead, item.message, include_booking_link=req.include_booking_link)
            sent_ids.append(msg.id)
            # Start the cadence now that touch 1 has actually gone out -
            # this is what the import flow was missing: leads sat at
            # status=NEW with no cadence ever started unless something
            # else explicitly called start_cadence.
            from app.services.cadence_service import start_cadence
            start_cadence(db, lead)
        except Exception as e:
            skipped.append({"lead_id": item.lead_id, "reason": str(e)})

    return {"sent_count": len(sent_ids), "skipped_count": len(skipped), "sent_ids": sent_ids, "skipped": skipped}
