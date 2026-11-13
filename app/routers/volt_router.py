"""VOLT Marketing recruiting program (Issue #535).

    PUBLIC (no login)                         /volt/public/{slug}/...
        GET  ''                     portal: open and closed projects
        POST /register              account + profile + serial + first draft application
                                    (referral input is ignored: referrals are not launching)
        POST /login                 applicant sign-in
    APPLICANT (any signed-in login; role volt_rep is fenced to /volt/rep/* in deps)
                                              /volt/rep/{slug}/...
    STAFF (god owner oversight, or org_admin/manager membership in the VOLT org)
                                              /volt/ops/{org_id}/...

Every applicant endpoint resolves the caller's OWN profile from the session. No
applicant endpoint accepts a rep, user or organization id that widens access.
Every staff endpoint re-checks authority for the organization in the path.
"""
from __future__ import annotations

import json

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.models.volt_models import (
    VoltApplicantAccess, VoltExportPreset, VoltApplication, VoltDocument, VoltProject, VoltRep, VoltSession, VoltSessionAttendee, VoltTalentSignup,
)
from app.services.volt import common as C
from app.services.volt import core, docs, hiring, mail, reports, sessions, talent

from app.services.volt import access as volt_access


def _volt_guard(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """EVERY applicant and staff VOLT request: a suspended or removed login,
    or a session VOLT revoked, is refused here - before any endpoint runs,
    downloads included. The owner is never refused."""
    pp = request.path_params or {}
    org_id = pp.get("org_id")
    if org_id is None and pp.get("slug") is not None:
        org_id = C.program_by_slug(db, pp["slug"]).organization_id
    if org_id:
        volt_access.enforce(db, user, org_id, getattr(request.state, "auth_session", None))


public_router = APIRouter(prefix="/volt/public", tags=["volt-public"])
rep_router = APIRouter(prefix="/volt/rep", tags=["volt-rep"], dependencies=[Depends(_volt_guard)])
ops_router = APIRouter(prefix="/volt/ops", tags=["volt-ops"], dependencies=[Depends(_volt_guard)])


def _send_after(background: BackgroundTasks, row) -> None:
    """Send a queued VOLT email after the response, from its own session.
    The row is committed with the request, so a lost background task is
    picked up by the email poller's retry pass."""
    if row is not None and row.state == "queued":
        background.add_task(mail.flush_in_background, [row.id])


def _token(db, user, request):
    from app.services.auth_service import create_access_token
    return create_access_token(user, db, request=request)


# ═════════════════════════════════════════════════════════════ PUBLIC ══════

@public_router.get("/{slug}")
@limiter.limit("120/minute")
def portal(request: Request, slug: str, db: Session = Depends(get_db)):
    prog = C.program_by_slug(db, slug)
    ps = [C.project_dict(p) for p in C.projects(db, prog.organization_id)]
    for p in ps:
        p.pop("required_docs", None)
    return {"name": prog.name, "slug": prog.slug, "availability_options": hiring.AVAILABILITY,
            "open_projects": [p for p in ps if p["recruiting"]],
            "past_projects": [p for p in ps if not p["recruiting"]]}


@public_router.post("/{slug}/register")
@limiter.limit("30/minute")
def register(request: Request, slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    # (No BackgroundTasks parameter: the rate limiter's wrapper cannot resolve
    # it. The task rides on the response instead - same effect.)
    from fastapi.responses import JSONResponse
    from starlette.background import BackgroundTask
    prog = C.program_by_slug(db, slug)
    user, rep, app = core.register(db, prog, body)
    db.flush()
    row = mail.on_registered(db, prog, rep, app)
    db.commit()
    out = {"access_token": _token(db, user, request), "token_type": "bearer", "rep": C.public_rep(rep),
           "application_id": app.id}
    task = BackgroundTask(mail.flush_in_background, [row.id]) if row is not None and row.state == "queued" else None
    return JSONResponse(out, background=task)


@public_router.post("/{slug}/talent-pool")
@limiter.limit("10/minute")
def talent_signup(request: Request, slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    """Get Future Opportunities. Saves interest only - never an account or an
    application - and answers the same way for new, repeated and existing
    applicant emails."""
    prog = C.program_by_slug(db, slug)
    talent.signup(db, prog, body)
    db.commit()
    return {"ok": True, "message": "You're on the list. We saved your interest in future VOLT Marketing opportunities."}


@public_router.post("/{slug}/login")
@limiter.limit("10/minute")
def login(request: Request, slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    from app.routers.auth_router import _login_clear_failures, _login_record_failure, _login_throttle_check
    from app.services.auth_service import authenticate_user
    prog = C.program_by_slug(db, slug)
    email = str(body.get("email") or "").strip().lower()
    _login_throttle_check(request, email, db)
    user = authenticate_user(db, email, str(body.get("password") or ""))
    if user is None:
        _login_record_failure(request, email, db)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    if volt_access.blocked(db, prog.organization_id, user):
        # Same message for suspended and removed; nothing else is revealed.
        raise HTTPException(status_code=403, detail={
            "message": "Your VOLT Marketing access is not active. Contact support@voltmarketing.live.",
            "code": "volt_access_inactive"})
    _login_clear_failures(request, email, db)
    rep = C.rep_for_user(db, prog, user)
    return {"access_token": _token(db, user, request), "token_type": "bearer",
            "has_profile": rep is not None, "rep": C.public_rep(rep),
            "staff_access": C.staff_access(db, user, prog.organization_id)}


# ═════════════════════════════════════════════════════════════ APPLICANT ═══

def _me(db, slug, user):
    prog = C.program_by_slug(db, slug)
    rep = C.rep_for_user(db, prog, user)
    return prog, rep


def _need_rep(rep):
    if rep is None:
        raise HTTPException(status_code=404, detail={"message": "Start your VOLT profile first.", "code": "no_profile"})
    return rep


@rep_router.get("/{slug}/me")
def me(slug: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    if rep is None:
        return {"has_profile": False, "account": {"email": user.email, "full_name": user.full_name},
                "staff_access": C.staff_access(db, user, prog.organization_id)}
    apps = (db.query(VoltApplication).filter(VoltApplication.rep_serial == rep.serial)
            .order_by(VoltApplication.created_at.asc()).all())
    app_dicts = [core.application_dict(db, prog, rep, a) for a in apps]
    for d, a in zip(app_dicts, apps):
        d["zoom"] = _zoom_state(db, a)
    profile_docs = core.requirements(db, prog, rep, None)
    doc_requests = [dict(d, scope="Profile") for d in profile_docs if d["state"] in ("requested", "replace")]
    for a in app_dicts:
        if a["editable"]:
            doc_requests += [dict(d, scope=a["project"]["short_name"], application_id=a["id"])
                             for d in a["documents"] if d["state"] in ("requested", "replace")]
    return {"has_profile": True, "profile": core.rep_profile_dict(rep, user, private=True),
            "hiring": hiring.profile_dict(rep), "hiring_missing": hiring.missing_profile(rep),
            "availability_options": hiring.AVAILABILITY,
            "applications": app_dicts,
            "profile_documents": profile_docs, "document_requests": doc_requests,
            "sessions": sessions.for_rep(db, rep),
            "updates": _updates(db, prog, rep, apps),
            "support_email": mail.support_address(prog),
            "staff_access": C.staff_access(db, user, prog.organization_id)}


def _zoom_state(db, a: VoltApplication) -> Dict[str, Any]:
    """The Zoom step for ONE application: waiting | choose | booked."""
    b = sessions.active_booking(db, a)
    if b is not None:
        return {"state": "booked", "stage": a.schedule_stage, "session_id": b.session_id}
    if sessions.eligible(a):
        return {"state": "choose", "stage": a.schedule_stage}
    return {"state": "waiting", "stage": None}


_UPDATE_ACTIONS = {"status": "Application status", "zoom_booked": "Zoom booked",
                   "zoom_booking_canceled": "Zoom booking canceled", "schedule_stage": "Zoom step",
                   "reviewed": "Document reviewed"}


def _updates(db, prog, rep, apps) -> List[Dict[str, Any]]:
    """Staff updates the applicant may see: stage changes, document reviews,
    the Zoom step. Never internal notes, never who on staff did it."""
    from app.models.volt_models import VoltAuditEvent, VoltDocument
    ids = [a.id for a in apps]
    doc_ids = [d.id for d in db.query(VoltDocument.id).filter(VoltDocument.rep_serial == rep.serial).all()]
    from sqlalchemy import and_, or_
    conds = []
    if ids:
        conds.append(and_(VoltAuditEvent.entity_type == "application", VoltAuditEvent.entity_id.in_(ids)))
    if doc_ids:
        conds.append(and_(VoltAuditEvent.entity_type == "document", VoltAuditEvent.entity_id.in_(doc_ids)))
    if not conds:
        return []
    rows = (db.query(VoltAuditEvent).filter(VoltAuditEvent.organization_id == prog.organization_id, or_(*conds),
                                            VoltAuditEvent.action.in_(list(_UPDATE_ACTIONS)))
            .order_by(VoltAuditEvent.at.desc()).limit(40).all())
    out = []
    for r in rows:
        if r.action == "status":
            text = "Status: %s" % C.STATUS_LABELS.get(r.new_value, r.new_value)
            if r.new_value == "needs_update" and r.reason:
                text += " - " + r.reason
        elif r.action == "schedule_stage":
            text = ("Zoom %s step opened" % r.new_value) if r.new_value else "Zoom step paused"
        elif r.action == "reviewed":
            text = "Document review: %s" % ({"accepted": "accepted", "replace": "replacement requested"}
                                             .get(r.new_value, r.new_value or "updated"))
        else:
            text = _UPDATE_ACTIONS[r.action]
        out.append({"at": C.iso(r.at), "text": text,
                    "application_id": r.entity_id if r.entity_type == "application" else None})
    return out


@rep_router.post("/{slug}/profile")
def create_profile(slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    prog = C.program_by_slug(db, slug)
    rep, app = core.create_profile_for_user(db, prog, user, body)
    db.commit()
    return {"rep": C.public_rep(rep), "application_id": app.id}


@rep_router.patch("/{slug}/profile")
def edit_profile(slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    core.update_profile(db, prog, _need_rep(rep), body)
    db.commit()
    return core.rep_profile_dict(rep, user, private=True)


@rep_router.put("/{slug}/hiring-profile")
def put_hiring_profile(slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Draft save of the person-level hiring file (location, availability,
    experience). Saves partial work; submit decides what is required."""
    prog, rep = _me(db, slug, user)
    hiring.save_profile(_need_rep(rep), body)
    db.commit()
    return {"saved": True, "hiring": hiring.profile_dict(rep), "hiring_missing": hiring.missing_profile(rep)}


@rep_router.post("/{slug}/applications")
def start_app(slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    app = core.start_application(db, prog, _need_rep(rep), body)
    db.commit()
    return core.application_dict(db, prog, rep, app)


@rep_router.get("/{slug}/applications/{app_id}")
def get_app(slug: str, app_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    app = core.own_application(db, _need_rep(rep), app_id)
    out = core.application_dict(db, prog, rep, app)
    out["profile_documents"] = core.requirements(db, prog, rep, None)
    out["hiring"] = hiring.profile_dict(rep)
    out["profile"] = core.rep_profile_dict(rep, user, private=True)
    out["availability_options"] = hiring.AVAILABILITY
    out["missing"] = core.missing_items(db, prog, rep, app) if app.status in ("draft", "needs_update") else []
    return out


@rep_router.put("/{slug}/applications/{app_id}/answers")
def put_answers(slug: str, app_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    app = core.own_application(db, _need_rep(rep), app_id)
    core.save_answers(db, app, body.get("answers") or {})
    db.commit()
    return {"saved": True, "updated_at": C.iso(app.updated_at)}


@rep_router.post("/{slug}/applications/{app_id}/submit")
def submit_app(slug: str, app_id: str, background: BackgroundTasks, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    app = core.own_application(db, _need_rep(rep), app_id)
    was = app.status
    core.submit(db, prog, rep, app, user)
    if app.status != was:
        _send_after(background, mail.on_submitted(db, prog, rep, app))
    db.commit()
    return core.application_dict(db, prog, rep, app)


@rep_router.post("/{slug}/documents")
async def upload_doc(slug: str, file: UploadFile = File(...), doc_key: str = Form(...),
                     upload_key: str = Form(...), application_id: Optional[str] = Form(None),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    d = await docs.upload(db, prog, _need_rep(rep), file, doc_key, application_id or None, upload_key)
    db.commit()
    return core.doc_dict(d)


def _file_response(db, d: VoltDocument):
    data = docs.fetch_bytes(db, d)
    safe = "".join(ch for ch in d.filename if ch.isalnum() or ch in "._- ")[:120] or "document"
    return Response(content=data, media_type=d.content_type,
                    headers={"Content-Disposition": 'inline; filename="%s"' % safe,
                             "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@rep_router.get("/{slug}/documents/{doc_id}/file")
def own_file(slug: str, doc_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    d = db.get(VoltDocument, doc_id)
    if rep is None or d is None or d.rep_serial != rep.serial or d.organization_id != prog.organization_id:
        raise HTTPException(status_code=404, detail="File not found.")
    return _file_response(db, d)


# ── Zoom: the applicant's own scheduling step ───────────────────────────────

@rep_router.get("/{slug}/applications/{app_id}/zoom-times")
def zoom_times(slug: str, app_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Times THIS application may choose. Empty until staff open the step.
    Never a join link."""
    prog, rep = _me(db, slug, user)
    a = core.own_application(db, _need_rep(rep), app_id)
    return {"state": _zoom_state(db, a), "times": sessions.available_for(db, a)}


@rep_router.post("/{slug}/applications/{app_id}/zoom-booking")
def zoom_book(slug: str, app_id: str, background: BackgroundTasks, body: Dict[str, Any] = Body(...),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    _need_rep(rep)
    row, created = sessions.book(db, rep, app_id, str(body.get("session_id") or ""), user)
    a = db.get(VoltApplication, app_id)
    s = db.get(VoltSession, row.session_id)
    email = mail.on_booking(db, prog, rep, a, s, row, "confirmed") if created else None
    db.commit()
    _send_after(background, email)
    return {"booked": True, "created": created, "zoom": _zoom_state(db, a),
            "session": [x for x in sessions.for_rep(db, rep) if x["id"] == s.id][0],
            "application": core.application_dict(db, prog, rep, a)}


@rep_router.post("/{slug}/applications/{app_id}/zoom-booking/cancel")
def zoom_cancel(slug: str, app_id: str, background: BackgroundTasks, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    _need_rep(rep)
    row = sessions.cancel_booking(db, rep, app_id, user)
    a = db.get(VoltApplication, app_id)
    email = mail.on_booking(db, prog, rep, a, db.get(VoltSession, row.session_id), row, "canceled") if row else None
    db.commit()
    _send_after(background, email)
    return {"canceled": row is not None, "zoom": _zoom_state(db, a)}


# ═════════════════════════════════════════════════════════════ STAFF ═══════

def _staff(db, user, org_id, write=False):
    access = C.require_staff(db, user, org_id, write=write)
    return C.program_for_org(db, org_id), access


def _app_in(db, org_id, app_id) -> VoltApplication:
    a = db.get(VoltApplication, app_id)
    if a is None or a.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Application not found.")
    return a


def _rep_in(db, org_id, serial) -> VoltRep:
    try:
        r = db.get(VoltRep, int(serial))
    except (TypeError, ValueError):
        r = None
    if r is None or r.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Rep not found.")
    return r


def _session_in(db, org_id, sid) -> VoltSession:
    s = db.get(VoltSession, sid)
    if s is None or s.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Session not found.")
    return s


def _dev_principal(db, user, org_id) -> bool:
    from app.services.volt import devrelay
    return devrelay.is_principal(db, user, org_id)


# Restricted logins receive a separate, flat applicant view. The shared
# staff dossier includes documents, audit history and other projects.
ACA_FIELDS = (
    "applicant", "first_name", "last_name", "email", "phone",
    "rep_code", "status_label", "city", "state", "earliest_start",
    "availability", "outreach_experience", "comfortable_explaining", "languages",
    "created_at", "submitted_at", "approved_at", "started_at",
)


def _applicant_scope(db, user, org_id, params):
    access = C.require_staff(db, user, org_id, applicant_read=True)
    filters = reports.filters_from(dict(params))
    if access != "aca_applicants":
        return access, filters, None
    project = C.project_by_key(db, org_id, "aca")
    if filters["project_id"] not in (None, project.id):
        raise HTTPException(status_code=403, detail="This login has ACA applicant access only.")
    filters = {key: filters[key] for key in ("status", "reached", "date_from", "date_to")}
    filters["project_id"] = project.id
    grant = db.get(VoltApplicantAccess, (org_id, user.id))
    visible = _grant_fields(grant, "view_fields")
    if "status_label" not in visible:
        filters["status"] = None
    if reports.REACHED[filters["reached"]] not in visible:
        filters.update(reached="created", date_from=None, date_to=None)
    return access, filters, grant


def _columns(values, allowed, *, empty=False):
    if (not isinstance(values, list) or len(values) > len(allowed)
            or any(not isinstance(key, str) or key not in allowed for key in values)
            or len(set(values)) != len(values) or (not empty and not values)):
        raise HTTPException(status_code=422, detail="Choose unique, authorized applicant fields.")
    return values


def _grant_fields(grant, attribute):
    return [key for key in C.loads(getattr(grant, attribute), []) if key in ACA_FIELDS]


@ops_router.get("/{org_id}/export-options")
def ops_export_options(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    access, _, grant = _applicant_scope(db, user, org_id, {})
    allowed = _grant_fields(grant, "export_fields") if grant else list(reports.CSV_COLUMNS)
    default = [key for key in allowed if grant or key in reports.DEFAULT_CSV_COLUMNS]
    presets = db.query(VoltExportPreset).filter_by(organization_id=org_id, user_id=user.id).order_by(
        VoltExportPreset.name).all()
    return {"columns": {key: reports.CSV_COLUMNS[key] for key in allowed}, "default_columns": default,
            "view_columns": {key: reports.CSV_COLUMNS[key] for key in _grant_fields(grant, "view_fields")} if grant else {},
            "aca_fields": {key: reports.CSV_COLUMNS[key] for key in ACA_FIELDS} if access == "owner" else {},
            "presets": [{"name": item.name, "columns": keys} for item in presets
                        if (keys := C.loads(item.columns, [])) and all(key in allowed for key in keys)]}


@ops_router.put("/{org_id}/export-options/presets")
def ops_save_export_preset(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    _, _, grant = _applicant_scope(db, user, org_id, {})
    allowed = _grant_fields(grant, "export_fields") if grant else reports.CSV_COLUMNS
    name = body.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise HTTPException(status_code=422, detail="Enter a preset name of up to 80 characters.")
    columns = _columns(body.get("columns"), allowed)
    db.merge(VoltExportPreset(organization_id=org_id, user_id=user.id, name=name.strip(),
                              columns=json.dumps(columns)))
    db.commit()
    return {"name": name.strip(), "columns": columns}


@ops_router.put("/{org_id}/aca-access")
def ops_aca_access(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    _, access = _staff(db, user, org_id, write=True)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the VOLT owner can manage ACA applicant access.")
    email = body.get("email")
    active = body.get("active")
    if not isinstance(email, str) or not isinstance(active, bool):
        raise HTTPException(status_code=422, detail="Provide an existing login email and access status.")
    target = db.query(User).filter(func.lower(User.email) == email.strip().lower()).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Existing login not found.")
    if target.role in ("god_admin", "volt_rep"):
        raise HTTPException(status_code=422, detail="Select a staff login.")
    view_fields = _columns(body.get("view_fields"), ACA_FIELDS, empty=True)
    export_fields = _columns(body.get("export_fields"), view_fields, empty=True)
    db.merge(VoltApplicantAccess(organization_id=org_id, user_id=target.id, active=active,
                                 view_fields=json.dumps(view_fields), export_fields=json.dumps(export_fields)))
    C.audit(db, org_id, user, "access", target.id, "aca_applicant_access",
            new=json.dumps({"active": active, "view_fields": view_fields, "export_fields": export_fields}))
    db.commit()
    return {"email": target.email, "active": active, "view_fields": view_fields, "export_fields": export_fields}


@ops_router.get("/{org_id}/aca-access")
def ops_aca_access_list(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, access = _staff(db, user, org_id)
    if access != "owner":
        raise HTTPException(status_code=403, detail="Only the VOLT owner can manage ACA applicant access.")
    grants = db.query(VoltApplicantAccess, User).join(User, User.id == VoltApplicantAccess.user_id).filter(
        VoltApplicantAccess.organization_id == org_id).order_by(User.email).all()
    return {"items": [{"email": target.email, "active": grant.active,
                        "view_fields": _grant_fields(grant, "view_fields"),
                        "export_fields": _grant_fields(grant, "export_fields")} for grant, target in grants]}


@ops_router.get("/{org_id}")
def ops_home(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    access = C.require_staff(db, user, org_id, applicant_read=True)
    prog = C.program_for_org(db, org_id)
    if access == "aca_applicants":
        project = C.project_by_key(db, org_id, "aca")
        return {"access": access, "me": {"email": user.email, "full_name": user.full_name},
                "projects": [{"id": project.id, "key": project.key, "name": project.name}]}
    f = reports.filters_from(dict(request.query_params))
    return {"program": {"organization_id": org_id, "name": prog.name, "slug": prog.slug,
                        "profile_required_docs": C.loads(prog.profile_required_docs, [])},
            "access": access, "me": {"email": user.email, "full_name": user.full_name},
            "dev_principal": _dev_principal(db, user, org_id),
            "projects": [C.project_dict(p) for p in C.projects(db, org_id)],
            "overview": reports.overview(db, org_id, f), "leaderboard": reports.leaderboard(db, org_id, f),
            "filters": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()},
            "zoom": {"method": "staff_supplied_link",
                     "note": "VOLT stores the participant join link you paste. It does not create, edit or cancel the meeting in Zoom."}}


@ops_router.get("/{org_id}/applications")
def ops_queue(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, f, grant = _applicant_scope(db, user, org_id, request.query_params)
    fields = _grant_fields(grant, "view_fields") if grant else None
    items = reports.rows(db, org_id, f) if fields is None or fields else []
    if fields is not None:
        items = [{key: row.get(key) for key in fields} for row in items]
    return {"items": items, "count": len(items),
            "filters": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()},
            "reached_label": reports.REACHED_LABELS[f["reached"]]}


@ops_router.get("/{org_id}/export.csv")
def ops_export(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _, f, grant = _applicant_scope(db, user, org_id, request.query_params)
    allowed = _grant_fields(grant, "export_fields") if grant else reports.CSV_COLUMNS
    if not allowed:
        raise HTTPException(status_code=403, detail="Your login has no authorized export fields.")
    requested = request.query_params.getlist("column")
    columns = (_columns(requested, allowed) if requested
               else [key for key in allowed if grant or key in reports.DEFAULT_CSV_COLUMNS])
    csv_content = reports.csv_text(db, org_id, f, columns)
    C.audit(db, org_id, user, "export", "applications", "applicant_csv_export",
            new=json.dumps({"columns": columns, "project_id": f.get("project_id")}))
    db.commit()
    return Response(content=csv_content, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="volt-marketing-applications.csv"',
                             "Cache-Control": "private, no-store"})


@ops_router.get("/{org_id}/applications/{app_id}")
def ops_file(org_id: str, app_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, access = _staff(db, user, org_id)
    a = _app_in(db, org_id, app_id)
    rep = db.get(VoltRep, a.rep_serial)
    owner = db.get(User, rep.user_id)
    orig = db.get(VoltRep, rep.original_recruiter_serial) if rep.original_recruiter_serial else None
    all_docs = (db.query(VoltDocument).filter(VoltDocument.rep_serial == rep.serial)
                .filter((VoltDocument.application_id == a.id) | VoltDocument.application_id.is_(None))
                .order_by(VoltDocument.doc_key.asc(), VoltDocument.version.desc()).all())
    others = (db.query(VoltApplication).filter(VoltApplication.rep_serial == rep.serial,
                                               VoltApplication.id != a.id).all())
    nexts = list(core.STAFF_TRANSITIONS.get(a.status, ()))
    if a.status == "withdrawn":
        nexts = [a.closed_from_status or "submitted"]          # reopen, to exactly where it was
    return {"application": core.application_dict(db, prog, rep, a, for_staff=True),
            "profile": dict(core.rep_profile_dict(rep, owner, private=True), is_test=rep.is_test,
                            recruiter_active=rep.recruiter_active),
            "hiring": hiring.profile_dict(rep), "availability_options": hiring.AVAILABILITY,
            "profile_documents": core.requirements(db, prog, rep, None),
            "document_versions": [core.doc_dict(d) for d in all_docs],
            "original_recruiter": C.public_rep(orig),
            "project_recruiter": C.public_rep(db.get(VoltRep, a.referrer_serial) if a.referrer_serial else None),
            "other_applications": [{"id": o.id, "project": (db.get(VoltProject, o.project_id).short_name),
                                    "status": o.status} for o in others],
            "history": core.history(db, org_id, [("application", a.id), ("rep", rep.serial)] +
                                    [("document", d.id) for d in all_docs]),
            "next_actions": nexts if access != "viewer" else [],
            "missing": core.missing_items(db, prog, rep, a),
            "zoom": dict(_zoom_state(db, a), sessions=[x for x in sessions.for_rep(db, rep)
                                                       if x.get("application_id") == a.id]),
            "person": volt_access.describe(db, org_id, owner, rep)}


@ops_router.post("/{org_id}/applications/{app_id}/status")
def ops_status(org_id: str, app_id: str, background: BackgroundTasks, body: Dict[str, Any] = Body(...),
               db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    a = _app_in(db, org_id, app_id)
    before = (a.status, a.return_reason)
    core.set_status(db, prog, a, body.get("status"), body.get("reason"), user)
    if (a.status, a.return_reason) != before:
        _send_after(background, mail.on_status(db, prog, a))
    db.commit()
    return {"id": a.id, "status": a.status, "approved_at": C.iso(a.approved_at), "started_at": C.iso(a.started_at)}


@ops_router.post("/{org_id}/applications/{app_id}/referral")
def ops_referral(org_id: str, app_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    a = _app_in(db, org_id, app_id)
    core.correct_project_referral(db, prog, a, body.get("referrer_rep_id"), body.get("reason"), user)
    db.commit()
    return {"id": a.id, "referrer": C.public_rep(db.get(VoltRep, a.referrer_serial) if a.referrer_serial else None)}


@ops_router.post("/{org_id}/reps/{serial}/original-recruiter")
def ops_original(org_id: str, serial: int, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    rep = _rep_in(db, org_id, serial)
    core.correct_original_recruiter(db, prog, rep, body.get("referrer_rep_id"), body.get("reason"), user)
    db.commit()
    return {"rep_id": rep.serial, "original_recruiter": C.public_rep(
        db.get(VoltRep, rep.original_recruiter_serial) if rep.original_recruiter_serial else None)}


@ops_router.get("/{org_id}/reps")
def ops_reps(org_id: str, q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    rows = db.query(VoltRep).filter(VoltRep.organization_id == org_id)
    nq = C.normalize_name(q)
    if nq:
        sid = C.serial_from_code(q)
        rows = rows.filter((VoltRep.search_name.like("%" + nq + "%")) | (VoltRep.serial == (sid or -1)))
    rows = rows.order_by(VoltRep.serial.desc()).limit(100).all()
    return {"items": [dict(C.public_rep(r), recruiter_active=r.recruiter_active, is_test=r.is_test,
                           created_at=C.iso(r.created_at)) for r in rows]}


@ops_router.patch("/{org_id}/reps/{serial}")
def ops_rep_patch(org_id: str, serial: int, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    rep = _rep_in(db, org_id, serial)
    for field in ("recruiter_active", "is_test"):
        if field in body:
            old, new = getattr(rep, field), bool(body[field])
            if old != new:
                setattr(rep, field, new)
                C.audit(db, org_id, user, "rep", rep.serial, "rep_updated", field=field, old=old, new=new,
                        reason=body.get("reason"))
    db.commit()
    return dict(C.public_rep(rep), recruiter_active=rep.recruiter_active, is_test=rep.is_test)


@ops_router.get("/{org_id}/documents/{doc_id}/file")
def ops_doc_file(org_id: str, doc_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    d = db.get(VoltDocument, doc_id)
    if d is None or d.organization_id != org_id:
        raise HTTPException(status_code=404, detail="File not found.")
    return _file_response(db, d)


@ops_router.post("/{org_id}/documents/{doc_id}/review")
def ops_doc_review(org_id: str, doc_id: str, background: BackgroundTasks, body: Dict[str, Any] = Body(...),
                   db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    d = db.get(VoltDocument, doc_id)
    if d is None or d.organization_id != org_id:
        raise HTTPException(status_code=404, detail="File not found.")
    before = (d.status, d.reason)
    docs.review(db, prog, d, body.get("status"), body.get("reason"), user)
    if (d.status, d.reason) != before:
        _send_after(background, mail.on_doc_replace(db, prog, d))
    db.commit()
    return core.doc_dict(d)


def _doc_list(raw) -> List[Dict[str, str]]:
    out, seen = [], set()
    for item in raw or []:
        label = str((item or {}).get("label") or "").strip()[:80]
        key = str((item or {}).get("key") or "").strip().lower() or C.normalize_name(label).replace(" ", "_")
        key = "".join(ch for ch in key if ch.isalnum() or ch == "_")[:40]
        if label and key and key not in seen:
            seen.add(key)
            out.append({"key": key, "label": label})
    return out


@ops_router.patch("/{org_id}/projects/{project_id}")
def ops_project(org_id: str, project_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    import json
    _staff(db, user, org_id, write=True)
    p = db.get(VoltProject, project_id)
    if p is None or p.organization_id != org_id:
        raise HTTPException(status_code=404, detail="Project not found.")
    if "status" in body:
        st = str(body["status"]).lower()
        if st not in ("open", "closed"):
            raise HTTPException(status_code=422, detail="Status is open or closed.")
        if st != p.status:
            C.audit(db, org_id, user, "project", p.id, "project_status", field="status", old=p.status, new=st)
            p.status = st
    if "required_docs" in body:
        new = _doc_list(body["required_docs"])
        C.audit(db, org_id, user, "project", p.id, "required_docs", field="required_docs",
                old=p.required_docs, new=json.dumps(new))
        p.required_docs = json.dumps(new)
    for field in ("question", "summary"):
        if field in body and body[field] is not None:
            setattr(p, field, str(body[field])[:300])
    db.commit()
    return C.project_dict(p)


@ops_router.patch("/{org_id}/program")
def ops_program(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    import json
    prog, _ = _staff(db, user, org_id, write=True)
    if "profile_required_docs" in body:
        new = _doc_list(body["profile_required_docs"])
        C.audit(db, org_id, user, "program", org_id, "profile_required_docs", field="profile_required_docs",
                old=prog.profile_required_docs, new=json.dumps(new))
        prog.profile_required_docs = json.dumps(new)
    db.commit()
    return {"profile_required_docs": C.loads(prog.profile_required_docs, [])}


# ── Talent Pool (Get Future Opportunities) ───────────────────────────────────

@ops_router.get("/{org_id}/talent-pool")
def ops_talent(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    params = dict(request.query_params)
    names = talent.project_names(db, org_id)
    rows = talent.query(db, org_id, params)
    return {"items": [talent.row_dict(db, r, names) for r in rows], "count": len(rows),
            "interests": [{"key": k, "label": v} for k, v in names.items()] + [{"key": talent.ANY, "label": "Any future opportunity"}],
            "note": "Talent Pool signups are interest only. They are not applications and are not counted in applications, approvals, starts or the leaderboard."}


@ops_router.get("/{org_id}/talent-pool.csv")
def ops_talent_csv(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    return Response(content=talent.csv_text(db, org_id, dict(request.query_params)), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="volt-marketing-talent-pool.csv"',
                             "Cache-Control": "private, no-store"})


# ── Zoom sessions ────────────────────────────────────────────────────────────

@ops_router.get("/{org_id}/sessions")
def ops_sessions(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    rows = (db.query(VoltSession).filter(VoltSession.organization_id == org_id)
            .order_by(VoltSession.starts_at.desc()).limit(200).all())
    return {"items": [sessions.staff_dict(db, s) for s in rows], "method": "staff_supplied_link"}


@ops_router.post("/{org_id}/sessions")
def ops_session_create(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    s = sessions.save(db, org_id, body, user)
    db.commit()
    return sessions.staff_dict(db, s)


@ops_router.patch("/{org_id}/sessions/{sid}")
def ops_session_edit(org_id: str, sid: str, background: BackgroundTasks, body: Dict[str, Any] = Body(...),
                     db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    s = sessions.save(db, org_id, body, user, _session_in(db, org_id, sid))
    rows = _notify_attendees(db, prog, s, "rescheduled") if getattr(s, "_rescheduled", False) else []
    db.commit()
    for r in rows:
        _send_after(background, r)
    return sessions.staff_dict(db, s)


def _notify_attendees(db, prog, s, event):
    out = []
    for att in db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == s.id).all():
        if att.booking_status not in (None, "active") or not att.application_id:
            continue
        rep = db.get(VoltRep, att.rep_serial)
        a = db.get(VoltApplication, att.application_id)
        if rep is not None and a is not None:
            out.append(mail.on_booking(db, prog, rep, a, s, att, event))
    return out


@ops_router.post("/{org_id}/sessions/{sid}/cancel")
def ops_session_cancel(org_id: str, sid: str, background: BackgroundTasks, body: Dict[str, Any] = Body(default={}),
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    s = _session_in(db, org_id, sid)
    was = s.status
    sessions.cancel(db, org_id, s, user, (body or {}).get("reason"))
    rows = _notify_attendees(db, prog, s, "canceled") if was != "canceled" else []
    db.commit()
    for r in rows:
        _send_after(background, r)
    return sessions.staff_dict(db, s)


@ops_router.post("/{org_id}/sessions/{sid}/booking")
def ops_session_booking(org_id: str, sid: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """Open or close this session for applicant booking. Existing bookings stay."""
    _staff(db, user, org_id, write=True)
    s = sessions.set_booking_open(db, org_id, _session_in(db, org_id, sid), bool(body.get("open")), user)
    db.commit()
    return sessions.staff_dict(db, s)


@ops_router.post("/{org_id}/sessions/{sid}/bookings/{serial}/cancel")
def ops_booking_cancel(org_id: str, sid: str, serial: int, background: BackgroundTasks,
                       body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Staff cancel ONE attendee's booking (frees the seat)."""
    prog, _ = _staff(db, user, org_id, write=True)
    s = _session_in(db, org_id, sid)
    att = (db.query(VoltSessionAttendee).filter(VoltSessionAttendee.session_id == s.id,
                                                VoltSessionAttendee.rep_serial == int(serial)).first())
    if att is None or att.booking_status not in (None, "active"):
        raise HTTPException(status_code=404, detail="That applicant has no active booking here.")
    rep = _rep_in(db, org_id, serial)
    reason = str((body or {}).get("reason") or "").strip()[:1000] or "Canceled by staff"
    email = None
    if att.application_id:
        row = sessions.cancel_booking(db, rep, att.application_id, user, by_staff=True, reason=reason)
        if row is not None:
            email = mail.on_booking(db, prog, rep, db.get(VoltApplication, att.application_id), s, row, "canceled")
    else:
        att.booking_status, att.canceled_at, att.cancel_reason = "canceled", C.now(), reason
        C.audit(db, org_id, user, "session", s.id, "assignment_canceled", field=C.rep_code(rep.serial), reason=reason)
    db.commit()
    _send_after(background, email)
    return sessions.staff_dict(db, s)


@ops_router.post("/{org_id}/applications/{app_id}/scheduling")
def ops_scheduling(org_id: str, app_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    """Staff decide when an application reaches the Zoom step: interview,
    orientation, or off (off keeps any existing booking)."""
    _staff(db, user, org_id, write=True)
    a = _app_in(db, org_id, app_id)
    sessions.set_schedule_stage(db, org_id, a, body.get("stage"), user)
    db.commit()
    return {"id": a.id, "schedule_stage": a.schedule_stage, "zoom": _zoom_state(db, a)}


# ── People & Access ─────────────────────────────────────────────────────────

@ops_router.get("/{org_id}/people")
def ops_people(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    qp = dict(request.query_params)
    yes = lambda k: str(qp.get(k) or "").lower() in ("1", "true", "yes")
    out = volt_access.people(db, org_id, q=qp.get("q") or "", role=qp.get("role") or "",
                             access=qp.get("access") or "", include_archived=yes("include_archived"),
                             include_test=yes("include_test"))
    out["can_manage"] = volt_access.actor_level(db, user, org_id) is not None
    out["actor_level"] = volt_access.actor_level(db, user, org_id)
    out["me"] = {"user_id": user.id, "name": user.full_name or user.email}
    return out


@ops_router.get("/{org_id}/people/history")
def ops_people_history(org_id: str, user_id: str = "", db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    return {"items": volt_access.history(db, org_id, user_id or None)}


def _person(db, org_id, user_id) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Person not found.")
    in_org = (db.query(VoltRep).filter(VoltRep.organization_id == org_id, VoltRep.user_id == user_id).first()
              is not None) or volt_access.staff_role(db, org_id, user_id) is not None \
        or volt_access.row_for(db, org_id, user_id) is not None
    if not in_org and target.role != "god_admin":     # the owner: refused with a clear reason below
        raise HTTPException(status_code=404, detail="Person not found.")
    return target


@ops_router.post("/{org_id}/people/{user_id}/access")
def ops_person_access(org_id: str, user_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    out = volt_access.change(db, org_id, user, _person(db, org_id, user_id), body.get("action"), body.get("reason"))
    db.commit()
    return out


@ops_router.post("/{org_id}/people/{user_id}/archive")
def ops_person_archive(org_id: str, user_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    target = _person(db, org_id, user_id)
    rep = db.query(VoltRep).filter(VoltRep.organization_id == org_id, VoltRep.user_id == target.id).first()
    if rep is None:
        raise HTTPException(status_code=409, detail="Only applicants have an application record to archive.")
    volt_access.set_archived(db, org_id, user, rep, bool(body.get("archived")), body.get("reason"))
    db.commit()
    return volt_access.describe(db, org_id, target, rep)


@ops_router.post("/{org_id}/sessions/{sid}/attendance")
def ops_session_attendance(org_id: str, sid: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                           user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    s = _session_in(db, org_id, sid)
    sessions.mark_attendance(db, org_id, s, body.get("rep_id"), str(body.get("attendance") or ""), user)
    db.commit()
    return sessions.staff_dict(db, s)


# ── synthetic test cleanup (owner only) ──────────────────────────────────────

@ops_router.post("/{org_id}/test-records/purge")
def ops_purge_tests(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Removes ONLY reps flagged is_test, with their applications, documents,
    test-only sessions, audit rows and applicant-only logins. Owner only; one
    run at a time per tenant (app/services/volt/cleanup.py)."""
    if C.staff_access(db, user, org_id) != "owner":
        raise HTTPException(status_code=403, detail="Only the platform owner can remove test records.")
    C.program_for_org(db, org_id)
    from app.services.volt import cleanup
    return cleanup.purge(db, org_id)


# ═══════════════════════════════════════════════ SUPPORT MAILBOX ══════
# VOLT staff only. Tokens never leave the server; the status says which
# mailbox is connected, whether it works and what was sent.

@ops_router.get("/{org_id}/email")
def ops_email_status(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, access = _staff(db, user, org_id)
    out = mail.status(db, prog)
    out["can_connect"] = access in ("owner", "staff")
    return out


@ops_router.post("/{org_id}/email/connect")
def ops_email_connect(org_id: str, request: Request, db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """Start Microsoft's sign-in for VOLT's support mailbox. The person signs
    in AS that mailbox on Microsoft's page; the callback refuses any other."""
    prog, _ = _staff(db, user, org_id, write=True)
    from app.models.oauth_models import FLOW_VOLT_MAILBOX, PROVIDER_MICROSOFT
    from app.services import oauth_state_service
    state = oauth_state_service.issue_state(
        db, provider=PROVIDER_MICROSOFT, subject=user, flow=FLOW_VOLT_MAILBOX,
        client_ip=(request.client.host if request.client else None), target_organization_id=org_id)
    try:
        return {"authorization_url": mail.authorization_url(state, mail.support_address(prog))}
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@ops_router.post("/{org_id}/email/poll-now")
def ops_email_poll(org_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    box = mail.mailbox(db, org_id)
    if box is None:
        raise HTTPException(status_code=409, detail="The support mailbox is not connected.")
    res = mail.poll(db, box)
    retried = mail.retry_due(db)
    return {"result": res, "retried": retried, "status": mail.status(db, prog)}


@ops_router.get("/{org_id}/inbox")
def ops_inbox(org_id: str, show: str = Query("open"), db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    return {"threads": mail.threads(db, org_id, "all" if show == "all" else "open")}


@ops_router.get("/{org_id}/inbox/thread")
def ops_inbox_thread(org_id: str, conversation: str = Query(..., max_length=500), db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    return mail.thread(db, org_id, conversation)


@ops_router.post("/{org_id}/inbox/reply")
def ops_inbox_reply(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    return mail.reply(db, prog, str(body.get("conversation_id") or ""), user, str(body.get("text") or ""))


@ops_router.post("/{org_id}/inbox/handled")
def ops_inbox_handled(org_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    conv = str(body.get("conversation_id") or "")
    mail.mark_handled(db, org_id, conv, user, body.get("handled") is not False)
    db.commit()
    return mail.thread(db, org_id, conv)


@ops_router.get("/{org_id}/inbox/messages/{message_id}/attachments/{att_id}")
def ops_inbox_attachment(org_id: str, message_id: str, att_id: str, db: Session = Depends(get_db),
                         user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    raw, name = mail.attachment(db, org_id, message_id, att_id)
    # Untrusted file: always a download, never rendered by the browser here.
    return Response(content=raw, media_type="application/octet-stream", headers={
        "Content-Disposition": 'attachment; filename="%s"' % name.replace('"', ""),
        "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; sandbox"})
