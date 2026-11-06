"""VOLT - the applicant hiring file and project questions.

TWO LEVELS
----------
PERSON (volt_reps, shared by every project application): location, preferred
regions, earliest start, availability, work history or the explicit "No
previous work experience" choice, relevant experience, additional info, and an
optional resume (profile drawer, key "resume").

PROJECT (volt_applications.answers JSON): the questions in PROJECT_QUESTIONS for
that project key. A project without a question set (Midterms, or one staff add
later) keeps its single free-text question.

DRAFTS SAVE ANYTHING, SUBMIT CHECKS EVERYTHING
---------------------------------------------
save_profile() accepts partial work (it only rejects malformed values, e.g. a
bad ZIP or an impossible month). What is REQUIRED is decided by missing_*()
at submit time, so a returning draft applicant is shown exactly what the newer
form still needs, and submitted applications keep their historical status.

Nothing here decides eligibility. Travel, door-to-door comfort, transportation
and license status are answers for staff to review; no answer rejects anyone.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.volt_models import VoltApplication, VoltProject, VoltRep

YES_NO_DETAILS = [["yes", "Yes"], ["no", "No"], ["details", "Would like details"]]

PROJECT_QUESTIONS: Dict[str, List[Dict[str, Any]]] = {
    "fiber": [
        {"key": "willing_travel", "label": "Are you willing to travel for assignments?", "type": "choice",
         "options": YES_NO_DETAILS, "required": True},
        {"key": "comfortable_d2d", "label": "Are you comfortable with door-to-door sales?", "type": "choice",
         "options": YES_NO_DETAILS, "required": True},
        {"key": "relevant_experience", "type": "text", "required": False,
         "label": "Relevant door-to-door, telecom, fiber or field sales experience"},
        {"key": "travel_regions", "type": "text", "required": False,
         "label": "Travel availability and preferred regions"},
        {"key": "transportation", "label": "Do you have reliable transportation?", "type": "choice",
         "options": [["yes", "Yes"], ["no", "No"], ["explain", "It depends (explain below)"]], "required": True},
        {"key": "transportation_notes", "type": "text", "required": False,
         "label": "Anything about your transportation situation you want us to know"},
    ],
    "aca": [
        {"key": "relevant_experience", "type": "text", "required": False,
         "label": "Relevant insurance, enrollment, sales or customer service experience"},
        {"key": "license_status", "label": "Current health insurance license status", "type": "choice",
         "options": [["licensed", "Licensed"], ["in_progress", "In progress"], ["not_licensed", "Not currently licensed"]],
         "required": True},
        {"key": "licensed_states", "type": "text", "required": False, "required_if": ["license_status", "licensed"],
         "label": "States where you are licensed"},
        {"key": "license_number", "type": "text", "required": False, "label": "License number (optional)"},
    ],
}

AVAILABILITY = [["full_time", "Full-time"], ["part_time", "Part-time"], ["weekdays", "Weekdays"],
                ["weekends", "Weekends"], ["evenings", "Evenings"], ["flexible", "Flexible"]]
_AV_KEYS = {k for k, _ in AVAILABILITY}
_MONTH = re.compile(r"^(19|20)\d\d-(0[1-9]|1[0-2])$")
_ZIP = re.compile(r"^\d{5}(-\d{4})?$")
MAX_ROLES = 10


def _txt(v, n=2000) -> str:
    return str(v if v is not None else "").strip()[:n]


def questions_for(project: Optional[VoltProject]) -> List[Dict[str, Any]]:
    if project is None:
        return []
    if project.key in PROJECT_QUESTIONS:
        return PROJECT_QUESTIONS[project.key]
    return [{"key": "relevant_experience", "type": "text", "required": True,
             "label": project.question or "Tell us about your relevant experience"}]


def answers_of(app: VoltApplication) -> Dict[str, Any]:
    """Answers with the pre-2026-11-06 single answer ("experience") carried over."""
    try:
        a = json.loads(app.answers) if app.answers else {}
    except (TypeError, ValueError):
        a = {}
    if not isinstance(a, dict):
        a = {}
    if a.get("experience") and not a.get("relevant_experience"):
        a["relevant_experience"] = a["experience"]
    return a


def clean_answers(project: VoltProject, raw: Dict[str, Any]) -> Dict[str, str]:
    qs = {q["key"]: q for q in questions_for(project)}
    out: Dict[str, str] = {}
    errs: Dict[str, str] = {}
    for k, v in (raw or {}).items():
        if k not in qs:
            continue                                   # only this project's questions are kept
        q = qs[k]
        val = _txt(v, 4000)
        if q["type"] == "choice" and val and val not in {o[0] for o in q["options"]}:
            errs[k] = "Choose one of the options."
            continue
        out[k] = val
    if errs:
        raise HTTPException(status_code=422, detail={"message": "Please check the highlighted answers.", "fields": errs})
    return out


def missing_project(project: VoltProject, answers: Dict[str, Any]) -> List[str]:
    miss = []
    for q in questions_for(project):
        val = str(answers.get(q["key"]) or "").strip()
        need = q.get("required")
        cond = q.get("required_if")
        if cond and str(answers.get(cond[0]) or "") == cond[1]:
            need = True
        if need and not val:
            miss.append("Answer: " + q["label"])
    return miss


# ── person-level hiring file ────────────────────────────────────────────────

def _month(v) -> str:
    v = _txt(v, 7)
    return v if (not v or _MONTH.match(v)) else "!"


def clean_profile(body: Dict[str, Any]) -> Dict[str, Any]:
    """Draft-tolerant: only malformed values are rejected."""
    errs: Dict[str, str] = {}
    out: Dict[str, Any] = {}
    if "city" in body:
        out["city"] = _txt(body.get("city"), 80)
    if "state" in body:
        out["state"] = _txt(body.get("state"), 40)
    if "zip_code" in body:
        z = _txt(body.get("zip_code"), 10)
        if z and not _ZIP.match(z):
            errs["zip_code"] = "Enter a 5-digit ZIP code."
        out["zip_code"] = z
    if "preferred_regions" in body:
        out["preferred_regions"] = _txt(body.get("preferred_regions"), 500)
    if "earliest_start" in body:
        d = _txt(body.get("earliest_start"), 10)
        if d:
            try:
                dv = date.fromisoformat(d)
                if dv.year < 2000 or dv.year > date.today().year + 3:
                    raise ValueError
                out["earliest_start"] = dv
            except ValueError:
                errs["earliest_start"] = "Enter a valid start date."
        else:
            out["earliest_start"] = None
    if "availability" in body:
        av = body.get("availability") or {}
        types = [t for t in (av.get("types") or []) if t in _AV_KEYS]
        order = [k for k, _ in AVAILABILITY]
        out["availability"] = json.dumps({"types": [k for k in order if k in set(types)],
                                          "notes": _txt(av.get("notes"), 300)})
    if "work_history" in body:
        roles = []
        for i, r in enumerate((body.get("work_history") or [])[:MAX_ROLES]):
            r = r or {}
            role = {"employer": _txt(r.get("employer"), 120), "title": _txt(r.get("title"), 120),
                    "start": _month(r.get("start")), "end": _month(r.get("end")),
                    "current": bool(r.get("current")), "summary": _txt(r.get("summary"), 1000)}
            if role["start"] == "!":
                errs["work_history.%d.start" % i] = "Use month and year."
                role["start"] = ""
            if role["end"] == "!":
                errs["work_history.%d.end" % i] = "Use month and year."
                role["end"] = ""
            if role["current"]:
                role["end"] = ""
            if role["start"] and role["end"] and role["end"] < role["start"]:
                errs["work_history.%d.end" % i] = "The end month is before the start month."
            if any(role[k] for k in ("employer", "title", "start", "end", "summary")) or role["current"]:
                roles.append(role)
        out["work_history"] = json.dumps(roles)
    if "no_experience" in body:
        out["no_experience"] = bool(body.get("no_experience"))
    if "relevant_experience" in body:
        out["relevant_experience"] = _txt(body.get("relevant_experience"), 2000)
    if "additional_info" in body:
        out["additional_info"] = _txt(body.get("additional_info"), 2000)
    if errs:
        raise HTTPException(status_code=422, detail={"message": "Please check the highlighted fields.", "fields": errs})
    return out


def save_profile(rep: VoltRep, body: Dict[str, Any]) -> VoltRep:
    data = clean_profile(body)
    for k, v in data.items():
        setattr(rep, k, v)
    rep.hiring_updated_at = datetime.utcnow()
    return rep


def _loads(raw, default):
    try:
        v = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        v = None
    return default if v is None else v


def profile_dict(rep: VoltRep) -> Dict[str, Any]:
    return {"city": rep.city or "", "state": rep.state or "", "zip_code": rep.zip_code or "",
            "preferred_regions": rep.preferred_regions or "",
            "earliest_start": rep.earliest_start.isoformat() if rep.earliest_start else "",
            "availability": _loads(rep.availability, {"types": [], "notes": ""}),
            "work_history": _loads(rep.work_history, []), "no_experience": bool(rep.no_experience),
            "relevant_experience": rep.relevant_experience or "", "additional_info": rep.additional_info or "",
            "updated_at": rep.hiring_updated_at.replace(microsecond=0).isoformat() + "Z" if rep.hiring_updated_at else None}


def _complete_role(r: Dict[str, Any]) -> bool:
    return bool(r.get("employer") and r.get("title") and r.get("start") and (r.get("current") or r.get("end")))


def missing_profile(rep: VoltRep) -> List[str]:
    p = profile_dict(rep)
    miss = []
    if not p["city"]:
        miss.append("Profile: current city")
    if not p["state"]:
        miss.append("Profile: state")
    if not p["zip_code"]:
        miss.append("Profile: ZIP code")
    if not p["earliest_start"]:
        miss.append("Profile: earliest available start date")
    if not p["availability"].get("types"):
        miss.append("Profile: availability")
    roles = p["work_history"]
    if not p["no_experience"]:
        if not roles:
            miss.append("Experience: add a job, or choose “No previous work experience”")
        elif not all(_complete_role(r) for r in roles):
            miss.append("Experience: finish each job (company, title, start month, end month or current)")
    return miss


def summary_fields(rep: Optional[VoltRep]) -> Dict[str, Any]:
    """Non-sensitive hiring fields for the staff queue and CSV."""
    if rep is None:
        return {}
    p = profile_dict(rep)
    labels = dict(AVAILABILITY)
    return {"city": p["city"], "state": p["state"], "earliest_start": p["earliest_start"],
            "availability": ", ".join(labels.get(t, t) for t in p["availability"].get("types", [])),
            "work_history_count": len(p["work_history"]), "no_experience": p["no_experience"],
            "has_experience": bool(p["work_history"] or p["relevant_experience"])}
