#!/usr/bin/env python3
"""SCI staging manager access: dependency-free checks (no DB, network, secrets).

Run:  python3 -I scripts/sci_staging_access_harness.py

Executes the real frontend module `auth/sciDoor.js` under node and statically
inspects the real backend / App.jsx source. Behaviour against a database is in
tests/test_sci_staging_bootstrap.py (needs pytest) and the staging live check.
"""
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = []


def read(rel):
    with open(os.path.join(REPO, rel), encoding="utf-8") as f:
        return f.read()


def check(name, ok):
    rows.append((name, bool(ok)))


BS = read("app/services/sci_staging_bootstrap.py")
tree = ast.parse(BS)

# ── backend, static ──
check("inert unless enabled flag",
      "_truthy(ENV_ENABLED)" in BS and BS.index("_truthy(ENV_ENABLED)") < BS.index("preflight(db)\n"))
check("fails closed outside staging", "environment.ENV_STAGING" in BS and "not_staging_environment" in BS)
check("refuses production-looking DB", "looks_like_production_db" in BS)
check("requires sci_staging database identity", "sci_staging" in BS and "database_not_sci_staging" in BS)
check("org id and exact name must match", "org_name_mismatch" in BS and "SCI_ORG_NAME" in BS)
check("blank email fails closed", "email_blank_or_invalid" in BS)
check("no TEMP_PASSWORD env lookup", not re.search(r"_env\(.*TEMP_PASSWORD", BS) and "environ.get(\"SCI_STAGING_MANAGER_TEMP" not in BS)
check("user created advisor, no org, active",
      'role="advisor"' in BS and "organization_id=None" in BS and "is_active=True" in BS)
check("password random + hashed inline", "hash_password(secrets.token_urlsafe(" in BS)
check("existing user reused by lower(email)", "func.lower(User.email) == email" in BS and '"reused"' in BS)
check("membership via canonical helper, role manager",
      "grant_workspace_membership(db, user.id, org_id, MANAGER_ROLE" in BS and 'MANAGER_ROLE = "manager"' in BS)
mem_src = BS.split("def _ensure_membership")[1].split("def _activation_state")[0]
check("no elevated roles granted", not re.search(r"org_admin|god_admin|super_admin|billing", mem_src))
check("activation via staff_activation.issue", "staff_activation.issue(" in BS)
check("activation base URL fixed to staging frontend",
      'FRONTEND_BASE = "https://sci-staging-frontend.onrender.com"' in BS)
check("send skipped when accepted", "skipped_accepted" in BS)
check("send skipped when marker exists (no restart spam)", "skipped_already_sent" in BS and "sent_markers" in BS)
fail_at = BS.index('if not result.get("success")')
check("failed send revokes unsent link and writes no marker",
      "STAFF_INVITE_REVOKED" in BS and fail_at < BS.index("log_action(\n        db, None, actor.id, action=SENT_ACTION"))
check("raw/url cleared after send", "raw = None" in BS and "url = None" in BS)

leaks = []
# _build_message hands the mail body to the mailer; it is the one legitimate carrier.
skip = {id(n) for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == "_build_message" for n in ast.walk(f)}
for node in ast.walk(tree):
    if id(node) in skip:
        continue
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "log"):
        leaks += [a.id for a in ast.walk(node) if isinstance(a, ast.Name) and a.id in ("raw", "url", "body")]
    if isinstance(node, ast.Return) and node.value is not None:
        leaks += [a.id for a in ast.walk(node.value) if isinstance(a, ast.Name) and a.id in ("raw", "url", "body")]
check("raw token/url never logged or returned", not leaks)
check("audit marker carries prefix only",
      '"token_prefix": prefix' in BS and "raw" not in BS.split("after={")[1].split("}")[0])

MG = read("app/migrate.py")
check("wired into migrate after schema step, before outcome log",
      MG.index("run_auto_migrations(engine)") < MG.index("sci_staging_bootstrap.run(") < MG.index("4. SAY WHAT HAPPENED"))
