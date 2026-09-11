"""THE CUSTOMER LAUNCH EXPERIENCE: resolved presentation, composed truthfully.

TWO JOBS, KEPT APART ON PURPOSE
-------------------------------
  RESOLVE   merge the four configuration layers into one presentation, one
            journey and one set of extra form sections. Pure configuration;
            touches no customer record and decides nothing that is true.

  COMPOSE   read the AUTHORITATIVE records — the implementation, its intake,
            its milestones, integrations, checks, training, the commercial
            agreement — and produce the customer's view of where they actually
            are.

The separation is the whole safety property. Configuration can never move a
progress bar, tick a step or claim a document was accepted, because the
composer reads state from the records and the config only supplies words and
pictures around it.

PROGRESS IS NOT PAGE VISITS
---------------------------
Every percentage here comes from `launch_intake.overview`, which counts
required fields actually answered, and from the launch programme's own rows. A
customer who clicks through all seven stages without typing anything is at 0%,
and the number they see says so.

NOTHING IN THIS MODULE WRITES
-----------------------------
It is read-only end to end, which is what makes the internal preview safe:
previewing a customer's onboarding runs exactly this code, so there is no
second path that could have a side effect the customer's own path does not.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.launch_experience_models import (
    LaunchExperienceConfig, SCOPE_BRAND, SCOPE_INDUSTRY, SCOPE_LABELS,
    SCOPE_ORGANIZATION, SCOPE_PLATFORM_DEFAULT, SCOPE_PRECEDENCE,
)
from app.models.models import Organization, Platform
from app.services import industry_templates

# ════════════════════════════════════════════════════════════════════════════
# THE PLATFORM DEFAULT
# ════════════════════════════════════════════════════════════════════════════
#
# A fresh install with no rows renders the complete premium experience from
# this. Every token here is a placeholder in the sense that a brand may
# override it — and none of them names a brand, a customer or a vertical,
# because the platform default is what an unconfigured customer sees.
#
# `{brand}` and `{customer}` are substituted at compose time. They are the only
# two tokens, deliberately: a template language in a settings row is a feature
# nobody asked for and a rendering bug nobody can find.

DEFAULT_PRESENTATION: Dict[str, Any] = {
    "eyebrow": "Welcome to the {brand} Ecosystem",
    "title": "{customer}",
    "subtitle": "Client Onboarding & Integration",
    "intro": ("This guided onboarding will help us get everything we need to "
              "build, integrate, and launch your complete workspace."),
    # Imagery. None by default: a shell that invents a hero picture for a
    # customer who has not supplied one is a shell that shows somebody else's
    # photograph on their launch page.
    "hero_image_url": None,
    "hero_logo_url": None,          # the customer's own logo artwork
    "hero_overlay": "deep",         # deep | soft | none
    "customer_tagline": None,       # the quote beside the hero logo
    "rail_image_url": None,
    "rail_tagline": None,           # e.g. three short lines, one per entry
    "help": {
        "title": "Need Help?",
        "body": "Our team is here if you have any questions during the "
                "onboarding process.",
        "cta_label": "Contact {brand}",
    },
    "guide": {
        "title": "Download Guide",
        "body": "Need a copy of the required information and files? Download "
                "our onboarding checklist.",
        "cta_label": "Download PDF",
        # No document by default, and the card says so rather than offering a
        # download that 404s.
        "url": None,
    },
    # What the seven-stage tracker is called on screen. A brand that runs
    # "implementations" and one that runs "onboarding" should not have to fork
    # a component to say so.
    "journey_title": "Your Onboarding Journey",
    "quote": None,                  # {"text": ..., "attribution": ...}
    "footer": {
        "links": [
            {"label": "Privacy Policy", "href": "/privacy-policy"},
            {"label": "Terms of Service", "href": "/terms"},
            {"label": "Support", "href": None},
        ],
        "copyright": "© {year} {brand}. All rights reserved.",
    },
}

# The seven stages of the approved journey. Labels are configuration; the STATE
# of each stage is not — it is derived from the implementation's own status.
DEFAULT_JOURNEY: List[Dict[str, Any]] = [
    {"key": "intake",       "label": "Complete", "sublabel": "Intake"},
    {"key": "access",       "label": "Provide", "sublabel": "Access & Files"},
    {"key": "build",        "label": "{brand}", "sublabel": "Builds"},
    {"key": "integrations", "label": "Integrations", "sublabel": ""},
    {"key": "review",       "label": "Review & Test", "sublabel": ""},
    {"key": "training",     "label": "Training", "sublabel": ""},
    {"key": "golive",       "label": "Go Live", "sublabel": ""},
]

DEFAULT_FORM: Dict[str, Any] = {
    # Extra sections appended to the platform intake, by step key. An industry
    # or a customer adds questions here; nothing is removed, because a platform
    # step that stops being asked is a platform decision rather than a per-brand
    # one.
    "sections": [],
    # Which commercial questions the customer is asked inside onboarding, and
    # at which step. The commercial engine decides the questions themselves.
    "commercial_step": "review",
}


def _merge(base: Any, over: Any) -> Any:
    """Deep-merge dictionaries; replace everything else.

    A LIST IS REPLACED, NOT CONCATENATED. A brand that supplies a five-stage
    journey means five stages, not its five appended to the platform's seven —
    and a footer with two links means two. Merging lists by position is how a
    configuration system starts producing arrangements nobody wrote.
    """
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = _merge(out.get(key), value) if key in out else value
        return out
    return copy.deepcopy(base) if over is None else copy.deepcopy(over)


def _rows(db: Session, *, industry_key: Optional[str],
          platform_id: Optional[str],
          organization_id: Optional[str]) -> Dict[str, LaunchExperienceConfig]:
    wanted = {
        SCOPE_PLATFORM_DEFAULT: None,
        SCOPE_INDUSTRY: industry_key,
        SCOPE_BRAND: platform_id,
        SCOPE_ORGANIZATION: organization_id,
    }
    found: Dict[str, LaunchExperienceConfig] = {}
    query = (db.query(LaunchExperienceConfig)
             .filter(LaunchExperienceConfig.is_active.is_(True)))
    for row in query.all():
        if row.scope_type not in wanted:
            continue
        if row.scope_type == SCOPE_PLATFORM_DEFAULT:
            found[row.scope_type] = row
        elif wanted[row.scope_type] and row.scope_id == wanted[row.scope_type]:
            found[row.scope_type] = row
    return found


def resolve(db: Session, *, industry: Optional[str] = None,
            platform_id: Optional[str] = None,
            organization_id: Optional[str] = None) -> Dict[str, Any]:
    """The merged configuration, plus which layers actually contributed.

    `layers` is returned because an operator looking at a customer's onboarding
    and wondering why it says what it says needs to know which row to edit.
    """
    industry_key = industry_templates.normalize(industry)
    rows = _rows(db, industry_key=industry_key, platform_id=platform_id,
                 organization_id=organization_id)

    presentation = copy.deepcopy(DEFAULT_PRESENTATION)
    journey = copy.deepcopy(DEFAULT_JOURNEY)
    form = copy.deepcopy(DEFAULT_FORM)
    layers: List[Dict[str, Any]] = [
        {"scope_type": SCOPE_PLATFORM_DEFAULT, "scope_id": None,
         "label": SCOPE_LABELS[SCOPE_PLATFORM_DEFAULT], "from_row": False},
    ]

    # THE INDUSTRY'S OWN STARTING EXPERIENCE, WITHOUT A ROW.
    #
    # An energy customer should get an energy journey and energy questions on
    # the day their workspace is created, not after somebody remembers to seed
    # a configuration row. So the industry template registry supplies this
    # layer in code, and a database row at industry scope then overrides it for
    # a brand that wants something different.
    industry_default = industry_templates.experience(industry_key)
    if industry_default:
        if industry_default.get("presentation"):
            presentation = _merge(presentation, industry_default["presentation"])
        if industry_default.get("journey"):
            journey = copy.deepcopy(industry_default["journey"])
        if industry_default.get("form"):
            form = _merge(form, industry_default["form"])
        layers.append({"scope_type": SCOPE_INDUSTRY, "scope_id": industry_key,
                       "label": SCOPE_LABELS[SCOPE_INDUSTRY], "from_row": False,
                       "name": industry_templates.resolve(industry_key)["label"]})

    for scope in SCOPE_PRECEDENCE:
        row = rows.get(scope)
        if row is None:
            continue
        if row.presentation:
            presentation = _merge(presentation, row.presentation)
        if row.journey:
            journey = copy.deepcopy(row.journey)
        if row.form:
            form = _merge(form, row.form)
        entry = {"scope_type": scope, "scope_id": row.scope_id,
                 "label": SCOPE_LABELS[scope], "from_row": True,
                 "name": row.name, "config_id": row.id}
        if scope == SCOPE_PLATFORM_DEFAULT:
            layers[0] = entry
        else:
            layers.append(entry)

    return {"presentation": presentation, "journey": journey, "form": form,
            "layers": layers, "industry": industry_key}


# ════════════════════════════════════════════════════════════════════════════
# COMPOSE
# ════════════════════════════════════════════════════════════════════════════


def _fill(value: Any, tokens: Dict[str, str]) -> Any:
    """Substitute {brand}, {customer} and {year}. Nothing else, anywhere.

    Walks dicts and lists so a token in a nested help card or footer line is
    filled without every caller knowing the shape. A string containing a token
    this does not know is left exactly as written rather than raising — a
    settings row must not be able to 500 a customer's onboarding page.
    """
    if isinstance(value, str):
        out = value
        for key, replacement in tokens.items():
            out = out.replace("{%s}" % key, replacement)
        return out
    if isinstance(value, dict):
        return {k: _fill(v, tokens) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, tokens) for v in value]
    return value


# The implementation statuses that mean each journey stage is behind us. Read
# from `launch_intake.lifecycle_for`, which already maps status to phase — this
# module does not invent a second lifecycle.
def _journey_state(db: Session, journey: List[Dict[str, Any]],
                   impl_status: Optional[str]) -> List[Dict[str, Any]]:
    from app.services import launch_intake

    phases = {p["key"]: p for p in launch_intake.lifecycle_for(impl_status)}
    out = []
    for index, stage in enumerate(journey):
        phase = phases.get(stage["key"])
        state = (phase or {}).get("state") or "next"
        out.append({
            "key": stage["key"],
            "n": index + 1,
            "label": stage.get("label") or stage["key"],
            "sublabel": stage.get("sublabel") or "",
            "state": state,
            "is_current": state == "now",
            "is_done": state == "done",
        })
    return out


def _commercial_block(db: Session, org: Organization) -> Dict[str, Any]:
    """The customer's own view of their commercial arrangement, or nothing.

    Imported inside the function so the launch engine does not hard-depend on
    the commercial package at import time: a customer on a standard
    subscription has no agreement and this block is simply absent.
    """
    try:
        from app.services.commercial import agreements as ag
    except Exception:                                        # pragma: no cover
        return {"has_agreement": False}

    agreement = ag.current_for_organization(db, org.id)
    if agreement is None:
        return {"has_agreement": False}

    view = ag.customer_view(db, agreement)
    return {
        "has_agreement": True,
        "headline": view["headline"],
        "status_line": view["status_line"],
        "open_question_count": view["open_question_count"],
        "answered_question_count": view["answered_question_count"],
        "question_total": view["question_total"],
        "questions": view["questions"],
    }


def _requirements(db: Session, impl, org: Organization) -> Dict[str, Any]:
    """What is outstanding, from the records that hold the answer.

    Deliberately assembled from four different owners — the intake, the launch
    programme's integrations, its checks and its training — rather than from a
    single "progress" column somebody could set.
    """
    from app.models.launch_delivery_models import (
        CHECK_PASS, INT_NOT_APPLICABLE, INT_VERIFIED, ImplementationCheck,
        ImplementationIntegration, ImplementationTraining,
    )

    integrations = (db.query(ImplementationIntegration)
                    .filter(ImplementationIntegration.implementation_id == impl.id)
                    .order_by(ImplementationIntegration.position).all())
    checks = (db.query(ImplementationCheck)
              .filter(ImplementationCheck.implementation_id == impl.id)
              .order_by(ImplementationCheck.position).all())
    training = (db.query(ImplementationTraining)
                .filter(ImplementationTraining.implementation_id == impl.id)
                .order_by(ImplementationTraining.position).all())

    return {
        "integrations": [{
            "key": r.key, "label": r.label, "provider": r.provider,
            "status": r.status, "required": bool(r.is_required),
            "settled": r.status in (INT_VERIFIED, INT_NOT_APPLICABLE),
            "owner_party": r.owner_party,
            "customer_action": r.owner_party == "customer",
        } for r in integrations],
        "checks": [{
            "key": r.key, "label": r.label, "category": r.category,
            "status": r.status, "required": bool(r.is_required),
            "settled": r.status == CHECK_PASS,
            "customer_approved": r.customer_approved_at is not None,
        } for r in checks],
        "training": [{
            "key": r.key, "title": r.title, "required": bool(r.is_required),
            "scheduled_at": r.scheduled_at,
            "completed": r.completed_at is not None,
            "acknowledged": r.customer_acknowledged_at is not None,
        } for r in training],
    }


def compose(db: Session, impl, org: Organization,
            user=None, *, preview: bool = False) -> Dict[str, Any]:
    """The whole customer-facing experience, presentation and truth together.

    READ ONLY. This is the single code path behind both the customer's own
    onboarding and the internal preview of it, which is why the preview cannot
    drift from what the customer will actually see and cannot have a side
    effect the customer's path does not.
    """
    from app.services import launch_intake

    platform = (db.query(Platform).filter(Platform.id == impl.platform_id).first()
                if impl.platform_id else None)
    brand_name = getattr(platform, "name", None) or "your implementation team"

    config = resolve(db, industry=getattr(org, "industry", None),
                     platform_id=impl.platform_id, organization_id=org.id)

    from datetime import date as _date
    tokens = {"brand": brand_name, "customer": org.name or "",
              "year": str(_date.today().year)}

    presentation = _fill(config["presentation"], tokens)
    # The customer's own logo is a fact about the organization, so it fills in
    # from the record unless a config row deliberately overrode it.
    if not presentation.get("hero_logo_url"):
        presentation["hero_logo_url"] = getattr(org, "brand_logo_url", None)

    overview = launch_intake.overview(db, impl.id, org.id)
    journey = _journey_state(db, _fill(config["journey"], tokens), impl.status)

    return {
        "preview": bool(preview),
        "presentation": presentation,
        "journey": journey,
        "form": config["form"],
        "layers": config["layers"],
        "industry": {
            "key": config["industry"],
            "label": industry_templates.resolve(
                getattr(org, "industry", None))["label"],
            "matched": industry_templates.is_known(getattr(org, "industry", None)),
        },
        # AUTHORITATIVE. Not a page-visit count, not a config value.
        "progress": {
            "intake_pct": overview.get("overall_pct", 0),
            "sections_total": len(overview.get("steps", []) or []),
            "sections_complete": len([s for s in overview.get("steps", []) or []
                                      if int(s.get("completion_pct") or 0) >= 100]),
            "file_count": overview.get("file_count", 0),
            "source": "required fields answered, counted from the stored intake",
        },
        "requirements": _requirements(db, impl, org),
        "commercial": _commercial_block(db, org),
    }
