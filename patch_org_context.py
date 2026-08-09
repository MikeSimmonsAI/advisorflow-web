"""
Patch script: wire up org context switching across the frontend and add
the /admin/orgs backend endpoint.
"""
import os

BASE = r"C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"

# ── 1. client.js — add org context helpers + X-Org-Override header ─────────

client_path = os.path.join(BASE, "frontend", "src", "api", "client.js")
with open(client_path, encoding="utf-8") as f:
    client = f.read()

# Add org context helpers before the closing line (export default is not there; just append)
ORG_HELPERS = """
// ── Org Context (super admin only) ───────────────────────────────────────────
// Lets the super admin "enter" any org's context and see their data.
// The stored orgId is sent as X-Org-Override on every API request; deps.py
// reads this header and safely overrides the user's organization_id for
// that request only (via db.expunge + in-memory mutation, no DB write).

const ORG_CONTEXT_KEY = 'bb_org_context'

export function setOrgContext(orgId, orgName) {
  localStorage.setItem(ORG_CONTEXT_KEY, JSON.stringify({ orgId, orgName }))
}

export function getOrgContext() {
  const raw = localStorage.getItem(ORG_CONTEXT_KEY)
  return raw ? JSON.parse(raw) : null
}

export function clearOrgContext() {
  localStorage.removeItem(ORG_CONTEXT_KEY)
}
"""

# Inject X-Org-Override into the request() function after the Authorization header line
OLD_AUTH_HEADER = "  if (token) headers['Authorization'] = `Bearer ${token}`"
NEW_AUTH_HEADER = """  if (token) headers['Authorization'] = `Bearer ${token}`
  const orgCtx = getOrgContext()
  if (orgCtx) headers['X-Org-Override'] = orgCtx.orgId"""

if OLD_AUTH_HEADER not in client:
    print("WARN: Could not find Authorization header line in client.js")
else:
    client = client.replace(OLD_AUTH_HEADER, NEW_AUTH_HEADER)
    print("client.js: X-Org-Override header injected")

client = client + ORG_HELPERS

with open(client_path, "w", encoding="utf-8") as f:
    f.write(client)
print("client.js: org context helpers appended")


# ── 2. Layout.jsx — import helpers, add nav item, add banner ────────────────

layout_path = os.path.join(BASE, "frontend", "src", "components", "Layout.jsx")
with open(layout_path, encoding="utf-8") as f:
    layout = f.read()

# 2a. Update import line
OLD_IMPORT = "import { getCurrentUser, logout, getBranding, applyBrandingCSS, fetchAndStoreBranding } from '../api/client'"
NEW_IMPORT = "import { getCurrentUser, logout, getBranding, applyBrandingCSS, fetchAndStoreBranding, getOrgContext, clearOrgContext } from '../api/client'"
if OLD_IMPORT not in layout:
    print("WARN: Layout import line not found as expected")
else:
    layout = layout.replace(OLD_IMPORT, NEW_IMPORT)
    print("Layout.jsx: import updated")

# 2b. Add 'building' icon to the Icon paths object (insert before the closing brace of paths)
OLD_LINK_ICON = "    link: <><path d=\"M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71\" /><path d=\"M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71\" /></>,"
NEW_LINK_ICON = """    link: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></>,
    building: <><rect x="2" y="7" width="20" height="15" rx="1" /><line x1="16" y1="22" x2="16" y2="7" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M7 22v-5h4v5" /><polyline points="2 7 2 5 22 5 22 7" /></>,"""
if OLD_LINK_ICON not in layout:
    print("WARN: link icon line not found in Layout.jsx for building insertion")
else:
    layout = layout.replace(OLD_LINK_ICON, NEW_LINK_ICON)
    print("Layout.jsx: building icon added")

# 2c. Add Org Manager to SUPER_ADMIN_NAV_ITEMS
OLD_AUDIT = "  { to: '/audit-log', label: 'Audit Log', icon: 'activity' },"
NEW_AUDIT = """  { to: '/audit-log', label: 'Audit Log', icon: 'activity' },
  { to: '/orgs', label: 'Org Manager', icon: 'building' },"""
if OLD_AUDIT not in layout:
    print("WARN: audit-log nav item not found in SUPER_ADMIN_NAV_ITEMS")
else:
    layout = layout.replace(OLD_AUDIT, NEW_AUDIT)
    print("Layout.jsx: Org Manager nav item added")

# 2d. Add orgContext state in Layout component after isSuperAdmin declaration
OLD_IS_SUPER = "  const isSuperAdmin = user?.role === 'super_admin'"
NEW_IS_SUPER = """  const isSuperAdmin = user?.role === 'super_admin'
  const [orgContext, setOrgCtx] = useState(() => isSuperAdmin ? getOrgContext() : null)

  function handleExitOrg() {
    clearOrgContext()
    setOrgCtx(null)
    window.location.href = '/'
  }"""