check("bootstrap failure cannot change exit code", "deploy continues" in MG)
check("production render.yaml untouched by this feature", "sci_staging_bootstrap" not in read("render.yaml"))

# ── frontend ──
APP = read("frontend/src/App.jsx")
door = APP.split("function SciFrontDoor")[1][:1800]
check("/sci route registered", '<Route path="/sci" element={<SciFrontDoor />} />' in APP)
check("/sci requires auth", 'isAuthenticated()) return <Navigate to="/login"' in door)
check("/sci opens Program Center", '<Navigate to="/program" replace />' in door)
check("post-login SCI landing wired in HomeRedirect", "shouldLandInSci(ctx)" in APP)

node = shutil.which("node")
o = {}
if node:
    d = tempfile.mkdtemp(prefix="sci_door_")
    shutil.copy(os.path.join(REPO, "frontend/src/auth/sciDoor.js"), os.path.join(d, "sciDoor.mjs"))
    script = os.path.join(d, "run.mjs")
    with open(script, "w") as f:
        f.write("""
import { decideSciDoor, shouldLandInSci, findSciWorkspace } from './sciDoor.mjs'
const sci = { organization_id: 'sci1', organization_name: 'Service Corporation International', role: 'manager' }
const other = { organization_id: 'o2', organization_name: 'Restland', role: 'advisor' }
const mk = (ws, extra) => Object.assign({ workspace_contexts: ws, workspace_count: ws.length, platform_contexts: [],
  has_back_office: false,
  default_context: ws.length === 1 ? { type: 'workspace', organization_id: ws[0].organization_id } : { type: 'workspace_selector' } }, extra || {})
const god = { platform_contexts: [{ path: '/god' }], has_back_office: true }
console.log(JSON.stringify({
 manager_enter: decideSciDoor(mk([sci])),
 multi_enter: decideSciDoor(mk([other, sci])),
 outsider_denied: decideSciDoor(mk([other])),
 nobody_denied: decideSciDoor(mk([])),
 null_denied: decideSciDoor(null),
 god_no_member: decideSciDoor(mk([], god)),
 god_with_member: decideSciDoor(mk([sci], god)),
 land_sci_only: shouldLandInSci(mk([sci])),
 no_land_other_only: shouldLandInSci(mk([other])),
 no_land_two: shouldLandInSci(mk([sci, other])),
 no_land_backoffice: shouldLandInSci(mk([sci], { has_back_office: true })),
 no_land_null: shouldLandInSci(null),
 find_other_is_null: findSciWorkspace(mk([other])),
}))
""")
    r = subprocess.run([node, script], capture_output=True, text=True)
    o = json.loads(r.stdout) if r.returncode == 0 else {}
check("node executed sciDoor.js", bool(o))
if o:
    check("SCI manager enters SCI", o["manager_enter"] == {"state": "enter", "organizationId": "sci1"})
    check("multi-workspace member selects only SCI", o["multi_enter"]["organizationId"] == "sci1")
    check("non-SCI user denied", o["outsider_denied"]["state"] == "denied")
    check("no-workspace user denied", o["nobody_denied"]["state"] == "denied")
    check("missing context fails closed", o["null_denied"]["state"] == "denied")
    check("god without SCI membership -> console, no auto-select", o["god_no_member"]["state"] == "god")
    check("god with SCI membership enters SCI", o["god_with_member"]["state"] == "enter")
    check("SCI-only manager lands in /sci", o["land_sci_only"] is True)
    check("other-only tenant unchanged", o["no_land_other_only"] is False)
    check("two-workspace user unchanged", o["no_land_two"] is False)
    check("back-office user unchanged", o["no_land_backoffice"] is False)
    check("null ctx unchanged", o["no_land_null"] is False)
    check("no SCI workspace -> null", o["find_other_is_null"] is None)

bad = [n for n, ok in rows if not ok]
for n, ok in rows:
    print(("PASS " if ok else "FAIL ") + n)
print("\n%d checks, %d failed" % (len(rows), len(bad)))
sys.exit(1 if bad else 0)
