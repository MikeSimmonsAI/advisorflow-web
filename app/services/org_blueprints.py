"""ORGANIZATION BLUEPRINTS — reusable vertical profiles, evaluated against records.

A blueprint answers "what does an organization of THIS KIND need", as data:

    core required      what every organization on the platform needs
    vertical required  what this business model cannot operate without
    optional           available, sold separately, never counted against launch

and a set of SETUP ITEMS (company, primary location, users, booking, SMS, email,
AI automation, plus vertical extras) that are either required, optional or not
applicable for that kind of organization.

WHAT THIS FILE WILL NOT DO
-------------------------
* Hard-code an organization. Blueprints are keyed by vertical. Which blueprint
  applies to an org is derived from its stored `industry` and its stored
  entitlement allow-list (see `select_blueprint`) — never from its id or name.
* Grant anything. Evaluating a blueprint reads; it never writes an allow-list.
  Applying the blueprint's features is the existing, audited
  `PUT /god/customers/{id}/features` call, made deliberately by an operator.
* Guess. Every status is computed from a real row: `locations`, `users`,
  `user_locations`, staff activation links, stored Twilio / A2P columns, stored
  email sender columns, calendar connection flags and `ai_employee_deployments`.
  Where the schema has no signal for something, the item says "Not Ready" with
  the reason "not tracked" rather than inventing a green tick.

STATUS VOCABULARY (API value → label)
    configured    Configured     every signal for this item is present
    partial       Partial        some of it is present; the reason says what is not
    needs_setup   Needs Setup    nothing is configured yet; the action says where
    not_ready     Not Ready      blocked by a prerequisite, or not tracked
    not_required  Not Required   this blueprint does not need it and it is off
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Organization, User
from app.models.location_models import Location

CONFIGURED = "configured"
PARTIAL = "partial"
NEEDS_SETUP = "needs_setup"
NOT_READY = "not_ready"
NOT_REQUIRED = "not_required"

STATUS_LABELS = {
    CONFIGURED: "Configured",
    PARTIAL: "Partial",
    NEEDS_SETUP: "Needs Setup",
    NOT_READY: "Not Ready",
    NOT_REQUIRED: "Not Required",
}

REQUIRED = "required"
OPTIONAL = "optional"

# The seven activation-readiness items, in the order the Control Center shows
# them. Vertical blueprints may add extras after these.
ACTIVATION_ITEMS = ("company", "primary_location", "users", "booking", "sms",
                    "email", "ai_automation")

ITEM_LABELS = {
    "company": "Company",
    "primary_location": "Primary Location",
    "users": "Users",
    "booking": "Booking",
    "sms": "SMS",
    "email": "Email",
    "ai_automation": "AI Automation",
    "wholesale_settings": "Wholesale Settings",
    "buyer_network": "Buyer Network",
    "funding_partners": "Funding Partners",
    "contacts_imported": "Contacts Imported",
}

# Platform-provided foundations that are not feature keys (they cannot be
# switched off per org), listed so the blueprint states them rather than
# pretending they are entitlements.
PLATFORM_FOUNDATIONS = [
    {"key": "identity", "label": "Identity & sign-in",
     "note": "Always on. One human, one identity; not a feature toggle."},
    {"key": "permissions", "label": "Roles & permissions",
     "note": "Always on. Role guards run on every route; not a feature toggle."},
]

# CORE REQUIRED — every blueprint. Only keys that exist in
# app.services.entitlements.FEATURES; an unregistered key is reported, never
# silently dropped (see _feature_item).
CORE_REQUIRED = ["leads", "crm", "users", "imports", "compliance", "audit_log",
                 "reports"]

BLUEPRINTS: Dict[str, Dict[str, Any]] = {
    "core": {
        "key": "core",
        "label": "Core organization",
        "description": "No vertical blueprint applies. Only the platform core is "
                       "required; everything else is optional.",
        "vertical_required": [],
        "optional": ["sms", "email", "voice", "campaigns", "cadences", "booking",
                     "calendar", "availability", "ai_assist", "crm_connectors",
                     "master_dashboard", "branding_settings", "tier_config",
                     "lead_cleanup", "case_files"],
        "setup": {"company": REQUIRED, "primary_location": REQUIRED,
                  "users": REQUIRED, "booking": OPTIONAL, "sms": OPTIONAL,
                  "email": OPTIONAL, "ai_automation": OPTIONAL},
        "vertical_setup": [],
    },
    "energy": {
        "key": "energy",
        "label": "Energy / utility brokerage",
        "description": "Energy procurement and brokerage: leads & customers, "
                       "contacts, rate requests, sales pipeline, move concierge, "
                       "communications, follow-up and renewals.",
        # Communications and reporting are the business model: rate requests
        # and renewals are worked by message, and renewals are a report.
        "vertical_required": ["sms", "email", "tier_config"],
        "optional": ["crm_connectors", "campaigns", "cadences", "voice", "booking",
                     "calendar", "availability", "ai_assist", "master_dashboard",
                     "branding_settings", "lead_cleanup"],
        "setup": {"company": REQUIRED, "primary_location": REQUIRED,
                  "users": REQUIRED, "booking": OPTIONAL, "sms": REQUIRED,
                  "email": REQUIRED, "ai_automation": OPTIONAL},
        "vertical_setup": [("contacts_imported", OPTIONAL)],
    },
    "wholesale_real_estate": {
        "key": "wholesale_real_estate",
        "label": "Wholesale real estate",
        "description": "Wholesale acquisition and disposition: lead intelligence, "
                       "property records, seller conversations, AI handoff, "
                       "buyers, funding and dispositions.",
        "vertical_required": ["wholesale_real_estate", "sms", "email", "ai_assist"],
        "optional": ["campaigns", "cadences", "voice", "booking", "calendar",
                     "availability", "crm_connectors", "master_dashboard",
                     "branding_settings", "tier_config", "lead_cleanup"],
        "setup": {"company": REQUIRED, "primary_location": REQUIRED,
                  "users": REQUIRED, "booking": OPTIONAL, "sms": REQUIRED,
                  "email": REQUIRED, "ai_automation": REQUIRED},
        "vertical_setup": [("wholesale_settings", REQUIRED),
                           ("buyer_network", OPTIONAL),
                           ("funding_partners", OPTIONAL)],
    },
}

BLUEPRINT_KEYS = tuple(BLUEPRINTS)


def blueprint_feature_keys(key: str, include_optional: bool = False) -> List[str]:
    """Every registered feature key a blueprint requires (plus optional).

    What an operator would pass to PUT /god/customers/{id}/features to apply
    the blueprint. Unregistered keys are excluded here because that endpoint
    refuses them; `evaluate_blueprint` still reports them.
    """
    from app.services import entitlements
    bp = BLUEPRINTS[key]
    keys = list(CORE_REQUIRED) + list(bp["vertical_required"])
    if include_optional:
        keys += list(bp["optional"])
    out: List[str] = []
    for k in keys:
        if k in entitlements.FEATURES and k not in out:
            out.append(k)
    return sorted(out)


def _explicit_allow_list(org: Organization) -> Optional[List[str]]:
    from app.services import entitlements
    return entitlements.enabled_for(org)


def select_blueprint(org: Organization) -> Tuple[str, str]:
    """(blueprint_key, why). Derived from stored industry and allow-list only."""
    from app.services import industry_templates
    raw = (org.industry or "").strip().lower()
    if "wholesale" in raw:
        return "wholesale_real_estate", "Industry '%s' names wholesale." % org.industry
    allowed = _explicit_allow_list(org)
    # A NULL allow-list (legacy "everything") says nothing about the business
    # model, so it is deliberately not read as "wholesale".
    if allowed is not None and "wholesale_real_estate" in allowed:
        return ("wholesale_real_estate",
                "The wholesale_real_estate module is explicitly enabled.")
    if industry_templates.normalize(org.industry) == "energy":
        return "energy", "Industry '%s' resolves to the energy template." % org.industry
    return "core", "No vertical blueprint matches industry '%s'." % (org.industry or "")


# ── status helpers ──────────────────────────────────────────────────────────

def _item(key: str, requirement: str, status: str, reason: str,
          action: Optional[Dict[str, str]] = None, **detail) -> Dict[str, Any]:
    return {
        "key": key,
        "label": ITEM_LABELS.get(key, key),
        "requirement": requirement,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "reason": reason,
        # Where to go to fix it. Only real destinations: a Control Center tab,
        # or an existing God route.
        "action": action,
        "detail": detail,
    }


def _not_required_or(requirement: str, feature_on: bool) -> bool:
    """True when an optional item for a feature that is off is Not Required."""
    return requirement == OPTIONAL and not feature_on


def _feature_on(org: Organization, key: str) -> bool:
    from app.services import entitlements
    return entitlements.org_has_feature(org, key)


# ── the setup-item evaluators: one per item, each reading real rows ─────────

def _eval_company(db, org, requirement):
    missing = [f for f, v in (("name", org.name), ("slug", org.slug),
                              ("brand", org.platform_id)) if not v]
    if not missing:
        return _item("company", requirement, CONFIGURED,
                     "Name, slug and brand are set.",
                     industry=org.industry, plan=org.plan)
    return _item("company", requirement, PARTIAL,
                 "Missing: %s." % ", ".join(missing),
                 {"label": "Edit company", "tab": "overview"})


def _eval_primary_location(db, org, requirement):
    locs = (db.query(Location)
            .filter(Location.organization_id == org.id,
                    Location.is_active == True).all())  # noqa: E712
    primary = next((l for l in locs if l.is_primary), None)
    action = {"label": "Add Location", "tab": "locations"}
    if not locs:
        return _item("primary_location", requirement, NEEDS_SETUP,
                     "No active location exists.", action, location_count=0)
    if primary is None:
        return _item("primary_location", requirement, PARTIAL,
                     "%d active location(s) but none is primary." % len(locs),
                     {"label": "Set primary", "tab": "locations"},
                     location_count=len(locs))
    gaps = []
    if not (primary.address_line1 or primary.city):
        gaps.append("address")
    if not primary.timezone:
        gaps.append("timezone")
    if gaps:
        return _item("primary_location", requirement, PARTIAL,
                     "Primary location '%s' has no %s." % (primary.name, " or ".join(gaps)),
                     {"label": "Edit location", "tab": "locations"},
                     location_count=len(locs), primary=primary.name)
    return _item("primary_location", requirement, CONFIGURED,
                 "Primary location '%s' has an address and timezone." % primary.name,
                 location_count=len(locs), primary=primary.name,
                 timezone=primary.timezone)


def _eval_users(db, org, requirement):
    from app.services.customer_readiness import customer_user_counts
    c = customer_user_counts(db, org.id)
    action = {"label": "Invite User", "tab": "people"}
    if not c["active"]:
        return _item("users", requirement, NEEDS_SETUP,
                     "No active user account — nobody can sign in.", action, **c)
    problems = []
    if not c["admins"]:
        problems.append("no active organization admin")
    if c["pending"]:
        problems.append("%d user(s) have never signed in" % c["pending"])
    if problems:
        return _item("users", requirement, PARTIAL,
                     "%d active user(s); %s." % (c["active"], "; ".join(problems)),
                     action, **c)
    return _item("users", requirement, CONFIGURED,
                 "%d active user(s), including an admin; all have signed in."
                 % c["active"], **c)


def _eval_booking(db, org, requirement):
    feature_on = _feature_on(org, "booking")
    if _not_required_or(requirement, feature_on):
        return _item("booking", requirement, NOT_REQUIRED,
                     "Booking is optional for this blueprint and the booking "
                     "feature is not enabled.")
    if requirement == REQUIRED and not feature_on:
        return _item("booking", requirement, NOT_READY,
                     "The booking feature is not enabled for this organization.",
                     {"label": "Manage Entitlements", "tab": "entitlements"})
    from app.services.customer_readiness import _booking, _calendar
    hours = _booking(db, org)
    cal = _calendar(db, org)
    action = {"label": "Configure Booking", "tab": "locations"}
    if hours["location_count"] == 0:
        return _item("booking", requirement, NOT_READY,
                     "No location exists, so a booking has nowhere to route.",
                     {"label": "Add Location", "tab": "locations"},
                     locations_with_hours=0, calendars_connected=cal["connected_count"])
    hours_ok = hours["with_hours"] == hours["location_count"]
    cal_ok = cal["connected_count"] > 0
    detail = dict(locations=hours["location_count"],
                  locations_with_hours=hours["with_hours"],
                  calendars_connected=cal["connected_count"],
                  active_users=cal["user_count"])
    if hours_ok and cal_ok:
        return _item("booking", requirement, CONFIGURED,
                     "Every active location has operating hours and %d user(s) "
                     "have a connected calendar." % cal["connected_count"], **detail)
    if hours["with_hours"] or cal_ok:
        missing = []
        if not hours_ok:
            missing.append("%d of %d locations have operating hours"
                           % (hours["with_hours"], hours["location_count"]))
        if not cal_ok:
            missing.append("no user has connected a calendar")
        return _item("booking", requirement, PARTIAL,
                     "Booking not fully configured: %s." % "; ".join(missing),
                     action, **detail)
    return _item("booking", requirement, NEEDS_SETUP,
                 "No location has operating hours and no user has connected a "
                 "calendar.", action, **detail)


_A2P_APPROVED = {"VERIFIED", "APPROVED"}


def _eval_sms(db, org, requirement):
    feature_on = _feature_on(org, "sms")
    if _not_required_or(requirement, feature_on):
        return _item("sms", requirement, NOT_REQUIRED,
                     "SMS is optional for this blueprint and not enabled.")
    if requirement == REQUIRED and not feature_on:
        return _item("sms", requirement, NOT_READY,
                     "The sms feature is not enabled for this organization.",
                     {"label": "Manage Entitlements", "tab": "entitlements"})
    sid = getattr(org, "org_twilio_account_sid", None)
    tok = getattr(org, "org_twilio_auth_token_encrypted", None)
    org_num = getattr(org, "org_twilio_phone_number", None)
    user_nums = (db.query(func.count(User.id))
                 .filter(User.organization_id == org.id, User.is_active == True,  # noqa: E712
                         User.twilio_phone_number.isnot(None),
                         User.twilio_phone_number != "").scalar() or 0)
    num_type = (getattr(org, "org_twilio_number_type", None) or "").lower() or None
    campaign = getattr(org, "twilio_a2p_campaign_status", None)
    brand = getattr(org, "twilio_a2p_brand_status", None)
    detail = dict(account_sid_present=bool(sid), auth_token_present=bool(tok),
                  org_number_present=bool(org_num), user_numbers=int(user_nums),
                  number_type=num_type, a2p_brand_status=brand,
                  a2p_campaign_status=campaign, verified_against_provider=False)
    action = {"label": "Enable SMS", "href": "/god/diagnostics/twilio"}
    has_number = bool(org_num) or user_nums > 0
    if not (sid or tok or has_number):
        return _item("sms", requirement, NEEDS_SETUP,
                     "No Twilio credentials and no sending number are stored.",
                     action, **detail)
    if not (sid and tok and has_number):
        missing = [n for n, ok in (("account SID", sid), ("auth token", tok),
                                   ("sending number", has_number)) if not ok]
        return _item("sms", requirement, PARTIAL,
                     "Twilio setup incomplete — missing %s." % ", ".join(missing),
                     action, **detail)
    if num_type == "10dlc" and (campaign or "").upper() not in _A2P_APPROVED:
        return _item("sms", requirement, PARTIAL,
                     "Credentials stored, but the A2P 10DLC campaign is %s."
                     % (campaign or "not registered"), action, **detail)
    return _item("sms", requirement, CONFIGURED,
                 "Twilio credentials and a sending number are stored%s. Stored, "
                 "not tested against Twilio."
                 % ("" if num_type != "10dlc" else "; A2P campaign %s" % campaign),
                 **detail)


def _eval_email(db, org, requirement):
    feature_on = _feature_on(org, "email")
    if _not_required_or(requirement, feature_on):
        return _item("email", requirement, NOT_REQUIRED,
                     "Email is optional for this blueprint and not enabled.")
    if requirement == REQUIRED and not feature_on:
        return _item("email", requirement, NOT_READY,
                     "The email feature is not enabled for this organization.",
                     {"label": "Manage Entitlements", "tab": "entitlements"})
    frm = getattr(org, "from_email", None)
    key = getattr(org, "resend_api_key", None)
    detail = dict(from_email=frm, org_api_key_present=bool(key),
                  verified_against_provider=False)
    action = {"label": "Configure email", "tab": "operations"}
    if frm and key:
        return _item("email", requirement, CONFIGURED,
                     "A sender address and an org API key are stored. Stored, not "
                     "tested against the provider.", **detail)
    if frm or key:
        return _item("email", requirement, PARTIAL,
                     "A sender address is set but no org API key (sends on the "
                     "shared platform key)." if frm else
                     "An API key is stored but no sender address is set.",
                     action, **detail)
    return _item("email", requirement, NEEDS_SETUP,
                 "No sender address and no org API key are stored.", action, **detail)


def _eval_ai(db, org, requirement):
    from app.models.ai_deployment_models import AIEmployeeDeployment
    from app.services.ai_deployment.constants import LIVE_STATES, RETIRED
    feature_on = _feature_on(org, "ai_assist")
    rows = (db.query(AIEmployeeDeployment.state, func.count(AIEmployeeDeployment.id))
            .filter(AIEmployeeDeployment.organization_id == org.id)
            .group_by(AIEmployeeDeployment.state).all())
    by_state = {s: int(n) for s, n in rows}
    live = sum(n for s, n in by_state.items() if s in LIVE_STATES)
    open_rows = sum(n for s, n in by_state.items() if s != RETIRED)
    detail = dict(deployments_by_state=by_state, live=live,
                  ai_assist_enabled=feature_on)
    action = {"label": "Configure AI", "href": "/god/ai-deployment"}
    if requirement == OPTIONAL and not feature_on and not open_rows:
        return _item("ai_automation", requirement, NOT_REQUIRED,
                     "AI automation is optional for this blueprint; ai_assist is "
                     "not enabled and no AI employee is deployed.", **detail)
    if requirement == REQUIRED and not feature_on:
        return _item("ai_automation", requirement, NOT_READY,
                     "The ai_assist feature is not enabled for this organization.",
                     {"label": "Manage Entitlements", "tab": "entitlements"}, **detail)
    if live:
        return _item("ai_automation", requirement, CONFIGURED,
                     "%d AI employee deployment(s) switched on." % live, **detail)
    if open_rows:
        return _item("ai_automation", requirement, PARTIAL,
                     "%d AI employee deployment(s) exist but none is switched on."
                     % open_rows, action, **detail)
    return _item("ai_automation", requirement, NEEDS_SETUP,
                 "No AI employee has been deployed for this organization.",
                 action, **detail)


def _eval_wholesale_settings(db, org, requirement):
    from app.models.wholesale_models import WholesaleSettings
    row = (db.query(WholesaleSettings)
           .filter(WholesaleSettings.organization_id == org.id).first())
    if row is not None:
        return _item("wholesale_settings", requirement, CONFIGURED,
                     "A wholesale settings record exists for this organization.")
    if not _feature_on(org, "wholesale_real_estate"):
        return _item("wholesale_settings", requirement, NOT_READY,
                     "The wholesale_real_estate module is not enabled.",
                     {"label": "Manage Entitlements", "tab": "entitlements"})
    return _item("wholesale_settings", requirement, NEEDS_SETUP,
                 "No wholesale settings record yet (created the first time the "
                 "workspace's wholesale settings are saved).",
                 {"label": "Open workspace", "tab": "operations"})


def _count_eval(key, model, noun, requirement, db, org, feature="wholesale_real_estate"):
    n = (db.query(func.count(model.id))
         .filter(model.organization_id == org.id).scalar() or 0)
    if n:
        return _item(key, requirement, CONFIGURED, "%d %s on record." % (n, noun),
                     count=int(n))
    if requirement == OPTIONAL and feature and not _feature_on(org, feature):
        return _item(key, requirement, NOT_REQUIRED,
                     "Optional, and the %s feature is not enabled." % feature, count=0)
    return _item(key, requirement, NEEDS_SETUP,
                 "No %s on record yet." % noun, {"label": "Open workspace",
                                                 "tab": "operations"}, count=0)


def _eval_buyer_network(db, org, requirement):
    from app.models.wholesale_models import WholesaleBuyer
    return _count_eval("buyer_network", WholesaleBuyer, "buyers", requirement, db, org)


def _eval_funding_partners(db, org, requirement):
    from app.models.wholesale_models import WholesaleFundingPartner
    return _count_eval("funding_partners", WholesaleFundingPartner,
                       "funding partners", requirement, db, org)


def _eval_contacts_imported(db, org, requirement):
    from app.models.intake_models import OrgContact
    return _count_eval("contacts_imported", OrgContact, "contacts", requirement,
                       db, org, feature=None)


EVALUATORS = {
    "company": _eval_company,
    "primary_location": _eval_primary_location,
    "users": _eval_users,
    "booking": _eval_booking,
    "sms": _eval_sms,
    "email": _eval_email,
    "ai_automation": _eval_ai,
    "wholesale_settings": _eval_wholesale_settings,
    "buyer_network": _eval_buyer_network,
    "funding_partners": _eval_funding_partners,
    "contacts_imported": _eval_contacts_imported,
}


def _safe_eval(key, db, org, requirement):
    fn = EVALUATORS.get(key)
    if fn is None:
        return _item(key, requirement, NOT_READY, "not tracked")
    try:
        return fn(db, org, requirement)
    except Exception as exc:  # a missing table must not blank the whole screen
        return _item(key, requirement, NOT_READY,
                     "not tracked (%s)" % exc.__class__.__name__)


# ── features ────────────────────────────────────────────────────────────────

def _feature_item(org: Organization, key: str, requirement: str,
                  tier: str) -> Dict[str, Any]:
    from app.services import entitlements
    registered = key in entitlements.FEATURES
    if not registered:
        return {"key": key, "label": key, "tier": tier, "requirement": requirement,
                "registered": False, "enabled": False, "requires": [],
                "missing_dependencies": [], "status": NOT_READY,
                "status_label": STATUS_LABELS[NOT_READY],
                "reason": "Feature key is not registered in the entitlement catalogue."}
    enabled = entitlements.org_has_feature(org, key)
    requires = list(entitlements.REQUIRES.get(key, ()))
    missing = [d for d in requires if not entitlements.org_has_feature(org, d)]
    if enabled and missing:
        st, why = NOT_READY, "Enabled, but depends on %s which is not enabled." % ", ".join(missing)
    elif enabled:
        st, why = CONFIGURED, "Enabled."
    elif requirement == REQUIRED:
        st, why = NEEDS_SETUP, "Required by this blueprint and not enabled."
    else:
        st, why = NOT_REQUIRED, "Optional and not enabled."
    return {"key": key, "label": entitlements.FEATURES[key], "tier": tier,
            "requirement": requirement, "registered": True, "enabled": enabled,
            "requires": requires, "missing_dependencies": missing,
            "status": st, "status_label": STATUS_LABELS[st], "reason": why}


def describe(key: str) -> Dict[str, Any]:
    """The static definition of a blueprint, for display."""
    bp = BLUEPRINTS[key]
    return {
        "key": bp["key"], "label": bp["label"], "description": bp["description"],
        "platform_foundations": PLATFORM_FOUNDATIONS,
        "core_required": list(CORE_REQUIRED),
        "vertical_required": list(bp["vertical_required"]),
        "optional": list(bp["optional"]),
        "setup": dict(bp["setup"]),
        "vertical_setup": [{"key": k, "requirement": r} for k, r in bp["vertical_setup"]],
    }


def activation_items(db: Session, org: Organization,
                     blueprint_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """The seven Activation Readiness items, evaluated for this org."""
    key = blueprint_key or select_blueprint(org)[0]
    setup = BLUEPRINTS[key]["setup"]
    return [_safe_eval(k, db, org, setup.get(k, OPTIONAL)) for k in ACTIVATION_ITEMS]


def _summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    counted = [i for i in items if i["status"] != NOT_REQUIRED]
    done = sum(1 for i in counted if i["status"] == CONFIGURED)
    by = {}
    for i in items:
        by[i["status"]] = by.get(i["status"], 0) + 1
    total = len(counted)
    return {"total": total, "complete": done,
            "percent": int(round(100.0 * done / total)) if total else 100,
            "by_status": by}


def evaluate_blueprint(db: Session, org: Organization,
                       blueprint_key: Optional[str] = None) -> Dict[str, Any]:
    """The full blueprint evaluation: features by tier + setup items + summary."""
    if blueprint_key is not None and blueprint_key not in BLUEPRINTS:
        raise KeyError(blueprint_key)
    selected, why = select_blueprint(org)
    key = blueprint_key or selected
    bp = BLUEPRINTS[key]

    core = [_feature_item(org, k, REQUIRED, "core") for k in CORE_REQUIRED]
    vertical = [_feature_item(org, k, REQUIRED, "vertical") for k in bp["vertical_required"]]
    optional = [_feature_item(org, k, OPTIONAL, "optional") for k in bp["optional"]]

    setup = activation_items(db, org, key)
    extras = [_safe_eval(k, db, org, r) for k, r in bp["vertical_setup"]]

    required_features = core + vertical
    feat_done = sum(1 for f in required_features if f["status"] == CONFIGURED)
    return {
        "organization_id": org.id,
        "blueprint": {**describe(key),
                      "selected": key == selected,
                      "auto_selected_key": selected,
                      "selected_because": why},
        "features": {"core_required": core, "vertical_required": vertical,
                     "optional": optional,
                     "required_total": len(required_features),
                     "required_enabled": feat_done,
                     "missing_required": [f["key"] for f in required_features
                                          if f["status"] != CONFIGURED],
                     "apply_keys": blueprint_feature_keys(key)},
        "setup": setup,
        "vertical_setup": extras,
        "activation": _summary(setup),
        "overall": _summary(setup + [i for i in extras if i["requirement"] == REQUIRED]
                            + [{"status": f["status"]} for f in required_features]),
    }
