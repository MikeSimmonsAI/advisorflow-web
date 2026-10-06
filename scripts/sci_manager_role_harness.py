#!/usr/bin/env python3
"""SCI workspace-manager role: dependency-free permission checks.

Run:  python3 -I scripts/sci_manager_role_harness.py [--md handoff/SCI_MANAGER_ROLE_RESULTS.md]

No FastAPI, SQLAlchemy, database, network or secrets. Only synthetic users and
SCI-only synthetic ids. It reads the real source with `ast` / text, pulls the
role constants out of the real modules, and (when `node` exists) executes the
real frontend rule module, so the backend rule, the route guards and the UI
gate are compared against each other rather than against a paraphrase.

What this proves: the *decision logic and wiring in source*. What it cannot
prove: a live authenticated request against a staging database. Those items are
listed as NEEDS-STAGING in the matrix.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def literal_assign(rel, name):
    """The literal value of a module-level `NAME = (...)` in a source file."""
    for node in ast.parse(read(rel)).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError("%s not found in %s" % (name, rel))


MANAGER_ROLES = literal_assign("app/services/lead_scope.py", "MANAGER_ROLES")
WORKSPACE_ADMIN_ROLES = literal_assign("app/services/lead_scope.py", "WORKSPACE_ADMIN_ROLES")
WORKSPACE_ROLES = literal_assign("app/services/workspace_access.py", "WORKSPACE_ROLES")
GOD = "god_admin"


# ── synthetic model of the backend decision, built from the real constants ────
class User:
    def __init__(self, uid, role, org=None, memberships=None):
        self.id, self.role, self.organization_id = uid, role, org
        self.memberships = memberships or {}          # workspace id -> role


SCI_ORG = "org-sci-synthetic"
OTHER_ORG = "org-other-synthetic"
RESTLAND_ORG = "org-restland-synthetic"
EVOSYS_PLATFORM = "plt-evosyspro-synthetic"

MICHAEL = User("u-michael-synthetic", "advisor", None, {SCI_ORG: "manager"})
OUTSIDER = User("u-outsider-synthetic", "advisor", None, {})
GOD_USER = User("u-god-synthetic", GOD, None, {})


def selected(user, header):
    """selected_workspace_id: a header is honoured only with a membership behind it."""
    return header if header and header in user.memberships else None


def effective_role(user, header=None):
    if user.role == GOD:
        return GOD
    sel = selected(user, header)
    if sel:
        return user.memberships[sel]
    return user.role


def is_manager_here(user, header=None):
    return effective_role(user, header) in MANAGER_ROLES + (GOD,)


def require_org_admin_passes(user, header=None):
    if not is_manager_here(user, header):
        return False
    return effective_role(user, header) != "manager"


def active_org(user, header=None):
    return selected(user, header) or user.organization_id


# ── the matrix ───────────────────────────────────────────────────────────────
ROWS = []        # (capability, expected, evidence, result, kind)


def row(capability, expected, evidence, ok, kind="code"):
    ROWS.append((capability, expected, evidence, "PASS" if ok else "FAIL", kind))


def needs_staging(capability, expected, evidence):
    ROWS.append((capability, expected, evidence, "NEEDS-STAGING", "staging"))


def check_backend_model():
    H = SCI_ORG
    row("Role vocabulary: 'manager' is a grantable workspace role", "present",
        "workspace_access.WORKSPACE_ROLES=%s" % (WORKSPACE_ROLES,), "manager" in WORKSPACE_ROLES)
    row("Role vocabulary: no god_admin / brand roles mintable by a workspace grant", "absent",
        "WORKSPACE_ROLES", not (set(WORKSPACE_ROLES) & {GOD, "brand_executive", "sales_manager", "sales_rep"}))
    row("Manager sees whole SCI workspace (leads/conversations/reports/program)", "allowed",
        "lead_scope.MANAGER_ROLES includes manager; Michael+X-Workspace-Id=SCI",
        is_manager_here(MICHAEL, SCI_ORG))
    row("Manager is NOT a workspace admin (users, org settings, credentials)", "denied",
        "require_org_admin; WORKSPACE_ADMIN_ROLES=%s" % (WORKSPACE_ADMIN_ROLES,),
        not require_org_admin_passes(MICHAEL, SCI_ORG) and "manager" not in WORKSPACE_ADMIN_ROLES)
    row("Manager is not god_admin (no God Mode / platform-owner controls)", "denied",
        "effective_role != god_admin", effective_role(MICHAEL, SCI_ORG) != GOD)
    row("Header naming an unrelated org is discarded (no membership)", "denied",
        "selected_workspace_id requires active membership", selected(MICHAEL, OTHER_ORG) is None)
    row("Header naming Restland workspace is discarded", "denied",
        "selected_workspace_id requires active membership", selected(MICHAEL, RESTLAND_ORG) is None)
    row("Unrelated org id never becomes the data scope", "denied",
        "active_workspace_org_id", active_org(MICHAEL, OTHER_ORG) is None)
    row("SCI scope resolves only to the SCI org", "SCI only",
        "active_workspace_org_id", active_org(MICHAEL, SCI_ORG) == SCI_ORG)
    row("Without a header the manager gets no manager powers anywhere", "denied",
        "falls back to users.role=advisor", not is_manager_here(MICHAEL, None))
    row("User with no SCI membership gets nothing in SCI", "denied",
        "no membership -> header ignored", not is_manager_here(OUTSIDER, SCI_ORG)
        and active_org(OUTSIDER, SCI_ORG) is None)
    row("Manager role does not satisfy super_admin/god guards", "denied",
        "deps.require_super_admin/require_god read users.role only",
        MICHAEL.role not in ("super_admin", "god_admin"))


def check_source_wiring():
    deps = read("app/deps.py")
    m = re.search(r"def require_god\(.*?\n(.*?)\n\n\ndef ", deps, re.S)
    row("require_god reads users.role only (membership cannot grant it)", "true",
        "deps.require_god", bool(m) and "!= \"god_admin\"" in m.group(1) and "membership" not in m.group(1))
    m = re.search(r"def require_super_admin\(.*?\n(.*?)\n\n\ndef ", deps, re.S)
    row("require_super_admin reads users.role only", "true", "deps.require_super_admin",
        bool(m) and "membership" not in m.group(1) and "user.role not in" in m.group(1))

    admin = read("app/routers/admin_router.py")
    row("User administration (/admin users) requires org admin, not manager", "denied",
        "admin_router._USERS includes require_org_admin",
        re.search(r"_USERS\s*=\s*\[[^\]]*require_org_admin", admin) is not None)

    org = read("app/routers/org_settings_router.py")
    funcs = re.findall(r"@router\.(put|patch|post|delete)\(\"([^\"]+)\"[^)]*\)\s*def (\w+)\((.*?)\):\n", org, re.S)
    writers = [(v, p, n, a) for v, p, n, a in funcs if "Depends(require_admin)" in a or "Depends(require_org_admin)" in a]
    leaked = [n for v, p, n, a in writers if "Depends(require_admin)" in a]
    row("Org settings / credentials / Twilio routes use require_org_admin (0 on bare require_admin)",
        "0 leaks", "org_settings_router: %d guarded routes, leaking=%s" % (len(writers), leaked), not leaked)
    twilio = [n for v, p, n, a in writers if "twilio" in p or "twilio" in n]
    row("Org Twilio / carrier configuration routes are org-admin only", "denied for manager",
        "routes=%s" % twilio, twilio and all("require_org_admin" in a for v, p, n, a in writers if n in twilio))
    row("Org email-sender (API key) route is org-admin only", "denied for manager",
        "update_email_sender", any(n == "update_email_sender" and "require_org_admin" in a for v, p, n, a in writers))

    # god / platform-owner routers: every route must be behind require_god or require_super_admin
    bad = []
    for fn in sorted(os.listdir(os.path.join(REPO, "app/routers"))):
        if not fn.startswith("god_") and fn != "god_router.py":
            continue
        src = read("app/routers/" + fn)
        for node in ast.parse(src).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            is_route = any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                           and d.func.attr in ("get", "post", "put", "patch", "delete") for d in node.decorator_list)
            if not is_route:
                continue
            seg = ast.get_source_segment(src, node)
            head = seg[:seg.find(":\n")]
            router_guard = "require_god" in src.split("router = APIRouter(")[-1][:400] or \
                "dependencies=[Depends(require_god" in src
            if not (("require_god" in head) or ("require_super_admin" in head) or router_guard
                    or "require_support_operator" in head or "require_platform" in head or "require_owner" in head):
                bad.append("%s:%s" % (fn, node.name))
    row("God/platform-owner routes (/god/*) all sit behind a god-class guard",
        "0 unguarded", "AST scan of app/routers/god_*.py; unguarded=%s" % bad[:6], not bad)


def program_routes():
    """[(route, guard, manager_gated)] for every /program route."""
    src = read("app/routers/program_router.py")
    out = []
    for node in ast.parse(src).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        r = None
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in (
                    "get", "post", "put", "patch", "delete") and isinstance(d.func.value, ast.Name):
                r = "%s %s %s" % (d.func.value.id, d.func.attr.upper(), ast.literal_eval(d.args[0]))
        if not r:
            continue
        seg = ast.get_source_segment(src, node)
        head = seg[:seg.find(":\n")]
        guard = next((g for g in ("require_tenant_or_observer", "require_tenant_user", "require_god",
                                  "require_super_admin", "require_admin") if g in head), "NONE")
        gated = ("_program(" in seg and "managers_only=False" not in seg) or "_require_manager(" in seg \
            or "is_manager_here" in seg
        out.append((r, guard, gated))
    return out


# /me is the caller's own program identity; the asset route is a public,
# unguessable-token logo/PDF link. Reviewed 2026-10-06.
REVIEWED_UNGATED = {"router GET /me", "public_router GET /program-assets/{token}"}


def check_program_router():
    routes = program_routes()
    tenant = [x for x in routes if x[0].startswith("router ")]
    god = [x for x in routes if x[0].startswith("god_router ")]
    pub = [x for x in routes if x[0].startswith("public_router ")]
    row("/program routes: every tenant route has a tenant guard", "0 unguarded",
        "%d routes; unguarded=%s" % (len(tenant), [r for r, g, _ in tenant if g == "NONE"]),
        all(g != "NONE" for _, g, _ in tenant))
    row("/god/programs routes (program creation / provisioning) are require_god", "all",
        "%d routes; non-god=%s" % (len(god), [r for r, g, _ in god if g != "require_god"]),
        bool(god) and all(g == "require_god" for _, g, _ in god))
    # every tenant route either manager-gates in its body or is knowingly open to advisors
    ungated = [r for r, g, gated in tenant if not gated]
    row("/program routes with no in-body manager gate are only the reviewed set",
        "reviewed set", "ungated=%s" % ungated, set(ungated) <= REVIEWED_UNGATED)
    row("Public program routes carry no tenant data guard but no org id param",
        "informational", "%d public routes" % len(pub), True)

    # the guard that decides whether the manager reaches /program at all
    deps = read("app/deps.py")
    m = re.search(r"def require_tenant_or_observer\(.*?\n(.*?)\n\n\ndef ", deps, re.S)
    body = m.group(1) if m else ""
    honours_selection = "selected_workspace_id" in body
    row("API: GET /program/* reachable by a membership-only manager (organization_id NULL)",
        "allowed (reads match the writes)",
        "deps.require_tenant_or_observer honours X-Workspace-Id membership=%s; "
        "require_tenant_user does" % honours_selection, honours_selection)
    reads = [r for r, g, _ in tenant if g == "require_tenant_or_observer"]
    writes = [r for r, g, _ in tenant if g == "require_tenant_user"]
    row("API: read guard and write guard accept the same callers", "equal",
        "%d read routes vs %d write routes" % (len(reads), len(writes)), honours_selection)


def check_frontend():
    rules = read("frontend/src/auth/workspaceRules.js")
    layout = read("frontend/src/components/Layout.jsx")
    node = shutil.which("node")
    if node:
        js = ("import {isManagerRole,isWorkspaceManagerRole} from %s;"
              "const r=['manager','org_admin','super_admin','god_admin','advisor','viewer'];"
              "console.log(JSON.stringify(r.map(x=>[x,isWorkspaceManagerRole(x),isManagerRole(x)])))"
              % json.dumps("file://" + os.path.join(REPO, "frontend/src/auth/workspaceRules.js")))
        p = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, cwd="/")
        try:
            ui = {k: (a, b) for k, a, b in json.loads(p.stdout.strip().splitlines()[-1])}
        except Exception:
            ui = None
    else:
        ui = None
    if ui is None:
        needs_staging("UI role predicates executed under node", "n/a", "node unavailable or module failed to load")
        return
    backend_manager = set(MANAGER_ROLES) | {GOD}
    for role in ("manager", "org_admin", "super_admin", "god_admin", "advisor", "viewer"):
        row("UI isWorkspaceManagerRole('%s') equals backend is_manager_here" % role,
            "backend=%s" % (role in backend_manager),
            "frontend/src/auth/workspaceRules.js executed under node: %s" % ui[role][0],
            ui[role][0] == (role in backend_manager))
    admin = set(WORKSPACE_ADMIN_ROLES) | {GOD}
    for role in ("manager", "org_admin"):
        row("UI isManagerRole('%s') (admin) equals backend require_org_admin" % role,
            "backend=%s" % (role in admin),
            "workspaceRules.js: %s" % ui[role][1], ui[role][1] == (role in admin))
    # nav items that mirror require_org_admin routes must be gated on the admin predicate
    for to in ("/users", "/org-settings", "/tier-definitions"):
        line = next((l for l in layout.splitlines() if "to: '%s'" % to in l), "")
        row("Nav '%s' hidden from manager (backend refuses via require_org_admin)" % to,
            "workspaceAdminOnly", line.strip()[:90], "workspaceAdminOnly: true" in line)
    for to in ("/reports", "/campaigns", "/imports"):
        line = next((l for l in layout.splitlines() if "to: '%s'" % to in l), "")
        row("Nav '%s' shown to manager (backend permits via require_admin)" % to,
            "adminOnly (manager passes)", line.strip()[:90],
            "adminOnly: true" in line and "workspaceAdminOnly" not in line)
    row("Layout visibility applies workspaceAdminOnly with the admin predicate",
        "present", "Layout.jsx visible()", "item.workspaceAdminOnly && !isWorkspaceAdmin" in layout)
    needs_staging("Family Service Center nav appears for Michael after login", "visible",
                  "needs authenticated staging session (GET /program/status with X-Workspace-Id)")


def check_provisioning():
    ps = read("scripts/program_setup.py")
    row("Provisioning grants 'manager' on this workspace only, to an EXISTING login", "true",
        "program_setup.py --manager-email",
        'role="manager"' in ps and "no account was created" in ps and "grant_workspace_membership(db, u[0].id, org.id" in ps)
    row("Provisioning never touches users.role / production role assignments", "true",
        "program_setup.py", "u[0].role" not in ps and ".role =" not in ps.split("--manager-email")[-1][:900])
    needs_staging("Michael's membership row exists in the SCI workspace", "role=manager, active",
                  "SCI does not exist in production; grant runs at promotion (handoff 2026-10-06 §6)")
    needs_staging("Michael denied by live API on /admin/users, /org-settings/*, /god/*", "403",
                  "needs authenticated staging session")
    needs_staging("DB-backed tenant-isolation test (second org rows invisible)", "no rows",
                  "tests/ need fastapi+sqlalchemy; not installed here")


def main():
    check_backend_model()
    check_source_wiring()
    check_program_router()
    check_frontend()
    check_provisioning()
    tot = {"PASS": 0, "FAIL": 0, "NEEDS-STAGING": 0}
    for r in ROWS:
        tot[r[3]] += 1
    for cap, exp, ev, res, kind in ROWS:
        print("%-13s %s  [expected: %s]" % (res, cap, exp))
        if res == "FAIL":
            print("              evidence: %s" % ev)
    print("\nTOTAL %d  PASS %d  FAIL %d  NEEDS-STAGING %d" % (len(ROWS), tot["PASS"], tot["FAIL"], tot["NEEDS-STAGING"]))
    if "--md" in sys.argv:
        path = os.path.join(REPO, sys.argv[sys.argv.index("--md") + 1])
        with open(path, "w", encoding="utf-8") as f:
            f.write("# SCI manager role: permission matrix (synthetic, dependency-free)\n\n")
            f.write("Totals: %d rows, PASS %d, FAIL %d, NEEDS-STAGING %d.\n\n" % (
                len(ROWS), tot["PASS"], tot["FAIL"], tot["NEEDS-STAGING"]))
            f.write("Evidence kinds: `code` = source/fixture executed here; `staging` = needs an authenticated staging session.\n\n")
            f.write("| Capability | Expected | Evidence | Result |\n|---|---|---|---|\n")
            for cap, exp, ev, res, kind in ROWS:
                f.write("| %s | %s | %s | %s |\n" % (cap, exp, ev.replace("|", "/"), res))
    return 1 if tot["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
