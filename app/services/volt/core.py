"""VOLT - applicants, applications and click-to-confirm referrals.

MIKE'S REFERRAL RULE, AS CODE
-----------------------------
Credit is given ONLY to a recruiter id the applicant clicked (search result or
invite confirmation). This module never receives a typed name for referral
purposes - the request carries `referrer_rep_id` and nothing else - and it
re-validates that id on the server: same tenant, in the recruiter directory,
not the applicant themself. A typed name, a fuzzy guess and "the first result"
have no path into these functions at all.

ONE PERSON, ONE SERIAL; ONE APPLICATION PER PROJECT
---------------------------------------------------
The serial is the database's autoincrement key (volt_reps.serial). The unique
constraint on (rep_serial, project_id) decides duplicate applications; the code
catches the constraint and returns the row that won, so a double click, a retry
or a second tab all end on the same application.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_models import (
    VoltApplication, VoltAuditEvent, VoltDocument, VoltProgram, VoltProject, VoltRep,
)
from app.services.volt import common as C

SEARCH_MIN = 2
SEARCH_LIMIT = 8
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def field_error(fields: Dict[str, str], message: str = "Please check the highlighted fields.", status: int = 422):
    raise HTTPException(status_code=status, detail={"message": message, "fields": fields})


# ── validation ───────────────────────────────────────────────────────────────

def clean_profile(body: Dict[str, Any], *, need_email: bool = True, need_password: bool = False) -> Dict[str, Any]:
    errs: Dict[str, str] = {}
    first = str(body.get("first_name") or "").strip()
    last = str(body.get("last_name") or "").strip()
    email = str(body.get("email") or "").strip().lower()
    phone = str(body.get("phone") or "").strip()
    dob_raw = str(body.get("date_of_birth") or "").strip()
    if not first:
        errs["first_name"] = "Enter your first name."
    elif len(first) > 70:
        errs["first_name"] = "Use 70 characters or fewer."
    if not last:
        errs["last_name"] = "Enter your last name."
    elif len(last) > 70:
        errs["last_name"] = "Use 70 characters or fewer."
    if need_email:
        if not email:
            errs["email"] = "Enter your email address."
        elif not EMAIL_RE.match(email) or len(email) > 254:
            errs["email"] = "Enter a valid email address, like name@example.com."
    digits = re.sub(r"\D", "", phone)
    if not phone:
        errs["phone"] = "Enter your phone number."
    elif not re.fullmatch(r"[+() .0-9-]+", phone) or not 10 <= len(digits) <= 15:
        errs["phone"] = "Enter a valid phone number, like (555) 010-0100."
    dob: Optional[date] = None
    if not dob_raw:
        errs["date_of_birth"] = "Enter your date of birth."
    else:
        try:
            dob = date.fromisoformat(dob_raw)
        except ValueError:
            errs["date_of_birth"] = "Enter your date of birth as a full date."
        else:
            if dob > date.today():
                errs["date_of_birth"] = "Date of birth cannot be in the future."
            elif dob.year < 1900:
                errs["date_of_birth"] = "Check the year of your date of birth."
    password = str(body.get("password") or "")
    if need_password and len(password) < 8:
        errs["password"] = "Use at least 8 characters."
    if errs:
        field_error(errs)
    return {"first_name": first, "last_name": last, "email": email, "phone": phone,
            "date_of_birth": dob, "password": password}


# ── recruiter directory ──────────────────────────────────────────────────────

def directory_search(db: Session, prog: VoltProgram, q: str, *, exclude_serial: Optional[int] = None,
                     include_test: bool = False) -> Dict[str, Any]:
    query = C.normalize_name(q)
    if len(query.replace(" ", "")) < SEARCH_MIN:
        return {"query": query, "min_length": SEARCH_MIN, "results": []}
    like = "%" + query.replace("%", "").replace("_", "") + "%"
    rows = (db.query(VoltRep).filter(VoltRep.organization_id == prog.organization_id,
                                     VoltRep.recruiter_active.is_(True),
                                     VoltRep.search_name.like(like)))
    if exclude_serial:
        rows = rows.filter(VoltRep.serial != exclude_serial)
    if not include_test:
        rows = rows.filter(VoltRep.is_test.is_(False))
    rows = rows.order_by(VoltRep.last_name.asc(), VoltRep.first_name.asc(), VoltRep.serial.asc()).limit(SEARCH_LIMIT).all()
    return {"query": query, "min_length": SEARCH_MIN, "results": [C.public_rep(r) for r in rows]}


def resolve_referrer(db: Session, prog: VoltProgram, referrer_rep_id: Any, *, self_serial: Optional[int] = None,
                     self_user_id: Optional[str] = None) -> Optional[VoltRep]:
    """A clicked recruiter id -> that recruiter, or a 422. None/blank -> direct signup."""
    if referrer_rep_id in (None, "", 0):
        return None
    try:
        sid = int(referrer_rep_id)
    except (TypeError, ValueError):
        field_error({"referrer": "Select your recruiter from the list, or clear the field."})
    rep = db.get(VoltRep, sid)
    if rep is None or rep.organization_id != prog.organization_id or not rep.recruiter_active:
        field_error({"referrer": "That recruiter is not available. Select a match from the list, or clear the field."})
    if (self_serial and rep.serial == self_serial) or (self_user_id and rep.user_id == self_user_id):
        field_error({"referrer": "You cannot refer yourself. Choose who referred you, or leave it blank."})
    return rep


def invite_preview(db: Session, prog: VoltProgram, ref: str, project_key: Optional[str]) -> Dict[str, Any]:
    """What a shared link suggests. Suggests only - nothing is credited until
    the applicant confirms and the submission carries the id."""
    out: Dict[str, Any] = {"recruiter": None, "project": None, "status": "none"}
    if project_key:
        p = (db.query(VoltProject).filter(VoltProject.organization_id == prog.organization_id,
                                          VoltProject.key == project_key.strip().lower()).first())
        out["project"] = C.project_dict(p) if p else None
    if ref:
        sid = C.serial_from_code(ref)
        rep = db.get(VoltRep, sid) if sid else None
        if rep is None or rep.organization_id != prog.organization_id:
            out["status"] = "invalid"
        elif not rep.recruiter_active:
            out["status"] = "inactive"
        else:
            out["status"] = "valid"
            out["recruiter"] = C.public_rep(rep)
    return out


# ── registration and profile ─────────────────────────────────────────────────

def _open_project(db: Session, prog: VoltProgram, key: str) -> VoltProject:
    p = C.project_by_key(db, prog.organization_id, key)
    if p.status != "open":
        raise HTTPException(status_code=409, detail={"message": "Recruiting is closed for %s." % p.name,
                                                     "code": "recruiting_closed"})
    return p


def register(db: Session, prog: VoltProgram, body: Dict[str, Any]):
    """New person: account + profile + serial + first application (draft)."""
    from app.services.auth_service import hash_password
    data = clean_profile(body, need_email=True, need_password=True)
    project = _open_project(db, prog, body.get("project_key"))
    existing = db.query(User).filter(func.lower(User.email) == data["email"]).first()
    if existing is not None:
        # Nothing about the existing account or profile is exposed or changed.
        raise HTTPException(status_code=409, detail={
            "message": "This email already has an account. Sign in to continue, or reset your password.",
            "code": "account_exists", "fields": {"email": "This email already has an account."}})
    referrer = resolve_referrer(db, prog, body.get("referrer_rep_id"))
    user = User(email=data["email"], full_name=("%s %s" % (data["first_name"], data["last_name"])).strip(),
                password_hash=hash_password(data["password"]), role="volt_rep", organization_id=None,
                is_active=True, must_change_password=False)
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail={
            "message": "This email already has an account. Sign in to continue, or reset your password.",
            "code": "account_exists", "fields": {"email": "This email already has an account."}})
    rep = _new_rep(db, prog, user, data, referrer)
    app = _new_application(db, prog, rep, project, referrer, body.get("referral_source"))
    C.audit(db, prog.organization_id, user, "rep", rep.serial, "registered",
            field="original_recruiter", new=referrer.serial if referrer else None)
    return user, rep, app


def _new_rep(db, prog, user, data, referrer) -> VoltRep:
    rep = VoltRep(organization_id=prog.organization_id, user_id=user.id, first_name=data["first_name"],
                  last_name=data["last_name"], phone=data["phone"], date_of_birth=data["date_of_birth"],
                  search_name=C.normalize_name("%s %s" % (data["first_name"], data["last_name"])),
                  original_recruiter_serial=referrer.serial if referrer else None,
                  original_recruited_at=C.now() if referrer else None,
                  is_test=C.is_test_email(user.email))
    db.add(rep)
    db.flush()
    return rep


def create_profile_for_user(db: Session, prog: VoltProgram, user: User, body: Dict[str, Any]):
    """An EXISTING, signed-in account starts a VOLT profile. Email is the account's."""
    if C.rep_for_user(db, prog, user) is not None:
        raise HTTPException(status_code=409, detail="You already have a VOLT profile.")
    data = clean_profile(body, need_email=False)
    project = _open_project(db, prog, body.get("project_key"))
    referrer = resolve_referrer(db, prog, body.get("referrer_rep_id"), self_user_id=user.id)
    try:
        rep = _new_rep(db, prog, user, data, referrer)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="You already have a VOLT profile.")
    app = _new_application(db, prog, rep, project, referrer, body.get("referral_source"))
    C.audit(db, prog.organization_id, user, "rep", rep.serial, "registered",
            field="original_recruiter", new=referrer.serial if referrer else None)
    return rep, app


