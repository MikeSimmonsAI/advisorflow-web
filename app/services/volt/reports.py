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
    travel = params.get("travel") or None
    if travel not in TRAVEL_FILTERS:
        travel = None
    experience = params.get("experience") or None
    if experience not in ("has", "none", "d2d"):
        experience = None
    return {"project_id": params.get("project_id") or None, "status": status, "reached": reached,
            "recruiter": rec, "date_from": _d(params.get("date_from")), "date_to": _d(params.get("date_to")),
            "include_test": str(params.get("include_test") or "").lower() in ("1", "true", "yes"),
            "location": (str(params.get("location") or "").strip().lower()[:60] or None),
            "travel": travel, "experience": experience,
            "include_archived": str(params.get("include_archived") or "").lower() in ("1", "true", "yes")}


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
    if f.get("hide_archived") and not f.get("include_archived"):
        q = q.filter(VoltRep.archived_at.is_(None))      # archived: out of the active queue only
    return q


def overview(db: Session, org_id: str, f: Dict[str, Any]) -> Dict[str, Any]:
    base = {k: v for k, v in f.items() if k not in ("status", "reached", "location", "travel", "experience")}

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
    base = {k: v for k, v in f.items() if k not in ("status", "reached", "recruiter", "location", "travel", "experience")}
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
    f = dict(f, hide_archived=True)       # the queue and its CSV: archived people only when asked
    q = application_query(db, org_id, f).order_by(getattr(VoltApplication, REACHED[f["reached"]]).desc())
    apps = q.limit(limit).all() if limit else q.all()
    serials = {a.rep_serial for a in apps} | {a.referrer_serial for a in apps if a.referrer_serial}
    reps = {r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_(serials)).all()} if serials else {}
    origs = {r.original_recruiter_serial for r in reps.values() if r.original_recruiter_serial} - set(reps)
    if origs:
        reps.update({r.serial: r for r in db.query(VoltRep).filter(VoltRep.serial.in_(origs)).all()})
    projects = {p.id: p for p in db.query(VoltProject).filter(VoltProject.organization_id == org_id).all()}
    # Sign-in email lives on the linked login (users), not on volt_reps.
    uids = {r.user_id for r in reps.values() if r.user_id}
    emails = {u.id: u.email for u in db.query(User.id, User.email).filter(User.id.in_(uids)).all()} if uids else {}
    from app.services.volt import hiring
    from app.models.volt_models import VoltSession, VoltSessionAttendee
    zoom = {}
    ids = [a.id for a in apps]
    if ids:
        for att, sess in (db.query(VoltSessionAttendee, VoltSession)
                          .join(VoltSession, VoltSession.id == VoltSessionAttendee.session_id)
                          .filter(VoltSessionAttendee.application_id.in_(ids), VoltSessionAttendee.booking_status == "active",
                                  VoltSession.status == "scheduled").all()):
            zoom[att.application_id] = {"session_id": sess.id, "kind": sess.kind, "starts_at": C.iso(sess.starts_at),
                                        "timezone": sess.timezone, "attendance": att.attendance}
    out = []
    for a in apps:
        rep = reps.get(a.rep_serial)
        p = projects.get(a.project_id)
        hs = hiring.summary_fields(rep)
        ans = hiring.answers_of(a)
        if f.get("location"):
            hay = " ".join([hs.get("city") or "", hs.get("state") or "", (rep.zip_code if rep else "") or "",
                            (rep.preferred_regions if rep else "") or ""]).lower()
            if f["location"] not in hay:
                continue
        if f.get("travel") and not _travel_match(f["travel"], ans):
            continue
        if f.get("experience") and not _experience_match(f["experience"], ans, hs):
            continue
        orig = reps.get(rep.original_recruiter_serial) if rep and rep.original_recruiter_serial else None
        ref = reps.get(a.referrer_serial) if a.referrer_serial else None
        out.append({"application_id": a.id, "rep_id": a.rep_serial, "rep_code": C.rep_code(a.rep_serial),
                    "applicant": C.display_name(rep) if rep else "",
                    "first_name": (rep.first_name or None) if rep else None,
                    "last_name": (rep.last_name or None) if rep else None,
                    "email": emails.get(rep.user_id) if rep else None,
                    "phone": (rep.phone or None) if rep else None, "is_test": bool(rep and rep.is_test),
                    "project_id": a.project_id, "project_key": p.key if p else None,
                    "project": (p.short_name or p.name) if p else "", "status": a.status,
                    "status_label": C.STATUS_LABELS.get(a.status, a.status),
                    "original_recruiter": C.public_rep(orig), "project_recruiter": C.public_rep(ref),
                    "created_at": C.iso(a.created_at), "submitted_at": C.iso(a.submitted_at),
                    "approved_at": C.iso(a.approved_at), "started_at": C.iso(a.started_at),
                    "updated_at": C.iso(a.updated_at),
                    "city": hs.get("city"), "state": hs.get("state"), "earliest_start": hs.get("earliest_start"),
                    "availability": hs.get("availability"), "work_history_count": hs.get("work_history_count", 0),
                    "no_experience": hs.get("no_experience", False),
                    "willing_travel": ans.get("willing_travel"), "comfortable_d2d": ans.get("comfortable_d2d"),
                    "transportation": ans.get("transportation"), "license_status": ans.get("license_status"),
                    "earliest_departure": ans.get("earliest_departure"), "time_away": ans.get("time_away"),
                    "drivers_license": ans.get("drivers_license"), "smartphone": ans.get("smartphone"),
                    "d2d_experience": ans.get("d2d_experience"), "d2d_duration": ans.get("d2d_duration"),
                    "homeowner_comfort": ans.get("homeowner_comfort"), "training": ans.get("training"),
                    "commitment": ans.get("commitment"),
                    "outreach_experience": ans.get("outreach_experience"),
                    "comfortable_explaining": ans.get("comfortable_explaining"), "languages": ans.get("languages"),
                    "reference": C.app_ref(a), "archived": bool(rep and rep.archived_at),
                    "schedule_stage": a.schedule_stage, "zoom": zoom.get(a.id)})
    return out


