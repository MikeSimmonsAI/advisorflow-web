"""Wholesale custom access: what one team member may see and do (#439).

ONE engine for every Wholesale workspace - EvoSys Pro's and BookaBoost's - on
the existing seat (app/services/product_access.py) and team tree
(app/services/wholesale_team.py). It adds three things:

  1. A MODULE / ACTION map over the real Wholesale API. Every /wholesale route
     resolves to exactly one module and one action; a route the map does not
     know is refused for a custom-access login (default deny). The catalogue the
     builder shows is computed FROM the live routes, so a toggle exists only
     where the code really has that action.
  2. EFFECTIVE ACCESS for a person: profile permissions + their own grants -
     their own denials, plus record visibility (assigned / team / workspace).
  3. A DELEGATION CEILING: nobody can give anyone more than they hold.

Who it applies to
  * God / super admin and the workspace's own admins: unchanged, full access.
  * A seat with no access row: unchanged - the legacy team roles
    (member / manager / team lead / field) keep exactly the access they had.
  * A seat with an access row: Wholesale only, and only what the row allows.

What it can never grant: God Mode, another workspace, billing, platform
secrets, the advisor/CRM side, or team administration beyond the granter's own.
None of those are modules here, and the server fence (deps.get_current_user)
refuses every non-Wholesale path for a custom-access login.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.wholesale_access_models import WholesaleAccessProfile, WholesaleMemberAccess

# ── the vocabulary ──────────────────────────────────────────────────────────
ACTIONS = ("view", "create", "edit", "delete", "assign", "export", "approve", "publish", "manage")
ACTION_LABELS = {"view": "View", "create": "Create", "edit": "Edit", "delete": "Delete", "assign": "Assign",
                 "export": "Export", "approve": "Approve", "publish": "Publish", "manage": "Manage"}

ASSIGNED, TEAM, WORKSPACE = "assigned", "team", "workspace"
SCOPES = (ASSIGNED, TEAM, WORKSPACE)
SCOPE_LABELS = {ASSIGNED: "Only records assigned to them",
                TEAM: "Their records and their team's",
                WORKSPACE: "Every record in this workspace they have access to"}

# key, label, description, scoped (records here carry an assignee, so record
# visibility applies), group. Multifamily is NOT a module: its screen is the
# Discovery Inbox filtered to multifamily (the same /wholesale/evosense/inbox
# API), so it follows Acquisition Command and cannot be restricted separately.
MODULES: List[Tuple[str, str, str, bool, str]] = [
    ("acquisition", "Acquisition Command", "Command center, Discovery Inbox (including its Multifamily view), funnel and handoffs.", False, "Acquisition"),
    ("properties", "Properties and discovery", "Property records, EvoSense discovery, strategies and hunts, comps and valuation.", True, "Acquisition"),
    ("foreclosures", "Foreclosures", "Foreclosure filings and events.", False, "Acquisition"),
    ("sellers", "Seller leads and contacts", "Sellers, callbacks, notes, call queue, first contact and follow-ups.", True, "Acquisition"),
    ("canvassing", "Canvassing / Driving for Dollars", "Canvass routes and door-knocking.", True, "Acquisition"),
    ("appointments", "Appointments and follow-ups", "Field appointments, walkthroughs and their outcomes.", True, "Acquisition"),
    ("buyer_intel", "Buyer Intelligence", "Buyer research, verification and the buyer network.", False, "Dispositions"),
    ("buy_boxes", "Buy Boxes", "What each buyer is looking for.", False, "Dispositions"),
    ("underwriting", "Underwriting and deal calculations", "Analysis, comps on a deal, repairs, valuation and investor reports.", True, "Deals"),
    ("deals", "Deal creation and management", "Deals, stages, offers, title and closing steps.", True, "Deals"),
    ("deal_room", "Deal Room", "Private buyer deal room: listings, investors, threads.", False, "Dispositions"),
    ("investor_outreach", "Investor Outreach", "Sending deals to buyers and tracking responses and proof of funds.", True, "Dispositions"),
    ("dispositions", "Dispositions", "Disposition plans, buyer matching, share links and publication.", True, "Dispositions"),
    ("contracts", "Contracts and closing", "Contract templates, documents and e-signature.", True, "Deals"),
    ("funding", "Funding partners", "Funding partners and submissions.", False, "Deals"),
    ("team_board", "Team Board and assignments", "The team board, work exceptions and assignments.", True, "Team"),
    ("reports", "Reports and analytics", "Disposition analytics and intel reports.", False, "Team"),
    ("imports", "Data imports and exports", "Importing property and contact files.", False, "Data"),
    ("paid_lookups", "Provider enrichment and paid lookups", "Skip tracing, contact lookups and enrichment that cost money.", False, "Data"),
    ("settings", "Wholesale settings", "Workspace settings, SMS settings, provider controls and the pilot.", False, "Admin"),
    ("team_admin", "Team and access administration", "Who is on the team and what they can do. Never more than the person granting it holds.", False, "Admin"),
]
MODULE_KEYS = [m[0] for m in MODULES]
SCOPED_MODULES = {m[0] for m in MODULES if m[3]}

# Ordered: the first matching rule wins. Paths are matched with {ids} already
# stripped to "*" segments by `normalize`.
_RULES: List[Tuple[str, str]] = [
    # team administration
    (r"^/wholesale/team/me$", "_self"),
    (r"^/wholesale/team(/|$)", "team_admin"),
    # paid lookups before the areas that host them
    (r"^/wholesale/(skip-trace|contact-lookup|enrichment)(/|$)", "paid_lookups"),
    (r"^/wholesale/ops/skip-trace(/|$)", "paid_lookups"),
    (r"^/wholesale/evosense/(enrichment|providers|provider-evaluations)(/|$)", "paid_lookups"),
    (r"^/wholesale/evosense/properties/\*/enrich$", "paid_lookups"),
    # Contact-lookup batches buy lookups (each has a lookup and spend cap) and
    # a valuation request draws on the RentCast budget: paid, not "sellers".
    (r"^/wholesale/evosense/contact-batches(/|$)", "paid_lookups"),
    (r"^/wholesale/evosense/properties/\*/valuation$", "paid_lookups"),
    (r"^/wholesale/evosense/valuation(/|$)", "paid_lookups"),
    (r"^/wholesale/intel/providers$", "paid_lookups"),
    # imports
    (r"^/wholesale/evosense/import(/|$)", "imports"),
    (r"^/wholesale/properties/import(/|$)", "imports"),
    (r"^/wholesale/deals/\*/comps/import$", "imports"),
    # settings and controls
    (r"^/wholesale/(settings|sms)(/|$)", "settings"),
    (r"^/wholesale/evosense/controls(/|$)", "settings"),
    (r"^/wholesale/ops/pilot(/|$)", "settings"),
    (r"^/wholesale/data-sources(/|$)", "settings"),
    (r"^/wholesale/deal-room/(settings|activate|pause)(/|$)", "settings"),
    # foreclosures
    (r"^/wholesale/evosense/foreclosure", "foreclosures"),
    # acquisition command
    (r"^/wholesale/(command-center|dashboard|needs-you|operating-board|events)(/|$)", "acquisition"),
    (r"^/wholesale/evosense/(command-center|funnel|inbox|handoffs|opportunity|opportunity-queue|opportunity-radar|automation|events|capabilities)(/|$)", "acquisition"),
    # sellers
    (r"^/wholesale/(sellers|outreach-seller)(/|$)", "sellers"),
    (r"^/wholesale/ops/(leads|notes|callbacks)(/|$)", "sellers"),
    (r"^/wholesale/evosense/(call-queue|first-contact|follow-ups|permission-links|owner-names)(/|$)", "sellers"),
    (r"^/wholesale/deals/\*/seller-reply$", "sellers"),
    # contacting a property's owner is seller work, not property research
    (r"^/wholesale/evosense/properties/\*/(outreach|nurture|reply|permission-link|follow-up|call-outcome|owner|contacts)(/|$)", "sellers"),
    # canvassing and appointments
    (r"^/wholesale/canvass(/|$)", "canvassing"),
    (r"^/wholesale/appointments(/|$)", "appointments"),
    (r"^/wholesale/offers(/|$)", "deals"),          # a deal's offers (WholesaleOffer), not field offers
    # buyers
    (r"^/wholesale/buy-boxes(/|$)", "buy_boxes"),
    (r"^/wholesale/(buyer-intel|buyers)(/|$)", "buyer_intel"),
    (r"^/wholesale/intel/buyer/", "buyer_intel"),
    (r"^/wholesale/deals/\*/(buyer-board|buyer-intel|matches|match-buyers)$", "buyer_intel"),
    # investor outreach
    (r"^/wholesale/outreach(/|$)", "investor_outreach"),
    (r"^/wholesale/deals/\*/(outreach|cadence)$", "investor_outreach"),
    # deal room
    (r"^/wholesale/deal-room(/|$)", "deal_room"),
    # dispositions
    (r"^/wholesale/disposition(/|$)", "dispositions"),
    (r"^/wholesale/share-links(/|$)", "dispositions"),
    (r"^/wholesale/deals/\*/(disposition|disposition/preview|disposition-summary|share-links|share-activity|select-buyer|publication|publication/state|package|package\.pdf|package/(publish|submit|withdraw)|provenance)$", "dispositions"),
    # underwriting
    (r"^/wholesale/deals/\*/(analysis|analysis/recalculate|underwriting|valuation|comps|comps/find|comps/find/add|repairs|repairs/itemized|repairs/photo-analysis|repairs/photo-ai|economics-correction|fill-sheet)$", "underwriting"),
    (r"^/wholesale/comps(/|$)", "underwriting"),
    (r"^/wholesale/intel/(deal|evosense)/\*/(underwriting|investor-report)$", "underwriting"),
    # contracts and closing documents
    (r"^/wholesale/(contract-templates|contract-kits|documents|files)(/|$)", "contracts"),
    (r"^/wholesale/deals/\*/(contract|contracts/\*/(preview|send)|documents|documents/upload|title)$", "contracts"),
    # funding
    (r"^/wholesale/funding(/|$)", "funding"),
    # team board
    (r"^/wholesale/(exceptions|approvals)(/|$)", "team_board"),
    # reports
    (r"^/wholesale/intel(/|$)", "reports"),
    (r"^/wholesale/disposition/analytics$", "reports"),
    # deals (everything else on a deal)
    (r"^/wholesale/(deals|ops/deals)(/|$)", "deals"),
    # properties and discovery (catch-all for evosense and properties)
    (r"^/wholesale/(properties|evosense)(/|$)", "properties"),
]
_COMPILED = [(re.compile(p), m) for p, m in _RULES]

_ID_SEG = re.compile(r"^(?:\{[^}]+\}|[0-9a-fA-F-]{16,}|[A-Za-z0-9_-]*\d[A-Za-z0-9_-]{6,})$")


def normalize(path: str) -> str:
    """'/wholesale/deals/{deal_id}/offers' and '/wholesale/deals/9f2c.../offers'
    both become '/wholesale/deals/*/offers'."""
    segs = (path or "/").rstrip("/").split("/")
    out = []
    for i, s in enumerate(segs):
        if i >= 2 and s and _ID_SEG.match(s) and not s.endswith((".pdf", ".jpg")):
            out.append("*")
        else:
            out.append(s)
    return "/".join(out) or "/"


def module_for(path: str) -> Optional[str]:
    """The module a Wholesale path belongs to, or None (unknown: default deny)."""
    n = normalize(path)
    for rx, mod in _COMPILED:
        if rx.search(n):
            return mod
    return None


def action_for(path: str, method: str) -> str:
    """The action a request performs, from its method and the real verbs in the path."""
    n = normalize(path).lower()
    m = (method or "GET").upper()
    last = n.rsplit("/", 1)[-1]
    if "export" in n or last.endswith(".csv"):
        return "export"
    if m == "GET":
        return "view"
    if last in ("assign", "reassign") or n.endswith("/threads/*/assign"):
        return "assign"
    if last in ("decide", "approve", "approvals") or n.endswith("/approvals/*/decide"):
        return "approve"
    if last in ("publish", "submit", "withdraw", "state", "redraft", "close") and ("package" in n or "publication" in n or "listings" in n):
        return "publish"
    if "share-links" in n and m == "POST":
        return "publish"
    if m == "DELETE":
        return "delete"
    if m in ("PUT", "PATCH"):
        mod = module_for(path)
        return "manage" if mod in ("settings", "team_admin") else "edit"
    # POST
    mod = module_for(path)
    if mod in ("settings", "team_admin"):
        return "manage"
    if mod == "properties" and re.search(r"/evosense/(strategies|strategy-starters|sources|reprocess|roll-fix|ground-truth|truth-gate|routing-reviews|identity-reviews)", n):
        return "manage"
    if "*" in n.split("/")[:-1] and last not in _CREATE_TAILS:
        return "edit"                       # a verb on an existing record: stage, cancel, complete, merge...
    return "create"


# POSTs under a record that ADD something (or send something, which adds a
# message): these stay "create". Any other verb on a record is an edit.
_CREATE_TAILS = {"notes", "photos", "batch", "documents", "upload", "comps", "add", "signals", "contacts",
                 "offers", "repairs", "itemized", "files", "follow-up", "listing", "invites", "outreach",
                 "nurture", "reply", "send", "resend", "remind", "signature-request", "permission-link",
                 "from-exception", "underwriting", "disposition", "cadence", "photo", "proof-of-funds"}


def catalog(routes: Iterable[Any]) -> List[Dict[str, Any]]:
    """The modules and the actions the live API really has in each, from the
    app's own route table - the only source the builder's toggles come from."""
    seen: Dict[str, Set[str]] = {k: set() for k in MODULE_KEYS}
    for r in routes:
        path = getattr(r, "path", "") or ""
        if not path.startswith("/wholesale/"):
            continue
        mod = module_for(path)
        if mod not in seen:
            continue
        for meth in (getattr(r, "methods", None) or ()):
            if meth in ("HEAD", "OPTIONS"):
                continue
            seen[mod].add(action_for(path, meth))
    out = []
    for key, label, desc, scoped, group in MODULES:
        acts = [a for a in ACTIONS if a in seen[key]]
        if "view" not in acts and acts:
            acts.insert(0, "view")
        out.append({"key": key, "label": label, "description": desc, "group": group,
                    "record_scoped": scoped, "actions": acts,
                    "action_labels": {a: ACTION_LABELS[a] for a in acts}})
    return out


# ── starter templates (starting points, never rigid roles) ──────────────────
def _all(*mods, acts=ACTIONS):
    return {m: list(acts) for m in mods}


TEMPLATES: Dict[str, Dict[str, Any]] = {
    "acquisition_wholesaler": {
        "name": "Acquisition Wholesaler", "record_scope": ASSIGNED,
        "description": "Finds and works sellers and their own deals.",
        "permissions": {**_all("acquisition", "foreclosures", acts=("view",)),
                        "properties": ["view", "create", "edit"], "sellers": ["view", "create", "edit"],
                        "appointments": ["view", "create", "edit"], "canvassing": ["view", "create", "edit"],
                        "deals": ["view", "create", "edit"], "underwriting": ["view", "create", "edit"],
                        "contracts": ["view", "create"]}},
    "wholesale_manager": {
        "name": "Wholesale Manager", "record_scope": TEAM,
        "description": "Runs a team: everything their people do, plus assignments and approvals. No settings or team admin.",
        "permissions": {m: list(ACTIONS) for m in MODULE_KEYS if m not in ("settings", "team_admin", "paid_lookups")}},
    "field_representative": {
        "name": "Field Representative", "record_scope": ASSIGNED,
        "description": "Their appointments and canvass routes, and the properties on them.",
        "permissions": {"appointments": ["view", "edit"], "canvassing": ["view", "edit"], "properties": ["view"]}},
    "buyer_disposition": {
        "name": "Buyer / Disposition Specialist", "record_scope": WORKSPACE,
        "description": "Buyers, buy boxes, the deal room and getting deals sold.",
        "permissions": {"buyer_intel": ["view", "create", "edit"], "buy_boxes": ["view", "create", "edit", "delete"],
                        "deal_room": ["view", "create", "edit", "publish"], "investor_outreach": ["view", "create", "edit"],
                        "dispositions": ["view", "create", "edit", "publish"], "deals": ["view"], "underwriting": ["view"]}},
    "research_assistant": {
        "name": "Research Assistant / VA", "record_scope": WORKSPACE,
        "description": "Researches properties and owners. No sending, no paid lookups, no exports.",
        "permissions": {"properties": ["view", "edit"], "foreclosures": ["view"], "sellers": ["view", "edit"],
                        "buyer_intel": ["view", "edit"]}},
    "investor_restricted": {
        "name": "Investor - restricted deal access", "record_scope": ASSIGNED,
        "description": "View-only deals they are assigned and the deal room. Investors who buy through the private Deal Room keep using their own investor login instead.",
        "permissions": {"deals": ["view"], "deal_room": ["view"], "underwriting": ["view"]}},
    "custom": {"name": "Custom", "record_scope": ASSIGNED, "description": "Start from nothing.", "permissions": {}},
}


# ── permission sets ─────────────────────────────────────────────────────────
def clean(perms: Any, routes_catalog: Optional[List[Dict[str, Any]]] = None,
          add_view: bool = True) -> Dict[str, List[str]]:
    """Only known modules and actions survive; unknown keys are dropped, never kept.
    `add_view=False` for TAKE-AWAYS: denying Edit must not also deny View."""
    out: Dict[str, List[str]] = {}
    if not isinstance(perms, dict):
        return out
    allowed = {c["key"]: set(c["actions"]) for c in routes_catalog} if routes_catalog else None
    for mod, acts in perms.items():
        if mod not in MODULE_KEYS or not isinstance(acts, (list, tuple, set)):
            continue
        keep = [a for a in ACTIONS if a in set(acts) and (allowed is None or a in allowed.get(mod, set()))]
        if add_view and keep and "view" not in keep and (allowed is None or "view" in allowed.get(mod, {"view"})):
            keep.insert(0, "view")              # doing something always needs seeing it
        if keep:
            out[mod] = keep
    return out


def _load(s: Optional[str]) -> Dict[str, List[str]]:
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def combine(profile: Dict[str, List[str]], grants: Dict[str, List[str]], denies: Dict[str, List[str]]) -> Dict[str, List[str]]:
    out: Dict[str, Set[str]] = {}
    for src in (profile or {}, grants or {}):
        for mod, acts in src.items():
            out.setdefault(mod, set()).update(acts)
    for mod, acts in (denies or {}).items():
        if mod in out:
            out[mod] -= set(acts)
            if "view" in acts:
                out[mod] = set()               # no view means nothing in the module
    return {m: [a for a in ACTIONS if a in s] for m, s in out.items() if s}


def subset(a: Dict[str, List[str]], b: Dict[str, List[str]]) -> List[str]:
    """What `a` has that `b` does not, as readable 'Module: Action' items."""
    labels = {m[0]: m[1] for m in MODULES}
    out = []
    for mod, acts in a.items():
        extra = set(acts) - set(b.get(mod, []))
        for act in ACTIONS:
            if act in extra:
                out.append("%s: %s" % (labels.get(mod, mod), ACTION_LABELS[act]))
    return out


FULL = {m: list(ACTIONS) for m in MODULE_KEYS}
_SCOPE_RANK = {ASSIGNED: 0, TEAM: 1, WORKSPACE: 2}


# ── who gets what ───────────────────────────────────────────────────────────
def _is_admin_here(db: Session, user: User, org_id: str) -> bool:
    if getattr(user, "role", None) in ("god_admin", "super_admin"):
        return True
    try:
        from app.services.lead_scope import effective_role
        return (effective_role(user, db) or "").lower() in ("org_admin", "super_admin")
    except Exception:  # noqa: BLE001 - never widen
        return False


def access_row(db: Session, user_id: str, seat_scope: str) -> Optional[WholesaleMemberAccess]:
    return (db.query(WholesaleMemberAccess)
            .filter(WholesaleMemberAccess.seat_scope == seat_scope, WholesaleMemberAccess.user_id == user_id).first())


def effective(db: Session, user: User, org_id: str) -> Dict[str, Any]:
    """{mode: admin|legacy|custom|none, permissions, record_scope, profile}."""
    from app.services import wholesale_team as T
    if _is_admin_here(db, user, org_id):
        return {"mode": "admin", "permissions": FULL, "record_scope": WORKSPACE, "profile": None}
    scope = T.seat_key_for_org(db, org_id)
    role = T.role_of(db, user, org_id)
    if role is None:
        return {"mode": "none", "permissions": {}, "record_scope": ASSIGNED, "profile": None}
    row = access_row(db, user.id, scope)
    if row is None:
        return {"mode": "legacy", "permissions": FULL, "record_scope": WORKSPACE, "profile": None, "legacy_role": role}
    prof = db.query(WholesaleAccessProfile).filter(WholesaleAccessProfile.id == row.profile_id,
                                                   WholesaleAccessProfile.seat_scope == scope).first() if row.profile_id else None
    perms = combine(_load(prof.permissions_json) if prof else {}, _load(row.grants_json), _load(row.denies_json))
    rs = row.record_scope or (prof.record_scope if prof else ASSIGNED)
    return {"mode": "custom", "permissions": perms, "record_scope": rs if rs in SCOPES else ASSIGNED,
            "profile": {"id": prof.id, "name": prof.name} if prof else None}


def allows(eff: Dict[str, Any], module: str, action: str) -> bool:
    return action in (eff.get("permissions") or {}).get(module, [])


def ceiling(db: Session, actor: User, org_id: str) -> Tuple[Dict[str, List[str]], str]:
    """The most this actor may hand to anyone: their own permissions (minus team
    administration beyond what they hold) and their own record visibility."""
    eff = effective(db, actor, org_id)
    if eff["mode"] == "admin":
        return FULL, WORKSPACE
    if eff["mode"] == "custom" and allows(eff, "team_admin", "manage"):
        return eff["permissions"], eff["record_scope"]
    return {}, ASSIGNED


def check_delegation(db: Session, actor: User, org_id: str, perms: Dict[str, List[str]], record_scope: str) -> None:
    cap, cap_scope = ceiling(db, actor, org_id)
    over = subset(perms, cap)
    if over:
        raise PermissionError("You can't give access you don't have: " + ", ".join(over[:6]) + ("..." if len(over) > 6 else ""))
    if _SCOPE_RANK.get(record_scope, 0) > _SCOPE_RANK.get(cap_scope, 0):
        raise PermissionError("You can't give wider record visibility than your own.")


# ── the server fence for custom-access logins ───────────────────────────────
_SETTINGS_WRITABLE = ("/settings/profile", "/settings/profile-photo", "/settings/notifications")
_SHELL = ("/auth/", "/branding", "/notifications", "/settings", "/program/status", "/launch/me",
          "/workspace-views", "/work/identity", "/me", "/health", "/push", "/site-intake/wholesale",
          "/wholesale-invites")


def _under(path: str, prefixes) -> bool:
    for p in prefixes:
        base = p.rstrip("/")
        if path == base or path.startswith(base + "/"):
            return True
    return False


def refusal(db: Session, user: User, org_id: str, path: str, method: str) -> Optional[str]:
    """None when this custom-access request may go ahead, else why not.
    Only called for a person whose seat here has an access row."""
    path = path or "/"
    if _under(path, ("/settings",)):
        # The CRM side's own settings (Twilio numbers, products, booking page,
        # appointment types, social links) are not Wholesale. A custom login may
        # read its settings and change only its own profile, photo and alerts.
        if _under(path, ("/settings/admin",)):
            return "This login is for the Wholesale side only."
        if method.upper() in ("GET", "HEAD") or _under(path, _SETTINGS_WRITABLE):
            return None
        return "This login is for the Wholesale side only."
    if _under(path, _SHELL) or (method.upper() == "GET" and _under(path, ("/org-settings",))):
        return None
    if not path.startswith("/wholesale/"):
        return "This login is for the Wholesale side only."
    mod = module_for(path)
    if mod == "_self":
        return None
    if mod is None:
        return "Your access doesn't include this."
    eff = effective(db, user, org_id)
    act = action_for(path, method)
    if not allows(eff, mod, act):
        label = dict((m[0], m[1]) for m in MODULES).get(mod, mod)
        return "Your access doesn't include %s: %s. Ask the workspace owner." % (label, ACTION_LABELS.get(act, act))
    return _record_refusal(db, user, org_id, eff, mod, path)


def ended_refusal(path: str, method: str) -> Optional[str]:
    """For a login whose custom access here was suspended or removed."""
    if _under(path, ("/auth/", "/branding", "/health", "/wholesale-invites")):
        return None
    if method.upper() in ("GET", "HEAD") and _under(path, _SHELL) and not _under(path, ("/settings",)):
        return None
    return "Your access to this workspace's Wholesale team has been suspended or removed. Ask the workspace owner."


# Direct record addresses whose record carries an assignee.
_RECORD_PATHS = [
    (re.compile(r"^/wholesale/(?:ops/)?deals/([^/]+)"), "deal"),
    (re.compile(r"^/wholesale/intel/deal/([^/]+)"), "deal"),
    (re.compile(r"^/wholesale/properties/([^/]+)"), "property"),
    (re.compile(r"^/wholesale/exceptions/([^/]+)/"), "exception"),
    (re.compile(r"^/wholesale/appointments/([^/]+)"), "appointment"),
    (re.compile(r"^/wholesale/canvass/routes/([^/]+)"), "canvass"),
    (re.compile(r"^/wholesale/ops/callbacks/([^/]+)"), "callback"),
]


def _record_owner(db: Session, kind: str, org_id: str, rid: str):
    """(found, assigned_to_id) for a record in THIS workspace."""
    from app.models.wholesale_models import WholesaleDeal, WholesaleProperty, WholesaleWorkException
    from app.models.wholesale_ops_models import WholesaleCanvassRoute, WholesaleFieldAppointment, WholesaleSellerCallback
    model = {"deal": WholesaleDeal, "property": WholesaleProperty, "exception": WholesaleWorkException,
             "appointment": WholesaleFieldAppointment, "canvass": WholesaleCanvassRoute,
             "callback": WholesaleSellerCallback}[kind]
    row = db.query(model.assigned_to_id).filter(model.id == rid, model.organization_id == org_id).first()
    return (row is not None, row[0] if row is not None else None)


# Records that have no assignee of their own but belong to one that does
# (a deal's approvals, offers, documents...). They are visible exactly when
# their owning deal / property / route is.
_CHILD_PATHS = [
    (re.compile(r"^/wholesale/approvals/([^/]+)"), "approval"),
    (re.compile(r"^/wholesale/offers/([^/]+)"), "offer"),
    (re.compile(r"^/wholesale/comps/([^/]+)"), "comp"),
    (re.compile(r"^/wholesale/documents/([^/]+)"), "document"),
    (re.compile(r"^/wholesale/outreach/([^/]+)"), "outreach"),
    (re.compile(r"^/wholesale/share-links/([^/]+)"), "share_link"),
    (re.compile(r"^/wholesale/files/([^/]+)"), "file"),
    (re.compile(r"^/wholesale/sellers/([^/]+)"), "seller"),
    (re.compile(r"^/wholesale/canvass/doors/([^/]+)"), "door"),
    (re.compile(r"^/wholesale/ops/notes/([^/]+)"), "note"),
    (re.compile(r"^/wholesale/ops/leads/([^/]+)"), "lead"),
    (re.compile(r"^/wholesale/evosense/follow-ups/([^/]+)"), "followup"),
]


def _child_owners(db: Session, kind: str, org_id: str, rid: str):
    """(found, [owner of each assigned parent]) for a child record in THIS
    workspace. An empty list means it hangs off nothing that has an owner."""
    from app.models.wholesale_models import (WholesaleApproval, WholesaleBuyerOutreach, WholesaleComp, WholesaleDeal,
                                             WholesaleDocument, WholesaleFile, WholesaleOffer, WholesaleProperty,
                                             WholesaleSellerProfile)
    from app.models.wholesale_ops_models import (WholesaleCanvassDoor, WholesaleCanvassRoute, WholesaleDealNote,
                                                 WholesaleSellerCallback)
    from app.models.wholesale_share_models import WholesaleShareLink
    from app.models.evosense_models import EvoSenseFollowUp

    def owners(model, ids):
        ids = [i for i in ids if i]
        if not ids:
            return []
        return [r[0] for r in db.query(model.assigned_to_id).filter(model.id.in_(ids), model.organization_id == org_id)]

    if kind == "followup":
        row = db.query(EvoSenseFollowUp.assigned_to_id).filter(EvoSenseFollowUp.id == rid,
                                                               EvoSenseFollowUp.organization_id == org_id).first()
        return (row is not None, [row[0]] if row is not None and row[0] else [])
    if kind == "lead":
        deals = [r[0] for r in db.query(WholesaleDeal.assigned_to_id).filter(WholesaleDeal.organization_id == org_id,
                                                                             WholesaleDeal.seller_lead_id == rid)]
        cbs = [r[0] for r in db.query(WholesaleSellerCallback.assigned_to_id).filter(
            WholesaleSellerCallback.organization_id == org_id, WholesaleSellerCallback.lead_id == rid)]
        return (True, deals + [c for c in cbs if c])
    model, deal_col, prop_col, route_col = {
        "approval": (WholesaleApproval, "deal_id", None, None),
        "offer": (WholesaleOffer, "deal_id", None, None),
        "comp": (WholesaleComp, "deal_id", None, None),
        "document": (WholesaleDocument, "deal_id", None, None),
        "outreach": (WholesaleBuyerOutreach, "deal_id", None, None),
        "share_link": (WholesaleShareLink, "deal_id", None, None),
        "file": (WholesaleFile, "deal_id", "property_id", None),
        "seller": (WholesaleSellerProfile, None, "property_id", None),
        "door": (WholesaleCanvassDoor, None, None, "route_id"),
        "note": (WholesaleDealNote, "deal_id", "property_id", None),
    }[kind]
    row = db.query(model).filter(model.id == rid, model.organization_id == org_id).first()
    if row is None:
        return (False, [])
    out = []
    if deal_col:
        out += owners(WholesaleDeal, [getattr(row, deal_col, None)])
    if prop_col:
        out += owners(WholesaleProperty, [getattr(row, prop_col, None)])
    if route_col:
        out += owners(WholesaleCanvassRoute, [getattr(row, route_col, None)])
    return (True, out)


def _record_refusal(db, user, org_id, eff, mod, path) -> Optional[str]:
    if eff.get("record_scope") == WORKSPACE or mod not in SCOPED_MODULES:
        return None
    for rx, kind in _CHILD_PATHS:
        m = rx.match(path)
        if not m:
            continue
        rid = m.group(1)
        if not _ID_SEG.match(rid):
            break                              # a named sub-collection, not a record id
        found, parent_owners = _child_owners(db, kind, org_id, rid)
        if not found:
            return None                        # the route answers its own 404
        # A child of an assigned record is visible when ANY owning record is;
        # one that has a parent but no visible parent is refused (an unassigned
        # deal is as closed to an assigned-only login as it is directly).
        if not parent_owners:
            return None                        # hangs off nothing with an owner
        visible = visible_user_ids(db, org_id, user, eff)
        if visible is not None and not any(o in visible for o in parent_owners):
            return "This record isn't assigned to you or your team."
        return None
    for rx, kind in _RECORD_PATHS:
        m = rx.match(path)
        if not m:
            continue
        rid = m.group(1)
        if not _ID_SEG.match(rid):
            return None                        # a named sub-collection, not a record id
        found, owner = _record_owner(db, kind, org_id, rid)
        if not found:
            return None                        # the route answers its own 404
        visible = visible_user_ids(db, org_id, user, eff)
        if visible is not None and owner not in visible:
            return "This record isn't assigned to you or your team."
        return None
    return None


def visible_user_ids(db: Session, org_id: str, user: User, eff: Optional[Dict[str, Any]] = None) -> Optional[Set[str]]:
    """Whose records this person may see: None = everyone in the workspace."""
    eff = eff or effective(db, user, org_id)
    if eff["mode"] != "custom":
        return None
    if eff["record_scope"] == WORKSPACE:
        return None
    if eff["record_scope"] == ASSIGNED:
        return {user.id}
    from app.models.sales_models import Membership, SCOPE_PRODUCT
    from app.services import wholesale_team as T
    rows = (db.query(Membership.user_id, Membership.reports_to_user_id)
            .filter(Membership.scope_type == SCOPE_PRODUCT, Membership.scope_id == T.seat_key_for_org(db, org_id),
                    Membership.is_active.is_(True)).all())
    kids: Dict[str, List[str]] = {}
    for uid, boss in rows:
        if boss:
            kids.setdefault(boss, []).append(uid)
    seen, todo = {user.id}, [user.id]
    while todo:
        for k in kids.get(todo.pop(), []):
            if k not in seen:
                seen.add(k)
                todo.append(k)
    return seen


def scope_query(q, column, db: Session, org_id: str, user: User):
    """Narrow a list query to the records this person may see (by assignee)."""
    visible = visible_user_ids(db, org_id, user)
    if visible is None:
        return q
    return q.filter(column.in_(list(visible)))


# ── profiles ────────────────────────────────────────────────────────────────
def profile_json(db: Session, p: WholesaleAccessProfile) -> Dict[str, Any]:
    members = (db.query(WholesaleMemberAccess.user_id)
               .filter(WholesaleMemberAccess.seat_scope == p.seat_scope, WholesaleMemberAccess.profile_id == p.id).count())
    return {"id": p.id, "name": p.name, "description": p.description, "template_key": p.template_key,
            "permissions": _load(p.permissions_json), "record_scope": p.record_scope,
            "is_archived": bool(p.is_archived), "version": p.version, "members": members,
            "updated_at": p.updated_at.isoformat() + "Z" if p.updated_at else None}


def now() -> datetime:
    return datetime.utcnow()