def update_profile(db: Session, prog: VoltProgram, rep: VoltRep, body: Dict[str, Any]) -> VoltRep:
    data = clean_profile(body, need_email=False)
    rep.first_name, rep.last_name = data["first_name"], data["last_name"]
    rep.phone, rep.date_of_birth = data["phone"], data["date_of_birth"]
    rep.search_name = C.normalize_name("%s %s" % (rep.first_name, rep.last_name))
    return rep


# ── applications ─────────────────────────────────────────────────────────────

def _new_application(db, prog, rep, project, referrer, source) -> VoltApplication:
    app = VoltApplication(organization_id=prog.organization_id, rep_serial=rep.serial, project_id=project.id,
                          status="draft", answers=json.dumps({}),
                          referrer_serial=referrer.serial if referrer else None,
                          referral_source=(source if source in ("search", "invite") else "search") if referrer else None,
                          referral_confirmed_at=C.now() if referrer else None)
    db.add(app)
    db.flush()
    if referrer:
        C.audit(db, prog.organization_id, None, "application", app.id, "referral_confirmed",
                field="referrer", new=referrer.serial)
    return app


def _lookup(db, rep, project) -> Optional[VoltApplication]:
    return (db.query(VoltApplication).filter(VoltApplication.rep_serial == rep.serial,
                                             VoltApplication.project_id == project.id).first())


