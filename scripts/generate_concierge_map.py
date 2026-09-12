#!/usr/bin/env python3
"""Generate the AdvisorFlow machine concierge map from the current source tree.

WHAT THIS IS
    A retrieval index over the IMPLEMENTED product, written for a machine to
    read: pages, navigation, interactive actions, API endpoints, workflow
    edges, permissions, integrations and feature gates -- one JSON object per
    line in docs/concierge/advisorflow_concierge_map.jsonl.

WHAT THIS IS NOT
    Documentation prose, a roadmap, or a wish list. Nothing here is asserted
    unless it was read out of the source tree. Anything that could not be
    proven is emitted with status "unresolved" and listed in the manifest's
    known_gaps rather than guessed.

DEPENDENCIES
    Standard library only. This script imports NOTHING from app/ or
    frontend/ and must never be imported by the application. It reads source
    files as text and parses them; it does not execute them.

USAGE
    python scripts/generate_concierge_map.py
    python scripts/generate_concierge_map.py --out docs/concierge
"""

from __future__ import annotations

import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FE = os.path.join(ROOT, "frontend", "src")
APP = os.path.join(ROOT, "app")

SCHEMA_VERSION = "1.0"


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def git(*args):
    try:
        out = subprocess.run(["git", "-C", ROOT] + list(args),
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# The concierge deliverables are this script's own OUTPUT, and the two
# scripts import nothing from the application, so neither can change what the
# map describes. "git_dirty" is meant to answer "was the code I read
# uncommitted?", so those four paths are excluded from it deliberately.
SELF_PATHS = (
    "docs/concierge/advisorflow_concierge_map.jsonl",
    "docs/concierge/advisorflow_concierge_manifest.json",
    "scripts/generate_concierge_map.py",
    "scripts/validate_concierge_map.py",
)


def code_is_dirty():
    lines = [l for l in git("status", "--porcelain").splitlines() if l.strip()]
    for line in lines:
        path = line[3:].strip().strip('"')
        if path.startswith("docs/concierge/"):
            continue
        if path in SELF_PATHS:
            continue
        return True
    return False


def read(path):
    # utf-8-sig: several router modules carry a UTF-8 BOM, which ast.parse
    # rejects outright. Reading them as utf-8 would drop those routers from
    # the map entirely.
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read()


# ===========================================================================
# surfaces
# ===========================================================================

SURFACES = [
    "public_web", "authentication", "god_mode", "executive", "brand_sales",
    "sales_workspace", "customer_workspace", "finance_compensation",
    "billing", "support", "ai_workforce", "customer_launch",
    "shared_platform", "unknown",
]

PUBLIC_ROUTE_PREFIXES = (
    "/deal-room/", "/demo/", "/portal/access/", "/portal/view/",
    "/book/", "/survey/", "/appointments/confirm/", "/activate",
    "/setup-integrations", "/onboarding",
)
AUTH_ROUTES = {"/login", "/change-password", "/workspaces"}


def route_surface(path):
    p = path or ""
    if p in AUTH_ROUTES:
        return "authentication"
    for pre in PUBLIC_ROUTE_PREFIXES:
        if p.startswith(pre):
            return "public_web"
    if p.startswith("/god"):
        return "god_mode"
    if p.startswith("/executive"):
        return "executive"
    if p.startswith("/sales") or p == "/demo-suite" or p.startswith("/demo-suite/"):
        return "sales_workspace"
    if p.startswith("/launch"):
        return "customer_launch"
    if p.startswith("/commercial"):
        return "finance_compensation"
    if p == "/billing":
        return "billing"
    if p == "/help":
        return "support"
    if p.startswith("/ai-"):
        return "ai_workforce"
    if p.startswith("/training") or p == "/demo":
        return "shared_platform"
    return "customer_workspace"


API_SURFACE_RULES = (
    ("/god", "god_mode"),
    ("/executive", "executive"),
    ("/exec", "executive"),
    ("/sales", "sales_workspace"),
    ("/launch", "customer_launch"),
    ("/launch-experience", "customer_launch"),
    ("/commercial", "finance_compensation"),
    ("/compensation", "finance_compensation"),
    ("/billing", "billing"),
    ("/support", "support"),
    ("/workforce", "ai_workforce"),
    ("/ai-operations", "ai_workforce"),
    ("/ai-deployment", "ai_workforce"),
    ("/ai", "ai_workforce"),
    ("/auth", "authentication"),
    ("/webhooks", "shared_platform"),
    ("/health", "shared_platform"),
    ("/demo-suite", "sales_workspace"),
    ("/demo", "shared_platform"),
    ("/training", "shared_platform"),
    ("/setup", "authentication"),
    ("/integrations", "shared_platform"),
    ("/platform", "god_mode"),
    ("/customers", "god_mode"),
    ("/me", "customer_workspace"),
)


def api_surface(path, auth_required, roles):
    p = path or ""
    for pre, surf in API_SURFACE_RULES:
        if p == pre or p.startswith(pre + "/") or p.startswith(pre + "{"):
            return surf
    if not auth_required:
        return "public_web"
    if roles == ["god_admin"]:
        return "god_mode"
    return "customer_workspace"


# ===========================================================================
# authorization vocabulary (mirrors app/deps.py and the services named below)
# ===========================================================================

DEP_ROLES = {
    "require_god": ["god_admin"],
    "require_super_admin": ["super_admin", "god_admin"],
    "require_admin": ["org_admin", "super_admin", "god_admin"],
    "require_brand_executive": ["brand_executive", "god_admin"],
    "require_sales_member": ["sales_rep", "sales_manager", "god_admin"],
    "require_sales_manager": ["sales_manager", "god_admin"],
    "require_tenant_user": [],
    "require_tenant_or_observer": [],
    "require_not_observation": [],
    "get_current_user": [],
    "require_commercial_approver": ["commercial_approver", "god_admin"],
}

DEP_SCOPE = {
    "require_god": "platform",
    "require_super_admin": "platform",
    "require_admin": "org",
    "require_brand_executive": "brand",
    "require_sales_member": "brand",
    "require_sales_manager": "brand",
    "require_tenant_user": "org",
    "require_tenant_or_observer": "org",
    "require_not_observation": "org",
    "get_current_user": "user",
}

AUTH_DEPS = set(DEP_ROLES) | {"require_capability", "require_feature",
                              "require_feature_capability", "oauth2_scheme"}

DOMAIN_WORDS = {
    "leads": "Lead", "lead": "Lead", "organizations": "Organization",
    "orgs": "Organization", "customers": "CustomerOrganization",
    "opportunities": "Opportunity", "proposals": "Proposal",
    "cadence": "Cadence", "cadences": "Cadence", "appointments": "Appointment",
    "availability": "Availability", "calendar": "CalendarConnection",
    "users": "User", "access": "CapabilityGrant", "billing": "Subscription",
    "compensation": "CompensationLedger", "implementations": "Implementation",
    "launch": "LaunchIntake", "workforce": "AIEmployee",
    "support": "SupportTicket", "brands": "Platform", "platform": "Platform",
    "workspaces": "Workspace", "campaigns": "Campaign",
    "templates": "Template", "imports": "ImportBatch",
    "import": "ImportBatch", "sms": "Message", "email": "Message",
    "voice": "VoiceCall", "contacts": "Contact", "crm": "CRMRecord",
    "compliance": "Suppression", "audit": "AuditLog",
    "commercial": "CommercialAgreement", "tier-definitions": "TierDefinition",
    "demo-suite": "DemoEnvironment", "training": "TrainingAssignment",
    "pipeline": "PipelineStage", "replies": "Reply", "activity": "ActivityEvent",
}


def domain_objects_for(path):
    out = []
    for seg in (path or "").strip("/").split("/"):
        if not seg or seg.startswith("{"):
            continue
        name = DOMAIN_WORDS.get(seg)
        if name and name not in out:
            out.append(name)
    return out[:4]


# ===========================================================================
# frontend: route table
# ===========================================================================

GUARD_COMPONENTS = {
    "ProtectedRoute", "GodRoute", "SalesRoute", "ExecutiveRoute",
    "LaunchRoute", "GodCustomerAppRoute", "WorkspaceRoute",
}
LAYOUT_COMPONENTS = {
    "Layout", "GodModeLayout", "GodShell", "ExecutiveSuite", "SalesShell",
    "LaunchBoundary",
}
NOISE_COMPONENTS = {"Navigate", "ContextBanner", "DemoBanner", "Route",
                    "Routes", "BrowserRouter", "ToastProvider"}

GUARD_SEMANTICS = {
    "ProtectedRoute": {
        "roles": [], "scope": "org",
        "note": "authenticated + password changed; renders a refusal screen "
                "inside Layout when a require* flag is not met",
    },
    "GodRoute": {
        "roles": ["god_admin"], "scope": "platform",
        "note": "role must be god_admin; anything else renders NotFound, which "
                "deliberately does not disclose that a platform area exists",
    },
    "GodCustomerAppRoute": {
        "roles": ["god_admin"], "scope": "org",
        "note": "god_admin plus a selected organization context (sent as "
                "X-Org-Override); redirects to /god/customers with no context. "
                "No membership is created and God authority is preserved",
    },
    "SalesRoute": {
        "roles": [], "scope": "brand",
        "note": "client guard is authentication only; authorization is "
                "server-side in app/services/sales_access.py and SalesShell "
                "refuses on GET /sales/me",
    },
    "ExecutiveRoute": {
        "roles": [], "scope": "brand",
        "note": "client guard is authentication only; GET /executive/context "
                "returns 403 without a brand_executive grant",
    },
    "LaunchRoute": {
        "roles": [], "scope": "org",
        "note": "client guard is authentication only; which customer's intake "
                "is served is decided server-side from the session -- /launch "
                "takes no organization id in the URL",
    },
    "WorkspaceRoute": {
        "roles": [], "scope": "workspace",
        "note": "resolves membership from GET /auth/my-contexts and, when the "
                "workspace is not listed, GET /auth/workspace/{id}; god_admin "
                "is deliberately NOT exempt",
    },
}

ROUTE_FLAGS = ("requireGodAdmin", "requireSuperAdmin", "requireAdmin")
FLAG_ROLES = {
    "requireGodAdmin": ["god_admin"],
    "requireSuperAdmin": ["super_admin", "god_admin"],
    "requireAdmin": ["org_admin", "super_admin", "god_admin"],
}

APP_LOCAL_COMPONENTS = {
    "HomeRedirect", "WorkspaceSelector", "WorkspaceRoute",
    "GodCustomerAppRoute", "ProtectedRoute", "GodRoute", "SalesRoute",
    "ExecutiveRoute", "LaunchRoute", "GodModeLayout",
}


def opening_tag(text, start):
    i, depth = start, 0
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif c == ">" and depth == 0:
            return text[start:i + 1]
        i += 1
    return text[start:]


def parse_app_routes():
    path = os.path.join(FE, "App.jsx")
    text = read(path)
    src = rel(path)
    out = []
    for m in re.finditer(r"<Route\b", text):
        tag = opening_tag(text, m.start())
        line = text[:m.start()].count("\n") + 1
        mp = re.search(r'path=\{?["\']([^"\']+)["\']', tag)
        if not mp:
            continue
        route = mp.group(1)
        comps = [c for c in re.findall(r"<([A-Z][A-Za-z0-9_]*)", tag) if c != "Route"]
        guard = next((c for c in comps if c in GUARD_COMPONENTS), None)
        layout = next((c for c in comps if c in LAYOUT_COMPONENTS), None)
        page = next((c for c in comps
                     if c not in GUARD_COMPONENTS
                     and c not in LAYOUT_COMPONENTS
                     and c not in NOISE_COMPONENTS), None)
        flags = [f for f in ROUTE_FLAGS if re.search(r"\b%s\b" % f, tag)]
        redirect = None
        if page is None and "Navigate" in comps:
            mr = re.search(r'<Navigate\s+to=\{?["\']([^"\']+)["\']', tag)
            redirect = mr.group(1) if mr else None
        # A route whose whole element IS the guard (WorkspaceRoute,
        # GodCustomerAppRoute) has no separate page component: the guard
        # decides access and then renders the tenant application itself.
        if page is None and guard and not redirect:
            page = guard
        out.append({
            "route": route, "line": line, "page_component": page,
            "guard": guard, "layout": layout, "flags": flags,
            "redirect_to": redirect,
            "params": re.findall(r":([A-Za-z0-9_]+)", route),
            "source": src,
        })
    return out


def parse_app_imports():
    text = read(os.path.join(FE, "App.jsx"))
    imports = {}
    for m in re.finditer(r"import\s+([A-Za-z0-9_{},\s*]+?)\s+from\s+['\"]([^'\"]+)['\"]", text):
        for name in re.findall(r"[A-Za-z0-9_]+", m.group(1)):
            if name and name[0].isupper():
                imports[name] = m.group(2)
    for m in re.finditer(r"const\s+([A-Z][A-Za-z0-9_]*)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\(['\"]([^'\"]+)['\"]", text):
        imports[m.group(1)] = m.group(2)
    return imports


def resolve_specifier(spec, from_file):
    if not spec or not spec.startswith("."):
        return None
    base = os.path.normpath(os.path.join(os.path.dirname(from_file), spec))
    for cand in (base + ".jsx", base + ".js",
                 os.path.join(base, "index.jsx"), os.path.join(base, "index.js")):
        if os.path.isfile(cand):
            return cand
    return None


def local_imports(path):
    text = read(path)
    out = []
    for pat in (r"from\s+['\"](\.[^'\"]+)['\"]", r"import\(\s*['\"](\.[^'\"]+)['\"]\s*\)"):
        for m in re.finditer(pat, text):
            f = resolve_specifier(m.group(1), path)
            if f and os.path.commonpath([f, FE]) == FE:
                out.append(f)
    seen, uniq = set(), []
    for f in out:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    return uniq


# ===========================================================================
# frontend: navigation arrays
# ===========================================================================

NAV_SOURCES = [
    (os.path.join(FE, "components", "Layout.jsx"), "NAV_GROUPS",
     "customer_workspace", "tenant_sidebar"),
    (os.path.join(FE, "components", "Layout.jsx"), "SUPER_ADMIN_NAV_ITEMS",
     "shared_platform", "tenant_sidebar_platform_admin"),
    (os.path.join(FE, "pages", "GodShell.jsx"), "NAV", "god_mode", "god_rail"),
    (os.path.join(FE, "pages", "GodShell.jsx"), "JUMP", "god_mode",
     "god_rail_jump_to"),
    (os.path.join(FE, "pages", "sales", "SalesShell.jsx"), "NAV",
     "sales_workspace", "sales_rail_my_work"),
    (os.path.join(FE, "pages", "sales", "SalesShell.jsx"), "REP_ONLY_NAV",
     "sales_workspace", "sales_rail_rep_only"),
    (os.path.join(FE, "pages", "sales", "SalesShell.jsx"), "MANAGER_NAV",
     "sales_workspace", "sales_rail_my_team"),
    (os.path.join(FE, "pages", "executive", "ExecutiveSuite.jsx"), "NAV",
     "executive", "executive_rail"),
]

NAV_KEYS = ("to", "path", "label", "group", "icon", "permission", "capability",
            "featureKey", "adminOnly", "launchOnly", "fiberOnly", "soon",
            "end", "countKey", "action", "hint", "badge", "external")


def balanced(text, start, open_ch, close_ch):
    depth, i, in_str = 0, start, None
    while i < len(text):
        c = text[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "'\"`":
            in_str = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def strip_comments(text):
    # LINE COMMENTS FIRST, DELIBERATELY. A line comment in this codebase can
    # contain a path such as /proposals/*, and stripping block comments first
    # would treat that as an opening /* and delete everything up to the next
    # */ — which is how three whole sidebar groups went missing.
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def objects_at_top_level(body):
    i = 0
    while i < len(body):
        if body[i] == "{":
            end = balanced(body, i, "{", "}")
            yield body[i:end]
            i = end
        else:
            i += 1


def parse_object_literal(src):
    obj = {}
    for key in NAV_KEYS:
        m = re.search(r"\b%s\s*:\s*(?:(['\"])(.*?)\1|(true|false|null)|([A-Za-z0-9_.]+))"
                      % re.escape(key), src, re.S)
        if not m:
            continue
        if m.group(2) is not None:
            obj[key] = m.group(2)
        elif m.group(3) is not None:
            obj[key] = {"true": True, "false": False, "null": None}[m.group(3)]
        else:
            obj[key] = m.group(4)
    return obj


def parse_nav_arrays():
    items = []
    for path, name, surface, container in NAV_SOURCES:
        if not os.path.isfile(path):
            continue
        text = strip_comments(read(path))
        m = re.search(r"const\s+%s\s*=\s*\[" % re.escape(name), text)
        if not m:
            continue
        start = text.index("[", m.start())
        body = text[start:balanced(text, start, "[", "]")]
        section = None
        for lit in objects_at_top_level(body[1:-1]):
            obj = parse_object_literal(lit)
            mi = re.search(r"\bitems\s*:\s*\[", lit)
            if mi:
                section = obj.get("label") or obj.get("group") or section
                inner_start = lit.index("[", mi.start())
                inner = lit[inner_start:balanced(lit, inner_start, "[", "]")]
                for sub in objects_at_top_level(inner[1:-1]):
                    sobj = parse_object_literal(sub)
                    sobj.update({"_section": section, "_container": container,
                                 "_surface": surface, "_source": rel(path)})
                    items.append(sobj)
                continue
            if obj.get("group") and not obj.get("label"):
                section = obj["group"]
                continue
            if not obj.get("label"):
                continue
            obj.update({"_section": section, "_container": container,
                        "_surface": surface, "_source": rel(path)})
            items.append(obj)
    return items


# ===========================================================================
# frontend: interactive controls
# ===========================================================================

STR_LIT = r"`(?:[^`\\]|\\.)*`|'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
CLIENT_CALL_RE = re.compile(r"\bapi\.(get|post|put|patch|delete)\(", re.I)
NAVIGATE_RE = re.compile(r"\bnavigate\(\s*(?:`([^`]*)`|'([^']*)'|\"([^\"]*)\")")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

TRIVIAL_LABELS = {
    "", "x", "close", "back", "cancel", "dismiss", "ok",
    "prev", "previous", "next", "...", "?", "+", "-",
    "show", "hide", "expand", "collapse", "more", "less",
    "copy", "copied", "refresh", "reload", "clear", "done", "got it",
    "loading", "saving", "try again",
}

ALWAYS_KEEP = {
    "enter", "launch", "activate", "deactivate", "approve", "reject",
    "submit", "save", "send", "start", "stop", "pause", "resume",
    "archive", "restore", "publish", "provision", "connect", "disconnect",
    "kill switch", "impersonate", "return to god mode", "exit",
    "send onboarding", "find team time", "new appointment", "customize deal",
    "send proposal", "add user", "manage access", "import leads",
    "start cadence", "create lead", "new organization", "reset password",
}


def first_arg_expr(text, open_paren):
    end = balanced(text, open_paren, "(", ")")
    inner = text[open_paren + 1:end - 1]
    depth, in_str, i = 0, None, 0
    while i < len(inner):
        c = inner[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "'\"`":
            in_str = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            return inner[:i]
        i += 1
    return inner


def client_path(expr):
    """Turn an api.* first argument into a server path pattern, or None."""
    expr = (expr or "").strip()
    if not expr:
        return None
    parts, i, literal_seen = [], 0, False
    while i < len(expr):
        m = re.match(STR_LIT, expr[i:], re.S)
        if m:
            body = m.group(0)[1:-1]
            body = re.sub(r"\$\{[^}]*\}", "{}", body)
            parts.append(body)
            literal_seen = True
            i += len(m.group(0))
            continue
        m2 = re.match(r"\s*\+\s*", expr[i:])
        if m2:
            i += m2.end()
            continue
        m3 = re.match(r"[^+]+", expr[i:])
        if m3:
            parts.append("{}")
            i += m3.end()
            continue
        i += 1
    if not literal_seen:
        return None
    path = "".join(parts).split("?")[0]
    path = re.sub(r"(\{\})+", "{}", path)
    if not path.startswith("/"):
        return None
    path = re.sub(r"//+", "/", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path or None


def pattern_of(path):
    p = re.sub(r"\{[^}/]*\}", "{}", path or "")
    if len(p) > 1 and p.endswith("/"):
        p = p[:-1]
    return p


def api_calls_in(text):
    out = []
    for m in CLIENT_CALL_RE.finditer(text):
        method = m.group(1).upper()
        paren = text.index("(", m.start())
        p = client_path(first_arg_expr(text, paren))
        if p:
            out.append({"method": method, "path": p})
    return out


def navigates_in(text):
    return [next(g for g in m.groups() if g is not None)
            for m in NAVIGATE_RE.finditer(text)]


def function_bodies(text):
    bodies = {}
    patterns = [
        r"(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\(",
        r"const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\(",
        r"const\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?[A-Za-z0-9_]+\s*=>",
        r"const\s+([A-Za-z0-9_]+)\s*=\s*useCallback\(",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            name = m.group(1)
            if name in bodies:
                continue
            arrow = text.find("=>", m.end())
            brace = text.find("{", m.end() - 1)
            if arrow != -1 and (brace == -1 or arrow < brace):
                brace = text.find("{", arrow)
            if brace == -1 or brace - m.end() > 400:
                continue
            bodies[name] = text[brace:balanced(text, brace, "{", "}")]
    return bodies


def effects_of(body):
    return {
        "api_calls": api_calls_in(body),
        "navigates": navigates_in(body),
        "confirm": bool(re.search(r"window\.confirm\(|\bconfirm\s*\(|setConfirm\(|"
                                  r"confirmRequired|ConfirmDialog", body)),
    }


def jsx_text(inner):
    plain = re.sub(r"<[^>]*>", " ", inner)
    braces = re.findall(r"\{([^{}]*)\}", plain)
    plain_wo = re.sub(r"\{[^{}]*\}", " ", plain)
    plain_wo = plain_wo.replace("&nbsp;", " ").replace("&amp;", "&")
    text = " ".join(plain_wo.split())
    if text:
        return text
    cands = []
    for b in braces:
        for m in re.finditer(r"'([^']{2,60})'|\"([^\"]{2,60})\"", b):
            cands.append(next(g for g in m.groups() if g is not None))
    cands = [c.strip() for c in cands if c.strip()
             and c.strip().lower() not in TRIVIAL_LABELS]
    return max(cands, key=len) if cands else ""


CONTROL_TAGS = (("button", "button"), ("Link", "link"), ("NavLink", "link"),
                ("a", "link"))


def prop_bindings(text):
    """`onFoo={expr}` attribute values, so a control whose handler is a
    callback PROP can still be traced when the parent that supplies it lives
    in the same module — which in this codebase it usually does."""
    out = {}
    for m in re.finditer(r"\b(on[A-Z][A-Za-z0-9_]*)=\{", text):
        st = text.index("{", m.start())
        expr = text[st:balanced(text, st, "{", "}")]
        out.setdefault(m.group(1), []).append(expr)
    return out


def enclosing_form_submit(text, pos):
    """onSubmit expression of the nearest <form> opening before `pos`."""
    idx = text.rfind("<form", 0, pos)
    if idx == -1 or pos - idx > 8000:
        return ""
    head = opening_tag(text, idx)
    m = re.search(r"onSubmit=\{", head)
    if not m:
        return ""
    st = head.index("{", m.start())
    return head[st:balanced(head, st, "{", "}")]


def parse_controls(path):
    text = read(path)
    resolved = {n: effects_of(b) for n, b in function_bodies(text).items()}
    props = prop_bindings(text)
    controls = []

    def onclick_effect(expr, depth=0):
        eff = effects_of(expr)
        eff["delegates_to"] = []
        names = [m.group(1) for m in
                 re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", expr)]
        bare = expr.strip().strip("{}").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", bare):
            names.append(bare)
        for nm in names:
            if nm in resolved:
                eff["api_calls"] += resolved[nm]["api_calls"]
                eff["navigates"] += resolved[nm]["navigates"]
                eff["confirm"] = eff["confirm"] or resolved[nm]["confirm"]
        # Handler supplied as a callback prop: follow the parent's binding.
        if not eff["api_calls"] and not eff["navigates"] and depth < 2:
            for nm in names:
                if nm in resolved or not nm.startswith(("on", "act", "handle")):
                    continue
                for cand in props.get(nm, [])[:4]:
                    sub = onclick_effect(cand, depth + 1)
                    eff["api_calls"] += sub["api_calls"]
                    eff["navigates"] += sub["navigates"]
                    eff["confirm"] = eff["confirm"] or sub["confirm"]
                    if sub["api_calls"] or sub["navigates"]:
                        eff["delegates_to"].append(nm)
                if nm in props and nm not in eff["delegates_to"]:
                    eff["delegates_to"].append(nm)
        return eff

    for tagname, kind in CONTROL_TAGS:
        for m in re.finditer(r"<%s(\s|>)" % re.escape(tagname), text):
            head = opening_tag(text, m.start())
            label = ""
            if not head.rstrip().endswith("/>"):
                close = text.find("</%s>" % tagname, m.start() + len(head))
                if close != -1 and close - m.start() < 6000:
                    label = jsx_text(text[m.start() + len(head):close])
            if not label:
                ml = re.search(r'(?:aria-label|title)=\{?["\']([^"\']{2,60})["\']', head)
                label = ml.group(1) if ml else ""
            label = label.strip()
            if not label or len(label) > 70:
                continue
            onclick = ""
            for attr in ("onClick", "onSubmit"):
                mo = re.search(r"%s=\{" % attr, head)
                if mo and not onclick:
                    st = head.index("{", mo.start())
                    onclick = head[st:balanced(head, st, "{", "}")]
            is_submit = bool(re.search(r'type=\{?["\']submit', head))
            if is_submit and not onclick:
                onclick = enclosing_form_submit(text, m.start())
            mt = re.search(r'to=\{?["\']([^"\']+)["\']', head)
            href = re.search(r'href=\{?["\']([^"\']+)["\']', head)
            eff = onclick_effect(onclick) if onclick else {
                "api_calls": [], "navigates": [], "confirm": False,
                "delegates_to": []}
            element_type = kind
            if is_submit:
                element_type = "form_submit"
            elif re.search(r'role=\{?["\']menuitem', head):
                element_type = "menu_item"
            elif re.search(r'role=\{?["\']tab', head):
                element_type = "tab"
            controls.append({
                "label": label,
                "element_type": element_type,
                "onclick": " ".join(onclick.split())[:400],
                "to": mt.group(1) if mt else None,
                "href": href.group(1) if href else None,
                "api_calls": eff["api_calls"],
                "navigates": eff["navigates"],
                "confirm": eff["confirm"],
                "delegates_to": sorted(set(eff.get("delegates_to") or [])),
                "line": text[:m.start()].count("\n") + 1,
            })
    return controls


def keep_control(c):
    low = c["label"].lower().strip(" .!")
    if low in TRIVIAL_LABELS:
        return False
    if low in ALWAYS_KEEP:
        return True
    if c["api_calls"] or c["to"] or c["navigates"]:
        return True
    return False


# ===========================================================================
# backend: routers and endpoints
# ===========================================================================

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")


def unparse(node):
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def dep_descriptor(node):
    inner = node
    if isinstance(node, ast.Call) and unparse(node.func).endswith("Depends"):
        inner = node.args[0] if node.args else node
    name, arg = "", None
    if isinstance(inner, ast.Call):
        name = unparse(inner.func).split(".")[-1]
        if inner.args:
            arg = const_str(inner.args[0])
    else:
        name = unparse(inner).split(".")[-1]
    return {"name": name, "arg": arg}


class Module:
    """One app/routers/*.py file, parsed once."""

    def __init__(self, dotted, path):
        self.dotted = dotted
        self.path = path
        self.src = read(path)
        self.routers = {}
        self.includes = []
        self.aliases = {}
        self.endpoints = []
        self.ok = True
        try:
            self.tree = ast.parse(self.src)
        except SyntaxError:
            self.tree = None
            self.ok = False
            return
        self._routers()
        self._imports()
        self._includes()
        self._endpoints()

    def _routers(self):
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            val = node.value
            if not (isinstance(val, ast.Call) and unparse(val.func).endswith("APIRouter")):
                continue
            prefix, tags, deps = "", [], []
            for kw in val.keywords:
                if kw.arg == "prefix":
                    prefix = const_str(kw.value) or ""
                elif kw.arg == "tags" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    tags = [const_str(e) for e in kw.value.elts if const_str(e)]
                elif kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    deps = [dep_descriptor(e) for e in kw.value.elts]
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    self.routers[tgt.id] = {"prefix": prefix, "tags": tags,
                                            "dependencies": deps}

    def _imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.routers"):
                mod = node.module
                for a in node.names:
                    local = a.asname or a.name
                    if mod == "app.routers":
                        self.aliases[local] = ("app.routers.%s" % a.name, None)
                    else:
                        self.aliases[local] = (mod, a.name)

    def _includes(self):
        for node in ast.walk(self.tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "include_router" and node.args):
                continue
            parent = unparse(node.func.value)
            if parent not in self.routers:
                continue
            prefix, deps = "", []
            for kw in node.keywords:
                if kw.arg == "prefix":
                    prefix = const_str(kw.value) or ""
                elif kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    deps = [dep_descriptor(e) for e in kw.value.elts]
            self.includes.append({"parent": parent,
                                  "target": unparse(node.args[0]),
                                  "prefix": prefix, "deps": deps,
                                  "line": node.lineno})

    def _endpoints(self):
        models = {n.name for n in ast.walk(self.tree)
                  if isinstance(n, ast.ClassDef)
                  and any("BaseModel" in unparse(b) for b in n.bases)}
        for node in ast.walk(self.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            paths, deco_deps = [], []
            response_model = status_code = None
            include, holder = True, None
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fn = dec.func
                if not isinstance(fn, ast.Attribute) or fn.attr not in HTTP_METHODS:
                    continue
                h = unparse(fn.value)
                if h not in self.routers:
                    continue
                holder = h
                p = const_str(dec.args[0]) if dec.args else ""
                paths.append((fn.attr.upper(), p or ""))
                for kw in dec.keywords:
                    if kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        deco_deps += [dep_descriptor(e) for e in kw.value.elts]
                    elif kw.arg == "response_model":
                        response_model = unparse(kw.value)
                    elif kw.arg == "status_code":
                        status_code = unparse(kw.value)
                    elif kw.arg == "include_in_schema":
                        include = not (isinstance(kw.value, ast.Constant)
                                       and kw.value.value is False)
            if not paths or holder is None:
                continue
            sig_deps, request_model = [], None
            args = node.args
            positional = list(args.args)
            defaults = list(args.defaults)
            pad = len(positional) - len(defaults)
            for i, a in enumerate(positional):
                default = defaults[i - pad] if i >= pad else None
                ann = unparse(a.annotation) if a.annotation else ""
                if default is not None and isinstance(default, ast.Call) \
                        and unparse(default.func).endswith("Depends"):
                    sig_deps.append(dep_descriptor(default))
                elif ann.split("[")[0].strip() in models:
                    request_model = ann
            for a, default in zip(args.kwonlyargs, args.kw_defaults):
                ann = unparse(a.annotation) if a.annotation else ""
                if default is not None and isinstance(default, ast.Call) \
                        and unparse(default.func).endswith("Depends"):
                    sig_deps.append(dep_descriptor(default))
                elif ann.split("[")[0].strip() in models:
                    request_model = ann
            try:
                body_src = ast.get_source_segment(self.src, node) or ""
            except Exception:
                body_src = ""
            doc = (ast.get_docstring(node) or "").strip().split("\n")[0][:400]
            by_method = {}
            for method, p in paths:
                by_method.setdefault(method, []).append(p)
            for method, plist in by_method.items():
                primary = sorted(plist, key=lambda s: (len(s), s))[-1]
                self.endpoints.append({
                    "module": rel(self.path), "router_var": holder,
                    "method": method, "sub_path": primary,
                    "sub_path_aliases": [p for p in plist if p != primary],
                    "handler": node.name, "line": node.lineno, "doc": doc,
                    "route_deps": deco_deps, "sig_deps": sig_deps,
                    "response_model": response_model,
                    "request_model": request_model,
                    "status_code": status_code, "include_in_schema": include,
                    "writes": bool(re.search(r"db\.(add|delete|commit|merge)\(", body_src)),
                })


MODULE_CACHE = {}


def load_module(dotted):
    if dotted in MODULE_CACHE:
        return MODULE_CACHE[dotted]
    p = os.path.join(ROOT, *dotted.split(".")) + ".py"
    mod = Module(dotted, p) if os.path.isfile(p) else None
    MODULE_CACHE[dotted] = mod
    return mod


def resolve_target(expr, aliases):
    if "." in expr:
        head, attr = expr.rsplit(".", 1)
        if head in aliases:
            return aliases[head][0], attr
    if expr in aliases:
        mod, attr = aliases[expr]
        return mod, (attr or "router")
    return None, None


def expand_router(dotted, var, prefix, inherited_deps, trail, gaps, seen):
    key = (dotted, var, prefix)
    if key in seen:
        return []
    seen.add(key)
    mod = load_module(dotted)
    if mod is None or not mod.ok:
        gaps.append("router module could not be parsed: %s" % dotted)
        return []
    if var not in mod.routers:
        gaps.append("router variable '%s' not found in %s" % (var, dotted))
        return []
    meta = mod.routers[var]
    base = prefix + meta["prefix"]
    deps = inherited_deps + meta["dependencies"]
    out = []
    for ep in mod.endpoints:
        if ep["router_var"] != var:
            continue
        rec = dict(ep)
        full = re.sub(r"//+", "/", (base + ep["sub_path"]) or "/")
        if len(full) > 1 and full.endswith("/"):
            full = full[:-1]
        rec["path"] = full
        rec["path_aliases"] = sorted({
            (re.sub(r"//+", "/", (base + a) or "/").rstrip("/") or "/")
            for a in ep["sub_path_aliases"]})
        rec["inherited_deps"] = deps
        rec["router_tags"] = meta["tags"]
        rec["mount_trail"] = list(trail)
        out.append(rec)
    for inc in mod.includes:
        if inc["parent"] != var:
            continue
        cmod, cvar = resolve_target(inc["target"], mod.aliases)
        if not cmod:
            gaps.append("nested include_router target unresolved: %s in %s"
                        % (inc["target"], dotted))
            continue
        out += expand_router(cmod, cvar, base + inc["prefix"],
                             deps + inc["deps"],
                             trail + ["%s:%d" % (rel(mod.path), inc["line"])],
                             gaps, seen)
    return out


def parse_main_includes():
    tree = ast.parse(read(os.path.join(APP, "main.py")))
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("app.routers"):
            mod = node.module
            for a in node.names:
                local = a.asname or a.name
                if mod == "app.routers":
                    aliases[local] = ("app.routers.%s" % a.name, None)
                else:
                    aliases[local] = (mod, a.name)
    mounts = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "include_router" and node.args):
            continue
        prefix, deps = "", []
        for kw in node.keywords:
            if kw.arg == "prefix":
                prefix = const_str(kw.value) or ""
            elif kw.arg == "dependencies" and isinstance(kw.value, (ast.List, ast.Tuple)):
                deps = [dep_descriptor(e) for e in kw.value.elts]
        mounts.append({"target": unparse(node.args[0]), "prefix": prefix,
                       "deps": deps, "line": node.lineno})
    return aliases, mounts


def build_api_index(gaps):
    aliases, mounts = parse_main_includes()
    endpoints = []
    for mount in mounts:
        dotted, var = resolve_target(mount["target"], aliases)
        if not dotted:
            gaps.append("app/main.py include_router target unresolved: %s"
                        % mount["target"])
            continue
        eps = expand_router(dotted, var, mount["prefix"], mount["deps"],
                            ["app/main.py:%d" % mount["line"]], gaps, set())
        for e in eps:
            e["mount_line"] = mount["line"]
        endpoints += eps
    reached = {m.path for m in MODULE_CACHE.values() if m}
    present = set()
    for dp, dn, fn in os.walk(os.path.join(APP, "routers")):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if f.endswith(".py") and f != "__init__.py":
                present.add(os.path.join(dp, f))
    return endpoints, sorted(rel(p) for p in (present - reached))


def endpoint_authz(rec):
    names, caps, feats = [], [], []
    for d in rec["inherited_deps"] + rec["route_deps"] + rec["sig_deps"]:
        nm = d["name"]
        names.append(nm)
        if nm == "require_capability" and d["arg"]:
            caps.append(d["arg"])
        elif nm == "require_feature" and d["arg"]:
            feats.append(d["arg"])
        elif nm == "require_feature_capability" and d["arg"]:
            caps.append(d["arg"])
            feats.append(d["arg"])
    auth = any(n in AUTH_DEPS for n in names)
    roles = []
    for n in names:
        roles += DEP_ROLES.get(n, [])
    scope = {"platform": False, "brand": False, "org": False,
             "workspace": False, "user": False}
    for n in names:
        s = DEP_SCOPE.get(n)
        if s == "platform":
            scope["platform"] = True
        elif s == "brand":
            scope["brand"] = True
        elif s == "org":
            scope["org"] = True
            scope["workspace"] = True
        elif s == "user":
            scope["user"] = True
    return {"auth_required": auth, "roles": sorted(set(roles)),
            "capabilities": sorted(set(caps)), "features": sorted(set(feats)),
            "scope": scope, "dep_names": sorted({n for n in names if n})}


# ===========================================================================
# configuration
# ===========================================================================

def parse_features():
    p = os.path.join(APP, "services", "entitlements.py")
    if not os.path.isfile(p):
        return {}
    tree = ast.parse(read(p))
    for node in ast.walk(tree):
        hit = ((isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "FEATURES")
               or (isinstance(node, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "FEATURES"
                           for t in node.targets)))
        if hit and isinstance(node.value, ast.Dict):
            return {const_str(k): const_str(v)
                    for k, v in zip(node.value.keys, node.value.values)
                    if const_str(k)}
    return {}


CAP_RE = re.compile(
    r'_cap\(\s*"([a-z0-9_]+)"\s*,\s*\n?\s*"((?:[^"\\]|\\.)*)"'
    r'(?:[^)]*?requires_feature\s*=\s*(None|"[a-z0-9_]+"))?'
    r'(?:[^)]*?delegable\s*=\s*(True|False))?', re.S)


def parse_capabilities():
    p = os.path.join(APP, "services", "capabilities.py")
    if not os.path.isfile(p):
        return []
    text = read(p)
    caps = []
    for m in CAP_RE.finditer(text):
        rf = m.group(3)
        caps.append({"key": m.group(1),
                     "label": m.group(2).replace('\\"', '"'),
                     "requires_feature": None if rf in (None, "None") else rf.strip('"'),
                     "delegable": (m.group(4) != "False")})
    scoped = {}
    for name in ("BRAND_SCOPED_CAPABILITIES", "PLATFORM_SCOPED_CAPABILITIES"):
        m = re.search(r"%s\s*=\s*\(([^)]*)\)" % name, text, re.S)
        if m:
            scoped[name] = re.findall(r'"([a-z0-9_]+)"', m.group(1))
    for c in caps:
        if c["key"] in scoped.get("BRAND_SCOPED_CAPABILITIES", []):
            c["scope"] = "brand_sales_org"
        elif c["key"] in scoped.get("PLATFORM_SCOPED_CAPABILITIES", []):
            c["scope"] = "platform"
        else:
            c["scope"] = "customer_org"
    return caps


def parse_support_features():
    p = os.path.join(APP, "services", "support_entitlements.py")
    if not os.path.isfile(p):
        return {}
    m = re.search(r"SUPPORT_FEATURES:\s*Dict\[str,\s*str\]\s*=\s*\{(.*?)\}",
                  read(p), re.S)
    return dict(re.findall(r'"([a-z0-9_]+)":\s*"([^"]+)"', m.group(1))) if m else {}


# Non-ASCII glyph-only controls carry no operational meaning either.
TRIVIAL_LABELS |= {"×", "✕", "✖", "‹", "›",
                   "«", "»", "…", "−",
                   "loading…", "saving…"}


# ===========================================================================
# curated layer: natural-language aliases, integrations, workflow edges
# ===========================================================================

ROUTE_ALIASES = {
    "/": ["home", "overview", "dashboard", "my workspace"],
    "/login": ["sign in", "log in", "login screen"],
    "/change-password": ["reset my password", "change password",
                         "forced password change"],
    "/workspaces": ["workspace picker", "choose a workspace", "switch workspace"],
    "/workspace/:organizationId": ["enter a workspace",
                                   "open the customer workspace",
                                   "go into the customer"],
    "/leads": ["lead list", "my leads", "where do I work a lead", "prospect list"],
    "/leads/:leadId": ["lead detail", "open a lead", "lead record",
                       "work this lead"],
    "/replies": ["work replies", "inbound replies", "reply inbox"],
    "/cadence": ["cadences", "sequences", "follow-up sequence",
                 "put a lead into a cadence"],
    "/cadence-templates": ["cadence builder", "build a cadence",
                           "cadence templates"],
    "/email-queue": ["outbound email queue", "pending emails", "email outbox"],
    "/auto-send": ["auto send queue", "ai drafts awaiting approval",
                   "approve drafts"],
    "/activity": ["activity feed", "what has been sent", "outreach history"],
    "/workqueue": ["work queue", "my tasks", "what do I need to do"],
    "/availability": ["my availability", "set my hours", "booking hours"],
    "/campaigns": ["campaign builder", "bulk campaign", "blast"],
    "/re-engagement": ["reactivation", "re-engage old leads",
                       "database reactivation"],
    "/compliance": ["dnc list", "do not contact", "suppression list", "opt outs"],
    "/crm": ["native crm", "contacts", "crm records"],
    "/crm-connectors": ["connect my crm", "gohighlevel", "hubspot",
                        "crm integration"],
    "/lead-cleanup": ["clean up leads", "duplicate leads", "merge duplicates"],
    "/admin": ["team performance", "advisor performance", "team dashboard"],
    "/reports": ["reporting", "analytics", "reports"],
    "/import-batches": ["lead imports", "import leads", "csv upload",
                        "import history"],
    "/import-batches/:batchId": ["review an import", "import batch review",
                                 "staged rows"],
    "/users": ["user management", "add somebody", "add a user", "team members"],
    "/users/:userId": ["user detail", "edit a user", "someone's access"],
    "/settings": ["my settings", "my profile", "personal settings"],
    "/org-settings": ["organization settings", "branding", "company settings"],
    "/tier-definitions": ["tier config", "lead tiers", "tier definitions"],
    "/audit-log": ["audit log", "who changed what", "activity audit"],
    "/10dlc": ["a2p 10dlc", "carrier registration",
               "register my brand for sms"],
    "/system-health": ["system health", "is the platform up", "platform health"],
    "/billing": ["my billing", "invoices", "subscription", "payment"],
    "/help": ["help and support", "open a ticket", "contact support", "ask ai"],
    "/ai-hub": ["ai hub", "ai settings", "ai controls"],
    "/ai-team": ["your ai team", "my ai employees", "ai staff"],
    "/ai-team/:employeeId": ["ai employee detail", "configure an ai employee"],
    "/ai-workforce": ["my ai workforce", "hire an ai employee", "ai workforce"],
    "/ai-workforce-command": ["workforce command", "turn ai on", "stop ai",
                              "ai handoffs", "ai reviews"],
    "/ai-workforce-command/:employeeId": ["ai employee command",
                                          "one ai employee's work"],
    "/proposals": ["proposals", "quotes"],
    "/proposals/:proposalId": ["proposal editor", "edit a proposal"],
    "/templates": ["message templates", "email templates"],
    "/pipeline": ["pipeline", "deal pipeline"],
    "/commercial": ["my agreement", "my commercial terms", "my deal"],
    "/commercial/agreements/:agreementId": ["commercial console",
                                            "custom agreement",
                                            "internal deal terms"],
    "/fiber-capture": ["fiber lead capture", "fiber lead form"],
    "/provision-client": ["provision a client", "new client provisioning"],
    "/orgs": ["org manager", "organizations list"],
    "/onboarding": ["onboarding wizard", "first run setup"],
    "/activate": ["activate my account", "accept invitation"],
    "/setup-integrations": ["setup integrations",
                            "connect my tools during setup"],
    "/demo": ["demo console", "demo controls"],
    "/demo-suite": ["demo suite", "where is the demo", "present a demo"],
    "/demo-suite/:platformId": ["a brand's demo", "demo for this brand"],
    "/training": ["training", "learning path", "my training"],
    "/training/:pathKey": ["a training path", "training module"],
    "/launch": ["launch pad", "customer onboarding", "my onboarding",
                "intake form"],
    "/launch/:stepKey": ["a launch pad step", "onboarding step"],
    "/launch/preview/:organizationId": ["preview a customer's onboarding",
                                        "see the launch pad as the customer"],
    "/launch/preview/:organizationId/:stepKey": ["preview one onboarding step"],
    "/book/:token": ["public booking page", "book an appointment",
                     "scheduling link"],
    "/survey/:token": ["public survey", "survey link"],
    "/appointments/confirm/:token": ["confirm an appointment",
                                     "appointment confirmation"],
    "/deal-room/:token": ["deal room", "customer deal room"],
    "/demo/:token": ["demo site", "prospect demo website"],
    "/portal/access/:token": ["portal access", "customer portal link"],
    "/portal/view/:proposalId": ["view a proposal", "proposal portal"],
    "/sales": ["my day", "sales home", "back office home"],
    "/sales/pipeline": ["my pipeline", "my deals", "my customer pipeline"],
    "/sales/prospects": ["prospects", "my prospect list"],
    "/sales/availability": ["my sales availability", "my calendar hours"],
    "/sales/onboarding": ["sold / onboarding", "implementations I sold",
                          "handoff"],
    "/sales/customers/:orgId/catalogue": ["sell add-ons", "customer catalogue",
                                          "sell more to a customer"],
    "/sales/opportunities/:oppId": ["opportunity detail", "open a deal",
                                    "customize deal", "deal screen"],
    "/sales/team": ["team availability", "when is my team free",
                    "find team time"],
    "/sales/manager": ["team command", "manager dashboard", "manager controls"],
    "/sales/compensation": ["compensation command", "where is compensation",
                            "the pay stuff", "commission ledger"],
    "/sales/my-compensation": ["my compensation", "my commission",
                               "what have I earned"],
    "/sales/calendar": ["team calendar", "sales calendar"],
    "/sales/team-pipeline": ["team pipeline", "my team's deals"],
    "/sales/proposals": ["team proposals", "demos and proposals"],
    "/sales/demos": ["demos to build", "demo queue",
                     "where is the demo builder"],
    "/sales/demo-build/:oppId": ["build a demo", "demo build screen",
                                 "demo builder"],
    "/sales/salespeople": ["salespeople", "my reps", "sales roster"],
    "/executive": ["executive suite", "executive home"],
    "/executive/command-center": ["executive command center", "exec dashboard"],
    "/executive/organizations": ["executive portfolio",
                                 "my customers as an executive",
                                 "customer health"],
    "/executive/customer-health": ["customer health"],
    "/executive/revenue": ["executive revenue", "revenue view"],
    "/executive/team": ["executive sales team", "team view"],
    "/executive/organizations/:orgId/view": ["one customer's performance",
                                             "executive org view"],
    "/executive/workspace": ["executive workspace", "exec work items"],
    "/executive/workspace/:itemId": ["an executive work item"],
    "/god": ["god mode", "command center", "platform command center",
             "owner console"],
    "/god/platform": ["platform overview", "platform admin", "platform home"],
    "/god/organizations": ["organizations", "org records", "add company"],
    "/god/customers": ["customers", "customer list", "commercial lifecycle"],
    "/god/customers/new": ["create a customer organization", "new customer",
                           "customer setup", "new organization",
                           "add a customer",
                           "where do I create a customer organization"],
    "/god/customers/:orgId": ["customer detail", "open a customer"],
    "/god/customers/:orgId/360": ["customer 360",
                                  "everything about a customer"],
    "/god/workspaces": ["workspaces", "live tenant environments",
                        "enter a customer workspace"],
    "/god/users-all": ["users and identity", "every user",
                       "one row per human"],
    "/god/access": ["manage access", "permissions for a person",
                    "grant access"],
    "/god/access/new": ["add a person", "create a user in god mode"],
    "/god/access/:userId": ["a person's access", "edit someone's grants"],
    "/god/executive-access": ["executive access",
                              "which customers an executive sees"],
    "/god/diagnostics/user-access": ["access and permissions diagnostic",
                                     "what can this person reach",
                                     "who has permission to do this"],
    "/god/diagnostics/qualification": ["lead qualification diagnostic",
                                       "who may be contacted"],
    "/god/diagnostics/twilio": ["twilio diagnostics", "sms delivery problems"],
    "/god/diagnostics/job-runs": ["background jobs", "job runs",
                                  "cron history"],
    "/god/brands/:brandId": ["brand detail", "white-label brand"],
    "/god/sales-operations": ["sales operations", "platform sales ops"],
    "/god/opportunities": ["all opportunities", "platform pipeline"],
    "/god/proposals": ["all proposals", "platform proposals"],
    "/god/meetings": ["meetings", "platform meetings"],
    "/god/provision/:oppId": ["provision a sold deal",
                              "turn a sale into a customer"],
    "/god/implementations": ["implementations", "onboarding handoff"],
    "/god/implementations/:implId": ["an implementation",
                                     "implementation detail"],
    "/god/launches": ["customer launches", "launch engine",
                      "onboarding intake status", "send onboarding"],
    "/god/pricing": ["pricing and compensation", "discount floors",
                     "commission plans"],
    "/god/compensation": ["sales compensation", "compensation ledger",
                          "pay run"],
    "/god/billing": ["billing and revenue", "who owes us",
                     "customer billing"],
    "/god/revenue-history": ["revenue history", "payment history"],
    "/god/workforce": ["ai workforce", "job library", "kill switch",
                       "activation staging"],
    "/god/ai-operations": ["ai operations", "what the ai attempted",
                           "provider resolution"],
    "/god/ai-deployment": ["ai deployment", "ai workforce builder",
                           "brand commercial terms for ai"],
    "/god/support": ["support console", "support queue", "tickets"],
    "/god/audit": ["audit and security", "platform audit"],
    "/god/roadmap": ["roadmap", "what is built", "does this feature exist"],
    "/god/maintenance": ["maintenance ops", "booking cleanup", "phone audit"],
    "/god/voice": ["voice configuration", "voice agents"],
    "/god/lead-scraper": ["lead scraper", "back office prospecting"],
    "/god/lead-browser": ["lead browser", "search all leads"],
    "/god/demo-suite": ["god demo suite", "brand demo environments"],
    "/god/training": ["assign training", "training administration"],
    "/god/customer-app": ["enter the customer app as god",
                          "tenant app as the owner",
                          "how do I get into the customer"],
    "/scraper": ["scraper"],
}

INTEGRATIONS = [
    {"name": "Microsoft 365 / Outlook",
     "purpose": "Mailbox and calendar connection for sending, polling replies "
                "and free/busy.",
     "path_hints": ["/microsoft", "/integrations/microsoft", "/me/calendar",
                    "/sales/calendar"],
     "route_hints": ["/settings", "/setup-integrations", "/sales/availability",
                     "/availability"],
     "aliases": ["outlook", "hook up outlook", "connect microsoft",
                 "office 365", "microsoft 365", "exchange calendar",
                 "where do I connect microsoft 365"],
     "scope": "user_or_org",
     "credential_owner": "the connecting user (per-mailbox OAuth); brand or "
                         "org level where the brand owns the app registration"},
    {"name": "Google (Calendar / Contacts)",
     "purpose": "Calendar connection for availability and free/busy, and "
                "Google Contacts import.",
     "path_hints": ["/google", "/contacts/google", "/me/calendar",
                    "/sales/calendar"],
     "route_hints": ["/settings", "/setup-integrations", "/availability",
                     "/sales/availability"],
     "aliases": ["connect google", "google calendar", "gmail calendar",
                 "google contacts"],
     "scope": "user_or_org",
     "credential_owner": "the connecting user (OAuth)"},
    {"name": "Twilio (SMS and voice transport)",
     "purpose": "Outbound and inbound SMS, delivery receipts, number "
                "provisioning and A2P 10DLC registration.",
     "path_hints": ["/sms", "/webhooks/twilio", "/god/diagnostics/twilio",
                    "/10dlc", "/dlc"],
     "route_hints": ["/10dlc", "/god/diagnostics/twilio"],
     "aliases": ["twilio", "sms provider", "text messaging", "10dlc",
                 "sending number"],
     "scope": "platform_or_org",
     "credential_owner": "platform by default; an organization may hold its "
                         "own under the twilio_credentials capability"},
    {"name": "Stripe",
     "purpose": "Customer subscriptions, invoices and payment state for "
                "billing.",
     "path_hints": ["/billing", "/god/billing", "/webhooks/stripe"],
     "route_hints": ["/billing", "/god/billing"],
     "aliases": ["stripe", "payments", "subscriptions", "card on file"],
     "scope": "platform", "credential_owner": "platform"},
    {"name": "Voice provider (AI calling)",
     "purpose": "Outbound and inbound AI voice calls, agent mappings and "
                "attempt policy.",
     "path_hints": ["/voice", "/webhooks/voice"],
     "route_hints": ["/god/voice"],
     "aliases": ["voice agent", "ai calling", "phone agent"],
     "scope": "platform_or_brand", "credential_owner": "platform"},
    {"name": "CRM connectors",
     "purpose": "Inbound lead delivery and record sync with external CRMs.",
     "path_hints": ["/crm", "/crm-inbound"],
     "route_hints": ["/crm-connectors", "/crm"],
     "aliases": ["gohighlevel", "ghl", "hubspot", "connect my crm",
                 "crm sync"],
     "scope": "org", "credential_owner": "organization"},
]

WORKFLOW_EDGES = [
    {"id": "workflow:public-capture-to-lead", "surface": "public_web",
     "from": "public capture (booking / survey / fiber / inbound webhook)",
     "to": "lead record",
     "trigger": "an unauthenticated submission on a public route or an "
                "inbound webhook",
     "api_hints": ["/leads/public", "/webhooks", "/public"],
     "automatic": True,
     "description": "Public intake creates or matches a lead in the owning "
                    "organization without a session.",
     "aliases": ["where do leads come from", "website lead"]},
    {"id": "workflow:lead-to-cadence", "surface": "customer_workspace",
     "from": "/leads/:leadId", "to": "/cadence",
     "trigger": "starting a cadence on a lead",
     "api_hints": ["/cadence"], "automatic": False,
     "description": "A lead is enrolled in a multi-touch cadence; eligibility, "
                    "consent and suppression are checked server-side before "
                    "any send.",
     "aliases": ["how do I put a lead into a cadence", "start a sequence"]},
    {"id": "workflow:cadence-to-conversation", "surface": "customer_workspace",
     "from": "/cadence", "to": "/replies",
     "trigger": "an inbound reply on email, SMS or voice",
     "api_hints": ["/ai-conversation", "/sms", "/email"], "automatic": True,
     "description": "An outbound touch that gets a reply becomes a "
                    "conversation to work in Replies.",
     "aliases": ["how do I work replies", "inbound response"]},
    {"id": "workflow:conversation-to-appointment",
     "surface": "customer_workspace",
     "from": "/replies", "to": "/availability",
     "trigger": "booking an appointment from a conversation",
     "api_hints": ["/calendar", "/availability", "/appointments"],
     "automatic": False,
     "description": "A worked reply produces an appointment against advisor "
                    "availability.",
     "aliases": ["how do I create an appointment", "book a meeting"]},
    {"id": "workflow:opportunity-to-proposal", "surface": "sales_workspace",
     "from": "/sales/opportunities/:oppId", "to": "/sales/proposals",
     "trigger": "pricing an opportunity and issuing a proposal",
     "api_hints": ["/sales/proposals", "/proposals"], "automatic": False,
     "description": "An opportunity is priced and a proposal is issued to the "
                    "prospect.",
     "aliases": ["send proposal", "quote the deal"]},
    {"id": "workflow:opportunity-to-demo", "surface": "sales_workspace",
     "from": "/sales/opportunities/:oppId", "to": "/sales/demo-build/:oppId",
     "trigger": "queueing a demo build for an opportunity",
     "api_hints": ["/sales/demo", "/demo-suite"], "automatic": False,
     "description": "An opportunity needing a demo enters the demo queue and "
                    "is built into a presentable demo site.",
     "aliases": ["build the demo", "demo for a prospect"]},
    {"id": "workflow:sold-to-provision", "surface": "god_mode",
     "from": "/sales/onboarding", "to": "/god/provision/:oppId",
     "trigger": "provisioning a won opportunity",
     "api_hints": ["/customers", "/god/ops", "/god/provision"],
     "automatic": False,
     "description": "A sold opportunity is provisioned into a customer "
                    "organization and an implementation record.",
     "aliases": ["turn the sale into a customer", "provision the account"]},
    {"id": "workflow:provision-to-launch-invite", "surface": "customer_launch",
     "from": "/god/launches", "to": "/launch",
     "trigger": "sending the onboarding invitation to the customer",
     "api_hints": ["/god/launch", "/launch"], "automatic": False,
     "description": "Send Onboarding issues the customer's Launch Pad "
                    "invitation; the customer then completes intake at "
                    "/launch, which is scoped to their own implementation "
                    "server-side.",
     "aliases": ["how do I send onboarding",
                 "what happens when I click send onboarding",
                 "invite the customer to onboard"]},
    {"id": "workflow:launch-to-workspace", "surface": "customer_launch",
     "from": "/launch", "to": "/workspace/:organizationId",
     "trigger": "completing intake, then activation of the customer's "
                "workspace",
     "api_hints": ["/launch", "/customers"], "automatic": False,
     "description": "Completed intake feeds configuration and activation; the "
                    "customer's people then work in the tenant workspace.",
     "aliases": ["go live", "workspace creation"]},
    {"id": "workflow:god-to-customer-workspace", "surface": "god_mode",
     "from": "/god/customers", "to": "/god/customer-app",
     "trigger": "selecting a customer organization, then entering the tenant "
                "application",
     "api_hints": ["/god", "/platform"], "automatic": False,
     "description": "God Mode selects an organization context (carried as "
                    "X-Org-Override) and renders the tenant application. No "
                    "membership is created and God authority is preserved. "
                    "/workspace/{id} is NOT the owner's path in: that route "
                    "asserts membership and does not exempt god_admin.",
     "aliases": ["how do I get from god mode into a customer workspace",
                 "how do I get into the customer"]},
    {"id": "workflow:customer-workspace-back-to-god", "surface": "god_mode",
     "from": "/god/customer-app", "to": "/god",
     "trigger": "exiting the organization view from the context banner or the "
                "God rail",
     "api_hints": ["/god", "/platform"], "automatic": False,
     "description": "Exiting clears the organization override and returns the "
                    "owner to the control plane.",
     "aliases": ["how do I get back to god mode", "exit the customer"]},
    {"id": "workflow:hire-to-activate-ai-employee", "surface": "ai_workforce",
     "from": "/ai-workforce", "to": "/ai-workforce-command",
     "trigger": "hiring and configuring an AI employee, then requesting "
                "activation",
     "api_hints": ["/workforce", "/ai-deployment"], "automatic": False,
     "description": "An AI employee is hired and configured in the customer "
                    "workspace. Whether it may act is decided at execution "
                    "time by activation stage plus entitlement; completing an "
                    "activation is a platform act in God Mode.",
     "aliases": ["how do I turn ai on", "hire an ai employee"]},
    {"id": "workflow:ai-kill-switch", "surface": "ai_workforce",
     "from": "/god/workforce", "to": "/god/ai-operations",
     "trigger": "using the kill switch to stop AI action, then reading what "
                "was attempted",
     "api_hints": ["/god/workforce", "/ai-operations"], "automatic": False,
     "description": "The platform kill switch stops AI employees from acting; "
                    "AI Operations is where attempts, provider resolution and "
                    "refusals are read back.",
     "aliases": ["how do I stop ai", "kill switch"]},
    {"id": "workflow:ticket-to-remediation", "surface": "support",
     "from": "/help", "to": "/god/support",
     "trigger": "a customer raising a ticket, which lands in the brand's "
                "support queue",
     "api_hints": ["/support", "/god/support"], "automatic": True,
     "description": "A customer ticket enters the brand queue with a "
                    "first-response target from its support entitlement; safe "
                    "automated remediation, approval-gated remediation and "
                    "human escalation are decided there.",
     "aliases": ["open a ticket", "support escalation"]},
    {"id": "workflow:import-stage-review-commit",
     "surface": "customer_workspace",
     "from": "/import-batches", "to": "/import-batches/:batchId",
     "trigger": "uploading a file, reviewing staged rows, then committing them",
     "api_hints": ["/leads/import", "/import"], "automatic": False,
     "description": "Lead import is three separate authorities: stage, review "
                    "and commit. lead_import_stage, lead_import_review and "
                    "lead_import_commit are distinct capabilities.",
     "aliases": ["how do I import leads", "csv import"]},
]


# ===========================================================================
# helpers
# ===========================================================================

def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s or "unnamed"


def route_slug(route):
    """Stable id fragment for a route.

    '*' is spelled out rather than dropped: without this, /god and the /god/*
    catch-all both slugged to 'god' and collided.
    """
    if route in ("/", ""):
        return "root"
    return slug(route.replace("*", "wildcard"))


def page_id(route):
    return "page:" + route_slug(route)


def api_id(method, path):
    return "api:%s:%s" % (method, path)


def count_by(records, key):
    out = {}
    for r in records:
        out[r.get(key)] = out.get(r.get(key), 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], str(kv[0]))))


def describe_result(c, opens_type, opens_target, methods):
    if any(m in MUTATING for m in methods):
        verb = "changes data server-side (%s)" % ", ".join(sorted(methods))
    elif methods:
        verb = "reads data (%s)" % ", ".join(sorted(methods))
    else:
        verb = "no backend call proven in source"
    if opens_type == "route":
        return "%s; navigates to %s" % (verb, opens_target)
    if opens_type == "modal":
        return "%s; opens a modal or panel" % verb
    if opens_type == "external":
        return "%s; opens %s" % (verb, opens_target)
    return verb


def action_aliases(label):
    low = label.lower().strip(" .!")
    out = {low}
    for pre, alts in (("new ", ("create ", "add ")),
                      ("add ", ("create ", "new ")),
                      ("create ", ("new ", "add "))):
        if low.startswith(pre):
            for a in alts:
                out.add(a + low[len(pre):])
    return sorted(a for a in out if a and len(a) > 2)


def nav_aliases(label, dest):
    low = (label or "").lower()
    out = {low, "where is %s" % low}
    if dest:
        out |= set(ROUTE_ALIASES.get(dest.split("#")[0], [])[:4])
    return sorted(a for a in out if a and len(a) > 2)


def human_title(comp, route, nav_entries):
    for n in nav_entries:
        if n.get("label"):
            return n["label"]
    if comp:
        return re.sub(r"(?<!^)(?=[A-Z])", " ", comp).replace("A I", "AI").strip()
    return route


def page_description(route, comp, guard, nav_entries):
    bits = []
    hint = next((n.get("hint") for n in nav_entries if n.get("hint")), None)
    if hint:
        bits.append(hint.rstrip(".") + ".")
    bits.append("Rendered by %s at %s." % (comp, route) if comp
                else "Route %s." % route)
    if guard:
        bits.append("Guard: %s." % guard)
    return " ".join(bits)


def endpoint_aliases(rec):
    out = {rec["handler"].replace("_", " ").strip()}
    for t in rec.get("router_tags") or []:
        out.add(t.replace("-", " ").replace("_", " "))
    return sorted(a for a in out if a and len(a) > 2)


ROLE_DOCS = [
    ("god_admin", "God Mode: ROOT platform authority. There is no higher role "
                  "- no platform superadmin, root admin, super god or master "
                  "admin exists anywhere in the code. Enforced by require_god "
                  "server-side and GodRoute client-side.",
     ["platform"], "god_mode"),
    ("super_admin", "Platform-scoped administrator below God. Satisfies "
                    "require_super_admin and require_admin.",
     ["platform"], "shared_platform"),
    ("org_admin", "Administrator of one customer organization. Satisfies "
                  "require_admin within that organization's scope.",
     ["org"], "customer_workspace"),
    ("advisor", "Ordinary tenant user in a customer workspace; lead "
                "visibility is owner-scoped (app/services/lead_scope.py).",
     ["org"], "customer_workspace"),
    ("viewer", "Read-oriented tenant workspace role.",
     ["org"], "customer_workspace"),
    ("brand_executive", "Executive oversight of a brand's customers; "
                        "membership role checked by require_brand_executive "
                        "and narrowed per organization by executive "
                        "assignments (app/services/executive_authority.py).",
     ["brand"], "executive"),
    ("sales_manager", "Brand back-office manager; satisfies "
                      "require_sales_manager and carries view_team_pipeline "
                      "on GET /sales/me.", ["brand_sales_org"], "brand_sales"),
    ("sales_rep", "Brand back-office seller; satisfies require_sales_member, "
                  "scoped to their own book.", ["brand_sales_org"],
     "brand_sales"),
    ("commercial_approver", "Approval authority for custom commercial "
                            "agreements "
                            "(app/services/commercial/authority.py).",
     ["brand", "platform"], "finance_compensation"),
]

SALES_PERMS = [
    ("view_team_pipeline",
     "Manager-level visibility of the brand's team pipeline, calendar, "
     "proposals, roster and compensation screens; answered on GET /sales/me "
     "and enforced server-side in app/services/sales_access.py.",
     ["sales_manager", "god_admin"]),
    ("present_demo",
     "Right to present a brand's demonstration environment; answered from the "
     "demo_suite capability grant, the same row the Demo Suite routes check.",
     []),
]


# ===========================================================================
# build
# ===========================================================================

def build():
    gaps = []
    routes = parse_app_routes()
    imports = parse_app_imports()
    nav_items = parse_nav_arrays()
    endpoints, unmounted = build_api_index(gaps)
    features = parse_features()
    caps = parse_capabilities()
    support_features = parse_support_features()

    # ---- api endpoint records -------------------------------------------
    api_records = {}
    by_pattern = {}
    for ep in endpoints:
        az = endpoint_authz(ep)
        rid = api_id(ep["method"], ep["path"])
        status = "active" if ep["include_in_schema"] else "hidden"
        note = None
        if rid in api_records:
            prior = api_records[rid]
            rid = "%s#%s" % (rid, ep["handler"])
            status = "legacy"
            note = ("SHADOWED REGISTRATION: %s %s is already registered by "
                    "%s() at %s. FastAPI matches the first registration, so "
                    "this handler is unreachable at that path."
                    % (ep["method"], ep["path"], prior["handler"],
                       prior["source_files"][0]))
            gaps.append("duplicate route registration %s %s: %s:%d is matched "
                        "first and shadows %s:%d"
                        % (ep["method"], ep["path"], prior["_module"],
                           prior["_line"], ep["module"], ep["line"]))
        rec = {
            "record_type": "api_endpoint",
            "id": rid,
            "surface": api_surface(ep["path"], az["auth_required"], az["roles"]),
            "title": "%s %s" % (ep["method"], ep["path"]),
            "description": ep["doc"] or ("%s %s handled by %s() in %s"
                                         % (ep["method"], ep["path"],
                                            ep["handler"], ep["module"])),
            "method": ep["method"],
            "path": ep["path"],
            "path_pattern": pattern_of(ep["path"]),
            "path_aliases": ep["path_aliases"],
            "router": "%s::%s" % (ep["module"], ep["router_var"]),
            "router_tags": ep["router_tags"],
            "handler": ep["handler"],
            "purpose": ep["doc"] or None,
            "auth_required": az["auth_required"],
            "required_roles": az["roles"],
            "required_permissions": ["capability:%s" % c
                                     for c in az["capabilities"]],
            "required_scope": ("platform" if az["scope"]["platform"] else
                               "brand" if az["scope"]["brand"] else
                               "org" if az["scope"]["org"] else
                               "user" if az["scope"]["user"] else "none"),
            "scope_behavior": az["scope"],
            "request_model": ep["request_model"],
            "response_model": ep["response_model"],
            "entitlements": az["features"],
            "auth_dependencies": az["dep_names"],
            "domain_objects": domain_objects_for(ep["path"]),
            "called_by_pages": [],
            "called_by_actions": [],
            "mutates_data": ep["method"] in MUTATING,
            "persists_data": ep["writes"],
            "mount_trail": ep["mount_trail"],
            "aliases": endpoint_aliases(ep),
            "source_files": (["%s:%d" % (ep["module"], ep["line"])]
                             + ep["mount_trail"]),
            "status": status,
            "_module": ep["module"],
            "_line": ep["line"],
        }
        if note:
            rec["description"] = (rec["description"] + " " + note).strip()
            rec["shadowed_by"] = api_id(ep["method"], ep["path"])
        api_records[rid] = rec
        by_pattern.setdefault((ep["method"], pattern_of(ep["path"])), []).append(rid)
        for alias in ep["path_aliases"]:
            by_pattern.setdefault((ep["method"], pattern_of(alias)), []).append(rid)

    def resolve_api(method, path):
        pat = pattern_of(path)
        hit = by_pattern.get((method, pat))
        if hit:
            return hit[0]
        hit = by_pattern.get((method, pat.rstrip("/")))
        return hit[0] if hit else None

    # ---- page identity ---------------------------------------------------
    route_index = {}
    for r in routes:
        r["id"] = page_id(r["route"])
        route_index[r["route"]] = r

    parent_of = {}
    for r in routes:
        best = None
        for other in routes:
            if other["route"] in (r["route"], "/"):
                continue
            if r["route"].startswith(other["route"].rstrip("/") + "/"):
                if best is None or len(other["route"]) > len(best["route"]):
                    best = other
        parent_of[r["route"]] = best["id"] if best else None
    children = {}
    for route, pid in parent_of.items():
        if pid:
            children.setdefault(pid, []).append(page_id(route))

    nav_by_dest = {}
    for n in nav_items:
        dest = n.get("to") or n.get("path")
        if dest:
            nav_by_dest.setdefault(dest.split("#")[0], []).append(n)

    # ---- component graph -------------------------------------------------
    CRAWL_DEPTH = 2

    def component_files(comp):
        start = resolve_specifier(imports.get(comp, ""),
                                  os.path.join(FE, "App.jsx"))
        if not start:
            return []
        seen, frontier = [start], [start]
        for _ in range(CRAWL_DEPTH):
            nxt = []
            for f in frontier:
                for dep in local_imports(f):
                    if dep not in seen:
                        seen.append(dep)
                        nxt.append(dep)
            frontier = nxt
        return seen

    # Guards and redirects declared in App.jsx that render an existing page
    # component rather than one of their own. Proven in App.jsx: each of these
    # returns <ProtectedRoute><Overview /></ProtectedRoute>.
    RENDERS = {"WorkspaceRoute": "Overview", "GodCustomerAppRoute": "Overview",
               "HomeRedirect": "Overview"}

    controls_by_file, calls_by_file = {}, {}

    def controls_of(f):
        if f not in controls_by_file:
            try:
                controls_by_file[f] = parse_controls(f)
            except Exception as exc:
                gaps.append("could not parse controls in %s (%s)"
                            % (rel(f), exc))
                controls_by_file[f] = []
        return controls_by_file[f]

    def calls_of(f):
        if f not in calls_by_file:
            try:
                calls_by_file[f] = api_calls_in(read(f))
            except Exception:
                calls_by_file[f] = []
        return calls_by_file[f]

    # ---- page + action records ------------------------------------------
    page_records, action_records = [], []
    actions_by_key, action_ids = {}, set()

    for r in routes:
        route, comp = r["route"], r["page_component"]
        renders = RENDERS.get(comp)
        files = component_files(renders or comp) if comp else []
        own_file = files[0] if files else None
        guard = r["guard"]
        gsem = GUARD_SEMANTICS.get(guard, {})
        roles = list(gsem.get("roles", []))
        for f in r["flags"]:
            roles += FLAG_ROLES[f]

        nav_entries = nav_by_dest.get(route, [])
        nav_path, feature_flags, entitlements_, perms = [], [], [], []
        for n in nav_entries:
            if n.get("_section"):
                nav_path = [n["_container"], n["_section"], n.get("label")]
            else:
                nav_path = [n["_container"], n.get("label")]
            if n.get("featureKey"):
                entitlements_.append(n["featureKey"])
            if n.get("capability"):
                perms.append("capability:%s" % n["capability"])
            if n.get("permission"):
                perms.append("sales_permission:%s" % n["permission"])
            if n.get("adminOnly"):
                roles += FLAG_ROLES["requireAdmin"]
            for flag in ("launchOnly", "fiberOnly"):
                if n.get(flag):
                    feature_flags.append(flag)

        api_deps, api_dep_ids = [], []
        for f in files:
            for c in calls_of(f):
                label = "%s %s" % (c["method"], c["path"])
                if label not in api_deps:
                    api_deps.append(label)
                rid = resolve_api(c["method"], c["path"])
                if rid and rid not in api_dep_ids:
                    api_dep_ids.append(rid)
                    if r["id"] not in api_records[rid]["called_by_pages"]:
                        api_records[rid]["called_by_pages"].append(r["id"])

        important = []
        for f in files:
            fr = rel(f)
            for c in controls_of(f):
                if not keep_control(c):
                    continue
                key = (fr, c["line"], c["label"])
                if key in actions_by_key:
                    act = actions_by_key[key]
                    if route not in act["routes"]:
                        act["routes"].append(route)
                        act["page_ids"].append(r["id"])
                    if act["id"] not in important:
                        important.append(act["id"])
                    continue
                aid = "action:%s:%s" % (route_slug(route), slug(c["label"]))
                n = 2
                while aid in action_ids:
                    aid = "action:%s:%s-%d" % (route_slug(route),
                                               slug(c["label"]), n)
                    n += 1
                action_ids.add(aid)
                opens_type, opens_target = "none", None
                if c["to"]:
                    opens_type, opens_target = "route", c["to"]
                elif c["navigates"]:
                    opens_type, opens_target = "route", c["navigates"][0]
                elif c["href"]:
                    opens_type, opens_target = "external", c["href"]
                elif re.search(r"set[A-Z][A-Za-z0-9_]*\(\s*(?:true|\{)",
                               c["onclick"]):
                    # local state flipped on: a modal, drawer or inline panel
                    opens_type, opens_target = "modal", None
                methods = sorted({x["method"] for x in c["api_calls"]})
                call_labels = sorted({"%s %s" % (x["method"], x["path"])
                                      for x in c["api_calls"]})
                resolved_ids, doms = [], []
                for x in c["api_calls"]:
                    rid = resolve_api(x["method"], x["path"])
                    if rid and rid not in resolved_ids:
                        resolved_ids.append(rid)
                        api_records[rid]["called_by_actions"].append(aid)
                    for d in domain_objects_for(x["path"]):
                        if d not in doms:
                            doms.append(d)
                act = {
                    "record_type": "action",
                    "id": aid,
                    "surface": route_surface(route),
                    "title": c["label"],
                    "page_id": r["id"],
                    "page_ids": [r["id"]],
                    "route": route,
                    "routes": [route],
                    "label": c["label"],
                    "element_type": c["element_type"],
                    "description": "'%s' control in %s, reachable on %s"
                                   % (c["label"], fr, route),
                    "purpose": None,
                    "user_result": describe_result(c, opens_type, opens_target,
                                                   methods),
                    "opens": {"type": opens_type, "target": opens_target},
                    "api_calls": call_labels,
                    "api_endpoint_ids": resolved_ids,
                    "http_methods": methods,
                    "mutates_data": any(m in MUTATING for m in methods),
                    "domain_objects": doms,
                    "required_roles": sorted(set(roles)),
                    "required_permissions": sorted(set(perms)),
                    "required_scope": gsem.get("scope") or "org",
                    "feature_flags": sorted(set(feature_flags)),
                    "entitlements": sorted(set(entitlements_)),
                    "confirmation_required": bool(c["confirm"]),
                    "success_destination": (opens_target
                                            if opens_type == "route" else None),
                    "delegates_to": c.get("delegates_to") or [],
                    "aliases": action_aliases(c["label"]),
                    "source_files": ["%s:%d" % (fr, c["line"])],
                    "status": ("active" if (call_labels or opens_type != "none")
                               else "unresolved"),
                }
                if not call_labels and opens_type == "none":
                    if c.get("delegates_to"):
                        act["user_result"] = (
                            "Delegates to the %s callback supplied by its "
                            "parent component; the effect is whatever the "
                            "parent binds, which could not be resolved from "
                            "this module alone."
                            % ", ".join(c["delegates_to"]))
                    else:
                        act["user_result"] = (
                            "No backend call and no destination could be "
                            "proven from source; treat as local UI state "
                            "until verified.")
                action_records.append(act)
                actions_by_key[key] = act
                important.append(aid)

        prec = {
            "record_type": "page",
            "id": r["id"],
            "surface": route_surface(route),
            "title": human_title(comp, route, nav_entries),
            "description": page_description(route, comp, guard, nav_entries),
            "route": route,
            "route_params": r["params"],
            "component": comp,
            "layout": r["layout"],
            "guard": guard,
            "nav_path": nav_path,
            "purpose": page_description(route, comp, guard, nav_entries),
            "required_roles": sorted(set(roles)),
            "required_permissions": sorted(set(perms)),
            "required_scope": gsem.get("scope") or ("none" if not guard
                                                    else "org"),
            "enforcement": (gsem.get("note")
                            or "no client-side route guard in App.jsx"),
            "feature_flags": sorted(set(feature_flags)),
            "entitlements": sorted(set(entitlements_)),
            "parent_page": parent_of.get(route),
            "child_pages": sorted(children.get(r["id"], [])),
            "important_actions": important,
            "api_dependencies": api_deps,
            "api_endpoint_ids": api_dep_ids,
            "redirects_to": r["redirect_to"],
            "aliases": ROUTE_ALIASES.get(route, []),
            "source_files": (["%s:%d" % (r["source"], r["line"])]
                             + ([rel(own_file)] if own_file else [])),
            "status": "active" if (comp or r["redirect_to"]) else "unresolved",
        }
        if renders:
            prec["renders_component"] = renders
            prec["description"] += (" %s is declared inside App.jsx and renders "
                                    "%s." % (comp, renders))
        if comp and not own_file:
            if comp in APP_LOCAL_COMPONENTS:
                prec["description"] += (" %s is declared inside App.jsx rather "
                                        "than imported." % comp)
            else:
                prec["status"] = "unresolved"
                gaps.append("page %s: component %s could not be resolved to a "
                            "file" % (route, comp))
        page_records.append(prec)

    for a in action_records:
        a["page_ids"] = sorted(set(a["page_ids"]))
        a["routes"] = sorted(set(a["routes"]))

    for key, vals in ROUTE_ALIASES.items():
        if vals and key not in route_index:
            gaps.append("alias table names route %s which is not registered in "
                        "frontend/src/App.jsx" % key)

    # ---- navigation records ---------------------------------------------
    nav_records = []
    for n in nav_items:
        dest = n.get("to") or n.get("path")
        label = n.get("label")
        base = (dest or "").split("#")[0]
        dest_page = page_id(base) if base in route_index else None
        nid = "nav:%s:%s" % (n["_container"], slug(label))
        conds = []
        if n.get("adminOnly"):
            conds.append("org admin or above (adminOnly)")
        if n.get("featureKey"):
            conds.append("entitlement feature '%s' enabled" % n["featureKey"])
        if n.get("capability"):
            conds.append("capability '%s' granted "
                         "(GET /settings/my-capabilities)" % n["capability"])
        if n.get("permission"):
            conds.append("sales permission '%s' on GET /sales/me"
                         % n["permission"])
        if n.get("launchOnly"):
            conds.append("organization has an implementation (GET /launch/me)")
        if n.get("fiberOnly"):
            conds.append("fiber-enabled organization")
        if n.get("soon"):
            conds.append("rendered visibly disabled (soon)")
        status = "active"
        if n.get("soon"):
            status = "incomplete"
            gaps.append("nav '%s' (%s) is marked soon:true and has no route - "
                        "deferred by decision and rendered disabled"
                        % (label, n["_container"]))
        elif dest and not dest_page and not n.get("action"):
            if "#" in (dest or ""):
                status = "active"
            else:
                status = "unresolved"
                gaps.append("nav '%s' (%s) points at %s which is not a "
                            "registered route" % (label, n["_container"], dest))
        roles = []
        if n.get("adminOnly"):
            roles = FLAG_ROLES["requireAdmin"]
        if n["_container"].startswith("god_"):
            roles = ["god_admin"]
        if n["_container"] == "tenant_sidebar_platform_admin":
            roles = ["super_admin", "god_admin"]
        if n["_container"] == "executive_rail":
            roles = ["brand_executive", "god_admin"]
        nav_records.append({
            "record_type": "navigation",
            "id": nid,
            "surface": n["_surface"],
            "title": label,
            "label": label,
            "description": n.get("hint") or ("%s entry '%s'%s" % (
                n["_container"].replace("_", " "), label,
                (" -> %s" % dest) if dest else "")),
            "nav_section": n.get("_section") or n["_container"],
            "nav_container": n["_container"],
            "from_route": None,
            "destination_route": dest,
            "destination_page_id": dest_page,
            "required_roles": roles,
            "required_permissions": (
                (["capability:%s" % n["capability"]] if n.get("capability")
                 else [])
                + (["sales_permission:%s" % n["permission"]]
                   if n.get("permission") else [])),
            "visibility_conditions": conds,
            "action": n.get("action"),
            "aliases": nav_aliases(label, dest),
            "source_files": [n["_source"]],
            "status": status,
        })

    # ---- permission records ---------------------------------------------
    perm_records = []
    for c in caps:
        perm_records.append({
            "record_type": "permission",
            "id": "permission:capability:%s" % c["key"],
            "surface": "shared_platform",
            "title": c["label"],
            "name": "capability:%s" % c["key"],
            "description": ("%s Delegable to a customer organization: %s. "
                            "Granted at %s scope."
                            % (c["label"], "yes" if c["delegable"] else "no",
                               c["scope"])),
            "applies_to": sorted({rid for rid, r in api_records.items()
                                  if "capability:%s" % c["key"]
                                  in r["required_permissions"]}),
            "roles": (["god_admin"]
                      + (["org_admin", "super_admin"] if c["delegable"] else [])),
            "scopes": [c["scope"]],
            "requires_feature": c["requires_feature"],
            "delegable": c["delegable"],
            "aliases": [c["key"], c["label"].lower()[:60]],
            "source_files": ["app/services/capabilities.py"],
            "status": "active",
        })

    for name, desc, scopes, surf in ROLE_DOCS:
        perm_records.append({
            "record_type": "permission",
            "id": "permission:role:%s" % name,
            "surface": surf,
            "title": "Role: %s" % name,
            "name": "role:%s" % name,
            "description": desc,
            "applies_to": sorted({rid for rid, r in api_records.items()
                                  if name in r["required_roles"]}),
            "roles": [name],
            "scopes": scopes,
            "aliases": [name, name.replace("_", " ")],
            "source_files": ["app/deps.py", "app/models/sales_models.py",
                             "app/services/workspace_access.py"],
            "status": "active",
        })

    for key, desc, roles in SALES_PERMS:
        perm_records.append({
            "record_type": "permission",
            "id": "permission:sales:%s" % key,
            "surface": "brand_sales",
            "title": "Sales permission: %s" % key,
            "name": "sales_permission:%s" % key,
            "description": desc,
            "applies_to": sorted({n["id"] for n in nav_records
                                  if "sales_permission:%s" % key
                                  in n["required_permissions"]}),
            "roles": roles,
            "scopes": ["brand_sales_org"],
            "aliases": [key, key.replace("_", " ")],
            "source_files": ["app/services/sales_access.py",
                             "frontend/src/pages/sales/SalesShell.jsx"],
            "status": "active",
        })

    perm_records.append({
        "record_type": "permission",
        "id": "permission:org_override",
        "surface": "god_mode",
        "title": "Organization override (X-Org-Override)",
        "name": "header:X-Org-Override",
        "description": "The platform owner's way of scoping ordinary tenant "
                       "API calls to one customer organization without "
                       "creating a membership. Set by entering a customer from "
                       "God Mode and cleared on exit; god_admin only.",
        "applies_to": [],
        "roles": ["god_admin"],
        "scopes": ["platform", "org"],
        "aliases": ["org override", "viewing as", "enter the customer",
                    "impersonate an organization"],
        "source_files": ["frontend/src/api/client.js",
                         "frontend/src/components/ContextBanner.jsx",
                         "app/deps.py"],
        "status": "active",
    })

    # ---- feature gate records -------------------------------------------
    gate_records = []
    for key, label in sorted(features.items()):
        controls_ = sorted({rid for rid, r in api_records.items()
                            if key in r["entitlements"]})
        controls_ += sorted({n["id"] for n in nav_records
                             if ("entitlement feature '%s' enabled" % key)
                             in n["visibility_conditions"]})
        gate_records.append({
            "record_type": "feature_gate",
            "id": "gate:feature:%s" % key,
            "surface": "shared_platform",
            "title": "Feature entitlement: %s" % key,
            "name": key,
            "description": label,
            "gate_type": "entitlement",
            "controls": controls_,
            "conditions": ["the organization's enabled feature list contains "
                           "'%s' (a null list means every feature)" % key],
            "aliases": [key, label.lower()],
            "source_files": ["app/services/entitlements.py"],
            "status": "active",
        })
    for c in caps:
        gate_records.append({
            "record_type": "feature_gate",
            "id": "gate:capability:%s" % c["key"],
            "surface": "shared_platform",
            "title": "Capability: %s" % c["key"],
            "name": c["key"],
            "description": c["label"],
            "gate_type": "role" if not c["delegable"] else "entitlement",
            "controls": sorted({rid for rid, r in api_records.items()
                                if "capability:%s" % c["key"]
                                in r["required_permissions"]}),
            "conditions": (["granted at %s scope" % c["scope"]]
                           + (["requires feature '%s'" % c["requires_feature"]]
                              if c["requires_feature"] else [])
                           + (["delegable to a customer organization"]
                              if c["delegable"]
                              else ["never delegable - God only"])),
            "aliases": [c["key"]],
            "source_files": ["app/services/capabilities.py"],
            "status": "active",
        })
    for key, label in sorted(support_features.items()):
        gate_records.append({
            "record_type": "feature_gate",
            "id": "gate:support_feature:%s" % key,
            "surface": "support",
            "title": "Support entitlement: %s" % key,
            "name": key,
            "description": label,
            "gate_type": "package",
            "controls": [],
            "conditions": ["included by the organization's support package "
                           "(starter / growth / professional)"],
            "aliases": [key, label.lower()],
            "source_files": ["app/services/support_entitlements.py"],
            "status": "active",
        })
    for flag, cond in (
        ("launchOnly", "sidebar entry shown only when GET /launch/me reports "
                       "an implementation for the organization"),
        ("fiberOnly", "sidebar entry shown only for fiber-enabled "
                      "organizations"),
    ):
        gate_records.append({
            "record_type": "feature_gate",
            "id": "gate:nav_flag:%s" % flag,
            "surface": "customer_workspace",
            "title": "Navigation flag: %s" % flag,
            "name": flag,
            "description": cond,
            "gate_type": "feature_flag",
            "controls": sorted({n["id"] for n in nav_records
                                if any(flag.lower() in c.lower()
                                       for c in n["visibility_conditions"])}),
            "conditions": [cond],
            "aliases": [flag],
            "source_files": ["frontend/src/components/Layout.jsx"],
            "status": "active",
        })
    for flag, froles, cond in (
        ("requireAdmin", FLAG_ROLES["requireAdmin"],
         "route rendered only for org_admin, super_admin or god_admin"),
        ("requireSuperAdmin", FLAG_ROLES["requireSuperAdmin"],
         "route rendered only for super_admin or god_admin"),
        ("requireGodAdmin", FLAG_ROLES["requireGodAdmin"],
         "route rendered only for god_admin"),
    ):
        gate_records.append({
            "record_type": "feature_gate",
            "id": "gate:route_flag:%s" % flag,
            "surface": "shared_platform",
            "title": "Route guard flag: %s" % flag,
            "name": flag,
            "description": cond + " (ProtectedRoute in frontend/src/App.jsx). "
                                  "The matching server-side dependency is what "
                                  "actually enforces it.",
            "gate_type": "role",
            "controls": sorted({p["id"] for p in page_records
                                if p["guard"] == "ProtectedRoute"
                                and set(froles) == set(p["required_roles"])}),
            "conditions": [cond],
            "aliases": [flag],
            "source_files": ["frontend/src/App.jsx", "app/deps.py"],
            "status": "active",
        })

    # ---- integration records --------------------------------------------
    integ_records = []
    for spec in INTEGRATIONS:
        eps = sorted({rid for rid, r in api_records.items()
                      if any(r["path"] == h or r["path"].startswith(h + "/")
                             or r["path"].startswith(h + "{")
                             for h in spec["path_hints"])})
        pages = [page_id(rt) for rt in spec["route_hints"] if rt in route_index]
        acts = sorted({a["id"] for a in action_records
                       if any(any(call.split(" ", 1)[1].startswith(h)
                                  for h in spec["path_hints"])
                              for call in a["api_calls"])})
        status = "active" if eps else "unresolved"
        if not eps:
            gaps.append("integration '%s' matched no registered endpoint path"
                        % spec["name"])
        integ_records.append({
            "record_type": "integration",
            "id": "integration:%s" % slug(spec["name"]),
            "surface": "shared_platform",
            "title": spec["name"],
            "name": spec["name"],
            "description": spec["purpose"],
            "purpose": spec["purpose"],
            "connection_entry_points": pages,
            "related_pages": pages,
            "related_actions": acts[:40],
            "related_api_endpoints": eps[:120],
            "scope": spec["scope"],
            "credential_owner": spec["credential_owner"],
            "aliases": spec["aliases"],
            "source_files": sorted({api_records[e]["_module"] for e in eps})[:12],
            "status": status,
        })

    # ---- workflow edge records ------------------------------------------
    wf_records = []
    for spec in WORKFLOW_EDGES:
        eps = sorted({rid for rid, r in api_records.items()
                      if any(r["path"] == h or r["path"].startswith(h)
                             for h in spec["api_hints"])})
        unresolved_refs = []
        for key in ("from", "to"):
            val = spec[key]
            if val.startswith("/") and val not in route_index:
                unresolved_refs.append("%s route %s not registered"
                                       % (key, val))
        status = "active"
        if unresolved_refs or not eps:
            status = "unresolved"
            gaps.append("workflow %s unresolved: %s"
                        % (spec["id"], "; ".join(unresolved_refs)
                           or "no matching endpoints"))
        wf_records.append({
            "record_type": "workflow_edge",
            "id": spec["id"],
            "surface": spec["surface"],
            "title": "%s -> %s" % (spec["from"], spec["to"]),
            "description": spec["description"],
            "from": spec["from"],
            "to": spec["to"],
            "from_page_id": (page_id(spec["from"])
                             if spec["from"] in route_index else None),
            "to_page_id": (page_id(spec["to"])
                           if spec["to"] in route_index else None),
            "trigger": spec["trigger"],
            "action_id": None,
            "api_endpoint_ids": eps[:60],
            "conditions": [],
            "automatic": spec["automatic"],
            "aliases": spec["aliases"],
            "source_files": sorted({api_records[e]["_module"] for e in eps})[:10],
            "status": status,
        })

    # ---- finish ----------------------------------------------------------
    for rec in api_records.values():
        rec["called_by_pages"] = sorted(set(rec["called_by_pages"]))
        rec["called_by_actions"] = sorted(set(rec["called_by_actions"]))
        rec.pop("_module", None)
        rec.pop("_line", None)

    for f in unmounted:
        gaps.append("router file present in app/routers but never reached from "
                    "app/main.py, so none of its routes are served: %s" % f)

    for a in action_records:
        if a["status"] != "unresolved":
            continue
        if a.get("delegates_to"):
            gaps.append("action '%s' (%s) delegates to a callback prop (%s) "
                        "supplied outside its own module; its effect is "
                        "unproven here: %s"
                        % (a["label"], a["route"], ", ".join(a["delegates_to"]),
                           a["source_files"][0]))
        else:
            gaps.append("action '%s' (%s) could not be bound to an API call or "
                        "a destination from source: %s"
                        % (a["label"], a["route"], a["source_files"][0]))

    records = (page_records + nav_records + action_records
               + list(api_records.values()) + wf_records + perm_records
               + integ_records + gate_records)

    for s in sorted({r["surface"] for r in records}):
        if s not in SURFACES:
            gaps.append("record uses surface '%s' which is not in the declared "
                        "list" % s)

    nav_dests = {(n["destination_route"] or "").split("#")[0]
                 for n in nav_records if n["destination_route"]}
    unnavigated = sorted(p["route"] for p in page_records
                         if p["route"] not in nav_dests
                         and ":" not in p["route"] and "*" not in p["route"])

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
                                    .replace(microsecond=0).isoformat()
                                    .replace("+00:00", "Z"),
        "git_commit_sha": git("rev-parse", "HEAD"),
        "git_branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_dirty": code_is_dirty(),
        "repository": git("config", "--get", "remote.origin.url"),
        "generator": "scripts/generate_concierge_map.py",
        "validator": "scripts/validate_concierge_map.py",
        "record_count": len(records),
        "record_counts_by_type": count_by(records, "record_type"),
        "record_counts_by_surface": count_by(records, "surface"),
        "record_counts_by_status": count_by(records, "status"),
        "surfaces": sorted({r["surface"] for r in records}),
        "source_of_truth": {
            "frontend_routes": "frontend/src/App.jsx",
            "navigation": sorted({rel(s[0]) for s in NAV_SOURCES}),
            "backend_router_registration": "app/main.py",
            "authorization": ["app/deps.py", "app/services/sales_access.py",
                              "app/services/capabilities.py",
                              "app/services/executive_authority.py",
                              "app/services/workspace_access.py"],
            "entitlements": ["app/services/entitlements.py",
                             "app/services/support_entitlements.py"],
        },
        "coverage": {
            "frontend_routes_declared": len(routes),
            "page_records": len(page_records),
            "pages_unresolved": sum(1 for p in page_records
                                    if p["status"] == "unresolved"),
            "navigation_entries_declared": len(nav_items),
            "navigation_records": len(nav_records),
            "navigation_unresolved": sum(1 for n in nav_records
                                         if n["status"] == "unresolved"),
            "endpoints_registered": len(api_records),
            "endpoints_shadowed": sum(1 for r in api_records.values()
                                      if r["status"] == "legacy"),
            "router_files_never_reached": len(unmounted),
            "actions": len(action_records),
            "actions_with_resolved_api": sum(1 for a in action_records
                                             if a["api_endpoint_ids"]),
            "actions_unresolved": sum(1 for a in action_records
                                      if a["status"] == "unresolved"),
            "endpoints_reached_by_a_page": sum(1 for r in api_records.values()
                                               if r["called_by_pages"]),
            "routes_with_no_navigation_entry": unnavigated,
        },
        "generation_status": "complete",
        "known_gaps": sorted(set(gaps)),
    }
    return records, manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate the concierge map.")
    ap.add_argument("--out", default=os.path.join("docs", "concierge"))
    args = ap.parse_args(argv)

    out_dir = (args.out if os.path.isabs(args.out)
               else os.path.join(ROOT, args.out))
    os.makedirs(out_dir, exist_ok=True)

    records, manifest = build()

    map_path = os.path.join(out_dir, "advisorflow_concierge_map.jsonl")
    with open(map_path, "w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False))
            fh.write("\n")

    man_path = os.path.join(out_dir, "advisorflow_concierge_manifest.json")
    with open(man_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("wrote %s (%d records)" % (rel(map_path), len(records)))
    print("wrote %s" % rel(man_path))
    print("known gaps: %d" % len(manifest["known_gaps"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
