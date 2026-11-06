"""VOLT reports - ONE counting function for dashboard, leaderboard and CSV.

WHAT EACH NUMBER MEANS
----------------------
Every count is "applications (or distinct people) that REACHED a stage", and
the date filter applies to the date that stage was reached:

    Applications     created_at       an application was started
    Submitted        submitted_at     first submission
    Approved         approved_at      first approval (includes later starts)
    Started          started_at       marked Started
    Awaiting review  submitted_at     current stage is Submitted
    Unique people    created_at       distinct reps among Applications

The review queue takes the same `reached` filter, so clicking a number opens
exactly the list that number counted, and the CSV is that list.

LEADERBOARD
-----------
A recruiter's measures count DISTINCT PEOPLE whose project application credits
that recruiter (volt_applications.referrer_serial) and reached the stage.
With one project selected that is one application per person. In the ALL
PROJECTS view a person who started two projects for the same recruiter counts
ONCE - the leaderboard is a count of people, never of applications.

Synthetic test records (volt_reps.is_test) are excluded unless asked for.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import User
from app.models.volt_models import VoltApplication, VoltProject, VoltRep
from app.services.volt import common as C

REACHED = {"created": "created_at", "submitted": "submitted_at", "approved": "approved_at", "started": "started_at"}
REACHED_LABELS = {"created": "Application started", "submitted": "Submitted", "approved": "Approved",
                  "started": "Started"}


def _d(v) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def filters_from(params: Dict[str, Any]) -> Dict[str, Any]:
    reached = params.get("reached") or "created"
    if reached not in REACHED:
        reached = "created"
    status = params.get("status") or None
    if status not in C.STATUSES:
        status = None
    rec = params.get("recruiter")
    try:
        rec = int(rec) if rec not in (None, "") else None
    except (TypeError, ValueError):
        rec = C.serial_from_code(rec)
    return {"project_id": params.get("project_id") or None, "status": status, "reached": reached,
            "recruiter": rec, "date_from": _d(params.get("date_from")), "date_to": _d(params.get("date_to")),
            "include_test": str(params.get("include_test") or "").lower() in ("1", "true", "yes")}


def application_query(db: Session, org_id: str, f: Dict[str, Any]):
    col = getattr(VoltApplication, REACHED[f.get("reached") or "created"])
    q = (db.query(VoltApplication).join(VoltRep, VoltRep.serial == VoltApplication.rep_serial)
         .filter(VoltApplication.organization_id == org_id, col.isnot(None)))
    if f.get("project_id"):
        q = q.filter(VoltApplication.project_id == f["project_id"])
    if f.get("status"):
        q = q.filter(VoltApplication.status == f["status"])
    if f.get("recruiter"):
        q = q.filter(VoltApplication.referrer_serial == f["recruiter"])
    if f.get("date_from"):
        q = q.filter(col >= datetime.combine(f["date_from"], datetime.min.time()))
    if f.get("date_to"):
        q = q.filter(col < datetime.combine(f["date_to"] + timedelta(days=1), datetime.min.time()))
    if not f.get("include_test"):
        q = q.filter(VoltRep.is_test.is_(False))
    return q


def overview(db: Session, org_id: str, f: Dict[str, Any]) -> Dict[str, Any]:
    base = {k: v for k, v in f.items() if k not in ("status", "reached")}

    def n(reached, status=None, distinct=False):
        q = application_query(db, org_id, dict(base, reached=reached, status=status))
        if distinct:
            return q.with_entities(func.count(func.distinct(VoltApplication.rep_serial))).scalar() or 0
        return q.count()
    return {
        "unique_people": n("created", distinct=True),
        "applications": n("created"),
        "submitted": n("submitted"),
        "awaiting_review": n("submitted", status="submitted"),
        "approvals": n("approved"),
        "starts": n("started"),
        "links": {"unique_people": {"reached": "created"}, "applications": {"reached": "created"},
                  "submitted": {"reached": "submitted"},
                  "awaiting_review": {"reached": "submitted", "status": "submitted"},
                  "approvals": {"reached": "approved"}, "starts": {"reached": "started"}},
        "date_rule": "Each count is filtered by the date that stage was reached.",
    }


def leaderboard(db: Session, org_id: str, f: Dict[str, Any], *, public: bool = False) -> Dict[str, Any]:
    base = {k: v for k, v in f.items() if k not in ("status", "reached", "recruiter")}
    measures: Dict[int, Dict[str, int]] = {}
    for m in ("created", "submitted", "approved", "started"):
        q = (application_query(db, org_id, dict(base, reached=m))
             .filter(VoltApplication.referrer_serial.isnot(None))
             .with_entities(VoltApplication.referrer_serial, func.count(func.distinct(VoltApplication.rep_serial)))
             .group_by(VoltApplication.referrer_serial))
        for serial, count in q.all():
            measures.setdefault(serial, {"created": 0, "submitted": 0, "approved": 0, "started": 0})[m] = count
    reps = {r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_(list(measures))).all()} if measures else {}
    rows = []
    for serial, ms in measures.items():
        rep = reps.get(serial)
        if rep is None:
            continue
        rows.append(dict(C.public_rep(rep), registrations=ms["created"], submissions=ms["submitted"],
                         approvals=ms["approved"], starts=ms["started"]))
    rows.sort(key=lambda r: (-r["starts"], -r["approvals"], -r["submissions"], -r["registrations"], r["display_name"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {"project_id": f.get("project_id"), "scope": "project" if f.get("project_id") else "all_projects",
            "rule": ("Ranked by distinct direct recruits who reached Started for this project."
                     if f.get("project_id") else
                     "All projects: each person counts once per recruiter, even if they started more than one project."),
            "rows": rows}


def rows(db: Session, org_id: str, f: Dict[str, Any], limit: Optional[int] = None) -> List[Dict[str, Any]]:
    q = application_query(db, org_id, f).order_by(getattr(VoltApplication, REACHED[f["reached"]]).desc())
    apps = q.limit(limit).all() if limit else q.all()
    serials = {a.rep_serial for a in apps} | {a.referrer_serial for a in apps if a.referrer_serial}
    reps = {r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_(serials)).all()} if serials else {}
    origs = {r.original_recruiter_serial for r in reps.values() if r.original_recruiter_serial} - set(reps)
    if origs:
        reps.update({r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_(origs)).all()})
    projects = {p.id: p for p in db.query(VoltProject).filter(VoltProject.organization_id == org_id).all()}
    out = []
    for a in apps:
        rep = reps.get(a.rep_serial)
        p = projects.get(a.project_id)
        orig = reps.get(rep.original_recruiter_serial) if rep and rep.original_recruiter_serial else None
        ref = reps.get(a.referrer_serial) if a.referrer_serial else None
        out.append({"application_id": a.id, "rep_id": a.rep_serial, "rep_code": C.rep_code(a.rep_serial),
                    "applicant": C.display_name(rep) if rep else "", "is_test": bool(rep and rep.is_test),
                    "project_id": a.project_id, "project_key": p.key if p else None,
                    "project": (p.short_name or p.name) if p else "", "status": a.status,
                    "status_label": C.STATUS_LABELS.get(a.status, a.status),
                    "original_recruiter": C.public_rep(orig), "project_recruiter": C.public_rep(ref),
                    "created_at": C.iso(a.created_at), "submitted_at": C.iso(a.submitted_at),
                    "approved_at": C.iso(a.approved_at), "started_at": C.iso(a.started_at),
                    "updated_at": C.iso(a.updated_at)})
    return out


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def csv_text(db: Session, org_id: str, f: Dict[str, Any]) -> str:
    """The review queue's rows for the same filters. No DOB, no contact data,
    no document links."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Application ID", "Rep ID", "Applicant", "Project key", "Project", "Stage",
                "Original recruiter ID", "Original recruiter", "Project recruiter ID", "Project recruiter",
                "Application started (UTC)", "Submitted (UTC)", "Approved (UTC)", "Started (UTC)"])
    for r in rows(db, org_id, f):
        o, p = r["original_recruiter"] or {}, r["project_recruiter"] or {}
        w.writerow([_cell(x) for x in (r["application_id"], r["rep_code"], r["applicant"], r["project_key"],
                                        r["project"], r["status_label"], o.get("rep_code"), o.get("display_name"),
                                        p.get("rep_code"), p.get("display_name"), r["created_at"],
                                        r["submitted_at"], r["approved_at"], r["started_at"])])
    return buf.getvalue()