_existing = _lookup     # the pre-check; tests replace it to simulate a race


def start_application(db: Session, prog: VoltProgram, rep: VoltRep, body: Dict[str, Any]) -> VoltApplication:
    """Second (or later) project. Same profile, same serial. Never touches the
    original recruiter. Returns the existing application on a repeat."""
    project = C.project_by_key(db, prog.organization_id, body.get("project_key"))
    existing = _existing(db, rep, project)
    if existing is not None:
        return existing
    if project.status != "open":
        raise HTTPException(status_code=409, detail={"message": "Recruiting is closed for %s." % project.name,
                                                     "code": "recruiting_closed"})
    referrer = resolve_referrer(db, prog, body.get("referrer_rep_id"), self_serial=rep.serial)
    try:
        with db.begin_nested():
            app = _new_application(db, prog, rep, project, referrer, body.get("referral_source"))
    except IntegrityError:
        # The other tab / retry won the race. Its row is the application.
        existing = _lookup(db, rep, project)
        if existing is None:
            raise
        return existing
    return app


def own_application(db: Session, rep: VoltRep, app_id: str) -> VoltApplication:
    app = db.get(VoltApplication, app_id)
    if app is None or app.rep_serial != rep.serial:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


def save_answers(db: Session, app: VoltApplication, answers: Dict[str, Any]) -> VoltApplication:
    if app.status not in ("draft", "needs_update"):
        raise HTTPException(status_code=409, detail="This application is with our team and cannot be edited right now.")
    clean = {}
    for k, v in (answers or {}).items():
        if not isinstance(k, str) or len(k) > 60:
            continue
        clean[k] = str(v if v is not None else "")[:4000]
    app.answers = json.dumps(clean)
    return app


