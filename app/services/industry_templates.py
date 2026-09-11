"""WHAT KIND OF BUSINESS IS THIS, AND WHAT SHOULD IT START WITH.

THE DEFECT THIS FILE EXISTS TO CLOSE
------------------------------------
A brand-new customer in an unrelated industry opened its settings and found
funeral-home vocabulary: Pre-Need, At-Need, Imminent, Contract Sold, "At-Need
Arrangement Conference". Nobody chose those. They arrived because three
separate places each kept their own industry map and each fell back to the same
first customer's industry when they did not recognise the one they were given:

  org_settings_router.DEFAULT_TIERS         →  lead tiers shown in settings
  settings_router.INDUSTRY_APPT_TYPES       →  appointment types
  tier_config_service.INDUSTRY_TIER_SETS    →  TierDefinition rows + AI tracks

Three maps is why fixing one never fixed it, and a funeral fallback is why an
unknown industry produced funeral defaults instead of nothing in particular.

THIS IS NOT A FOURTH ENGINE
---------------------------
It is the one registry the other three now read. The tier-definition sets stay
where they are — `tier_config_service` owns the TierDefinition rows and this
module references its sets by key rather than copying them, because two copies
of a taxonomy is the bug being fixed, not the fix.

THE FALLBACK IS GENERIC, AND THAT IS THE POINT
----------------------------------------------
An unknown, missing or unrecognised industry resolves to GENERIC: a clean
service-business starting point with no vertical's vocabulary in it. Never to
whichever template happens to be first, and never to funeral. A customer who
tells us nothing gets neutral defaults they can shape; they do not get somebody
else's business.

ADDING AN INDUSTRY
------------------
Add a template below, and — if it needs its own AI tracks — a tier set in
`tier_config_service`. Nothing else has to change: the settings page, the
appointment-type endpoint, provisioning and the migration path all read this
registry.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# ── shared vocabulary ───────────────────────────────────────────────────────
#
# Appointment names every business can use, whatever it sells. A template's
# own list is these plus its own; nothing here is vertical-specific.
UNIVERSAL_APPOINTMENT_TYPES = [
    "General Consultation",
    "New Web Lead",
    "Walk-In",
    "Phone Call",
    "Video Call",
    "Referral Appointment",
    "Follow-Up Appointment",
]

# Every template carries these two tiers, because they describe the STATE OF A
# RECORD rather than the state of a deal, and every industry has both.
_EMAIL_ONLY = {"value": "email_only", "label": "Email Only", "color": "purple",
               "description": "No phone"}
_NEEDS_REVIEW = {"value": "partial", "label": "Needs Review", "color": "amber",
                 "description": "Incomplete info"}

GENERIC_KEY = "generic"

# ════════════════════════════════════════════════════════════════════════════
# TEMPLATES
# ════════════════════════════════════════════════════════════════════════════
#
# key                 canonical industry key, stored on organizations.industry
# label               what a human picks in the UI
# aliases             other spellings that resolve here
# segments            lines of business a customer may operate, where the
#                     distinction changes the configuration
# lead_tiers          the pipeline vocabulary shown in settings (tier_config)
# tier_definition_key which tier_config_service set seeds TierDefinition rows
# appointment_types   the booking vocabulary
# crm_stages          the deal stages this business actually works
# custom_fields       fields worth collecting that the core schema has no
#                     column for
# vocabulary          the words the AI should use about this business
# onboarding_questions  what to ask at customer creation to configure it

TEMPLATES: Dict[str, Dict[str, Any]] = {

    GENERIC_KEY: {
        "key": GENERIC_KEY,
        "label": "General service business",
        "aliases": ["general", "custom", "other", "unknown", "service",
                    "services", "default"],
        "segments": [],
        "lead_tiers": [
            {"value": "new_lead", "label": "New Lead", "color": "blue",
             "description": "Not yet contacted"},
            {"value": "contacted", "label": "Contacted", "color": "amber",
             "description": "Conversation started"},
            {"value": "quoted", "label": "Quoted", "color": "amber",
             "description": "Pricing presented"},
            {"value": "won", "label": "Won", "color": "green",
             "description": "Closed"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "direct_sales",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "Discovery Call", "Consultation", "Follow-Up Call",
        ],
        "crm_stages": ["New", "Contacted", "Qualified", "Quoted", "Won", "Lost"],
        "custom_fields": [],
        "vocabulary": {"lead": "lead", "leads": "leads",
                       "appointment": "appointment",
                       "customer": "customer", "customers": "customers"},
        "onboarding_questions": [],
    },

    "funeral": {
        "key": "funeral",
        "label": "Funeral home / cemetery",
        "aliases": ["cemetery", "funeral_cemetery", "funeral_home",
                    "deathcare", "memorial"],
        "segments": ["At-need", "Pre-need", "Cemetery property"],
        "lead_tiers": [
            {"value": "pre_need", "label": "Pre-Need", "color": "blue",
             "description": "Planning ahead"},
            {"value": "at_need", "label": "At-Need", "color": "red",
             "description": "Immediate need"},
            {"value": "imminent", "label": "Imminent", "color": "red",
             "description": "Within 90 days"},
            {"value": "contract_sold", "label": "Contract Sold", "color": "green",
             "description": "Closed"},
            _EMAIL_ONLY,
            _NEEDS_REVIEW,
        ],
        "tier_definition_key": "funeral",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "Pre-Need Planning Consultation",
            "Pre-Planning Consultation",
            "At-Need Arrangement Conference",
            "Immediate Need Consultation",
            "Urgent Arrangement Consultation",
            "Family File Review",
            "Property Ownership Review",
            "Property Transfer Appointment",
            "Cemetery Property Consultation",
            "Marker & Memorial Consultation",
            "Memorial Planning Consultation",
            "Memorial Flower Review",
            "Contract Review Appointment",
            "Family Services Appointment",
            "Family Services Consultation",
            "New Family Consultation",
            "Insurance & Benefits Review",
            "Veterans Benefits Consultation",
        ],
        "crm_stages": ["Inquiry", "Consultation", "Arrangement", "Contract",
                       "Served"],
        "custom_fields": [
            {"key": "property_type", "label": "Property type", "kind": "text"},
            {"key": "existing_contract", "label": "Existing contract",
             "kind": "text"},
        ],
        "vocabulary": {"lead": "family", "leads": "families",
                       "appointment": "consultation",
                       "customer": "family", "customers": "families"},
        "onboarding_questions": [],
    },

    "energy": {
        "key": "energy",
        "label": "Energy / energy procurement",
        "aliases": ["energy_procurement", "utilities", "utility", "power",
                    "electricity", "gas", "energy_broker", "energy_supply",
                    "light_and_power"],
        "segments": ["Residential", "Commercial / B2B"],
        "lead_tiers": [
            {"value": "new_inquiry", "label": "New Inquiry", "color": "blue",
             "description": "Not yet reviewed"},
            {"value": "rate_review", "label": "Rate Review", "color": "amber",
             "description": "Reviewing current usage and rate"},
            {"value": "proposal_sent", "label": "Proposal Sent", "color": "amber",
             "description": "Supplier options presented"},
            {"value": "contract_signed", "label": "Contract Signed",
             "color": "green", "description": "Enrolled with a supplier"},
            {"value": "renewal_due", "label": "Renewal Due", "color": "orange",
             "description": "Contract ending, renewal in play"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "energy",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "Energy Rate Review",
            "Residential Energy Consultation",
            "Commercial Energy Consultation",
            "Contract / Renewal Review",
            "Procurement Review",
            "Supplier Comparison Review",
            "Usage & Billing Review",
            "Follow-Up Consultation",
        ],
        "crm_stages": ["New Inquiry", "Usage Review", "Rate Comparison",
                       "Proposal Sent", "Contract Signed", "Renewal Watch"],
        "custom_fields": [
            {"key": "segment", "label": "Residential or commercial",
             "kind": "select",
             "options": ["Residential", "Commercial"]},
            {"key": "current_supplier", "label": "Current supplier",
             "kind": "text"},
            {"key": "contract_end_date", "label": "Contract end date",
             "kind": "date"},
            {"key": "annual_usage_kwh", "label": "Annual usage (kWh)",
             "kind": "number"},
            {"key": "rate_type", "label": "Rate type", "kind": "select",
             "options": ["Fixed", "Variable", "Indexed", "Unknown"]},
            {"key": "service_address", "label": "Service address", "kind": "text"},
        ],
        "vocabulary": {"lead": "prospect", "leads": "prospects",
                       "appointment": "consultation",
                       "customer": "account", "customers": "accounts"},
        "onboarding_questions": [
            {"key": "segments_served",
             "label": "Which lines of business does this organization operate?",
             "kind": "multiselect",
             "options": ["Residential", "Commercial / B2B"]},
            {"key": "procurement_model",
             "label": "How does this business earn on a contract?",
             "kind": "select",
             "options": ["Supplier residual", "Flat fee", "Both", "Other"]},
        ],
    },

    "roofing": {
        "key": "roofing",
        "label": "Roofing / exteriors",
        "aliases": ["roof", "exteriors", "storm_restoration"],
        "segments": ["Residential", "Commercial"],
        "lead_tiers": [
            {"value": "estimate_requested", "label": "Estimate Requested",
             "color": "blue", "description": "New lead"},
            {"value": "estimate_given", "label": "Estimate Given",
             "color": "amber", "description": "Quote sent"},
            {"value": "follow_up", "label": "Follow Up", "color": "amber",
             "description": "Waiting on decision"},
            {"value": "contract_signed", "label": "Contract Signed",
             "color": "green", "description": "Closed"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "roofing",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "Estimate Appointment", "Roof Inspection",
            "Storm Damage Assessment", "Contract Signing",
            "Material Selection Meeting", "Project Walkthrough",
            "Insurance Claim Review", "Post-Job Inspection",
        ],
        "crm_stages": ["Lead", "Inspection", "Estimate", "Contract",
                       "Scheduled", "Complete"],
        "custom_fields": [
            {"key": "roof_age", "label": "Roof age (years)", "kind": "number"},
            {"key": "insurance_claim", "label": "Insurance claim", "kind": "text"},
        ],
        "vocabulary": {"lead": "lead", "leads": "leads",
                       "appointment": "inspection",
                       "customer": "homeowner", "customers": "homeowners"},
        "onboarding_questions": [],
    },

    "real_estate": {
        "key": "real_estate",
        "label": "Real estate",
        "aliases": ["realestate", "realtor", "brokerage", "property"],
        "segments": ["Buyer side", "Seller side", "Investment"],
        "lead_tiers": [
            {"value": "buyer_lead", "label": "Buyer Lead", "color": "blue",
             "description": "Looking to buy"},
            {"value": "seller_lead", "label": "Seller Lead", "color": "amber",
             "description": "Looking to sell"},
            {"value": "showing_scheduled", "label": "Showing Scheduled",
             "color": "amber", "description": "Active"},
            {"value": "under_contract", "label": "Under Contract",
             "color": "green", "description": "Pending close"},
            {"value": "closed", "label": "Closed", "color": "green",
             "description": "Deal done"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "real_estate",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "Buyer Consultation", "Seller Consultation", "Home Showing",
            "Offer Review", "Contract Signing", "Closing Walkthrough",
            "Market Analysis Review", "Investment Property Consultation",
        ],
        "crm_stages": ["Lead", "Consultation", "Active", "Under Contract",
                       "Closed"],
        "custom_fields": [
            {"key": "price_range", "label": "Price range", "kind": "text"},
            {"key": "target_area", "label": "Target area", "kind": "text"},
        ],
        "vocabulary": {"lead": "client", "leads": "clients",
                       "appointment": "showing",
                       "customer": "client", "customers": "clients"},
        "onboarding_questions": [],
    },

    "insurance": {
        "key": "insurance",
        "label": "Insurance / benefits",
        "aliases": ["life_insurance", "health_insurance", "medicare",
                    "annuities", "benefits", "final_expense"],
        "segments": ["Individual", "Group / employer"],
        "lead_tiers": [
            {"value": "prospect", "label": "Prospect", "color": "blue",
             "description": "Initial contact"},
            {"value": "quoted", "label": "Quoted", "color": "amber",
             "description": "Quote sent"},
            {"value": "application", "label": "Application", "color": "amber",
             "description": "App in progress"},
            {"value": "policy_sold", "label": "Policy Sold", "color": "green",
             "description": "Closed"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "insurance",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "New Policy Consultation", "Benefits & Coverage Consultation",
            "Policy Review", "Annual Review", "Insurance & Benefits Review",
            "Life Insurance Consultation", "Medicare Review",
            "Veterans Benefits Consultation", "Claims Assistance",
            "Policy Renewal",
        ],
        "crm_stages": ["Prospect", "Quoted", "Application", "Issued",
                       "Renewal"],
        "custom_fields": [
            {"key": "coverage_type", "label": "Coverage type", "kind": "text"},
            {"key": "renewal_month", "label": "Renewal month", "kind": "text"},
        ],
        "vocabulary": {"lead": "prospect", "leads": "prospects",
                       "appointment": "consultation",
                       "customer": "policyholder", "customers": "policyholders"},
        "onboarding_questions": [],
    },

    "fiber": {
        "key": "fiber",
        "label": "Fiber / telecom",
        "aliases": ["fiber_internet", "telecom", "broadband", "door_to_door"],
        "segments": ["Residential", "Business"],
        "lead_tiers": [
            {"value": "prospect", "label": "Prospect", "color": "blue",
             "description": "New inquiry, not yet contacted"},
            {"value": "quoted", "label": "Quoted", "color": "amber",
             "description": "Service options presented"},
            {"value": "scheduled_install", "label": "Scheduled Install",
             "color": "orange", "description": "Install date set"},
            {"value": "active_customer", "label": "Active Customer",
             "color": "green", "description": "Service live"},
            {"value": "churned", "label": "Churned", "color": "red",
             "description": "Cancelled or lost"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "fiber",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "New Service Consultation", "Installation Appointment",
            "Service Upgrade Consultation", "Billing Review",
            "Tech Support Visit", "Door-to-Door Canvass",
            "Business Account Consultation", "Contract Renewal",
            "Equipment Swap", "Cancellation Retention Call",
        ],
        "crm_stages": ["Prospect", "Quoted", "Scheduled", "Installed",
                       "Active"],
        "custom_fields": [
            {"key": "service_address", "label": "Service address", "kind": "text"},
            {"key": "speed_tier", "label": "Speed tier", "kind": "text"},
        ],
        "vocabulary": {"lead": "prospect", "leads": "prospects",
                       "appointment": "appointment",
                       "customer": "subscriber", "customers": "subscribers"},
        "onboarding_questions": [],
    },

    "home_services": {
        "key": "home_services",
        "label": "Home services (HVAC, plumbing, electrical, and similar)",
        "aliases": ["hvac", "plumbing", "electrical", "pest_control",
                    "landscaping", "windows_doors", "painting", "flooring",
                    "cleaning", "pool_spa", "tree_service", "water_treatment"],
        "segments": ["Residential", "Commercial"],
        "lead_tiers": [
            {"value": "new_lead", "label": "New Lead", "color": "blue",
             "description": "Not yet contacted"},
            {"value": "scheduled", "label": "Scheduled", "color": "amber",
             "description": "Visit booked"},
            {"value": "quoted", "label": "Quoted", "color": "amber",
             "description": "Estimate given"},
            {"value": "job_booked", "label": "Job Booked", "color": "green",
             "description": "Work agreed"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "home_services",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "Service Call", "Estimate Appointment", "Maintenance Visit",
            "Emergency Call", "Follow-Up Visit",
        ],
        "crm_stages": ["Lead", "Scheduled", "Quoted", "Booked", "Complete"],
        "custom_fields": [
            {"key": "equipment_age", "label": "Equipment age", "kind": "text"},
        ],
        "vocabulary": {"lead": "lead", "leads": "leads",
                       "appointment": "service call",
                       "customer": "customer", "customers": "customers"},
        "onboarding_questions": [],
    },

    "dental": {
        "key": "dental",
        "label": "Dental / medical practice",
        "aliases": ["dentist", "orthodontics", "medical", "practice"],
        "segments": [],
        "lead_tiers": [
            {"value": "new_patient", "label": "New Patient", "color": "blue",
             "description": "First contact"},
            {"value": "consultation", "label": "Consultation", "color": "amber",
             "description": "Consult booked"},
            {"value": "treatment_plan", "label": "Treatment Plan",
             "color": "amber", "description": "Plan presented"},
            {"value": "active_patient", "label": "Active Patient",
             "color": "green", "description": "Ongoing care"},
            _EMAIL_ONLY,
        ],
        "tier_definition_key": "home_services",
        "appointment_types": UNIVERSAL_APPOINTMENT_TYPES + [
            "New Patient Exam", "Routine Cleaning", "Consultation",
            "Treatment Plan Review", "Cosmetic Consultation",
            "Orthodontic Consultation", "Emergency Visit",
        ],
        "crm_stages": ["Inquiry", "Consultation", "Treatment Plan", "Active",
                       "Recall"],
        "custom_fields": [],
        "vocabulary": {"lead": "patient", "leads": "patients",
                       "appointment": "appointment",
                       "customer": "patient", "customers": "patients"},
        "onboarding_questions": [],
    },
}

# alias -> canonical key, built once.
_ALIASES: Dict[str, str] = {}
for _key, _tpl in TEMPLATES.items():
    _ALIASES[_key] = _key
    for _alias in _tpl.get("aliases", []):
        _ALIASES[_alias] = _key


# ════════════════════════════════════════════════════════════════════════════
# RESOLUTION
# ════════════════════════════════════════════════════════════════════════════


def _slug(value: Optional[str]) -> str:
    """Lowercase, alphanumerics and single underscores. Nothing else.

    Written this way because the strings that arrive here are what a human
    typed or picked: "Energy / Energy Procurement", "Real-Estate",
    "funeral home". Replacing only spaces and hyphens left the slash in and
    quietly sent a real industry to the generic fallback.
    """
    out, last_us = [], False
    for ch in (value or "").strip().lower():
        if ch.isalnum():
            out.append(ch)
            last_us = False
        elif not last_us:
            out.append("_")
            last_us = True
    return "".join(out).strip("_")


def _candidates(key: str):
    """The key itself, then the meaningful pieces of it, longest first.

    "energy_energy_procurement" should find `energy`; "funeral_home_and_
    cemetery" should find `funeral`. Tried in descending length so a longer,
    more specific alias always wins over a shorter one it contains.
    """
    yield key
    parts = [p for p in key.split("_") if p]
    seen = set()
    for size in range(len(parts), 0, -1):
        for start in range(0, len(parts) - size + 1):
            candidate = "_".join(parts[start:start + size])
            if candidate in seen:
                continue
            seen.add(candidate)
            yield candidate


def normalize(industry: Optional[str]) -> str:
    """Canonical key for whatever somebody typed, or GENERIC.

    Case, punctuation, spacing and the brand's own phrasing all resolve. An
    industry this platform has never heard of resolves to GENERIC — never to
    the first template in the file, and never to funeral.
    """
    key = _slug(industry)
    if not key:
        return GENERIC_KEY
    for candidate in _candidates(key):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    return GENERIC_KEY


def resolve(industry: Optional[str]) -> Dict[str, Any]:
    """The template for an industry. Always returns one."""
    return TEMPLATES[normalize(industry)]


def is_known(industry: Optional[str]) -> bool:
    """Did this industry actually match a template, or did it fall through?

    Used where the difference matters — a settings page should be able to say
    "we are showing you generic defaults because nobody has told us what this
    business does", rather than presenting the generic set as a decision.
    """
    key = _slug(industry)
    if not key:
        return False
    return any(candidate in _ALIASES for candidate in _candidates(key))


def choices() -> List[Dict[str, Any]]:
    """Every industry a human may pick, for the settings dropdown."""
    out = [{"key": t["key"], "label": t["label"],
            "segments": list(t.get("segments") or [])}
           for t in TEMPLATES.values() if t["key"] != GENERIC_KEY]
    out.sort(key=lambda t: t["label"])
    out.append({"key": GENERIC_KEY, "label": TEMPLATES[GENERIC_KEY]["label"],
                "segments": []})
    return out


# ── the three things the rest of the platform asks for ──────────────────────


def lead_tiers(industry: Optional[str]) -> List[Dict[str, Any]]:
    return [dict(t) for t in resolve(industry)["lead_tiers"]]


def appointment_types(industry: Optional[str]) -> List[str]:
    return list(resolve(industry)["appointment_types"])


def crm_stages(industry: Optional[str]) -> List[str]:
    return list(resolve(industry)["crm_stages"])


def custom_fields(industry: Optional[str]) -> List[Dict[str, Any]]:
    return [dict(f) for f in resolve(industry)["custom_fields"]]


def vocabulary(industry: Optional[str]) -> Dict[str, str]:
    return dict(resolve(industry)["vocabulary"])


def onboarding_questions(industry: Optional[str]) -> List[Dict[str, Any]]:
    return [dict(q) for q in resolve(industry)["onboarding_questions"]]


def tier_definition_key(industry: Optional[str]) -> str:
    """Which `tier_config_service` set seeds this industry's TierDefinition rows.

    Kept as a reference rather than a copy: that module owns the rows, their
    AI tracks and their tone context, and a second copy of a taxonomy is the
    defect this registry was written to remove.
    """
    return resolve(industry)["tier_definition_key"]


def summary(industry: Optional[str]) -> Dict[str, Any]:
    """Everything a screen needs to describe this business's configuration."""
    tpl = resolve(industry)
    return {
        "key": tpl["key"],
        "label": tpl["label"],
        "matched": is_known(industry),
        "requested": industry,
        "segments": list(tpl.get("segments") or []),
        "lead_tiers": lead_tiers(industry),
        "appointment_types": appointment_types(industry),
        "crm_stages": crm_stages(industry),
        "custom_fields": custom_fields(industry),
        "vocabulary": vocabulary(industry),
        "onboarding_questions": onboarding_questions(industry),
    }


# ── compatibility for the maps this registry replaced ───────────────────────
#
# `org_settings_router` and `settings_router` imported their own dicts by these
# names. They now import them from here, so the names survive and the data has
# exactly one home. Callers that look up an unknown key still get GENERIC via
# the accessors above rather than a KeyError or a funeral default.

DEFAULT_TIERS: Dict[str, List[Dict[str, Any]]] = {
    key: [dict(t) for t in tpl["lead_tiers"]] for key, tpl in TEMPLATES.items()
}
# The historic key for the neutral set was "custom"; keep it pointing at the
# generic template so an existing org whose settings say "custom" is unchanged.
DEFAULT_TIERS["custom"] = [dict(t) for t in TEMPLATES[GENERIC_KEY]["lead_tiers"]]

INDUSTRY_APPT_TYPES: Dict[str, List[str]] = {
    key: list(tpl["appointment_types"]) for key, tpl in TEMPLATES.items()
}
INDUSTRY_APPT_TYPES["custom"] = list(TEMPLATES[GENERIC_KEY]["appointment_types"])
