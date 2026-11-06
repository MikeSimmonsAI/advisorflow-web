"""VOLT Marketing recruiting program (Issue #535).

    PUBLIC (no login)                         /volt/public/{slug}/...
        GET  ''                     portal: open and closed projects
        GET  /recruiters?q=         click-to-confirm recruiter directory (name + rep code only)
        GET  /invite?ref=&project=  what a shared link suggests (nothing is credited)
        POST /register              account + profile + serial + first draft application
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

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.limiter import limiter
from app.models.models import User
from app.models.volt_models import (
    VoltApplication, VoltDocument, VoltProject, VoltRep, VoltSession, VoltSessionAttendee,
)
from app.services.volt import common as C
from app.services.volt import core, docs, reports, sessions

public_router = APIRouter(prefix="/volt/public", tags=["volt-public"])
rep_router = APIRouter(prefix="/volt/rep", tags=["volt-rep"])
ops_router = APIRouter(prefix="/volt/ops", tags=["volt-ops"])


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
    return {"name": prog.name, "slug": prog.slug,
            "open_projects": [p for p in ps if p["recruiting"]],
            "past_projects": [p for p in ps if not p["recruiting"]]}


@public_router.get("/{slug}/recruiters")
@limiter.limit("40/minute")
def public_recruiters(request: Request, slug: str, q: str = "", test: int = 0, db: Session = Depends(get_db)):
    prog = C.program_by_slug(db, slug)
    return core.directory_search(db, prog, q, include_test=bool(test))


@public_router.get("/{slug}/invite")
@limiter.limit("60/minute")
def invite(request: Request, slug: str, ref: str = "", project: str = "", db: Session = Depends(get_db)):
    prog = C.program_by_slug(db, slug)
    return core.invite_preview(db, prog, ref, project)


@public_router.post("/{slug}/register")
@limiter.limit("30/minute")
def register(request: Request, slug: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    prog = C.program_by_slug(db, slug)
    user, rep, app = core.register(db, prog, body)
    db.commit()
    return {"access_token": _token(db, user, request), "token_type": "bearer", "rep": C.public_rep(rep),
            "application_id": app.id}


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
    orig = db.get(VoltRep, rep.original_recruiter_serial) if rep.original_recruiter_serial else None
    direct = (db.query(VoltRep).filter(VoltRep.original_recruiter_serial == rep.serial).count())
    project_refs = (db.query(VoltApplication.rep_serial).filter(VoltApplication.referrer_serial == rep.serial)
                    .distinct().count())
    app_dicts = [core.application_dict(db, prog, rep, a) for a in apps]
    profile_docs = core.requirements(db, prog, rep, None)
    doc_requests = [dict(d, scope="Profile") for d in profile_docs if d["state"] in ("requested", "replace")]
    for a in app_dicts:
        if a["editable"]:
            doc_requests += [dict(d, scope=a["project"]["short_name"], application_id=a["id"])
                             for d in a["documents"] if d["state"] in ("requested", "replace")]
    return {"has_profile": True, "profile": core.rep_profile_dict(rep, user, private=True),
            "original_recruiter": C.public_rep(orig), "applications": app_dicts,
            "profile_documents": profile_docs, "document_requests": doc_requests,
            "sessions": sessions.for_rep(db, rep),
            "referrals": {"code": C.rep_code(rep.serial), "direct_recruits": direct,
                          "project_referrals": project_refs,
                          "link_path": "/volt?ref=%s" % C.rep_code(rep.serial)},
            "staff_access": C.staff_access(db, user, prog.organization_id)}


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


@rep_router.get("/{slug}/recruiters")
def rep_recruiters(slug: str, q: str = "", db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    return core.directory_search(db, prog, q, exclude_serial=rep.serial if rep else None,
                                 include_test=bool(rep and rep.is_test))


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
def submit_app(slug: str, app_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    app = core.own_application(db, _need_rep(rep), app_id)
    core.submit(db, prog, rep, app, user)
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


@rep_router.get("/{slug}/leaderboard")
def rep_leaderboard(slug: str, project_id: str = "", db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    prog, rep = _me(db, slug, user)
    _need_rep(rep)
    f = reports.filters_from({"project_id": project_id})
    board = reports.leaderboard(db, prog.organization_id, f)
    # Names and counts only - the public shape of each recruiter.
    return board


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


@ops_router.get("/{org_id}")
def ops_home(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    prog, access = _staff(db, user, org_id)
    f = reports.filters_from(dict(request.query_params))
    return {"program": {"organization_id": org_id, "name": prog.name, "slug": prog.slug,
                        "profile_required_docs": C.loads(prog.profile_required_docs, [])},
            "access": access, "me": {"email": user.email, "full_name": user.full_name},
            "projects": [C.project_dict(p) for p in C.projects(db, org_id)],
            "overview": reports.overview(db, org_id, f), "leaderboard": reports.leaderboard(db, org_id, f),
            "filters": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()},
            "zoom": {"method": "staff_supplied_link",
                     "note": "VOLT stores the participant join link you paste. It does not create, edit or cancel the meeting in Zoom."}}


@ops_router.get("/{org_id}/applications")
def ops_queue(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    f = reports.filters_from(dict(request.query_params))
    items = reports.rows(db, org_id, f)
    return {"items": items, "count": len(items),
            "filters": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()},
            "reached_label": reports.REACHED_LABELS[f["reached"]]}


@ops_router.get("/{org_id}/export.csv")
def ops_export(org_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _staff(db, user, org_id)
    f = reports.filters_from(dict(request.query_params))
    return Response(content=reports.csv_text(db, org_id, f), media_type="text/csv",
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
    return {"application": core.application_dict(db, prog, rep, a, for_staff=True),
            "profile": dict(core.rep_profile_dict(rep, owner, private=True), is_test=rep.is_test,
                            recruiter_active=rep.recruiter_active),
            "profile_documents": core.requirements(db, prog, rep, None),
            "document_versions": [core.doc_dict(d) for d in all_docs],
            "original_recruiter": C.public_rep(orig),
            "project_recruiter": C.public_rep(db.get(VoltRep, a.referrer_serial) if a.referrer_serial else None),
            "other_applications": [{"id": o.id, "project": (db.get(VoltProject, o.project_id).short_name),
                                    "status": o.status} for o in others],
            "history": core.history(db, org_id, [("application", a.id), ("rep", rep.serial)] +
                                    [("document", d.id) for d in all_docs]),
            "next_actions": nexts if access != "viewer" else [],
            "missing": core.missing_items(db, prog, rep, a)}


@ops_router.post("/{org_id}/applications/{app_id}/status")
def ops_status(org_id: str, app_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    a = _app_in(db, org_id, app_id)
    core.set_status(db, prog, a, body.get("status"), body.get("reason"), user)
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
def ops_doc_review(org_id: str, doc_id: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    prog, _ = _staff(db, user, org_id, write=True)
    d = db.get(VoltDocument, doc_id)
    if d is None or d.organization_id != org_id:
        raise HTTPException(status_code=404, detail="File not found.")
    docs.review(db, prog, d, body.get("status"), body.get("reason"), user)
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
def ops_session_edit(org_id: str, sid: str, body: Dict[str, Any] = Body(...), db: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    s = sessions.save(db, org_id, body, user, _session_in(db, org_id, sid))
    db.commit()
    return sessions.staff_dict(db, s)


@ops_router.post("/{org_id}/sessions/{sid}/cancel")
def ops_session_cancel(org_id: str, sid: str, body: Dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                       user: User = Depends(get_current_user)):
    _staff(db, user, org_id, write=True)
    s = sessions.cancel(db, org_id, _session_in(db, org_id, sid), user, (body or {}).get("reason"))
    db.commit()
    return sessions.staff_dict(db, s)


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