# Travel filter: the current Fiber answer (how long they can be away), or the
# first Fiber form's yes / no / details answer for older applications.
TRAVEL_FILTERS = {"long": ("time_away", {"1_3_months", "3_plus_months"}),
                  "short": ("time_away", {"1_2_weeks", "3_4_weeks"}),
                  "discuss": ("time_away", {"discuss"}),
                  "yes": ("willing_travel", {"yes"}), "no": ("willing_travel", {"no"}),
                  "details": ("willing_travel", {"details"})}


def _travel_match(value: str, ans: Dict[str, Any]) -> bool:
    key, ok = TRAVEL_FILTERS[value]
    return ans.get(key) in ok


def _experience_match(value: str, ans: Dict[str, Any], hs: Dict[str, Any]) -> bool:
    d2d = ans.get("d2d_experience")
    if value == "d2d":
        return d2d == "yes"
    outreach = ans.get("outreach_experience")
    if value == "has":
        return (d2d in ("yes", "some") or outreach in ("yes", "some")
                or bool(hs.get("has_experience") or ans.get("relevant_experience")))
    # "none": the project's own answer when there is one, otherwise the file.
    if d2d:
        return d2d == "no"
    if outreach:
        return outreach == "no"
    return bool(hs.get("no_experience"))


def _label(key: str, value) -> str:
    """The applicant-facing wording of a choice, for the CSV."""
    if not value:
        return ""
    from app.services.volt import hiring
    for qs in hiring.PROJECT_QUESTIONS.values():
        for q in qs:
            if q["key"] == key and q.get("options"):
                return dict(q["options"]).get(value, value)
    return str(value)


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


