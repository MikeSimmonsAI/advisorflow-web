"""VOLT - the applicant hiring file and project questions.

TWO LEVELS
----------
PERSON (volt_reps, shared by every project application): location, preferred
regions, earliest start, availability, work history or the explicit "No
previous work experience" choice, relevant experience, additional info, and an
optional resume (profile drawer, key "resume").

PROJECT (volt_applications.answers JSON): the questions in PROJECT_QUESTIONS for
that project key, grouped into PROJECT_SECTIONS (each section is one step).
Fiber asks only for the person's location from the person-level file; its own
travel, door-to-door experience and coachability questions replace the generic
availability and work-history sections (PROJECT_PROFILE_FIELDS). A project without a question set (Midterms, or one staff add
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
_D2D_SOME = ["yes", "some"]

# Fiber (2026-11-06, Mike's direction): exclusively traveling door-to-door
# fiber sales. No generic availability or work-history section - the person
# answers travel readiness, door-to-door experience and coachability instead.
# The keys of the first Fiber form stay below as "legacy" so older applications
# remain readable; they are never asked again and never required.
FIBER_QUESTIONS: List[Dict[str, Any]] = [
    # Travel readiness
    {"key": "earliest_departure", "section": "travel", "type": "date", "required": True,
     "label": "Earliest date you could leave for a travel assignment"},
    {"key": "time_away", "section": "travel", "type": "choice", "required": True,
     "label": "How long could you stay away from home at a time?",
     "options": [["1_2_weeks", "1–2 weeks"], ["3_4_weeks", "3–4 weeks"], ["1_3_months", "1–3 months"],
                 ["3_plus_months", "More than 3 months"], ["discuss", "Not sure yet, I'd like to talk it through"]]},
    {"key": "travel_logistics", "section": "travel", "type": "text", "required": False,
     "label": "Travel logistics you would like to discuss",
     "hint": "For example family, pets, commitments at home or timing."},
    {"key": "drivers_license", "section": "travel", "type": "choice", "required": True,
     "label": "Do you have a valid driver's license?",
     "options": [["yes", "Yes"], ["in_progress", "Getting or renewing it"], ["no", "No"]]},
    {"key": "transportation", "section": "travel", "type": "choice", "required": True,
     "label": "Do you have reliable transportation?",
     "options": [["yes", "Yes"], ["no", "No"], ["explain", "It depends (explain below)"]]},
    {"key": "transportation_notes", "section": "travel", "type": "text", "required": False,
     "label": "Anything about your transportation you want us to know"},
    {"key": "smartphone", "section": "travel", "type": "choice", "required": True,
     "label": "Do you have a smartphone with a working mobile data plan?",
     "options": [["yes", "Yes"], ["limited", "Smartphone, but limited or no data plan"], ["no", "No smartphone"]]},
    # Door-to-door experience
    {"key": "d2d_experience", "section": "experience", "type": "choice", "required": True,
     "label": "Have you sold door-to-door before?",
     "options": [["yes", "Yes, door-to-door"], ["some", "Some in-person or field sales, not door-to-door"],
                 ["no", "No, I'm new to this"]]},
    {"key": "products_sold", "section": "experience", "type": "text", "required": True,
     "show_if": ["d2d_experience", _D2D_SOME], "label": "What did you sell?",
     "hint": "Products or services, for example fiber, solar, pest control, security."},
    {"key": "d2d_duration", "section": "experience", "type": "choice", "required": True,
     "show_if": ["d2d_experience", _D2D_SOME], "label": "How long did you sell?",
     "options": [["under_3m", "Under 3 months"], ["3_12m", "3–12 months"], ["1_2y", "1–2 years"],
                 ["2y_plus", "More than 2 years"]]},
    {"key": "recent_company", "section": "experience", "type": "text", "required": False,
     "show_if": ["d2d_experience", _D2D_SOME], "label": "Most recent company or team"},
    {"key": "results", "section": "experience", "type": "text", "required": False,
     "show_if": ["d2d_experience", _D2D_SOME], "label": "Results you are proud of, with the timeframe",
     "hint": "For example: 38 sales in 6 weeks."},
    {"key": "beginner_experience", "section": "experience", "type": "text", "required": True,
     "show_if": ["d2d_experience", ["no"]],
     "label": "What customer-facing, sales, sports or team experience has prepared you?",
     "hint": "A few sentences is plenty. Jobs, teams, volunteering and school all count."},
    # Coachability
    {"key": "motivation", "section": "coachability", "type": "text", "required": True,
     "label": "What draws you to traveling door-to-door fiber sales?"},
    {"key": "persistence", "section": "coachability", "type": "text", "required": True,
     "label": "Tell us about a time you kept going after setbacks or a long run of no's."},
    {"key": "coaching_response", "section": "coachability", "type": "text", "required": True,
     "label": "When a leader gives you direct feedback, what do you do with it?"},
    {"key": "homeowner_comfort", "section": "coachability", "type": "choice", "required": True,
     "label": "How comfortable are you starting a conversation with a homeowner you have not met?",
     "options": [["very", "Very comfortable"], ["comfortable", "Comfortable"],
                 ["learning", "Still learning, and willing to practice"], ["unsure", "Unsure"]]},
    {"key": "commitment", "section": "coachability", "type": "confirm", "required": True,
     "label": "I understand this is a traveling door-to-door sales role and am prepared to participate in "
              "training, practice, and accurate activity tracking.",
     "options": [["yes", "I understand"]]},
    # Replaced by the commitment above (approved demo, 2026-11-06); kept readable.
    {"key": "training", "legacy": True, "type": "choice", "required": False,
     "label": "Will you take part in training, role-play and team meetings?",
     "options": [["yes", "Yes"], ["questions", "I have questions first"]]},
    # First Fiber form (before 2026-11-06): read-only, shown when answered.
    {"key": "willing_travel", "legacy": True, "type": "choice", "required": False,
     "label": "Are you willing to travel for assignments?", "options": YES_NO_DETAILS},
    {"key": "comfortable_d2d", "legacy": True, "type": "choice", "required": False,
     "label": "Are you comfortable with door-to-door sales?", "options": YES_NO_DETAILS},
    {"key": "relevant_experience", "legacy": True, "type": "text", "required": False,
     "label": "Relevant door-to-door, telecom, fiber or field sales experience"},
    {"key": "travel_regions", "legacy": True, "type": "text", "required": False,
     "label": "Travel availability and preferred regions"},
]

PROJECT_QUESTIONS: Dict[str, List[Dict[str, Any]]] = {
    "fiber": FIBER_QUESTIONS,
    "aca": [
        {"key": "relevant_experience", "type": "text", "required": False,
         "label": "Relevant insurance, enrollment, sales or customer service experience"},
        {"key": "license_status", "label": "Current health insurance license status", "type": "choice",
         "options": [["licensed", "Licensed"], ["in_progress", "In progress"], ["not_licensed", "Not currently licensed"]],
         "required": True},
        {"key": "licensed_states", "type": "text", "required": False, "required_if": ["license_status", "licensed"],
         "label": "States where you are licensed"},
        {"key": "license_number", "type": "text", "required": False, "label": "License number"},
    ],
}

# The application's step sections for a project's questions.
PROJECT_SECTIONS: Dict[str, List[Dict[str, str]]] = {
    # Step names and headings follow the approved demo (You, Travel, Sales,
    # Mindset, Review); "You" is the account step before the application.
    "fiber": [{"id": "travel", "label": "Travel", "title": "Where are you starting from?",
               "intro": "This role travels to assigned markets. Share your home base and departure readiness."},
              {"id": "experience", "label": "Sales", "title": "What have you sold?",
               "intro": "Experienced closer or hungry beginner? Tell us where you're starting."},
              {"id": "coachability", "label": "Mindset", "title": "Bring the drive. Build the skill."}],
}
DEFAULT_SECTIONS = [{"id": "project", "label": "Project questions"}]

# Which person-level hiring fields a project's flow asks for. Fiber asks only
# for the current location; ACA (and any project added later) uses the full file.
FULL_PROFILE = ["location", "regions", "earliest_start", "availability", "work_history", "relevant_experience",
                "additional_info"]
PROJECT_PROFILE_FIELDS: Dict[str, List[str]] = {"fiber": ["location", "additional_info"]}

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


def sections_for(project: Optional[VoltProject]) -> List[Dict[str, str]]:
    return PROJECT_SECTIONS.get(project.key, DEFAULT_SECTIONS) if project is not None else DEFAULT_SECTIONS


def profile_fields_for(project: Optional[VoltProject]) -> List[str]:
    return PROJECT_PROFILE_FIELDS.get(project.key, FULL_PROFILE) if project is not None else FULL_PROFILE


def shown(q: Dict[str, Any], answers: Dict[str, Any]) -> bool:
    """A question whose show_if is not met is not asked (and not required)."""
    cond = q.get("show_if")
    return not cond or str(answers.get(cond[0]) or "") in cond[1]


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
        if q.get("legacy"):
            continue                                   # earlier answers are kept as they were, never re-asked
        val = _txt(v, 4000)
        if q["type"] in ("choice", "confirm") and val and val not in {o[0] for o in q["options"]}:
            errs[k] = "Choose one of the options."
            continue
        if q["type"] == "date" and val:
            try:
                dv = date.fromisoformat(val[:10])
                if dv.year < 2000 or dv.year > date.today().year + 3:
                    raise ValueError
                val = dv.isoformat()
            except ValueError:
                errs[k] = "Enter a valid date."
                continue
        out[k] = val
    if errs:
        raise HTTPException(status_code=422, detail={"message": "Please check the highlighted answers.", "fields": errs})
    return out


def missing_project(project: VoltProject, answers: Dict[str, Any]) -> List[str]:
    """Prefixed with the step's name ("Travel: ...") for sectioned
    flows, "Answer: ..." otherwise, so the form can link each item to its step."""
    labels = {s["id"]: s["label"] for s in PROJECT_SECTIONS.get(project.key, [])}
    miss = []
    for q in questions_for(project):
        if q.get("legacy") or not shown(q, answers):
            continue
        val = str(answers.get(q["key"]) or "").strip()
        need = q.get("required")
        cond = q.get("required_if")
        if cond and str(answers.get(cond[0]) or "") == cond[1]:
            need = True
        if need and not val:
            miss.append("%s: %s" % (labels.get(q.get("section"), "Answer"), q["label"]))
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


def missing_profile(rep: VoltRep, project: Optional[VoltProject] = None) -> List[str]:
    """What the person-level file still needs for this project's flow. Fiber
    asks only for the current location; the full file applies otherwise."""
    p = profile_dict(rep)
    fields = profile_fields_for(project)
    miss = []
    if not p["city"]:
        miss.append("Profile: current city")
    if not p["state"]:
        miss.append("Profile: state")
    if not p["zip_code"]:
        miss.append("Profile: ZIP code")
    if "availability" not in fields:
        return miss
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