def requirements(db: Session, prog: VoltProgram, rep: VoltRep, app: Optional[VoltApplication]) -> List[Dict[str, Any]]:
    """Each requested document with its applicant-facing state:
    requested | under_review | accepted | replace (with the reviewer's reason)."""
    if app is None:
        reqs = C.loads(prog.profile_required_docs, [])
        scope = (VoltDocument.application_id.is_(None))
    else:
        project = db.get(VoltProject, app.project_id)
        reqs = C.loads(project.required_docs if project else None, [])
        scope = (VoltDocument.application_id == app.id)
    docs = (db.query(VoltDocument).filter(VoltDocument.rep_serial == rep.serial, scope)
            .order_by(VoltDocument.version.desc()).all())
    latest: Dict[str, VoltDocument] = {}
    for d in docs:
        latest.setdefault(d.doc_key, d)
    out = []
    for r in reqs:
        key = str(r.get("key") or "")
        d = latest.get(key)
        out.append({"key": key, "label": r.get("label") or key, "state": d.status if d else "requested",
                    "reason": d.reason if d is not None and d.status == "replace" else None,
                    "document": doc_dict(d) if d else None,
                    "versions": sum(1 for x in docs if x.doc_key == key)})
    return out


def doc_dict(d: VoltDocument) -> Dict[str, Any]:
    return {"id": d.id, "doc_key": d.doc_key, "label": d.label, "version": d.version, "status": d.status,
            "reason": d.reason, "filename": d.filename, "content_type": d.content_type, "size_bytes": d.size_bytes,
            "uploaded_at": C.iso(d.uploaded_at), "reviewed_at": C.iso(d.reviewed_at),
            "application_id": d.application_id}


def missing_items(db, prog, rep, app) -> List[str]:
    project = db.get(VoltProject, app.project_id)
    answers = C.loads(app.answers, {})
    missing = []
    if project and project.question and not str(answers.get("experience") or "").strip():
        missing.append("Answer: " + project.question)
    for r in requirements(db, prog, rep, app) + requirements(db, prog, rep, None):
        if r["state"] in ("requested", "replace"):
            missing.append(("Replace: " if r["state"] == "replace" else "Upload: ") + r["label"])
    return missing


def submit(db: Session, prog: VoltProgram, rep: VoltRep, app: VoltApplication, actor: User) -> VoltApplication:
    if app.status in ("submitted", "approved", "started"):
        return app                                   # a repeat is a no-op
    project = db.get(VoltProject, app.project_id)
    if project is not None and project.status != "open" and app.status == "draft":
        raise HTTPException(status_code=409, detail={"message": "Recruiting is closed for %s." % project.name,
                                                     "code": "recruiting_closed"})
    missing = missing_items(db, prog, rep, app)
    if missing:
        raise HTTPException(status_code=422, detail={"message": "Finish the required items first.",
                                                     "missing": missing})
    old = app.status
    app.status = "submitted"
    app.submitted_at = app.submitted_at or C.now()
    C.audit(db, prog.organization_id, actor, "application", app.id, "status", field="status", old=old, new="submitted")
    return app


def next_action(db, prog, rep, app) -> Dict[str, Any]:
    if app.status == "draft":
        missing = missing_items(db, prog, rep, app)
        return {"label": "Finish your application" if missing else "Review and submit your application",
                "detail": "; ".join(missing) or "Everything required is in. Submit when you are ready.",
                "kind": "applicant"}
    if app.status == "needs_update":
        return {"label": "Update requested", "detail": app.return_reason or "Our team asked for a change.",
                "kind": "applicant"}
    if app.status == "submitted":
        return {"label": "Under review", "detail": "Our team is reviewing your application. Check back here for updates.",
                "kind": "wait"}
    if app.status == "approved":
        return {"label": "Approved", "detail": "Watch Upcoming Sessions for your orientation.", "kind": "wait"}
    return {"label": "Started", "detail": "You are on this project.", "kind": "done"}


def application_dict(db, prog, rep, app, *, for_staff: bool = False) -> Dict[str, Any]:
    project = db.get(VoltProject, app.project_id)
    referrer = db.get(VoltRep, app.referrer_serial) if app.referrer_serial else None
    out = {"id": app.id, "project": C.project_dict(project) if project else None, "status": app.status,
           "status_label": C.STATUS_LABELS.get(app.status, app.status), "answers": C.loads(app.answers, {}),
           "return_reason": app.return_reason, "referrer": C.public_rep(referrer),
           "referral_source": app.referral_source, "referral_confirmed_at": C.iso(app.referral_confirmed_at),
           "created_at": C.iso(app.created_at), "updated_at": C.iso(app.updated_at),
           "submitted_at": C.iso(app.submitted_at), "approved_at": C.iso(app.approved_at),
           "started_at": C.iso(app.started_at),
           "editable": app.status in ("draft", "needs_update"),
           "documents": requirements(db, prog, rep, app)}
    if not for_staff:
        out["next_action"] = next_action(db, prog, rep, app)
    return out


