"""
Launch Engine intake — the step schema, and the rules that act on it.

===========================================================================
THE SCHEMA LIVES ON THE SERVER
===========================================================================

The prototype defined its eight steps in `frontend/src/pages/launch/
launchConfig.js`, with a hand-written `pct` per step. That was right for a UI
prototype and is wrong the moment the numbers mean something: a completion
figure computed in the browser is a completion figure the customer's devtools
can set to 100, and a "required field" enforced only in React is not required.

So the schema is here, the completion arithmetic is here, and the frontend
fetches both. The step KEYS and field NAMES are unchanged from the prototype,
so the existing components keep working against real data.

===========================================================================
BRAND AND CUSTOMER ARE CONFIGURATION, NOT CODE
===========================================================================

Nothing in this file names a brand or a customer. The brand is read from the
`Platform` row the implementation points at, and the customer from the
`Organization`. The first customer configured is a row, not a branch — and the
guard test in tests/test_launch_engine.py greps these modules to keep it that
way, which is why even this paragraph does not name one.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.launch_intake_models import (
    STEP_COMPLETE, STEP_IN_PROGRESS, STEP_NOT_STARTED,
    LaunchIntakeFile, LaunchIntakeStep, LaunchIntakeSubmission,
)

log = logging.getLogger("launch_intake")


# ── field kinds ─────────────────────────────────────────────────────────────
# `secret` is the one that changes behaviour rather than presentation: a secret
# field is Fernet-encrypted on write and has NO read path back out of this
# application. Everything else is stored and returned as given.
KIND_TEXT = "text"
KIND_TEXTAREA = "textarea"
KIND_EMAIL = "email"
KIND_PHONE = "phone"
KIND_URL = "url"
KIND_DATE = "date"
KIND_SELECT = "select"
KIND_CHECKBOX = "checkbox"
KIND_SECRET = "secret"


def _f(key, label, kind=KIND_TEXT, required=False, options=None, help=None):
    return {"key": key, "label": label, "kind": kind, "required": bool(required),
            "options": options or [], "help": help}


# ── THE EIGHT STEPS ─────────────────────────────────────────────────────────
# Keys and field names match the Stage 1 prototype exactly. `required` is what
# the customer must answer before that step counts as complete; it is the ONLY
# thing that drives the percentage, so adding a required field moves every
# customer's number and should be a considered act.
STEP_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "company", "n": 1, "label": "Company Information",
        "title": "Company Information",
        "blurb": "The legal and operating details we build your system around — "
                 "how you are named on contracts, who we contact, and where you "
                 "serve customers.",
        "fields": [
            _f("legalName", "Legal company name", required=True),
            _f("dba", "Doing business as"),
            _f("ein", "EIN / Tax ID"),
            _f("contactFirst", "Primary contact first name", required=True),
            _f("contactLast", "Primary contact last name", required=True),
            _f("contactTitle", "Title"),
            _f("contactEmail", "Contact email", KIND_EMAIL, required=True),
            _f("contactMobile", "Mobile", KIND_PHONE),
            _f("contactBusiness", "Business phone", KIND_PHONE),
            _f("address", "Street address", required=True),
            _f("city", "City", required=True),
            _f("state", "State", required=True),
            _f("zip", "ZIP", required=True),
            _f("website", "Current website", KIND_URL),
            _f("founded", "Year founded"),
            _f("employees", "Employees"),
            _f("territory", "Service territory"),
            _f("statesServed", "States served"),
            _f("markets", "Utility markets / TDUs"),
            _f("goLive", "Target go-live date", KIND_DATE),
            _f("additionalInfo", "Anything else we should know", KIND_TEXTAREA),
        ],
    },
    {
        "key": "branding", "n": 2, "label": "Branding & Assets",
        "title": "Branding & Assets",
        "blurb": "Everything your new website and customer communications will be "
                 "dressed in. Send what you have; we will tell you what is missing.",
        "fields": [
            _f("brandColors", "Brand colours"),
            _f("fonts", "Fonts"),
            _f("logoNotes", "Logo notes", KIND_TEXTAREA),
            _f("toneOfVoice", "Tone of voice"),
            _f("brandNotes", "Other brand guidance", KIND_TEXTAREA),
        ],
    },
    {
        "key": "website", "n": 3, "label": "Website & Hosting Access",
        "title": "Website & Hosting Access",
        "blurb": "Where your current site and domain live, so we can build "
                 "alongside it and cut over cleanly without any downtime.",
        "fields": [
            _f("hostProvider", "Hosting provider", required=True),
            _f("hostLoginUrl", "Hosting login URL", KIND_URL),
            _f("hostUser", "Hosting username"),
            # SECRETS. Encrypted at rest, never returned. See save_step().
            _f("hostPass", "Hosting password", KIND_SECRET,
               help="Stored encrypted. It is never displayed again, including to you."),
            _f("cms", "CMS / platform"),
            _f("registrar", "Domain registrar", required=True),
            _f("dnsUrl", "Registrar / DNS login URL", KIND_URL),
            _f("registrarUser", "Registrar username"),
            _f("registrarPass", "Registrar password", KIND_SECRET,
               help="Stored encrypted. It is never displayed again, including to you."),
            _f("techContact", "Technical contact"),
        ],
    },
    {
        "key": "compare", "n": 4, "label": "ComparePower Integration",
        "title": "ComparePower Integration",
        "blurb": "Your ComparePower relationship and the people who own it. "
                 "We handle the technical integration end to end.",
        "fields": [
            _f("cpContact", "ComparePower contact", required=True),
            _f("cpEmail", "Contact email", KIND_EMAIL),
            _f("cpPhone", "Contact phone", KIND_PHONE),
            _f("cpTech", "Technical contact"),
            _f("cpDocsUrl", "API documentation URL", KIND_URL),
            _f("cpSandbox", "Sandbox access", KIND_SELECT, options=[
                {"value": "not_requested", "label": "Not requested"},
                {"value": "requested", "label": "Requested"},
                {"value": "granted", "label": "Granted"}]),
            _f("cpProd", "Production access", KIND_SELECT, options=[
                {"value": "not_requested", "label": "Not requested"},
                {"value": "requested", "label": "Requested"},
                {"value": "granted", "label": "Granted"}]),
            _f("cpPartnerId", "Partner / affiliate ID"),
            _f("cpNotes", "Notes", KIND_TEXTAREA),
        ],
    },
    {
        "key": "systems", "n": 5, "label": "Team & Current Systems",
        "title": "Team & Current Systems",
        "blurb": "The tools you run on today and the people who use them, so "
                 "nothing you depend on gets stranded when the new system goes live.",
        "fields": [
            _f("sysAdmin", "Systems administrator", required=True),
            _f("leadRecipient", "Who receives new enquiries today", required=True),
            _f("emailProvider", "Email provider"),
            _f("smsProvider", "SMS provider"),
            _f("calendarProvider", "Calendar"),
            _f("crm", "CRM"),
            _f("forms", "Web forms"),
            _f("otherSoftware", "Other software"),
            _f("userList", "People who need access", KIND_TEXTAREA),
        ],
    },
    {
        "key": "process", "n": 6, "label": "Customer Process",
        "title": "Customer Process",
        "blurb": "What happens after a customer gives you their information. This "
                 "is the most important section — it is what the automation is "
                 "built to reproduce.",
        "fields": [
            _f("processDetail", "Walk us through it, start to finish",
               KIND_TEXTAREA, required=True),
            _f("firstReceiver", "Who sees it first", required=True),
            _f("responseTime", "Target response time", KIND_SELECT, required=True, options=[
                {"value": "minutes", "label": "Within minutes"},
                {"value": "same_day", "label": "Same day"},
                {"value": "next_day", "label": "Next business day"},
                {"value": "varies", "label": "It varies"}]),
            _f("rateReviewer", "Who reviews rates with the customer"),
            _f("enrollmentHelper", "Who helps with enrolment"),
            _f("completionMarker", "How you know it is done", KIND_TEXTAREA),
            _f("noResponse", "What happens when they do not reply", KIND_TEXTAREA),
        ],
    },
    {
        "key": "files", "n": 7, "label": "Files & Documents",
        "title": "Files & Documents",
        "blurb": "The documents and samples we need in hand before the build starts.",
        # No text fields — this step's completion is measured in uploads.
        "fields": [],
        "requires_files": 1,
    },
    {
        "key": "review", "n": 8, "label": "Review & Submit",
        "title": "Review & Submit",
        "blurb": "A last look at everything you have given us, then sign off and "
                 "hand it to the implementation team.",
        "fields": [
            _f("sigName", "Your full name", required=True),
            _f("sigTitle", "Your title", required=True),
            _f("sigCompany", "Company", required=True),
            _f("sigAffirm", "I confirm this information is accurate",
               KIND_CHECKBOX, required=True),
        ],
    },
]

STEP_KEYS = [s["key"] for s in STEP_SCHEMA]
STEP_BY_KEY = {s["key"]: s for s in STEP_SCHEMA}

# The implementation lifecycle shown above the intake. Distinct from the steps
# on purpose: one is the project, the other is the form. Collapsing them is what
# made earlier onboarding screens read as a wizard rather than a programme.
LIFECYCLE = [
    {"key": "intake",       "label": "Complete Intake"},
    {"key": "access",       "label": "Provide Access & Files"},
    {"key": "build",        "label": "Build"},
    {"key": "integrations", "label": "Integrations"},
    {"key": "review",       "label": "Review & Test"},
    {"key": "training",     "label": "Training"},
    {"key": "golive",       "label": "Go Live"},
]

# Implementation.status is the platform's existing vocabulary and stays the
# source of truth. This maps it onto the customer-facing phase, because the
# customer should not be shown the words "data_migration" or "not_started".
# A status missing from this map lands on intake rather than crashing — a new
# implementation status must never blank the customer's progress bar.
_STATUS_TO_PHASE = {
    "not_started":       "intake",
    "kickoff_scheduled": "access",
    "configuration":     "build",
    "data_migration":    "build",
    "integrations":      "integrations",
    "testing":           "review",
    "training":          "training",
    "ready_for_launch":  "golive",
    "live":              None,        # everything behind it is done
}


def lifecycle_for(status: Optional[str]) -> List[Dict[str, Any]]:
    """The seven phases with done/now/next, derived from the implementation.

    Computed on the server so the tracker and the status can never disagree,
    and so `blocked` — which is not a position on the line — keeps showing the
    phase the project actually stopped at rather than resetting to the start.
    """
    keys = [p["key"] for p in LIFECYCLE]

    if status == "live":
        return [{**p, "state": "done"} for p in LIFECYCLE]

    phase = _STATUS_TO_PHASE.get(status or "not_started", "intake")
    if phase is None or phase not in keys:
        phase = "intake"
    idx = keys.index(phase)

    out = []
    for i, p in enumerate(LIFECYCLE):
        out.append({**p, "state": "done" if i < idx else ("now" if i == idx else "next")})
    return out


def secret_keys(step_key: str) -> List[str]:
    step = STEP_BY_KEY.get(step_key)
    if not step:
        return []
    return [f["key"] for f in step["fields"] if f["kind"] == KIND_SECRET]


def known_keys(step_key: str) -> List[str]:
    step = STEP_BY_KEY.get(step_key)
    if not step:
        return []
    return [f["key"] for f in step["fields"]]


def _answered(field: Dict[str, Any], answers: Dict[str, Any],
              secrets_present: set) -> bool:
    key = field["key"]
    if field["kind"] == KIND_SECRET:
        return key in secrets_present
    value = answers.get(key)
    if field["kind"] == KIND_CHECKBOX:
        return value is True
    return value is not None and str(value).strip() != ""


def step_completion(step_key: str, answers: Optional[Dict[str, Any]],
                    secrets_present: Optional[set] = None,
                    file_count: int = 0) -> Dict[str, Any]:
    """Percentage and the NAMES of what is still outstanding.

    Returning the missing field labels rather than only a number is the
    difference between a review screen that says "60%" and one that says which
    three questions are unanswered. The customer cannot act on a percentage.
    """
    step = STEP_BY_KEY.get(step_key)
    if not step:
        return {"pct": 0, "status": STEP_NOT_STARTED, "missing": [],
                "required_total": 0, "required_done": 0}

    answers = answers or {}
    secrets_present = secrets_present or set()

    required = [f for f in step["fields"] if f["required"]]
    done, missing = [], []
    for f in required:
        (done if _answered(f, answers, secrets_present) else missing).append(f)

    needs_files = int(step.get("requires_files") or 0)
    files_ok = file_count >= needs_files if needs_files else True
    if needs_files and not files_ok:
        missing = missing + [{"key": "__files__",
                              "label": "At least one document uploaded"}]

    req_total = len(required) + (1 if needs_files else 0)
    req_done = len(done) + (1 if needs_files and files_ok else 0)

    # A step with nothing required is complete once ANY answer is given, and
    # not-started otherwise — it would otherwise sit at 0% forever and drag the
    # overall figure down for a customer who has answered everything asked.
    if req_total == 0:
        any_answer = any(
            _answered(f, answers, secrets_present) for f in step["fields"])
        pct = 100 if any_answer else 0
        status = STEP_COMPLETE if any_answer else STEP_NOT_STARTED
        return {"pct": pct, "status": status, "missing": [],
                "required_total": 0, "required_done": 0}

    pct = int(round(100 * req_done / req_total))
    if pct >= 100:
        status = STEP_COMPLETE
    elif req_done > 0 or any(_answered(f, answers, secrets_present)
                             for f in step["fields"]):
        status = STEP_IN_PROGRESS
    else:
        status = STEP_NOT_STARTED

    return {"pct": pct, "status": status,
            "missing": [{"key": m["key"], "label": m["label"]} for m in missing],
            "required_total": req_total, "required_done": req_done}


# ── persistence ─────────────────────────────────────────────────────────────

def _rows(db: Session, impl_id: str, org_id: str) -> Dict[str, LaunchIntakeStep]:
    """Every step row for one implementation, keyed by step.

    organization_id is in the filter even though implementation_id alone is
    unique. Belt and braces on the property that matters most, and it means a
    wrong implementation id from anywhere returns nothing rather than another
    tenant's answers.
    """
    rows = (db.query(LaunchIntakeStep)
            .filter(LaunchIntakeStep.implementation_id == impl_id,
                    LaunchIntakeStep.organization_id == org_id)
            .all())
    return {r.step_key: r for r in rows}


def live_file_count(db: Session, impl_id: str, org_id: str,
                    step_key: Optional[str] = None) -> int:
    q = (db.query(LaunchIntakeFile)
         .filter(LaunchIntakeFile.implementation_id == impl_id,
                 LaunchIntakeFile.organization_id == org_id,
                 LaunchIntakeFile.deleted_at.is_(None)))
    if step_key:
        q = q.filter(LaunchIntakeFile.step_key == step_key)
    return q.count()


def overview(db: Session, impl_id: str, org_id: str) -> Dict[str, Any]:
    """Completion for every step plus the one overall number.

    The overall figure is computed HERE, once, and handed to both the ring and
    the meter. Two components each averaging their own copy is how a screen
    ends up showing 60% beside 62%.
    """
    rows = _rows(db, impl_id, org_id)
    files_total = live_file_count(db, impl_id, org_id)

    steps, total = [], 0
    for schema in STEP_SCHEMA:
        key = schema["key"]
        row = rows.get(key)
        secrets_present = set((row.secrets_encrypted or {}).keys()) if row else set()
        fc = files_total if schema.get("requires_files") else 0
        c = step_completion(key, row.answers if row else {}, secrets_present, fc)
        total += c["pct"]
        steps.append({
            "key": key, "n": schema["n"], "label": schema["label"],
            "title": schema["title"], "blurb": schema["blurb"],
            "pct": c["pct"], "status": c["status"], "missing": c["missing"],
            "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
        })

    return {
        "steps": steps,
        "overall_pct": int(round(total / len(STEP_SCHEMA))) if STEP_SCHEMA else 0,
        "file_count": files_total,
        "complete_steps": sum(1 for s in steps if s["status"] == STEP_COMPLETE),
        "total_steps": len(STEP_SCHEMA),
    }


def read_step(db: Session, impl_id: str, org_id: str,
              step_key: str) -> Dict[str, Any]:
    """One step's answers, with secrets reported as set/unset and never returned."""
    row = (db.query(LaunchIntakeStep)
           .filter(LaunchIntakeStep.implementation_id == impl_id,
                   LaunchIntakeStep.organization_id == org_id,
                   LaunchIntakeStep.step_key == step_key)
           .first())
    schema = STEP_BY_KEY[step_key]
    stored_secrets = set((row.secrets_encrypted or {}).keys()) if row else set()
    fc = live_file_count(db, impl_id, org_id) if schema.get("requires_files") else 0
    c = step_completion(step_key, row.answers if row else {}, stored_secrets, fc)

    return {
        "key": step_key,
        "answers": (row.answers or {}) if row else {},
        # Presence only. The value has no read path anywhere in this app.
        "secrets_set": sorted(stored_secrets),
        "status": c["status"], "pct": c["pct"], "missing": c["missing"],
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def save_step(db: Session, impl_id: str, org_id: str, step_key: str,
              payload: Dict[str, Any], actor_id: Optional[str]) -> Dict[str, Any]:
    """Persist one step. Unknown keys are DROPPED, secrets are encrypted.

    Dropping unknown keys rather than storing them keeps the JSON column an
    answer set rather than an open bucket a caller can write arbitrary data
    into — including data a later read path might render.
    """
    if step_key not in STEP_BY_KEY:
        raise ValueError("unknown step")

    allowed = set(known_keys(step_key))
    secrets = set(secret_keys(step_key))

    row = (db.query(LaunchIntakeStep)
           .filter(LaunchIntakeStep.implementation_id == impl_id,
                   LaunchIntakeStep.organization_id == org_id,
                   LaunchIntakeStep.step_key == step_key)
           .first())
    if row is None:
        row = LaunchIntakeStep(
            implementation_id=impl_id,
            # From the implementation, never from the request body.
            organization_id=org_id,
            step_key=step_key, answers={}, secrets_encrypted={})
        db.add(row)

    answers = dict(row.answers or {})
    stored_secrets = dict(row.secrets_encrypted or {})

    for key, value in (payload or {}).items():
        if key not in allowed:
            continue
        if key in secrets:
            # An empty string means "leave it alone", not "clear it". A form
            # that renders a blank password box every time would otherwise wipe
            # a stored credential on every unrelated save.
            if value is None or str(value).strip() == "":
                continue
            try:
                from app.utils.crypto import encrypt_value
                stored_secrets[key] = encrypt_value(str(value))
            except Exception as exc:  # noqa: BLE001
                # Refuse rather than fall back to plaintext. A column named
                # *_encrypted holding plaintext is worse than no column.
                log.error("launch_intake: could not encrypt %s: %s", key, type(exc).__name__)
                raise ValueError("secure storage unavailable for %s" % key)
        else:
            answers[key] = value

    row.answers = answers
    row.secrets_encrypted = stored_secrets
    row.updated_by = actor_id

    schema = STEP_BY_KEY[step_key]
    fc = live_file_count(db, impl_id, org_id) if schema.get("requires_files") else 0
    c = step_completion(step_key, answers, set(stored_secrets.keys()), fc)
    row.status = c["status"]
    row.completion_pct = c["pct"]

    db.commit()
    db.refresh(row)
    return read_step(db, impl_id, org_id, step_key)


def submission_blockers(db: Session, impl_id: str, org_id: str) -> List[Dict[str, Any]]:
    """What still stands between this customer and Submit.

    Returned as a list of concrete items, never a bare boolean — "you cannot
    submit yet" with no reason is the single most common way an onboarding
    form loses a customer.
    """
    ov = overview(db, impl_id, org_id)
    out = []
    for s in ov["steps"]:
        for m in s["missing"]:
            out.append({"step_key": s["key"], "step_label": s["label"],
                        "field_key": m["key"], "label": m["label"]})
    return out


def submit(db: Session, impl_id: str, org_id: str,
           actor_id: Optional[str]) -> LaunchIntakeSubmission:
    """Snapshot and hand over. Raises ValueError when anything is outstanding."""
    blockers = submission_blockers(db, impl_id, org_id)
    if blockers:
        raise ValueError("incomplete")

    rows = _rows(db, impl_id, org_id)
    ov = overview(db, impl_id, org_id)

    # Secrets are deliberately absent from the snapshot — see the model.
    snapshot = {k: dict(r.answers or {}) for k, r in rows.items()}
    review = rows.get("review")
    ra = (review.answers or {}) if review else {}

    sub = LaunchIntakeSubmission(
        implementation_id=impl_id,
        organization_id=org_id,
        snapshot=snapshot,
        completion={"overall_pct": ov["overall_pct"],
                    "steps": {s["key"]: s["pct"] for s in ov["steps"]}},
        file_count=ov["file_count"],
        signed_name=str(ra.get("sigName") or "")[:160] or None,
        signed_title=str(ra.get("sigTitle") or "")[:160] or None,
        signed_company=str(ra.get("sigCompany") or "")[:200] or None,
        submitted_at=datetime.utcnow(),
        submitted_by=actor_id,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def latest_submission(db: Session, impl_id: str,
                      org_id: str) -> Optional[LaunchIntakeSubmission]:
    return (db.query(LaunchIntakeSubmission)
            .filter(LaunchIntakeSubmission.implementation_id == impl_id,
                    LaunchIntakeSubmission.organization_id == org_id)
            .order_by(LaunchIntakeSubmission.submitted_at.desc())
            .first())