CSV_COLUMNS = dict(zip(['application_id', 'rep_code', 'applicant', 'first_name', 'last_name', 'email', 'phone', 'project_key', 'project', 'status_label', 'original_recruiter_id', 'original_recruiter', 'project_recruiter_id', 'project_recruiter', 'city', 'state', 'earliest_start', 'availability', 'work_history_count', 'no_experience', 'willing_travel', 'comfortable_d2d', 'transportation', 'earliest_departure', 'time_away', 'drivers_license', 'smartphone', 'd2d_experience', 'd2d_duration', 'homeowner_comfort', 'training', 'commitment', 'outreach_experience', 'comfortable_explaining', 'languages', 'license_status', 'created_at', 'submitted_at', 'approved_at', 'started_at'],
    ["Application ID", "Rep ID", "Full name", "First name", "Last name", "Email", "Phone", "Project key", "Project", "Stage",
                "Original recruiter ID", "Original recruiter", "Project recruiter ID", "Project recruiter",
                "City", "State", "Earliest start", "Availability", "Jobs listed", "No previous work experience",
                "Willing to travel (earlier Fiber form)", "Comfortable with door-to-door (earlier Fiber form)",
                "Reliable transportation",
                "Earliest departure", "Time away", "Driver's license", "Smartphone and data",
                "Door-to-door experience", "Time selling", "Comfort approaching homeowners", "Training and role-play (earlier Fiber form)",
                "Travel role commitment",
                "Outreach / customer service experience", "Comfortable explaining next steps", "Languages",
                "License status (earlier ACA form, no longer asked)",
                "Application started (UTC)", "Submitted (UTC)", "Approved (UTC)", "Started (UTC)"], strict=True))


# Owner default export: contact block first, in a useful order, then the rest.
# first_name/last_name stay selectable but are not defaults (Full name covers them).
# ACA-restricted staff never use this list: their default is only owner-granted fields.
CONTACT_DEFAULT = ("applicant", "email", "phone", "city", "state")
DEFAULT_CSV_COLUMNS = list(CONTACT_DEFAULT) + [
    key for key in CSV_COLUMNS if key not in CONTACT_DEFAULT and key not in ("first_name", "last_name")]


def csv_text(db: Session, org_id: str, f: Dict[str, Any], columns: Optional[List[str]] = None) -> str:
    """The review queue's rows for the same filters. No DOB, no document
    links. Default (no columns given) is the owner default."""
    buf = io.StringIO()
    w = csv.writer(buf)
    selected = columns if columns is not None else list(DEFAULT_CSV_COLUMNS)
    w.writerow([CSV_COLUMNS[key] for key in selected])
    for r in rows(db, org_id, f):
        o, p = r["original_recruiter"] or {}, r["project_recruiter"] or {}
        values = (r["application_id"], r["rep_code"], r["applicant"],
                  r["first_name"], r["last_name"], r["email"], r["phone"], r["project_key"],
                                        r["project"], r["status_label"], o.get("rep_code"), o.get("display_name"),
                                        p.get("rep_code"), p.get("display_name"),
                                        r["city"], r["state"], r["earliest_start"], r["availability"],
                                        r["work_history_count"], "Yes" if r["no_experience"] else "",
                                        r["willing_travel"], r["comfortable_d2d"], r["transportation"],
                                        r["earliest_departure"], _label("time_away", r["time_away"]),
                                        _label("drivers_license", r["drivers_license"]),
                                        _label("smartphone", r["smartphone"]),
                                        _label("d2d_experience", r["d2d_experience"]),
                                        _label("d2d_duration", r["d2d_duration"]),
                                        _label("homeowner_comfort", r["homeowner_comfort"]),
                                        _label("training", r["training"]),
                                        "Yes" if r["commitment"] == "yes" else "",
                                        _label("outreach_experience", r["outreach_experience"]),
                                        _label("comfortable_explaining", r["comfortable_explaining"]),
                                        r["languages"], _label("license_status", r["license_status"]),
                                        r["created_at"],
                                        r["submitted_at"], r["approved_at"], r["started_at"])
        cells = dict(zip(CSV_COLUMNS, values))
        w.writerow([_cell(cells[key]) for key in selected])
    return buf.getvalue()