def rep_profile_dict(rep: VoltRep, user: Optional[User], *, private: bool) -> Dict[str, Any]:
    out = C.public_rep(rep)
    out.update({"first_name": rep.first_name, "last_name": rep.last_name})
    if private:
        out.update({"email": getattr(user, "email", None), "phone": rep.phone,
                    "date_of_birth": rep.date_of_birth.isoformat() if rep.date_of_birth else None,
                    "created_at": C.iso(rep.created_at)})
    return out


# ── staff actions ────────────────────────────────────────────────────────────

STAFF_TRANSITIONS = {
    "submitted": ("needs_update", "approved"),
    "needs_update": ("approved",),
    "approved": ("started", "needs_update"),
    "started": (),
    "draft": (),
}


def set_status(db: Session, prog: VoltProgram, app: VoltApplication, status: str, reason: Optional[str],
               actor: User) -> VoltApplication:
    status = (status or "").strip().lower()
    if status not in C.STATUSES:
        raise HTTPException(status_code=422, detail="Unknown stage.")
    if status == app.status:
        if status == "needs_update" and reason and reason.strip() != (app.return_reason or ""):
            pass                                       # a corrected reason is a real change
        else:
            return app                                 # repeating an action never double-counts
    elif status not in STAFF_TRANSITIONS.get(app.status, ()):
        raise HTTPException(status_code=409, detail="A %s application cannot move to %s."
                            % (C.STATUS_LABELS[app.status], C.STATUS_LABELS[status]))
    if status == "needs_update":
        if not (reason or "").strip():
            field_error({"reason": "Tell the applicant exactly what to change."})
        app.return_reason = reason.strip()[:2000]
    old = app.status
    app.status = status
    if status == "approved":
        app.approved_at = app.approved_at or C.now()
    if status == "started":
        app.approved_at = app.approved_at or C.now()
        app.started_at = app.started_at or C.now()
    C.audit(db, prog.organization_id, actor, "application", app.id, "status", field="status", old=old, new=status,
            reason=(reason or None))
    return app


def correct_project_referral(db, prog, app: VoltApplication, referrer_rep_id, reason: str, actor: User):
    if not (reason or "").strip():
        field_error({"reason": "A correction needs a reason."})
    rep = db.get(VoltRep, app.rep_serial)
    new = resolve_referrer(db, prog, referrer_rep_id, self_serial=rep.serial) if referrer_rep_id else None
    old = app.referrer_serial
    if (new.serial if new else None) == old:
        return app
    app.referrer_serial = new.serial if new else None
    app.referral_source = "staff_correction" if new else None
    app.referral_confirmed_at = C.now() if new else None
    C.audit(db, prog.organization_id, actor, "application", app.id, "referral_corrected", field="referrer",
            old=old, new=app.referrer_serial, reason=reason.strip())
    return app


def correct_original_recruiter(db, prog, rep: VoltRep, referrer_rep_id, reason: str, actor: User):
    if not (reason or "").strip():
        field_error({"reason": "A correction needs a reason."})
    new = resolve_referrer(db, prog, referrer_rep_id, self_serial=rep.serial) if referrer_rep_id else None
    old = rep.original_recruiter_serial
    if (new.serial if new else None) == old:
        return rep
    rep.original_recruiter_serial = new.serial if new else None
    rep.original_recruited_at = C.now() if new else None
    C.audit(db, prog.organization_id, actor, "rep", rep.serial, "original_recruiter_corrected",
            field="original_recruiter", old=old, new=rep.original_recruiter_serial, reason=reason.strip())
    return rep


def history(db, org_id, entity_pairs) -> List[Dict[str, Any]]:
    from sqlalchemy import and_, or_
    conds = [and_(VoltAuditEvent.entity_type == t, VoltAuditEvent.entity_id == str(i)) for t, i in entity_pairs]
    if not conds:
        return []
    rows = (db.query(VoltAuditEvent).filter(VoltAuditEvent.organization_id == org_id, or_(*conds))
            .order_by(VoltAuditEvent.at.asc()).all())
    actors = {u.id: u.full_name for u in db.query(User).filter(
        User.id.in_({r.actor_user_id for r in rows if r.actor_user_id})).all()} if rows else {}
    return [{"at": C.iso(r.at), "action": r.action, "field": r.field, "old": r.old_value, "new": r.new_value,
             "reason": r.reason, "actor": actors.get(r.actor_user_id) or ("Applicant" if r.actor_user_id else "System"),
             "entity_type": r.entity_type} for r in rows]
