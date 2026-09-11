"""
The launch programme, per white-label brand.

===========================================================================
WHAT THIS OWNS, AND THE ONE THING IT DELIBERATELY DOES NOT
===========================================================================

It owns the delivery programme: which integrations a customer's launch tracks,
which validations have to pass, which training has to happen, and which of
those are required before Go Live.

It does NOT own the build milestone list. `provisioning.milestone_template()`
already produces that from the package the customer bought, and it runs at
provisioning time before anything here exists. A second milestone template
would be a second answer to "what does this customer's build consist of", and
the two would disagree the first time somebody edited one of them.

So a brand may ADD milestones here (`extra_milestones`, empty by default) and
may not replace them. Additive is safe: a brand that configures nothing gets
exactly the programme it gets today.

===========================================================================
DEFAULT, OVERRIDE, MERGE
===========================================================================

    DEFAULT_TEMPLATE          in this file, brand-neutral, the floor
    LaunchTemplate.config     one row per platform, the brand's answer

`resolve(db, platform_id)` merges them SHALLOWLY PER SECTION: a brand that
supplies `integrations` replaces the default integrations entirely, because a
brand's list of connections is a considered whole rather than an addition to
somebody else's. A brand that supplies only `golive` keeps the default
integrations, checks and training. Within `golive`, individual keys merge, so
turning one requirement off does not silently turn the other eleven off too.

===========================================================================
NOTHING HERE NAMES A BRAND OR A CUSTOMER
===========================================================================

The default programme is written in the vocabulary any customer of any brand
would recognise — "lead source", "website", "calendar" — never in the
vocabulary of the first customer configured. The guard test in
tests/test_launch_engine.py greps these modules for exactly that, and this
module is in its scope.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.launch_delivery_models import LaunchTemplate

log = logging.getLogger("launch_template")


# ── the go-live gate, and what it means for a requirement to be "on" ────────
#
# Every key here is a question the platform can answer from its own records.
# Nothing in this list is a promise a human types in; each one is computed in
# launch_delivery.readiness() from rows somebody actually created.
#
# The DEFAULTS are the honest floor for a first launch: everything that can be
# verified is required, and the two that are judgement calls rather than facts
# — a customer's approval of the testing, and whether the intake has been read
# rather than merely submitted — are off, because requiring a signature the
# brand has not decided to ask for would block launches on paperwork nobody
# agreed to.

GOLIVE_KEYS = (
    "intake_submitted",
    "intake_reviewed",
    "files_received",
    "access_received",
    "milestones_complete",
    "integrations_verified",
    "uat_complete",
    "customer_uat_approval",
    "training_complete",
    "no_open_blockers",
    "customer_signoff",
    "provider_signoff",
)

GOLIVE_LABELS = {
    "intake_submitted":      "Customer intake submitted",
    "intake_reviewed":       "Intake reviewed by the implementation team",
    "files_received":        "Required files received",
    "access_received":       "Required access and credentials received",
    "milestones_complete":   "Required build items complete",
    "integrations_verified": "Required integrations verified",
    "uat_complete":          "Testing complete",
    "customer_uat_approval": "Customer has approved the testing",
    "training_complete":     "Training complete",
    "no_open_blockers":      "No open blockers",
    "customer_signoff":      "Customer approval to go live",
    "provider_signoff":      "Implementation team approval to go live",
}

DEFAULT_GOLIVE: Dict[str, bool] = {
    "intake_submitted":      True,
    "intake_reviewed":       False,
    "files_received":        True,
    "access_received":       True,
    "milestones_complete":   True,
    "integrations_verified": True,
    "uat_complete":          True,
    "customer_uat_approval": False,
    "training_complete":     True,
    "no_open_blockers":      True,
    "customer_signoff":      True,
    "provider_signoff":      True,
}


# ── the default delivery programme ──────────────────────────────────────────

DEFAULT_INTEGRATIONS: List[Dict[str, Any]] = [
    {"key": "lead_source", "label": "Lead source connection", "required": True,
     "description": "The marketplace, partner or form that sends this customer "
                    "their enquiries."},
    {"key": "email", "label": "Email sending", "required": True,
     "description": "The address customer communications are sent from."},
    {"key": "sms", "label": "SMS and phone", "required": False,
     "description": "The number messages and calls come from."},
    {"key": "calendar", "label": "Calendar", "required": False,
     "description": "The calendars bookings are written into."},
    {"key": "website", "label": "Website and domain", "required": True,
     "description": "The domain, DNS and hosting the customer-facing site runs on."},
]

DEFAULT_CHECKS: List[Dict[str, Any]] = [
    {"key": "website", "label": "Website loads and is correct",
     "category": "Website", "required": True},
    {"key": "lead_capture", "label": "A new enquiry is captured end to end",
     "category": "Workflow", "required": True},
    {"key": "lead_routing", "label": "Enquiries reach the right person",
     "category": "Workflow", "required": True},
    {"key": "communications", "label": "Customer emails and messages send correctly",
     "category": "Communications", "required": True},
    {"key": "user_access", "label": "Every user can sign in with the right permissions",
     "category": "Access", "required": True},
    {"key": "data", "label": "Imported data is complete and correct",
     "category": "Data", "required": False},
    {"key": "calendar", "label": "Bookings appear on the right calendar",
     "category": "Workflow", "required": False},
    {"key": "reporting", "label": "Reporting shows the expected figures",
     "category": "Reporting", "required": False},
    {"key": "mobile", "label": "The experience works on a phone",
     "category": "Website", "required": False},
]

DEFAULT_TRAINING: List[Dict[str, Any]] = [
    {"key": "admin", "title": "Administrator walkthrough", "required": True,
     "description": "Settings, users, permissions and day-to-day administration."},
    {"key": "team", "title": "Team training", "required": True,
     "description": "The daily workflow for the people who use it most."},
]

DEFAULT_TEMPLATE: Dict[str, Any] = {
    "extra_milestones": [],
    "integrations": DEFAULT_INTEGRATIONS,
    "checks": DEFAULT_CHECKS,
    "training": DEFAULT_TRAINING,
    "golive": DEFAULT_GOLIVE,
}

_SECTIONS = ("extra_milestones", "integrations", "checks", "training")


# ── resolution ──────────────────────────────────────────────────────────────

def _clean_rows(rows: Any, *, name_key: str) -> List[Dict[str, Any]]:
    """Accept only rows that could actually become a database row.

    A template is edited by a person and stored as JSON, so it can contain
    anything. Anything without a usable key and label is dropped rather than
    seeded — a row with no label is a blank line on somebody's checklist, and
    a row with no key would break the unique constraint that makes seeding
    idempotent.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("key") or "").strip().lower()[:64]
        name = str(row.get(name_key) or "").strip()[:200]
        if not key or not name or key in seen:
            continue
        seen.add(key)
        item = dict(row)
        item["key"] = key
        item[name_key] = name
        item["required"] = bool(row.get("required", True))
        out.append(item)
    return out


