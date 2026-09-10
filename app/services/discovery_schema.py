"""
Seller discovery, as STRUCTURED ANSWERS rather than a wall of textareas.

WHAT PROBLEM THIS SOLVES
------------------------
`DiscoveryRecord` has fourteen `Text` columns and the Opportunity screen
rendered one giant textarea per column. A salesperson on a call does not type
four paragraphs into fourteen boxes; they tick what is true and move on. The
old screen was a database editor wearing a sales workflow's name.

WHAT THIS MODULE IS
-------------------
A presentation-and-adapter layer over the columns that already exist. It is
deliberately NOT a second discovery engine, and it adds NO per-question column:

  * `SCHEMA` declares, per existing DiscoveryRecord field, which control the
    seller should get and which options that control offers.
  * `render()` turns a structured answer into the readable sentence that goes
    into the field's EXISTING Text column, so every downstream reader —
    `provisioning._discovery_summary`, the demo-requirements carry-forward,
    proposals — keeps working with no change at all.
  * The structured answer itself is kept verbatim in ONE new nullable column,
    `discovery_records.structured_json`, so re-opening the form restores the
    ticks rather than trying to reverse-engineer them out of prose.
  * Long-form text that was captured BEFORE this existed is snapshotted into
    that same JSON under `legacy` the first time a field is answered
    structurally, and shown back to the seller under "Previous / detailed
    notes". Nothing is migrated and nothing is deleted.

WHY THE OPTION LISTS LIVE HERE AND NOT IN A BRAND CONFIG
--------------------------------------------------------
These are the questions the SALES ENGINE asks about any business — lead
sources, bottlenecks, what to put in a demo. They carry no EvoSys-specific,
vertical-specific or tenant-specific logic; a funeral home, a roofing company
and a law firm all answer the same list. When a brand genuinely needs its own
question set, `SCHEMA` is the one place it would be resolved from, and every
consumer already goes through `schema_payload()`.
"""
from typing import Any, Dict, List, Optional
import json

# ── control vocabulary ──────────────────────────────────────────────────────
# multi     — tick any number of options (+ optional "Other" free text)
# single    — pick exactly one (+ optional "Other" free text)
# composite — a small set of named parts, each its own single/number/text
# note      — one short free-text answer, deliberately not a wall
MULTI = "multi"
SINGLE = "single"
COMPOSITE = "composite"
NOTE = "note"

OTHER = "other"


def _opts(*pairs) -> List[Dict[str, str]]:
    return [{"value": v, "label": l} for v, l in pairs]


_OUTCOMES = _opts(
    ("more_appointments", "More appointments"),
    ("faster_response", "Faster lead response"),
    ("better_follow_up", "Better follow-up"),
    ("automate_outreach", "Automate outreach"),
    ("reduce_manual_work", "Reduce manual work"),
    ("sales_visibility", "Improve sales visibility"),
    ("increase_conversion", "Increase conversion"),
    ("scale_without_staff", "Scale without adding staff"),
    (OTHER, "Other"),
)


