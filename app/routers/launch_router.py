"""
Launch Engine — the customer's onboarding surface, and the staff view of it.

===========================================================================
THE ACCESS RULE, STATED ONCE
===========================================================================

A customer route NEVER takes an organization id. Not in the path, not in the
body, not in a query string. The org is resolved from the authenticated
session by `lead_scope.active_workspace_org_id`, which is the platform's one
seam for "which workspace is this request in" and already handles workspace
selection, membership validation and executive observation.

That is not stylistic. An `/launch/{org_id}` route is a customer-enumeration
endpoint wearing a feature's clothes: the ids are UUIDs, but they appear in
proposals, invoices and support threads, and the first person to paste the
wrong one reads somebody else's contract terms. There is no org id to guess
because there is no org id parameter.

Staff routes DO take an org id, because that is their whole job — and they go
through `require_god` plus `load_org_in_scope`, which 404s (never 403s) on an
org outside the actor's scope, so the endpoint cannot be used to discover
which ids exist.

===========================================================================
NO SECOND HIERARCHY
===========================================================================

This router creates no new notion of who a customer is, what stage they are
at, or who may act on them. It reads `Implementation` (which already carries
organization_id UNIQUE, platform_id, owner and status) and writes only the
intake tables. Brand comes from `Platform`, customer from `Organization`.
Nothing here knows the name of any particular brand or customer.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Request, Response,
    UploadFile, status,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, load_org_in_scope, require_god
from app.models.implementation_models import Implementation
from app.models.launch_intake_models import LaunchIntakeFile
from app.models.models import Organization, Platform, User
from app.services import launch_intake
from app.routers.audit_log_router import log_action

log = logging.getLogger("launch_router")


def _intake_audit(db: Session, impl: Implementation, actor_id: str, action: str,
                  *, details: Any = None, note: Optional[str] = None) -> None:
    """Record an intake lifecycle event on the SAME audit log everything else uses.

    The implementation history is read from the audit log (§38) and filtered to
    target_type "implementation", so before this the staff timeline could show
    a milestone being ticked but never showed the customer submitting their
    intake or a reviewer reopening it — the two events people actually ask
    about. This adds rows to the existing table through the existing helper; it
    introduces no second activity store, and a failure to audit must never turn
    a successful submission into a failed one.
    """
    try:
        log_action(
            db, impl.organization_id, actor_id,
            action=action,
            target_type="implementation",
            target_id=impl.id,
            platform_id=impl.platform_id,
            brand_sales_org_id=impl.brand_sales_org_id,
            details=details, note=note,
        )
    except Exception:                                   # pragma: no cover
        log.warning("intake audit failed for %s/%s", impl.id, action, exc_info=True)

router = APIRouter(prefix="/launch", tags=["Launch Engine"])
god_router = APIRouter(prefix="/god/launch", tags=["Launch Engine - Staff"])


# ── upload policy ───────────────────────────────────────────────────────────
# Deliberately narrower than "anything the customer has". These are the formats
# an implementation team actually needs, and every one of them is inert — no
# archives (a zip is an unopened box), no executables, no SVG (it is a script
# container that renders as a picture).
MAX_FILE_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_TYPES = {
    "application/pdf",
    "image/jpeg", "image/png", "image/webp", "image/heic",
    "text/csv", "text/plain",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

_NO_LAUNCH = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="No launch has been set up for your workspace yet. Your "
           "implementation team creates it when your project starts.")


# ── resolution ──────────────────────────────────────────────────────────────

def _caller_org_id(user: User, db: Session, request: Request) -> str:
    """The org this request is in, from the SESSION. Never from user input."""
    from app.services.lead_scope import active_workspace_org_id
    org_id = active_workspace_org_id(user, db, request)
    if not org_id:
        # A god with no customer selected, or a brand-sales user with no
        # tenant. Both are "you are not standing in a customer workspace",
        # which is a 409 rather than a refusal of this feature.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No customer workspace is selected for this session.")
    return org_id


def _impl_for_org(db: Session, org_id: str) -> Implementation:
    impl = (db.query(Implementation)
            .filter(Implementation.organization_id == org_id)
            .first())
    if impl is None:
        raise _NO_LAUNCH
    return impl


def _brand_of(db: Session, impl: Implementation) -> Dict[str, Any]:
    """The white-label brand the customer sees.

    Every value is read from the Platform row. A brand with nothing filled in
    still renders, because the UI falls back on shape, not on a hard-coded
    default that would show one brand's name to another brand's customer.
    """
    plat = None
    if impl.platform_id:
        plat = db.query(Platform).filter(Platform.id == impl.platform_id).first()
    if plat is None:
        return {"id": None, "name": "Your implementation team", "short": "—",
                "tagline": None, "logoUrl": None, "supportEmail": None,
                "supportPhone": None, "poweredBy": "AdvisorFlow"}
    short = plat.short_name or plat.logo_initial or (plat.name or "")[:2].upper()
    return {
        "id": plat.id,
        "name": plat.name,
        "short": short,
        "tagline": plat.tagline,
        "logoUrl": plat.logo_url,
        "supportEmail": plat.support_email,
        "supportPhone": plat.support_phone,
        "accent": plat.accent_color,
        "poweredBy": "AdvisorFlow",
    }


def _customer_of(db: Session, org: Organization, user: Optional[User]) -> Dict[str, Any]:
    name = org.name or ""
    return {
        "id": org.id,
        "name": name,
        "short": (org.brand_name or name or "")[:2].upper(),
        "logoUrl": org.brand_logo_url,
        "industry": org.industry,
        "user": None if user is None else {
            "name": user.full_name,
            "email": user.email,
            "title": getattr(user, "job_title", None),
            "initials": "".join(p[0] for p in (user.full_name or "").split()[:2]).upper(),
        },
    }


def _payload(db: Session, impl: Implementation, org: Organization,
             user: Optional[User]) -> Dict[str, Any]:
    ov = launch_intake.overview(db, impl.id, org.id)
    sub = launch_intake.latest_submission(db, impl.id, org.id)
    return {
        "implementation": {
            "id": impl.id,
            "status": impl.status,
            "target_launch_date": impl.target_launch_date.isoformat()
                                  if impl.target_launch_date else None,
            "owner_user_id": impl.owner_user_id,
            "kickoff_at": impl.kickoff_at.isoformat() if impl.kickoff_at else None,
        },
        "brand": _brand_of(db, impl),
        "customer": _customer_of(db, org, user),
        # Derived from the implementation's own status, so the tracker and the
        # record can never disagree about where the project is.
        "lifecycle": launch_intake.lifecycle_for(impl.status),
        "overview": ov,
        "submission": None if sub is None else {
            "id": sub.id,
            "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            "signed_name": sub.signed_name,
            "reviewed_at": sub.reviewed_at.isoformat() if sub.reviewed_at else None,
        },
        "blockers": launch_intake.submission_blockers(db, impl.id, org.id),
    }


# ── schema (no customer data) ───────────────────────────────────────────────

@router.get("/config")
def launch_config(_user: User = Depends(get_current_user)) -> dict:
    """The step schema the UI renders. Authenticated, but tenant-independent.

    Behind auth even though it holds no customer data: it is a precise
    description of what this platform asks its customers for, which is
    competitive information and costs nothing to keep private.
    """
    return {"steps": launch_intake.STEP_SCHEMA,
            "lifecycle": launch_intake.LIFECYCLE,
            "upload": {"max_bytes": MAX_FILE_BYTES,
                       "allowed_types": sorted(ALLOWED_UPLOAD_TYPES)}}


# ── the customer's own launch ───────────────────────────────────────────────

@router.get("/me")
def my_launch(request: Request,
              db: Session = Depends(get_db),
              user: User = Depends(get_current_user)) -> dict:
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if org is None:
        raise _NO_LAUNCH
    return _payload(db, impl, org, user)


@router.get("/me/steps/{step_key}")
def my_step(step_key: str, request: Request,
            db: Session = Depends(get_db),
            user: User = Depends(get_current_user)) -> dict:
    if step_key not in launch_intake.STEP_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown step")
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)
    return launch_intake.read_step(db, impl.id, org_id, step_key)


@router.get("/me/summary")
def my_summary(request: Request,
               db: Session = Depends(get_db),
               user: User = Depends(get_current_user)) -> dict:
    """Every step's answers at once, for the customer's own Review screen.

    The wizard loads one step at a time, which is right for editing and wrong
    for the final read-through: eight sequential requests to show one summary
    is eight chances to half-render. This is the same `read_step` the wizard
    already uses, looped — so secrets come back as `secrets_set` (key names,
    never values) here exactly as they do everywhere else, and there is no
    second serialiser to keep in step.

    Same access rule as the rest of `/launch/me`: no org id is accepted, the
    workspace comes from the session.
    """
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)
    return {"answers": {k: launch_intake.read_step(db, impl.id, org_id, k)
                        for k in launch_intake.STEP_KEYS}}


class StepSave(BaseModel):
    answers: Dict[str, Any] = {}


@router.put("/me/steps/{step_key}")
def save_my_step(step_key: str, body: StepSave, request: Request,
                 db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)) -> dict:
    """Save Draft and Save & Continue are the SAME write.

    The distinction is navigation, not persistence. Making "continue" the only
    thing that persists is how a customer who closes the tab on the last step
    loses an afternoon.
    """
    if step_key not in launch_intake.STEP_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown step")
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)

    sub = launch_intake.latest_submission(db, impl.id, org_id)
    if sub is not None and sub.reviewed_at is None:
        # Submitted and not yet looked at: freeze it. Editing underneath a
        # signed-off snapshot makes the sign-off meaningless.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Your intake has been submitted and is with the "
                   "implementation team. Contact them to reopen it.")
    try:
        return launch_intake.save_step(db, impl.id, org_id, step_key,
                                       body.answers or {}, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/me/submit")
def submit_my_launch(request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)) -> dict:
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)

    existing = launch_intake.latest_submission(db, impl.id, org_id)
    if existing is not None and existing.reviewed_at is None:
        raise HTTPException(status_code=409,
                            detail="This intake has already been submitted.")
    try:
        sub = launch_intake.submit(db, impl.id, org_id, user.id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": "Some required information is still missing.",
                    "blockers": launch_intake.submission_blockers(db, impl.id, org_id)})

    # Move the implementation off not_started, and never backwards. The intake
    # is the first phase of the project, so submitting it is real progress on
    # the record that already tracks progress — but a customer action must not
    # drag an implementation that is already testing back to configuration.
    from app.models.implementation_models import IMPL_NOT_STARTED, IMPL_CONFIGURATION
    if impl.status in (IMPL_NOT_STARTED, None):
        impl.status = IMPL_CONFIGURATION
    impl.last_activity_at = datetime.utcnow()
    _intake_audit(db, impl, user.id, "launch_intake_submitted",
                  details={"submission_id": sub.id,
                           "signed_name": sub.signed_name})
    db.commit()

    return {"submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
            "id": sub.id, "implementation_status": impl.status}


# ── files ───────────────────────────────────────────────────────────────────

def _file_row(db: Session, file_id: str, org_id: str) -> LaunchIntakeFile:
    row = (db.query(LaunchIntakeFile)
           .filter(LaunchIntakeFile.id == file_id,
                   # The isolation filter, in the same query as the lookup so
                   # it cannot be applied later and forgotten.
                   LaunchIntakeFile.organization_id == org_id,
                   LaunchIntakeFile.deleted_at.is_(None))
           .first())
    if row is None:
        # 404 for "not yours" as well as "not there" — a 403 would confirm the
        # id exists, which is how you enumerate another tenant's documents.
        raise HTTPException(status_code=404, detail="File not found")
    return row


@router.get("/me/files")
def list_my_files(request: Request,
                  db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)) -> dict:
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)
    rows = (db.query(LaunchIntakeFile)
            .filter(LaunchIntakeFile.implementation_id == impl.id,
                    LaunchIntakeFile.organization_id == org_id,
                    LaunchIntakeFile.deleted_at.is_(None))
            .order_by(LaunchIntakeFile.created_at.desc())
            .all())
    return {"files": [_file_public(r) for r in rows]}


def _file_public(r: LaunchIntakeFile) -> dict:
    return {"id": r.id, "filename": r.filename, "content_type": r.content_type,
            "file_size": r.file_size, "label": r.label, "step_key": r.step_key,
            "created_at": r.created_at.isoformat() if r.created_at else None}


@router.post("/me/files")
async def upload_my_file(request: Request,
                         file: UploadFile = File(...),
                         step_key: Optional[str] = Form(None),
                         label: Optional[str] = Form(None),
                         db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)) -> dict:
    org_id = _caller_org_id(user, db, request)
    impl = _impl_for_org(db, org_id)

    ctype = (file.content_type or "").lower().split(";")[0].strip()
    if ctype not in ALLOWED_UPLOAD_TYPES:
        raise HTTPException(status_code=400,
                            detail="That file type is not accepted here.")

    data = await file.read()
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="That file is larger than %d MB." % (MAX_FILE_BYTES // (1024 * 1024)))
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")

    # The customer's filename is kept for display and never used to build a
    # path. Nothing here writes to disk, so traversal has nowhere to go, but
    # the name is still trimmed rather than trusted wholesale.
    safe_name = (file.filename or "upload")[-255:]

    row = LaunchIntakeFile(
        implementation_id=impl.id,
        organization_id=org_id,           # from the implementation, not the form
        step_key=step_key if step_key in launch_intake.STEP_BY_KEY else None,
        label=(label or None) and str(label)[:160],
        filename=safe_name, content_type=ctype, file_size=len(data),
        file_data=data, uploaded_by=user.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _file_public(row)


@router.get("/me/files/{file_id}/download")
def download_my_file(file_id: str, request: Request,
                     db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    org_id = _caller_org_id(user, db, request)
    row = _file_row(db, file_id, org_id)
    return Response(
        content=row.file_data or b"",
        media_type=row.content_type or "application/octet-stream",
        headers={
            # `attachment` so a browser saves rather than renders. A customer
            # -supplied HTML or SVG rendered inline on the app's own origin is
            # stored XSS; downloading it is inert.
            "Content-Disposition": 'attachment; filename="%s"' % row.filename,
            "X-Content-Type-Options": "nosniff",
        })


@router.delete("/me/files/{file_id}")
def delete_my_file(file_id: str, request: Request,
                   db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)) -> dict:
    org_id = _caller_org_id(user, db, request)
    row = _file_row(db, file_id, org_id)
    row.deleted_at = datetime.utcnow()
    row.deleted_by = user.id
    row.file_data = None          # bytes go; the record of the act stays
    db.commit()
    return {"deleted": True, "id": file_id}


# ── staff view ──────────────────────────────────────────────────────────────

@god_router.get("")
def staff_list(db: Session = Depends(get_db),
               actor: User = Depends(require_god)) -> dict:
    """Every implementation with its intake standing.

    Deliberately reports implementations that have NO intake rows yet as
    `not_started` rather than omitting them. A list that only shows customers
    who have begun is a list that hides the ones who never did, which are
    exactly the ones somebody needs to call.
    """
    from app.services import implementation_service as impl_svc

    impls = db.query(Implementation).all()

    # THE TWO PROGRESS CONCEPTS ARE BOTH REPORTED, AND SEPARATELY NAMED.
    # A screen that shows "100%" without saying 100% of WHAT is the exact
    # confusion this list caused: a customer at 8/8 intake sections and 0/8
    # build milestones looked like a contradiction rather than like somebody
    # who has done their homework before the team has started.
    #
    # Batched, not per-row: `completion_for_many` is one query for every
    # implementation on the page.
    impl_completion = impl_svc.completion_for_many(db, [i.id for i in impls])

    # Brand names in one query too, so a list of fifty customers does not make
    # fifty platform lookups to print a label.
    plat_ids = {i.platform_id for i in impls if i.platform_id}
    plat_names = {}
    if plat_ids:
        for p in db.query(Platform).filter(Platform.id.in_(plat_ids)).all():
            plat_names[p.id] = p.name

    out = []
    for impl in impls:
        org = (db.query(Organization)
               .filter(Organization.id == impl.organization_id).first())
        ov = launch_intake.overview(db, impl.id, impl.organization_id)
        sub = launch_intake.latest_submission(db, impl.id, impl.organization_id)
        if sub is not None and sub.reviewed_at is None:
            intake_state = "submitted"
        elif sub is not None:
            intake_state = "reviewed"
        elif ov["overall_pct"] > 0:
            intake_state = "in_progress"
        else:
            intake_state = "not_started"
        c = impl_completion.get(impl.id) or {
            "total": 0, "settled": 0, "percent": 0, "required_open": [], "blocked": []}

        # WHAT IS ACTUALLY HOLDING THIS UP, separated into the two kinds that
        # call for different responses. A blocker means somebody is stuck; a
        # warning means somebody should look. Merging them is how a screen ends
        # up with one amber box nobody reads.
        blockers, warnings = [], []
        if c["blocked"]:
            blockers += ["Milestone blocked: %s" % m["label"] for m in c["blocked"]]
        if impl.status == "blocked":
            blockers.append(impl.blocker_note or "Implementation is blocked")
        if intake_state == "submitted":
            warnings.append("Intake submitted and waiting on review")
        if intake_state == "not_started":
            warnings.append("Customer has not started their intake")
        if impl.owner_user_id is None:
            warnings.append("No implementation owner assigned")
        if impl.target_launch_date is None:
            warnings.append("No target launch date set")

        out.append({
            "implementation_id": impl.id,
            "organization_id": impl.organization_id,
            "organization_name": org.name if org else None,
            "platform_id": impl.platform_id,
            "brand_name": plat_names.get(impl.platform_id),
            "implementation_status": impl.status,
            "implementation_owner_id": impl.owner_user_id,

            # ── customer intake: the eight questionnaire sections ──────────
            "intake_state": intake_state,
            "intake_pct": ov["overall_pct"],
            "intake_complete_steps": ov["complete_steps"],
            "intake_total_steps": ov["total_steps"],
            # Kept under their original names too: other callers (and the
            # mobile app) already read these, and renaming a field to improve a
            # label is how you break a client to fix a screen.
            "overall_pct": ov["overall_pct"],
            "complete_steps": ov["complete_steps"],
            "total_steps": ov["total_steps"],

            # ── implementation: the internal build milestones ──────────────
            "implementation_pct": c["percent"],
            "implementation_settled": c["settled"],
            "implementation_total": c["total"],
            "implementation_required_open": c["required_open"],

            "file_count": ov["file_count"],
            "target_launch_date": impl.target_launch_date.isoformat()
                                  if impl.target_launch_date else None,
            "submitted_at": sub.submitted_at.isoformat()
                            if sub and sub.submitted_at else None,
            "reviewed_at": sub.reviewed_at.isoformat()
                           if sub and sub.reviewed_at else None,
            "blockers": blockers,
            "warnings": warnings,
        })
    out.sort(key=lambda r: (r["intake_state"] != "submitted", r["organization_name"] or ""))
    return {"launches": out, "total": len(out)}


@god_router.get("/{organization_id}")
def staff_detail(organization_id: str,
                 db: Session = Depends(get_db),
                 actor: User = Depends(require_god)) -> dict:
    # 404s on anything outside the actor's scope, so this cannot enumerate.
    org = load_org_in_scope(db, actor, organization_id)
    impl = _impl_for_org(db, org.id)
    payload = _payload(db, impl, org, None)
    payload["answers"] = {
        k: launch_intake.read_step(db, impl.id, org.id, k)
        for k in launch_intake.STEP_KEYS
    }
    rows = (db.query(LaunchIntakeFile)
            .filter(LaunchIntakeFile.implementation_id == impl.id,
                    LaunchIntakeFile.deleted_at.is_(None))
            .order_by(LaunchIntakeFile.created_at.desc()).all())
    payload["files"] = [_file_public(r) for r in rows]
    return payload


@god_router.get("/{organization_id}/files/{file_id}/download")
def staff_download(organization_id: str, file_id: str,
                   db: Session = Depends(get_db),
                   actor: User = Depends(require_god)):
    org = load_org_in_scope(db, actor, organization_id)
    row = _file_row(db, file_id, org.id)
    return Response(
        content=row.file_data or b"",
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="%s"' % row.filename,
                 "X-Content-Type-Options": "nosniff"})


class ReviewBody(BaseModel):
    note: Optional[str] = None


@god_router.post("/{organization_id}/review")
def staff_review(organization_id: str, body: ReviewBody,
                 db: Session = Depends(get_db),
                 actor: User = Depends(require_god)) -> dict:
    """Acknowledge a submitted intake, which also reopens it for edits."""
    org = load_org_in_scope(db, actor, organization_id)
    impl = _impl_for_org(db, org.id)
    sub = launch_intake.latest_submission(db, impl.id, org.id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Nothing has been submitted yet.")
    sub.reviewed_at = datetime.utcnow()
    sub.reviewed_by = actor.id
    sub.review_note = (body.note or None)
    impl.last_activity_at = datetime.utcnow()
    # One staff action with two consequences, so it is audited as both: the
    # review happened, and the intake became editable again. A reader of the
    # history should not have to know that "reviewed" implies "reopened".
    _intake_audit(db, impl, actor.id, "launch_intake_reviewed",
                  details={"submission_id": sub.id}, note=sub.review_note)
    _intake_audit(db, impl, actor.id, "launch_intake_reopened",
                  details={"submission_id": sub.id})
    db.commit()
    return {"reviewed_at": sub.reviewed_at.isoformat(), "id": sub.id}