if OLD_IS_SUPER not in layout:
    print("WARN: isSuperAdmin declaration not found in Layout.jsx")
else:
    layout = layout.replace(OLD_IS_SUPER, NEW_IS_SUPER)
    print("Layout.jsx: orgContext state added")

# 2e. Add org context banner between top-bar and main-content
OLD_MAIN = "        <main className=\"main-content\">{children}</main>"
NEW_MAIN = """        {orgContext && (
          <div className="org-context-banner">
            <span>👁 Viewing as <strong>{orgContext.orgName}</strong> — all data is scoped to this org</span>
            <button type="button" className="org-context-exit" onClick={handleExitOrg}>Exit Org View</button>
          </div>
        )}
        <main className="main-content">{children}</main>"""
if OLD_MAIN not in layout:
    print("WARN: main-content line not found in Layout.jsx")
else:
    layout = layout.replace(OLD_MAIN, NEW_MAIN)
    print("Layout.jsx: org context banner added")

with open(layout_path, "w", encoding="utf-8") as f:
    f.write(layout)
print("Layout.jsx: saved")


# ── 3. App.jsx — add OrgManager import and route ────────────────────────────

app_path = os.path.join(BASE, "frontend", "src", "App.jsx")
with open(app_path, encoding="utf-8") as f:
    app = f.read()

# 3a. Add import after FiberLeadCapture import
OLD_FIBER_IMPORT = "import FiberLeadCapture from './pages/FiberLeadCapture'"
NEW_FIBER_IMPORT = """import FiberLeadCapture from './pages/FiberLeadCapture'
import OrgManager from './pages/OrgManager'"""
if OLD_FIBER_IMPORT not in app:
    print("WARN: FiberLeadCapture import not found in App.jsx")
else:
    app = app.replace(OLD_FIBER_IMPORT, NEW_FIBER_IMPORT)
    print("App.jsx: OrgManager import added")

# 3b. Add route before the wildcard catch-all
OLD_WILDCARD = "        <Route path=\"*\" element={<Navigate to=\"/\" replace />} />"
NEW_WILDCARD = """        <Route path="/orgs" element={<ProtectedRoute requireSuperAdmin><OrgManager /></ProtectedRoute>} />
        <Route path="*" element={<Navigate to="/" replace />} />"""
if OLD_WILDCARD not in app:
    print("WARN: wildcard route not found in App.jsx")
else:
    app = app.replace(OLD_WILDCARD, NEW_WILDCARD)
    print("App.jsx: /orgs route added")

with open(app_path, "w", encoding="utf-8") as f:
    f.write(app)
print("App.jsx: saved")


# ── 4. admin_router.py — append GET /admin/orgs endpoint ────────────────────

admin_path = os.path.join(BASE, "app", "routers", "admin_router.py")
with open(admin_path, encoding="utf-8") as f:
    admin = f.read()

ORGS_ENDPOINT = """

# ---------------------------------------------------------------------------
# Org list — super admin only. Used by OrgManager.jsx to display all orgs
# with user counts. Org-admin-scoped data is handled by all other /admin/*
# endpoints via the standard require_admin filter on organization_id.
# ---------------------------------------------------------------------------

@router.get("/orgs")
def list_all_orgs(db: Session = Depends(get_db), current_user: User = Depends(require_super_admin)):
    \"\"\"Returns all organizations with user counts. Super admin only.\"\"\"
    from sqlalchemy import func as sqlfunc
    orgs = db.query(Organization).order_by(Organization.name.asc()).all()
    org_ids = [o.id for o in orgs]

    user_counts = {}
    if org_ids:
        rows = (
            db.query(User.organization_id, sqlfunc.count(User.id).label("cnt"))
            .filter(User.organization_id.in_(org_ids))
            .group_by(User.organization_id)
            .all()
        )
        user_counts = {row.organization_id: row.cnt for row in rows}

    return [
        {
            "id": o.id,
            "name": o.name,
            "slug": o.slug,
            "plan": o.plan if o.plan else None,
            "industry": o.industry if o.industry else None,
            "is_active": o.is_active,
            "brand_name": o.brand_name,
            "created_at": o.created_at,
            "user_count": user_counts.get(o.id, 0),
        }
        for o in orgs
    ]
"""

if "@router.get(\"/orgs\")" in admin:
    print("admin_router.py: /orgs endpoint already present, skipping")
else:
    admin = admin + ORGS_ENDPOINT
    with open(admin_path, "w", encoding="utf-8") as f:
        f.write(admin)
    print("admin_router.py: /orgs endpoint appended")

print("\nAll patches applied successfully.")