# ── the schema ──────────────────────────────────────────────────────────────
# Order is the order the seller sees. `group` is the heading the panel renders
# these under; `required` is what the progress indicator counts.
SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "business_description",
        "label": "The business",
        "group": "Customer",
        "control": COMPOSITE,
        "required": True,
        "note_label": "Anything else worth knowing",
        "parts": [
            {"key": "business_type", "label": "Business type", "control": SINGLE,
             "allow_other": True,
             "options": _opts(
                 ("home_services", "Home services"),
                 ("professional_services", "Professional services"),
                 ("healthcare", "Healthcare / medical"),
                 ("legal", "Legal"),
                 ("real_estate", "Real estate"),
                 ("insurance", "Insurance"),
                 ("financial_services", "Financial services"),
                 ("automotive", "Automotive"),
                 ("fitness_wellness", "Fitness / wellness"),
                 ("education", "Education"),
                 ("retail_ecommerce", "Retail / e-commerce"),
                 ("hospitality", "Hospitality"),
                 ("construction_trades", "Construction / trades"),
                 ("funeral_cemetery", "Funeral / cemetery"),
                 ("nonprofit", "Non-profit"),
                 (OTHER, "Other"),
             )},
            {"key": "locations", "label": "Locations", "control": "number",
             "min": 1, "max": 999, "default": 1},
            {"key": "decision_maker", "label": "Primary decision maker",
             "control": "contact"},
            {"key": "stakeholders", "label": "Other people in the decision",
             "control": "contact"},
        ],
    },
    {
        "key": "team_size",
        "label": "Team size",
        "group": "Customer",
        "control": SINGLE,
        "required": True,
        "allow_other": True,
        "options": _opts(
            ("1", "Just them (1)"),
            ("2-5", "2–5"),
            ("6-10", "6–10"),
            ("11-25", "11–25"),
            ("26-50", "26–50"),
            ("51-100", "51–100"),
            ("100+", "100+"),
        ),
    },
    {
        "key": "lead_sources",
        "label": "Lead sources",
        "group": "Current process",
        "control": MULTI,
        "required": True,
        "allow_other": True,
        "options": _opts(
            ("website", "Website"),
            ("google", "Google"),
            ("facebook", "Facebook"),
            ("referrals", "Referrals"),
            ("purchased", "Purchased leads"),
            ("existing_list", "Existing customer list"),
            ("cold_outreach", "Cold outreach"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "current_process",
        "label": "How they reach leads today",
        "group": "Current process",
        "control": MULTI,
        "required": True,
        "allow_other": True,
        "note_label": "Short note on how that actually runs",
        "options": _opts(
            ("phone", "Phone"),
            ("sms", "SMS"),
            ("email", "Email"),
            ("manual_follow_up", "Manual follow-up"),
            ("crm_automation", "CRM automation"),
            ("ai", "AI"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "current_tools",
        "label": "Systems and tools in use",
        "group": "Current process",
        "control": MULTI,
        "required": False,
        "allow_other": True,
        "options": _opts(
            ("crm", "CRM"),
            ("spreadsheets", "Spreadsheets"),
            ("email_marketing", "Email marketing"),
            ("dialer", "Phone system / dialer"),
            ("calendar", "Calendar"),
            ("accounting", "Accounting"),
            ("website_forms", "Website forms"),
            ("none", "Nothing formal"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "follow_up_process",
        "label": "Follow-up cadence today",
        "group": "Current process",
        "control": MULTI,
        "required": False,
        "allow_other": True,
        "options": _opts(
            ("same_day_call", "Same-day call"),
            ("next_day_call", "Next-day call"),
            ("sms_sequence", "SMS sequence"),
            ("email_sequence", "Email sequence"),
            ("voicemail", "Voicemail drop"),
            ("none", "No structured follow-up"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "bottlenecks",
        "label": "What is going wrong",
        "group": "Pain",
        "control": MULTI,
        "required": True,
        "allow_other": True,
        "note_label": "In their words, briefly",
        "options": _opts(
            ("slow_response", "Slow lead response"),
            ("no_follow_up", "Leads not followed up"),
            ("missed_calls", "Missed calls"),
            ("poor_appointment_setting", "Poor appointment setting"),
            ("manual_work", "Manual work"),
            ("no_visibility", "No visibility"),
            ("too_many_systems", "Too many systems"),
            ("low_conversion", "Low conversion"),
            ("staffing", "Staffing limitations"),
            ("no_automation", "No automation"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "business_goals",
        "label": "What they want instead",
        "group": "Goals",
        "control": MULTI,
        "required": True,
        "allow_other": True,
        "options": _OUTCOMES,
    },
    {
        "key": "desired_outcome",
        "label": "What success looks like in 90 days",
        "group": "Goals",
        "control": NOTE,
        "required": False,
        "note_label": "One or two lines, only if they said something specific",
    },
    {
        "key": "appointment_process",
        "label": "Appointments",
        "group": "Appointments",
        "control": COMPOSITE,
        "required": True,
        "parts": [
            {"key": "books_today", "label": "Booking appointments today?",
             "control": SINGLE,
             "options": _opts(("yes", "Yes"), ("no", "No"),
                              ("sometimes", "Inconsistently"))},
            {"key": "who_books", "label": "Who books them", "control": SINGLE,
             "allow_other": True,
             "options": _opts(
                 ("owner", "Owner"), ("receptionist", "Front desk / receptionist"),
                 ("sales_team", "Sales team"), ("call_center", "Call centre"),
                 ("nobody", "Nobody consistently"), (OTHER, "Other"))},
            {"key": "calendar", "label": "Calendar / system used",
             "control": SINGLE, "allow_other": True,
             "options": _opts(
                 ("google", "Google Calendar"), ("outlook", "Outlook"),
                 ("calendly", "Calendly / scheduler"), ("crm", "Inside their CRM"),
                 ("paper", "Paper / whiteboard"), ("none", "None"),
                 (OTHER, "Other"))},
            {"key": "volume_month", "label": "Appointments per month",
             "control": "number", "min": 0, "max": 100000},
            {"key": "main_problem", "label": "Biggest scheduling problem",
             "control": SINGLE, "allow_other": True,
             "options": _opts(
                 ("no_shows", "No-shows"), ("slow_to_book", "Slow to get booked"),
                 ("after_hours", "Missed after-hours enquiries"),
                 ("double_booking", "Double booking"),
                 ("manual_scheduling", "All scheduling is manual"),
                 (OTHER, "Other"))},
        ],
    },
    {
        "key": "automation_opportunities",
        "label": "Where automation would land",
        "group": "Solution",
        "control": MULTI,
        "required": True,
        "allow_other": True,
        "options": _opts(
            ("sms_follow_up", "SMS follow-up"),
            ("email_follow_up", "Email follow-up"),
            ("ai_voice", "AI voice"),
            ("missed_call", "Missed-call follow-up"),
            ("lead_qualification", "Lead qualification"),
            ("scheduling", "Appointment scheduling"),
            ("reactivation", "Reactivation"),
            ("pipeline", "Pipeline automation"),
            ("reporting", "Reporting"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "required_integrations",
        "label": "Must connect to",
        "group": "Solution",
        "control": MULTI,
        "required": False,
        "allow_other": True,
        "options": _opts(
            ("crm", "Their CRM"),
            ("google_calendar", "Google Calendar"),
            ("outlook_calendar", "Outlook Calendar"),
            ("google_email", "Google email"),
            ("microsoft_email", "Microsoft email"),
            ("phone_system", "Phone system"),
            ("payments", "Payments / Stripe"),
            ("website_forms", "Website forms"),
            ("zapier", "Zapier"),
            ("none", "Nothing yet"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "demo_requirements",
        "label": "Show them in the demo",
        "group": "Solution",
        "control": MULTI,
        "required": True,
        "allow_other": True,
        "note_label": "Special demo notes",
        "options": _opts(
            ("website", "Website / landing page"),
            ("lead_workflow", "Lead workflow"),
            ("sms_automation", "SMS automation"),
            ("email_automation", "Email automation"),
            ("ai_conversation", "AI conversation"),
            ("calendar_booking", "Calendar booking"),
            ("pipeline", "Pipeline"),
            ("reporting", "Reporting"),
            ("billing", "Billing"),
            (OTHER, "Other"),
        ),
    },
    {
        "key": "opportunity_notes",
        "label": "Additional notes",
        "group": "Notes",
        "control": NOTE,
        "required": False,
        "note_label": "Anything that does not fit above",
    },
]

BY_KEY: Dict[str, Dict[str, Any]] = {f["key"]: f for f in SCHEMA}
REQUIRED_KEYS = tuple(f["key"] for f in SCHEMA if f.get("required"))


# ── rendering: structured answer → the sentence stored in the Text column ───

def _label_for(options: Optional[List[Dict[str, str]]], value: str) -> str:
    for o in (options or []):
        if o["value"] == value:
            return o["label"]
    return str(value)


def _clean(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _render_choice(spec: Dict[str, Any], val: Dict[str, Any]) -> str:
    """The chosen labels, with "Other" replaced by what was typed."""
    chosen = val.get("options")
    if chosen is None:
        one = _clean(val.get("value"))
        chosen = [one] if one else []
    parts: List[str] = []
    other_text = _clean(val.get("other"))
    for c in chosen:
        if c == OTHER:
            parts.append(other_text or "Other")
        else:
            parts.append(_label_for(spec.get("options"), c))
    if other_text and OTHER not in chosen:
        parts.append(other_text)
    return ", ".join(p for p in parts if p)


def render(key: str, val: Optional[Dict[str, Any]]) -> str:
    """The readable text for one structured answer.

    This is what lands in the field's existing Text column, so anything that
    already reads discovery as prose keeps reading prose.
    """
    spec = BY_KEY.get(key)
    if not spec or not isinstance(val, dict):
        return ""
    control = spec["control"]
    lines: List[str] = []

    if control in (MULTI, SINGLE):
        head = _render_choice(spec, val)
        if head:
            lines.append(head)
    elif control == COMPOSITE:
        parts = val.get("parts") or {}
        for p in spec.get("parts", []):
            raw = parts.get(p["key"])
            if isinstance(raw, dict):
                text = _render_choice(p, raw)
            else:
                text = _clean(raw)
            if text:
                lines.append("%s: %s" % (p["label"], text))

    note = _clean(val.get("note"))
    if note:
        lines.append(note)
    return "\n".join(lines).strip()


def is_answered(val: Optional[Dict[str, Any]]) -> bool:
    """True when a structured answer actually carries something."""
    if not isinstance(val, dict):
        return False
    if val.get("options"):
        return True
    if _clean(val.get("value")):
        return True
    if _clean(val.get("other")):
        return True
    if _clean(val.get("note")):
        return True
    for raw in (val.get("parts") or {}).values():
        if isinstance(raw, dict):
            if raw.get("options") or _clean(raw.get("value")) or _clean(raw.get("other")):
                return True
        elif _clean(raw):
            return True
    return False


# ── the JSON side-car ───────────────────────────────────────────────────────

def load(raw: Optional[str]) -> Dict[str, Any]:
    """`structured_json` as a dict, whatever state it is in.

    A record written before this column existed, or one holding something
    unparseable, reads as empty rather than raising — discovery must never
    fail to LOAD because of how it was stored.
    """
    if not raw:
        return {"fields": {}, "legacy": {}}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {"fields": {}, "legacy": {}}
    if not isinstance(data, dict):
        return {"fields": {}, "legacy": {}}
    fields = data.get("fields")
    legacy = data.get("legacy")
    return {
        "fields": fields if isinstance(fields, dict) else {},
        "legacy": legacy if isinstance(legacy, dict) else {},
    }


def dump(state: Dict[str, Any]) -> str:
    return json.dumps({"fields": state.get("fields") or {},
                       "legacy": state.get("legacy") or {}},
                      separators=(",", ":"), sort_keys=True)


def sanitize(incoming: Any) -> Dict[str, Dict[str, Any]]:
    """Keep only known fields, known parts and known option values.

    A browser is not trusted to decide what a discovery answer may contain.
    Free text ("other", "note", number and contact parts) is length-capped and
    stored as given; option values that are not in the schema are dropped.
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(incoming, dict):
        return out
    for key, val in incoming.items():
        spec = BY_KEY.get(key)
        if not spec or not isinstance(val, dict):
            continue
        clean: Dict[str, Any] = {}
        control = spec["control"]
        if control in (MULTI, SINGLE):
            allowed = {o["value"] for o in spec.get("options", [])}
            picked = val.get("options")
            if isinstance(picked, list):
                keep = [str(p) for p in picked if str(p) in allowed]
                if control == SINGLE:
                    keep = keep[:1]
                clean["options"] = keep
            other = _clean(val.get("other"))[:500]
            if other:
                clean["other"] = other
        elif control == COMPOSITE:
            parts_out: Dict[str, Any] = {}
            given = val.get("parts")
            if isinstance(given, dict):
                for p in spec.get("parts", []):
                    raw = given.get(p["key"])
                    if raw is None:
                        continue
                    if p["control"] == SINGLE:
                        if not isinstance(raw, dict):
                            continue
                        allowed = {o["value"] for o in p.get("options", [])}
                        picked = [str(x) for x in (raw.get("options") or [])
                                  if str(x) in allowed][:1]
                        sub: Dict[str, Any] = {"options": picked}
                        other = _clean(raw.get("other"))[:300]
                        if other:
                            sub["other"] = other
                        if picked or other:
                            parts_out[p["key"]] = sub
                    else:
                        text = _clean(raw if not isinstance(raw, dict)
                                      else raw.get("value"))[:300]
                        if text:
                            parts_out[p["key"]] = text
            if parts_out:
                clean["parts"] = parts_out
        note = _clean(val.get("note"))[:2000]
        if note:
            clean["note"] = note
        out[key] = clean
    return out


def apply(state: Dict[str, Any], incoming: Dict[str, Dict[str, Any]],
          current_text: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """Merge sanitized structured answers into the side-car.

    LEGACY IS SNAPSHOTTED, NOT OVERWRITTEN. The first time a field is answered
    structurally, whatever prose is already in its Text column is copied into
    `legacy` — so the long-form note a rep typed months ago survives the field
    being re-answered with tick boxes, and the panel can show it back under
    "Previous / detailed notes".
    """
    fields = dict(state.get("fields") or {})
    legacy = dict(state.get("legacy") or {})
    for key, val in incoming.items():
        # AN EMPTY ANSWER TO A QUESTION NOBODY HAS ANSWERED IS NOT A CLEAR.
        # A form that posts all fourteen keys every time - which any client is
        # free to do - would otherwise null out the long-form text on the
        # thirteen the seller did not touch. Once a field HAS a structured
        # answer, emptying it is a real instruction and is honoured.
        if not is_answered(val) and key not in fields:
            continue
        if key not in legacy:
            existing = _clean(current_text.get(key))
            previously_rendered = render(key, fields.get(key)) if key in fields else ""
            if existing and existing != previously_rendered:
                legacy[key] = existing
        fields[key] = val
    return {"fields": fields, "legacy": legacy}


# ── what the screen is told ─────────────────────────────────────────────────

def schema_payload() -> List[Dict[str, Any]]:
    """The schema, as the Opportunity screen consumes it.

    Sent from the server so the browser never hardcodes a question list — the
    same reason `/sales/me` sends the stage list rather than the client knowing
    the lifecycle.
    """
    return [dict(f) for f in SCHEMA]


def progress(fields: Dict[str, Any], text_values: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """"Discovery 7/9" — and WHICH two are missing.

    A field counts as answered if it has a structured answer OR already holds
    long-form text, so a deal captured before this existed does not suddenly
    report as unstarted.
    """
    answered, missing = [], []
    for key in REQUIRED_KEYS:
        if is_answered((fields or {}).get(key)) or _clean(text_values.get(key)):
            answered.append(key)
        else:
            missing.append({"key": key, "label": BY_KEY[key]["label"]})
    return {"answered": len(answered), "required": len(REQUIRED_KEYS),
            "missing": missing,
            "complete": len(missing) == 0}
