"""
Auto-Send Queue Router

Phase 1 (candidate review) endpoints - per the explicit, careful design
for this feature, NOTHING in this router ever sends a message without
the advisor explicitly confirming. Listing candidates, viewing one,
editing a draft, and the three resolution actions (confirm, edit+send,
override) all live here.

Every endpoint is scoped to the calling advisor's own candidates only -
an advisor never sees or acts on another advisor's auto-send queue,
same isolation discipline as every other per-advisor resource in this
app.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from datetime import datetime, timezone

from app.deps import get_db, get_current_user
from app.models.models import User, AutoSendCandidate, AutoSendCandidateStatus, Lead
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/auto-send", tags=["auto-send"])


def _get_candidate_for_advisor_or_404(db: Session, candidate_id: str, current_user: User) -> AutoSendCandidate:
    candidate = db.query(AutoSendCandidate).filter(
        AutoSendCandidate.id == candidate_id,
        AutoSendCandidate.advisor_id == current_user.id,
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Auto-send candidate not found.")
    return candidate


@router.get("/queue")
def list_auto_send_candidates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    The Phase 1 review queue - every PENDING candidate for the calling
    advisor, newest first. Resolved candidates (confirmed, edited,
    overridden, expired) don't show here - see /history for those.
    """
    candidates = (
        db.query(AutoSendCandidate, Lead)
        .join(Lead, AutoSendCandidate.lead_id == Lead.id)
        .filter(
            AutoSendCandidate.advisor_id == current_user.id,
            AutoSendCandidate.status == AutoSendCandidateStatus.PENDING,
        )
        .order_by(AutoSendCandidate.created_at.desc())
        .all()
    )

    return [
        {
            "candidate_id": candidate.id,
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name} {lead.last_name}",
            "reply_id": candidate.reply_id,
            "ai_drafted_body": candidate.ai_drafted_body,
            "eligibility_reasoning": candidate.eligibility_reasoning,
            "classification_confidence": candidate.classification_confidence,
            "created_at": candidate.created_at,
        }
        for candidate, lead in candidates
    ]


@router.get("/queue/counts")
def auto_send_queue_counts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Real, true count of pending candidates - for a badge/notification, not derived from a paginated list."""
    count = (
        db.query(AutoSendCandidate)
        .filter(AutoSendCandidate.advisor_id == current_user.id, AutoSendCandidate.status == AutoSendCandidateStatus.PENDING)
        .count()
    )
    return {"pending_count": count}


@router.post("/queue/{candidate_id}/confirm")
def confirm_candidate(
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Sends the AI-drafted body EXACTLY as drafted - the advisor read it,
    agreed with it, and confirmed. Uses send_exact_sms, which reuses
    the same DNC/suppression safety checks every other send path uses -
    this candidate queue is not a way around those checks.
    """
    candidate = _get_candidate_for_advisor_or_404(db, candidate_id, current_user)
    if candidate.status != AutoSendCandidateStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"This candidate is already {candidate.status.value}, not pending.")
    if not candidate.ai_drafted_body:
        raise HTTPException(status_code=400, detail="This candidate has no drafted body to send - use edit-and-send instead.")

    lead = db.query(Lead).filter(Lead.id == candidate.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="The lead for this candidate no longer exists.")

    from app.services.sms_service import send_exact_sms
    try:
        message = send_exact_sms(db, current_user, lead, candidate.ai_drafted_body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    candidate.final_sent_body = candidate.ai_drafted_body
    candidate.status = AutoSendCandidateStatus.CONFIRMED
    candidate.message_id = message.id
    candidate.resolved_at = datetime.now(timezone.utc)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="auto_send.confirm", target_type="auto_send_candidate", target_id=candidate.id,
        details={"lead_id": lead.id, "message_id": message.id},
    )

    return {"candidate_id": candidate.id, "status": candidate.status.value, "message_id": message.id}


class EditAndSendRequest(BaseModel):
    body: str


@router.post("/queue/{candidate_id}/edit-and-send")
def edit_and_send_candidate(
    candidate_id: str,
    req: EditAndSendRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Sends the advisor's EDITED version of the draft, not the original
    AI text. final_sent_body is recorded separately from
    ai_drafted_body specifically so it's always possible to see what
    the AI actually proposed versus what really went out - real signal
    for whether the drafting itself is doing a good job.
    """
    candidate = _get_candidate_for_advisor_or_404(db, candidate_id, current_user)
    if candidate.status != AutoSendCandidateStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"This candidate is already {candidate.status.value}, not pending.")
    if not req.body or not req.body.strip():
        raise HTTPException(status_code=400, detail="body cannot be empty.")

    lead = db.query(Lead).filter(Lead.id == candidate.lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="The lead for this candidate no longer exists.")

    from app.services.sms_service import send_exact_sms
    try:
        message = send_exact_sms(db, current_user, lead, req.body.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    candidate.final_sent_body = req.body.strip()
    candidate.status = AutoSendCandidateStatus.EDITED_SENT
    candidate.message_id = message.id
    candidate.resolved_at = datetime.now(timezone.utc)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="auto_send.edit_and_send", target_type="auto_send_candidate", target_id=candidate.id,
        details={"lead_id": lead.id, "message_id": message.id, "was_edited": req.body.strip() != candidate.ai_drafted_body},
    )

    return {"candidate_id": candidate.id, "status": candidate.status.value, "message_id": message.id}


@router.post("/queue/{candidate_id}/override")
def override_candidate(
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Declines the AI draft entirely - the advisor decided this needs a
    real, normal reply instead, handled exactly like any other reply
    in the Replies inbox. Does NOT send anything itself; just marks
    this candidate resolved so it no longer sits in the review queue,
    out of the advisor's way.
    """
    candidate = _get_candidate_for_advisor_or_404(db, candidate_id, current_user)
    if candidate.status != AutoSendCandidateStatus.PENDING:
        raise HTTPException(status_code=400, detail=f"This candidate is already {candidate.status.value}, not pending.")

    candidate.status = AutoSendCandidateStatus.OVERRIDDEN
    candidate.resolved_at = datetime.now(timezone.utc)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="auto_send.override", target_type="auto_send_candidate", target_id=candidate.id,
        details={"lead_id": candidate.lead_id},
    )

    return {"candidate_id": candidate.id, "status": candidate.status.value}


@router.get("/history")
def auto_send_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every resolved candidate (any status other than pending) for the calling advisor - the real, after-the-fact record of how this queue has actually been used."""
    candidates = (
        db.query(AutoSendCandidate, Lead)
        .join(Lead, AutoSendCandidate.lead_id == Lead.id)
        .filter(
            AutoSendCandidate.advisor_id == current_user.id,
            AutoSendCandidate.status != AutoSendCandidateStatus.PENDING,
        )
        .order_by(AutoSendCandidate.resolved_at.desc())
        .limit(100)
        .all()
    )

    return [
        {
            "candidate_id": candidate.id,
            "lead_id": lead.id,
            "lead_name": f"{lead.first_name} {lead.last_name}",
            "ai_drafted_body": candidate.ai_drafted_body,
            "final_sent_body": candidate.final_sent_body,
            "status": candidate.status.value,
            "resolved_at": candidate.resolved_at,
        }
        for candidate, lead in candidates
    ]