def resolve(db: Session, platform_id: Optional[str]) -> Dict[str, Any]:
    """The programme this brand's launches follow. Never raises, never empty.

    A brand with no row, an unreadable row, or a row holding rubbish gets the
    default. That is deliberate: a misconfigured template must degrade to the
    standard programme, not to no programme — an empty checklist would report
    a customer as ready for go-live because there was nothing left to do.
    """
    cfg: Dict[str, Any] = {}
    if platform_id:
        try:
            row = (db.query(LaunchTemplate)
                     .filter(LaunchTemplate.platform_id == platform_id).first())
            if row is not None and isinstance(row.config, dict):
                cfg = row.config
        except Exception:                                    # pragma: no cover
            log.warning("launch template unreadable for platform %s",
                        platform_id, exc_info=True)
            cfg = {}

    out = copy.deepcopy(DEFAULT_TEMPLATE)

    for section in _SECTIONS:
        if section not in cfg:
            continue
        name_key = "title" if section == "training" else "label"
        rows = _clean_rows(cfg.get(section), name_key=name_key)
        # An explicitly empty section is a real answer — "this brand tracks no
        # integrations" — and is honoured. A section of pure rubbish resolves
        # to empty for the same reason, which is visible on the screen rather
        # than silently reverting to a default the brand did not choose.
        out[section] = rows

    golive = dict(DEFAULT_GOLIVE)
    supplied = cfg.get("golive")
    if isinstance(supplied, dict):
        for k in GOLIVE_KEYS:
            if k in supplied:
                golive[k] = bool(supplied[k])
    out["golive"] = golive

    return out


def save(db: Session, platform_id: str, config: Dict[str, Any],
         actor_id: Optional[str] = None,
         name: Optional[str] = None) -> LaunchTemplate:
    """Create or replace one brand's template. Stored as given, read as merged.

    The raw config is kept rather than the resolved one, so a brand that
    supplied only `golive` still shows as having supplied only `golive` when
    somebody opens the editor — and picks up improvements to the default
    programme instead of being frozen against the day it was saved.
    """
    row = (db.query(LaunchTemplate)
             .filter(LaunchTemplate.platform_id == platform_id).first())
    if row is None:
        row = LaunchTemplate(platform_id=platform_id)
        db.add(row)
    row.config = config if isinstance(config, dict) else {}
    if name is not None:
        row.name = str(name)[:160] or None
    row.updated_by = actor_id
    db.commit()
    db.refresh(row)
    return row
