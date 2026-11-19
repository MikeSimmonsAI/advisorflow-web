"""Client Growth questionnaire templates and the one validator they share.

A template is a list of STEPS, each a list of FIELDS. The shared discovery
frame (goals, budget, team, contact, notes) is the same for every vertical;
a vertical's template adds its own fields (insurance products, licensed-agent
handoff). The stored definition is what validates a submission, so an answer
is always checked against the exact version it was given.

Nothing here names a prospect. "Prepared for ..." and the pre-filled contact
come from the invitation, never from a template.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

INSURANCE_KEY = "agency_growth_insurance"

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_PHONE = re.compile(r"^[0-9+().\-\s]{7,25}$")

NO_AUTOMATED_CONSENT = ("This only tells our team how you'd like to be reached about your proposal. "
                        "It is not permission for automated calls or texts.")


def _opt(value: str, label: str) -> Dict[str, str]:
    return {"value": value, "label": label}


def _shared_contact_fields() -> List[Dict[str, Any]]:
    return [
        {"key": "contact_name", "label": "Contact name", "type": "text", "required": True, "max": 120,
         "prefill": "contact_name", "autocomplete": "name"},
        {"key": "business_name", "label": "Agency", "type": "text", "required": True, "max": 160,
         "prefill": "business_name", "autocomplete": "organization"},
        {"key": "email", "label": "Best email", "type": "email", "required": True, "max": 160,
         "prefill": "email", "autocomplete": "email"},
        {"key": "phone", "label": "Phone", "type": "tel", "required": False, "max": 25, "optional_label": True,
         "autocomplete": "tel"},
        {"key": "preferred_contact", "label": "Preferred contact method", "type": "select", "required": False,
         "optional_label": True, "placeholder": "Choose one",
         "options": [_opt("email", "Email"), _opt("phone", "Phone call"), _opt("text", "Text message")],
         "helper": NO_AUTOMATED_CONSENT},
        {"key": "notes", "label": "Anything else we should know?", "type": "textarea", "required": False,
         "optional_label": True, "max": 2000,
         "placeholder": "Share any additional goals, challenges, or context (optional)."},
    ]


def insurance_v1() -> Dict[str, Any]:
    """The first template: insurance agencies. Version 1."""
    return {
        "schema": 1,
        "public": {
            "product": "Agency Growth Questionnaire",
            "eyebrow": "Prepared for {prepared_for}",
            "headline": "Your Agency Growth Plan",
            "description": "Tell us where you want to grow. We'll build a plan around your agency.",
            "estimate": "About 3 minutes",
            "cta": "Send My Growth Goals",
            "cta_helper": "We'll review your answers and follow up with a tailored proposal.",
            "success": ("Thanks, {first_name}. We've received your growth goals. Mike and the EvoSys Pro "
                        "team will review your answers and follow up."),
            "panel": {
                "title": "A plan built around your agency",
                "points": ["Attract new prospects", "Connect leads with your team", "Track quotes and new business"],
                "next_title": "What happens next?",
                "next": ["We review your goals", "We discuss the right approach", "You receive your proposal"],
                "team_title": "Your EvoSys Pro team",
                "team": ["Mike Simmons", "Michael Schlueter"],
            },
        },
        "steps": [
            {"key": "goals", "number": "01", "title": "Your goals", "fields": [
                {"key": "new_customers_per_month", "label": "New customers per month", "type": "count_or_help",
                 "required": True, "min": 1, "maxValue": 100000, "placeholder": "Enter your target",
                 "help_value": "help", "help_label": "Help me set a target"},
                {"key": "preferred_launch", "label": "Preferred launch", "type": "select", "required": True,
                 "placeholder": "Select a timeframe",
                 "options": [_opt("asap", "As soon as practical"), _opt("30_days", "Within 30 days"),
                             _opt("60_90_days", "Within 60–90 days"), _opt("exploring", "Exploring options")]},
                {"key": "insurance_products", "label": "Insurance products", "type": "multi", "required": True,
                 "template_specific": True,
                 "options": [_opt("auto", "Auto"), _opt("home", "Home"), _opt("renters", "Renters"),
                             _opt("life", "Life"), _opt("commercial", "Commercial"), _opt("other", "Other")]},
                {"key": "insurance_products_other", "label": "Other products", "type": "text", "required": False,
                 "max": 200, "template_specific": True, "placeholder": "Which other products?",
                 "show_if": {"field": "insurance_products", "includes": "other"}},
                {"key": "service_areas", "label": "Service areas", "type": "text", "required": False, "max": 400,
                 "placeholder": "Mesquite, Dallas, Garland, Rockwall, Forney",
                 "helper": "Cities or regions you want to focus on."},
            ]},
            {"key": "budget", "number": "02", "title": "Your budget", "fields": [
                {"key": "monthly_ad_budget", "label": "Monthly advertising budget", "type": "radio", "required": True,
                 "options": [_opt("under_1000", "Under $1,000"), _opt("1000_3000", "$1,000 to under $3,000"),
                             _opt("3000_5000", "$3,000 to under $5,000"), _opt("5000_plus", "$5,000+"),
                             _opt("help", "Help me decide")],
                 "helper": "Advertising spend is separate from service fees and VA costs."},
                {"key": "monthly_va_budget", "label": "Monthly VA budget", "type": "amount_or_help",
                 "required": False, "optional_label": True, "min": 0, "maxValue": 1000000,
                 "placeholder": "e.g. 1500", "help_value": "not_sure", "help_label": "Not sure yet"},
            ]},
            {"key": "team", "number": "03", "title": "Your team", "fields": [
                {"key": "va_status", "label": "Do you currently use virtual assistants?", "type": "radio",
                 "required": True,
                 "options": [_opt("yes", "Yes"), _opt("no", "No"), _opt("planning", "Planning to")]},
                {"key": "va_count", "label": "How many?", "type": "int", "required": False, "optional_label": True,
                 "min": 0, "maxValue": 1000, "show_if": {"field": "va_status", "equals": "yes"}},
                {"key": "va_hours", "label": "Working hours / time zone", "type": "text", "required": False,
                 "optional_label": True, "max": 160, "placeholder": "e.g. 9–5 Central",
                 "show_if": {"field": "va_status", "equals": "yes"}},
                {"key": "va_languages", "label": "Languages", "type": "text", "required": False,
                 "optional_label": True, "max": 160, "show_if": {"field": "va_status", "equals": "yes"}},
                {"key": "va_manager", "label": "Who manages them?", "type": "text", "required": False,
                 "optional_label": True, "max": 160, "show_if": {"field": "va_status", "equals": "yes"}},
                {"key": "support_needed", "label": "Where would you like help?", "type": "multi", "required": False,
                 "options": [_opt("lead_follow_up", "Lead follow-up"), _opt("appointment_booking", "Appointment booking"),
                             _opt("va_setup", "VA setup"), _opt("ai_assistance", "AI assistance")]},
                {"key": "handoff_preference", "label": "How should new prospects reach your agents?",
                 "type": "radio", "required": True, "template_specific": True,
                 "options": [_opt("appointments", "Scheduled appointments"),
                             _opt("live_transfer", "Live transfer to a licensed agent"),
                             _opt("both", "Both"), _opt("help", "Help me decide")]},
                {"key": "licensed_agents", "label": "Licensed agents receiving opportunities", "type": "int",
                 "required": False, "optional_label": True, "min": 0, "maxValue": 1000, "template_specific": True},
            ] + _shared_contact_fields()},
        ],
    }


BUILTIN: Dict[str, Dict[str, Any]] = {
    INSURANCE_KEY: {"version": 1, "vertical": "insurance", "name": "Agency Growth Questionnaire — Insurance",
                    "definition": insurance_v1},
}


def fields(definition: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [f for s in definition.get("steps", []) for f in s.get("fields", [])]


def _visible(f: Dict[str, Any], answers: Dict[str, Any]) -> bool:
    cond = f.get("show_if")
    if not cond:
        return True
    v = answers.get(cond.get("field"))
    if "equals" in cond:
        return v == cond["equals"]
    if "includes" in cond:
        return isinstance(v, list) and cond["includes"] in v
    return True


def _int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"\s*\d{1,9}\s*", v):
        return int(v)
    return None


def validate(definition: Dict[str, Any], raw: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Return (clean answers, {field: error}). Unknown keys are dropped, hidden
    conditional answers are dropped, every value is bounded."""
    errors: Dict[str, str] = {}
    clean: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return {}, {"_": "The answers could not be read."}
    flist = fields(definition)
    # Pass 1: unconditional fields, so show_if can read them.
    ordered = [f for f in flist if not f.get("show_if")] + [f for f in flist if f.get("show_if")]
    for f in ordered:
        key, t = f["key"], f["type"]
        if not _visible(f, clean):
            continue
        v = raw.get(key)
        empty = v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)
        if empty:
            if f.get("required"):
                errors[key] = "Please choose at least one." if t == "multi" else "This is required."
            continue
        opts = [o["value"] for o in f.get("options", [])]
        if t in ("text", "textarea", "email", "tel"):
            if not isinstance(v, str):
                errors[key] = "Please enter text."
                continue
            s = v.strip()
            if len(s) > int(f.get("max", 500)):
                errors[key] = "Please keep this under %d characters." % int(f.get("max", 500))
                continue
            if t == "email" and not _EMAIL.match(s):
                errors[key] = "Please enter a valid email address."
                continue
            if t == "tel" and not _PHONE.match(s):
                errors[key] = "Please enter a valid phone number."
                continue
            clean[key] = s
        elif t in ("select", "radio"):
            if v not in opts:
                errors[key] = "Please choose one of the options."
                continue
            clean[key] = v
        elif t == "multi":
            if not isinstance(v, list) or any(x not in opts for x in v):
                errors[key] = "Please choose from the options."
                continue
            clean[key] = [o for o in opts if o in v]          # option order, no duplicates
        elif t in ("count_or_help", "amount_or_help", "int"):
            if t != "int" and v == f.get("help_value"):
                clean[key] = v
                continue
            n = _int(v)
            lo, hi = int(f.get("min", 0)), int(f.get("maxValue", 10 ** 9))
            if n is None or n < lo or n > hi:
                errors[key] = ("Please enter a whole number of at least %d." % lo) if t != "int" or lo > 0 \
                    else "Please enter a whole number."
                continue
            clean[key] = n
        else:
            errors[key] = "Unsupported question."
    return clean, errors


def label_for(definition: Dict[str, Any], key: str, value: Any) -> Any:
    """Human-readable answer for staff screens. The stored value is unchanged."""
    f = next((x for x in fields(definition) if x["key"] == key), None)
    if f is None:
        return value
    opts = {o["value"]: o["label"] for o in f.get("options", [])}
    if f["type"] in ("count_or_help", "amount_or_help") and value == f.get("help_value"):
        return f.get("help_label")
    if f["type"] == "amount_or_help" and isinstance(value, int):
        return "${:,}".format(value)
    if isinstance(value, list):
        return [opts.get(v, v) for v in value]
    return opts.get(value, value)


def readable(definition: Dict[str, Any], answers: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every question in order, with its original stored value and its label."""
    out = []
    for s in definition.get("steps", []):
        for f in s.get("fields", []):
            if f["key"] in answers:
                out.append({"step": s["title"], "key": f["key"], "label": f["label"], "value": answers[f["key"]],
                            "display": label_for(definition, f["key"], answers[f["key"]])})
    return out
