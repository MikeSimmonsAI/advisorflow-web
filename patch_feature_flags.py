"""
patch_feature_flags.py
======================
Applies three major upgrades:

1. Per-org feature flags — super admin controls which admin nav items each org sees.
   Stored as JSON array in organizations.enabled_features.
   null = all enabled (backward compatible). [] = none.

2. Cadence template industry filtering — each org only sees cadence templates
   that match their industry. Auto-seeds the right default template set.
   Adds Fiber/D2D, Solar, Real Estate, and general Sales defaults.

3. Admin_router /orgs endpoint — now returns enabled_features per org so
   OrgManager.jsx can show current state for each toggle.

4. CORS — explicitly allow X-Org-Override header so compliance + other
   pages don't fail preflight when super admin is in org-override mode.
"""

import os
import re

BASE = r"C:\Dev\advisorflow-web"

def path(rel):
    return os.path.join(BASE, rel)

def read(rel):
    with open(path(rel), 'r', encoding='utf-8') as f:
        return f.read()

def write(rel, content):
    with open(path(rel), 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  saved: {rel}")

def replace_once(rel, old, new, label=""):
    content = read(rel)
    if old not in content:
        print(f"  WARNING [{rel}] target not found: {label or old[:60]}")
        return False
    content = content.replace(old, new, 1)
    write(rel, content)
    print(f"  patched: {rel} — {label or 'ok'}")
    return True

# ─────────────────────────────────────────────────────────────────────────────
# 1. auto_migrate.py — add enabled_features column to organizations
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] auto_migrate.py — enabled_features column")
replace_once(
    "app/auto_migrate.py",
    '    ("organizations", "tiktok_webhook_secret", "VARCHAR"),',
    '    ("organizations", "tiktok_webhook_secret", "VARCHAR"),\n    ("organizations", "enabled_features", "TEXT"),',
    "add enabled_features column"
)

# ─────────────────────────────────────────────────────────────────────────────
# 2. main.py — explicit CORS allow_headers so X-Org-Override passes preflight
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] main.py — CORS explicit headers")
replace_once(
    "app/main.py",
    '    allow_headers=["*"],',
    '    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Org-Override"],',
    "explicit CORS allow_headers"
)

# ─────────────────────────────────────────────────────────────────────────────
# 3. org_settings_router.py — add enabled_features to GET + PATCH /features
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] org_settings_router.py — enabled_features support")

# 3a. Add enabled_features to GET /org-settings/ response
replace_once(
    "app/routers/org_settings_router.py",
    '        "linkedin_url": getattr(org, "linkedin_url", None),\n    }',
    '        "linkedin_url": getattr(org, "linkedin_url", None),\n        "enabled_features": json.loads(org.enabled_features) if getattr(org, "enabled_features", None) else None,\n    }',
    "add enabled_features to GET response"
)

# 3b. Add PATCH /features endpoint at end of file
features_endpoint = '''

class FeaturesUpdate(BaseModel):
    enabled_features: list[str] | None = None  # None = all enabled; [] = none


@router.patch("/features")
def update_enabled_features(
    req: FeaturesUpdate,
    org_id: str = Query(..., description="Organization ID (required, super admin only)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Super admin only: set which admin features an org can access.
    Pass enabled_features=null to restore all-enabled state.
    Pass enabled_features=[] to disable all optional features.
    Pass enabled_features=["campaigns","reports",...] to restrict to a subset.
    """
    if current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Super admin only")
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    if req.enabled_features is None:
        org.enabled_features = None
    else:
        org.enabled_features = json.dumps(req.enabled_features)
    db.commit()
    return {"updated": True, "enabled_features": req.enabled_features}
'''

current = read("app/routers/org_settings_router.py")
if "update_enabled_features" not in current:
    with open(path("app/routers/org_settings_router.py"), 'a', encoding='utf-8') as f:
        f.write(features_endpoint)
    print("  patched: org_settings_router.py — PATCH /features endpoint added")
else:
    print("  skip: update_enabled_features already present")

# ─────────────────────────────────────────────────────────────────────────────
# 4. admin_router.py — add enabled_features to /orgs response
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] admin_router.py — enabled_features in /orgs response")
replace_once(
    "app/routers/admin_router.py",
    '            "user_count": user_counts.get(o.id, 0),\n        }\n        for o in orgs\n    ]',
    '            "user_count": user_counts.get(o.id, 0),\n            "enabled_features": __import__("json").loads(o.enabled_features) if getattr(o, "enabled_features", None) else None,\n        }\n        for o in orgs\n    ]',
    "add enabled_features to orgs list"
)

# ─────────────────────────────────────────────────────────────────────────────
# 5. cadence_template_router.py — industry-aware filtering + more defaults
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] cadence_template_router.py — industry-aware templates")

# 5a. Add industry normalization helper + new default templates
cadence_additions = '''

# ── Industry normalization ─────────────────────────────────────────────────────
# Maps org.industry values to cadence template industry keys.
# Fiber/D2D/telecom/solar all get their own sequences.
INDUSTRY_TO_CADENCE = {
    "fiber": "fiber",
    "fiber_internet": "fiber",
    "door_to_door": "fiber",
    "d2d": "fiber",
    "telecom": "fiber",
    "direct_sales": "fiber",
    "solar": "solar",
    "roofing": "roofing",
    "insurance": "insurance",
    "real_estate": "real_estate",
    "funeral": "funeral",
    "cemetery": "funeral",
    "home_services": "home_services",
}

def get_cadence_industry(org_industry: str) -> str:
    """Normalize org.industry → cadence template industry key."""
    if not org_industry:
        return "funeral"
    key = org_industry.lower().replace(" ", "_").replace("-", "_")
    return INDUSTRY_TO_CADENCE.get(key, "sales")

'''

# Add DEFAULTS extensions for fiber, solar, real_estate, home_services, sales
fiber_defaults = '''
    "fiber": {
        "name": "Fiber/D2D 6-Touch",
        "description": "Fast 6-touch sequence for fiber internet and door-to-door sales leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. We just expanded fiber service to your area — want to lock in availability? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hey {first_name}, still got a few install slots open this week. Takes less than 2 hours. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\\n\\nI wanted to follow up on your interest in fiber service. We have availability in your area and installation is quick.\\n\\n{booking_url}\\n\\n{advisor_name}", "subject_template": "Fiber service available in your area, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 11, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still interested in upgrading your internet? Happy to answer questions. {booking_url}"},
            {"touch_number": 5, "day_offset": 21, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, spots are going fast in your area. Let me know if you'd like to grab one. {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. I'm here whenever you're ready to get started. {booking_url}"},
        ]
    },
    "solar": {
        "name": "Solar 6-Touch",
        "description": "6-touch nurture for solar leads with energy savings focus.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. Ready to see how much you could save with solar? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, average homeowners in your area save $150+/month. Want a free savings estimate? {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 11, "channel": "email", "message_template": "Hi {first_name},\\n\\nI wanted to follow up on your solar interest. Our team can put together a custom savings estimate at no cost.\\n\\n{booking_url}\\n\\n{advisor_name}", "subject_template": "Your free solar savings estimate, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, incentive deadlines are approaching. Let me get you a number before rates change. {booking_url}"},
            {"touch_number": 5, "day_offset": 21, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still here to answer any solar questions you have. {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, this is my last follow-up. When you're ready to explore solar I'm here. {booking_url}"},
        ]
    },
    "real_estate": {
        "name": "Real Estate 7-Touch",
        "description": "7-touch sequence for buyer and seller real estate leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 9,  "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} with {org_name}. I'd love to help you find the right home. Ready to connect? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, the market is moving fast. Let's set up a quick call so you don't miss out. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\\n\\nI have some great listings that match what you're looking for. I'd love to walk you through them.\\n\\n{booking_url}\\n\\n{advisor_name}", "subject_template": "New listings for you, {first_name}"},
            {"touch_number": 4, "day_offset": 10, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, any questions about the buying process? Happy to walk you through it. {booking_url}"},
            {"touch_number": 5, "day_offset": 14, "send_hour": 14, "channel": "sms",   "message_template": "Hi {first_name}, I have a few properties I think you'd love. Worth 20 minutes? {booking_url}"},
            {"touch_number": 6, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still searching for the right home. I'm here when you're ready. {booking_url}"},
            {"touch_number": 7, "day_offset": 60, "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\\n\\nThis is my last follow-up for now. Reach out any time — I'd love to help you find your next home.\\n\\n{advisor_name}", "subject_template": "Still here for you, {first_name}"},
        ]
    },
    "home_services": {
        "name": "Home Services 5-Touch",
        "description": "5-touch follow-up for home services leads.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 9,  "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. Ready to schedule your free estimate? {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, following up on your service request. We have openings this week. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\\n\\nI wanted to circle back on your service inquiry. We'd love to get you on the schedule.\\n\\n{booking_url}\\n\\n{advisor_name}", "subject_template": "Your estimate is waiting, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 11, "channel": "sms",   "message_template": "Hi {first_name}, still interested? I can get you on the schedule quickly. {booking_url}"},
            {"touch_number": 5, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. I'm here whenever you're ready. {booking_url}"},
        ]
    },
    "sales": {
        "name": "General Sales 5-Touch",
        "description": "General-purpose 5-touch sales outreach sequence.",
        "touches": [
            {"touch_number": 1, "day_offset": 1,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}! This is {advisor_name} from {org_name}. I'd love to connect and see how I can help. {booking_url}"},
            {"touch_number": 2, "day_offset": 3,  "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, just following up. I'm here whenever you're ready. {booking_url}"},
            {"touch_number": 3, "day_offset": 7,  "send_hour": 10, "channel": "email", "message_template": "Hi {first_name},\\n\\nI wanted to check in. I'd love to find a time to connect and see how I can help.\\n\\n{booking_url}\\n\\n{advisor_name}", "subject_template": "Checking in, {first_name}"},
            {"touch_number": 4, "day_offset": 14, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, {advisor_name} here. Still happy to help whenever works for you. {booking_url}"},
            {"touch_number": 5, "day_offset": 30, "send_hour": 10, "channel": "sms",   "message_template": "Hi {first_name}, last reach out for now. I'm here when you're ready. {booking_url}"},
        ]
    },
'''

# Insert new defaults + industry helpers into cadence_template_router.py
replace_once(
    "app/routers/cadence_template_router.py",
    '    "insurance": {\n        "name": "Insurance 7-Touch",',
    '    "insurance": {\n        "name": "Insurance 7-Touch",',
    "baseline check"
)

# Add new DEFAULTS entries after existing ones
cadence_content = read("app/routers/cadence_template_router.py")

if '"fiber"' not in cadence_content:
    # Find end of DEFAULTS dict and insert new entries before closing }
    cadence_content = cadence_content.replace(
        '    "insurance": {\n        "name": "Insurance 7-Touch",',
        '    "insurance": {\n        "name": "Insurance 7-Touch",',
    )
    # Find the closing } of DEFAULTS
    # Insert fiber/solar/real_estate/home_services/sales defaults before closing brace
    old_close = '}\n\n\n# ── Pydantic models'
    new_close = '    ' + fiber_defaults.strip() + '\n}\n\n\n# ── Pydantic models'
    if old_close in cadence_content:
        cadence_content = cadence_content.replace(old_close, new_close, 1)
        print("  patched: cadence_template_router.py — new industry DEFAULTS inserted")
    else:
        # Try alternate pattern
        old_close2 = '}\n\n\n# ── Pydantic models ──'
        new_close2 = '    ' + fiber_defaults.strip() + '\n}\n\n\n# ── Pydantic models ──'
        if old_close2 in cadence_content:
            cadence_content = cadence_content.replace(old_close2, new_close2, 1)
            print("  patched: cadence_template_router.py — new industry DEFAULTS inserted (alt)")
        else:
            print("  WARNING: could not find DEFAULTS closing brace pattern")

    # Add industry helpers before list_templates endpoint
    cadence_content = cadence_content.replace(
        '\n# ── Endpoints ─────',
        cadence_additions + '\n# ── Endpoints ─────',
        1
    )
    print("  patched: cadence_template_router.py — INDUSTRY_TO_CADENCE + get_cadence_industry added")
    write("app/routers/cadence_template_router.py", cadence_content)
else:
    print("  skip: fiber cadence already present")

# 5b. Update list_templates to filter by org industry
print("  patching list_templates endpoint...")

old_list = '''@router.get("/")
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    templates = db.query(CadenceTemplate).filter(
        CadenceTemplate.organization_id == current_user.organization_id,
        CadenceTemplate.is_active == True,
    ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()

    # Auto-seed funeral defaults if this org has never had templates
    if not templates:
        try:
            _seed_defaults_for_org(db, current_user.organization_id, current_user.id, industry="funeral")
            templates = db.query(CadenceTemplate).filter(
                CadenceTemplate.organization_id == current_user.organization_id,
                CadenceTemplate.is_active == True,
            ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()'''

new_list = '''@router.get("/")
def list_templates(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from app.models.models import Organization as _Org
    from sqlalchemy import or_ as _or
    _org = db.query(_Org).filter(_Org.id == current_user.organization_id).first()
    _cadence_industry = get_cadence_industry((_org.industry if _org else None) or "funeral")

    templates = db.query(CadenceTemplate).filter(
        CadenceTemplate.organization_id == current_user.organization_id,
        CadenceTemplate.is_active == True,
        _or(
            CadenceTemplate.industry == _cadence_industry,
            CadenceTemplate.industry == "general",
            CadenceTemplate.industry == None,
        )
    ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()

    # Auto-seed industry-appropriate defaults if this org has no matching templates
    if not templates:
        try:
            _seed_defaults_for_org(db, current_user.organization_id, current_user.id, industry=_cadence_industry)
            templates = db.query(CadenceTemplate).filter(
                CadenceTemplate.organization_id == current_user.organization_id,
                CadenceTemplate.is_active == True,
                _or(
                    CadenceTemplate.industry == _cadence_industry,
                    CadenceTemplate.industry == "general",
                    CadenceTemplate.industry == None,
                )
            ).order_by(CadenceTemplate.is_default.desc(), CadenceTemplate.created_at.asc()).all()'''

replace_once("app/routers/cadence_template_router.py", old_list, new_list, "list_templates industry filter")

# 5c. Fix _seed_defaults_for_org to match exact industry key
old_seed_filter = '''    for key, data in DEFAULTS.items():
        if industry != "all" and key != industry:
            continue'''
new_seed_filter = '''    # Normalize: map org industry to cadence key
    effective_industry = get_cadence_industry(industry) if industry != "all" else "all"
    for key, data in DEFAULTS.items():
        if effective_industry != "all" and key != effective_industry:
            continue'''
replace_once("app/routers/cadence_template_router.py", old_seed_filter, new_seed_filter, "_seed_defaults industry normalize")

print("\n[6] client.js — store enabled_features in branding")
replace_once(
    "frontend/src/api/client.js",
    "      brand_color_accent: data.brand_color_accent || null,\n      industry: data.industry || 'funeral',\n    }",
    "      brand_color_accent: data.brand_color_accent || null,\n      industry: data.industry || 'funeral',\n      enabled_features: data.enabled_features || null,\n    }",
    "add enabled_features to stored branding"
)

print("\nAll backend patches complete.")
print("\nNow patching frontend files...")

# ─────────────────────────────────────────────────────────────────────────────
# 7. Layout.jsx — featureKey on ADMIN_NAV_ITEMS + nav filter logic
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] Layout.jsx — feature-flag nav filtering")

old_admin_items = """const ADMIN_NAV_ITEMS = [
  { to: '/admin', label: 'Master Dashboard', icon: 'shield' },
  { to: '/reports', label: 'Reports', icon: 'activity' },
  { to: '/users', label: 'Users', icon: 'user-plus' },
  { to: '/campaigns', label: 'Campaigns', icon: 'target' },
  { to: '/lead-cleanup', label: 'Lead Cleanup', icon: 'users' },
  { to: '/crm', label: 'CRM Integration', icon: 'link' },
  { to: '/tier-definitions', label: 'Tier Config', icon: 'layers' },
  { to: '/10dlc', label: 'A2P 10DLC', icon: 'shield-check' },
  { to: '/org-settings', label: 'Branding & Settings', icon: 'settings' },
]"""

new_admin_items = """// featureKey: which enabled_features key controls this item.
// null = always visible to any admin. super_admin always bypasses all flags.
const ADMIN_NAV_ITEMS = [
  { to: '/admin',            label: 'Master Dashboard',   icon: 'shield',       featureKey: 'master_dashboard' },
  { to: '/reports',          label: 'Reports',            icon: 'activity',     featureKey: 'reports' },
  { to: '/users',            label: 'Users',              icon: 'user-plus',    featureKey: 'users' },
  { to: '/campaigns',        label: 'Campaigns',          icon: 'target',       featureKey: 'campaigns' },
  { to: '/lead-cleanup',     label: 'Lead Cleanup',       icon: 'users',        featureKey: 'lead_cleanup' },
  { to: '/crm',              label: 'CRM Integration',    icon: 'link',         featureKey: 'crm_integration' },
  { to: '/tier-definitions', label: 'Tier Config',        icon: 'layers',       featureKey: 'tier_config' },
  { to: '/10dlc',            label: 'A2P 10DLC',          icon: 'shield-check', featureKey: 'a2p_10dlc' },
  { to: '/org-settings',     label: 'Branding & Settings',icon: 'settings',    featureKey: 'branding_settings' },
  { to: '/compliance',       label: 'Compliance',         icon: 'shield-check', featureKey: 'compliance' },
  { to: '/audit-log',        label: 'Audit Log',          icon: 'activity',     featureKey: 'audit_log' },
]"""

replace_once("frontend/src/components/Layout.jsx", old_admin_items, new_admin_items, "ADMIN_NAV_ITEMS with featureKey")

old_super_items = """// Super admin only — platform-level tools not visible to org supervisors
const SUPER_ADMIN_NAV_ITEMS = [
  { to: '/provision-client', label: 'Provision Client', icon: 'user-plus' },
  { to: '/templates', label: 'Templates', icon: 'file-text' },
  { to: '/cadence-templates', label: 'Cadence Builder', icon: 'sliders' },
  { to: '/compliance', label: 'Compliance', icon: 'shield-check' },
  { to: '/audit-log', label: 'Audit Log', icon: 'activity' },
  { to: '/orgs', label: 'Org Manager', icon: 'building' },
]"""

new_super_items = """// Platform Admin — super admin only, always visible (no feature-key restrictions)
// Compliance and Audit Log moved to ADMIN_NAV_ITEMS so super admin can grant them per org
const SUPER_ADMIN_NAV_ITEMS = [
  { to: '/provision-client', label: 'Provision Client', icon: 'user-plus' },
  { to: '/templates', label: 'Templates', icon: 'file-text' },
  { to: '/cadence-templates', label: 'Cadence Builder', icon: 'sliders' },
  { to: '/orgs', label: 'Org Manager', icon: 'building' },
]"""

replace_once("frontend/src/components/Layout.jsx", old_super_items, new_super_items,
             "SUPER_ADMIN_NAV_ITEMS — remove compliance/audit_log (now in ADMIN_NAV_ITEMS)")

# 7c. Add enabledFeatures / isFeatureEnabled after isSuperAdmin declaration
replace_once(
    "frontend/src/components/Layout.jsx",
    "  const isSuperAdmin = user?.role === 'super_admin'\n  const [orgContext, setOrgCtx] = useState(() => isSuperAdmin ? getOrgContext() : null)",
    "  const isSuperAdmin = user?.role === 'super_admin'\n  const [orgContext, setOrgCtx] = useState(() => isSuperAdmin ? getOrgContext() : null)\n  // Feature gates: null enabled_features = all features on (backward-compatible)\n  // Super admin always bypasses — they control the flags, so they see everything.\n  const enabledFeatures = isSuperAdmin ? null : (getBranding()?.enabled_features ?? null)\n  const isFeatureEnabled = (key) => !key || enabledFeatures === null || enabledFeatures.includes(key)",
    "add enabledFeatures + isFeatureEnabled to Layout"
)

# 7d. Filter ADMIN_NAV_ITEMS by feature key in the render
replace_once(
    "frontend/src/components/Layout.jsx",
    "          {ADMIN_NAV_ITEMS.map((item) => (\n            <NavLink key={item.to} to={item.to}\n              className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}\n              onClick={closeSidebar}\n            >\n              <Icon name={item.icon} />{item.label}\n            </NavLink>\n          ))}",
    "          {ADMIN_NAV_ITEMS.filter(item => isFeatureEnabled(item.featureKey)).map((item) => (\n            <NavLink key={item.to} to={item.to}\n              className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}\n              onClick={closeSidebar}\n            >\n              <Icon name={item.icon} />{item.label}\n            </NavLink>\n          ))}",
    "filter ADMIN_NAV_ITEMS by isFeatureEnabled"
)

# ─────────────────────────────────────────────────────────────────────────────
# 8. OrgManager.jsx — complete replacement with feature toggle UI
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] OrgManager.jsx — feature toggle UI")

ORG_MANAGER_JSX = """\
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, setOrgContext } from '../api/client'
import './OrgManager.css'

const ALL_FEATURES = [
  { key: 'master_dashboard', label: 'Master Dashboard' },
  { key: 'reports',          label: 'Reports' },
  { key: 'users',            label: 'Users' },
  { key: 'campaigns',        label: 'Campaigns' },
  { key: 'lead_cleanup',     label: 'Lead Cleanup' },
  { key: 'crm_integration',  label: 'CRM Integration' },
  { key: 'tier_config',      label: 'Tier Config' },
  { key: 'a2p_10dlc',        label: 'A2P 10DLC' },
  { key: 'branding_settings',label: 'Branding & Settings' },
  { key: 'compliance',       label: 'Compliance' },
  { key: 'audit_log',        label: 'Audit Log' },
]
"""
write("frontend/src/pages/OrgManager.jsx", ORG_MANAGER_JSX)

ORG_MANAGER_BODY = """\
export default function OrgManager() {
  const [orgs, setOrgs] = useState([])
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState({})
  const [featuresExpanded, setFeaturesExpanded] = useState({})
  const [orgFeatures, setOrgFeatures] = useState({})
  const [saving, setSaving] = useState({})
  const navigate = useNavigate()

  useEffect(() => {
    async function load() {
      try {
        const [orgsData, usersData] = await Promise.all([
          api.get('/admin/orgs'),
          api.get('/admin/users'),
        ])
        setOrgs(orgsData)
        setUsers(usersData)
        const featInit = {}
        orgsData.forEach(o => {
          featInit[o.id] = (o.enabled_features !== undefined && o.enabled_features !== null)
            ? o.enabled_features : null
        })
        setOrgFeatures(featInit)
      } catch (e) {
        setError(e.message)
      } finally {
        setLoading(false)
      }
    }
    load()
  }, [])

  function handleEnterOrg(org) {
    setOrgContext(org.id, org.name)
    window.location.href = '/'
  }

  function toggleExpand(orgId) {
    setExpanded(prev => ({ ...prev, [orgId]: !prev[orgId] }))
  }

  function toggleFeaturesExpand(orgId) {
    setFeaturesExpanded(prev => ({ ...prev, [orgId]: !prev[orgId] }))
  }

  function toggleFeature(orgId, key) {
    setOrgFeatures(prev => {
      const current = prev[orgId]
      const asList = current === null ? ALL_FEATURES.map(f => f.key) : [...current]
      const idx = asList.indexOf(key)
      if (idx === -1) asList.push(key)
      else asList.splice(idx, 1)
      return { ...prev, [orgId]: asList }
    })
  }

  function grantAll(orgId) {
    setOrgFeatures(prev => ({ ...prev, [orgId]: null }))
  }

  async function saveFeatures(orgId) {
    setSaving(prev => ({ ...prev, [orgId]: true }))
    try {
      await api.patch(`/org-settings/features?org_id=${orgId}`, {
        enabled_features: orgFeatures[orgId],
      })
    } catch (e) {
      alert('Failed to save: ' + e.message)
    } finally {
      setSaving(prev => ({ ...prev, [orgId]: false }))
    }
  }
"""
with open(path("frontend/src/pages/OrgManager.jsx"), 'a', encoding='utf-8') as f:
    f.write(ORG_MANAGER_BODY)
print("  appended: OrgManager.jsx — state + handlers")

ORG_MANAGER_RENDER1 = """\
  const usersByOrg = users.reduce((acc, u) => {
    if (!acc[u.organization_id]) acc[u.organization_id] = []
    acc[u.organization_id].push(u)
    return acc
  }, {})

  const filtered = orgs.filter(o =>
    !search || o.name.toLowerCase().includes(search.toLowerCase())
  )

  if (loading) return <div className="org-manager-loading">Loading organizations...</div>
  if (error) return <div className="org-manager-error">Error: {error}</div>

  return (
    <div className="org-manager">
      <div className="org-manager-header">
        <div>
          <h1 className="org-manager-title">Org Manager</h1>
          <p className="org-manager-subtitle">{orgs.length} organization{orgs.length !== 1 ? 's' : ''} on the platform</p>
        </div>
        <input
          className="org-manager-search"
          placeholder="Search organizations..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      {filtered.length === 0 && (
        <div className="org-manager-empty">No organizations match your search.</div>
      )}

      <div className="org-grid">
        {filtered.map(org => {
          const orgUsers = usersByOrg[org.id] || []
          const isExpanded = expanded[org.id]
          const isFeatExpanded = featuresExpanded[org.id]
          const adminCount = orgUsers.filter(u => u.role === 'org_admin').length
          const advisorCount = orgUsers.filter(u => u.role === 'advisor').length
          const features = orgFeatures[org.id]

          return (
            <div key={org.id} className={`org-card ${!org.is_active ? 'org-card--inactive' : ''}`}>
              <div className="org-card-top">
                <div className="org-card-name-row">
                  <h2 className="org-card-name">{org.name}</h2>
                  {!org.is_active && <span className="org-badge org-badge--inactive">Inactive</span>}
                </div>
                <div className="org-card-badges">
                  <span className={`org-badge org-badge--plan org-badge--${(org.plan || 'trial').toLowerCase()}`}>
                    {org.plan || 'trial'}
                  </span>
                  <span className="org-badge org-badge--industry">{org.industry || 'general'}</span>
                </div>
              </div>

              <div className="org-card-stats">
                <div className="org-stat">
                  <span className="org-stat-value">{orgUsers.length}</span>
                  <span className="org-stat-label">users</span>
                </div>
                <div className="org-stat">
                  <span className="org-stat-value">{adminCount}</span>
                  <span className="org-stat-label">admins</span>
                </div>
                <div className="org-stat">
                  <span className="org-stat-value">{advisorCount}</span>
                  <span className="org-stat-label">advisors</span>
                </div>
              </div>
              <div className="org-card-slug">/{org.slug}</div>
"""
with open(path("frontend/src/pages/OrgManager.jsx"), 'a', encoding='utf-8') as f:
    f.write(ORG_MANAGER_RENDER1)
print("  appended: OrgManager.jsx — render part 1")

ORG_MANAGER_RENDER2 = """\

              <div className="org-card-actions">
                <div className="org-expand-toggle" onClick={() => toggleExpand(org.id)}>
                  {isExpanded ? '\\u25be Hide team' : `\\u25b8 Team (${orgUsers.length})`}
                </div>
                <div className="org-expand-toggle" onClick={() => toggleFeaturesExpand(org.id)}>
                  {isFeatExpanded ? '\\u25be Hide features' : '\\u2699\\ufe0f Features'}
                </div>
                <button
                  type="button"
                  className="org-enter-btn"
                  onClick={() => handleEnterOrg(org)}
                  title={`View BookaBoost as ${org.name}`}
                >
                  Enter Org \\u2192
                </button>
              </div>

              {isFeatExpanded && (
                <div className="org-features-section">
                  <div className="org-features-header">
                    <span className="org-features-title">
                      Admin Feature Access{' '}
                      {features === null
                        ? <span className="org-features-status org-features-status--all">All enabled</span>
                        : <span className="org-features-status">{features.length}/{ALL_FEATURES.length} enabled</span>
                      }
                    </span>
                    <button type="button" className="org-features-grant-all" onClick={() => grantAll(org.id)}>
                      Grant All
                    </button>
                  </div>
                  <div className="org-features-grid">
                    {ALL_FEATURES.map(f => {
                      const checked = features === null || features.includes(f.key)
                      return (
                        <label key={f.key} className="org-feature-checkbox">
                          <input type="checkbox" checked={checked} onChange={() => toggleFeature(org.id, f.key)} />
                          <span>{f.label}</span>
                        </label>
                      )
                    })}
                  </div>
                  <button
                    type="button"
                    className="org-features-save"
                    onClick={() => saveFeatures(org.id)}
                    disabled={saving[org.id]}
                  >
                    {saving[org.id] ? 'Saving...' : 'Save Features'}
                  </button>
                </div>
              )}
"""
with open(path("frontend/src/pages/OrgManager.jsx"), 'a', encoding='utf-8') as f:
    f.write(ORG_MANAGER_RENDER2)
print("  appended: OrgManager.jsx — render part 2 (feature toggle UI)")

ORG_MANAGER_RENDER3 = """\

              {isExpanded && (
                <div className="org-user-list">
                  {orgUsers.length === 0 && (
                    <p className="org-user-empty">No users in this org yet.</p>
                  )}
                  {orgUsers.map(u => (
                    <div key={u.id} className={`org-user-row ${!u.is_active ? 'org-user-row--inactive' : ''}`}>
                      <div className="org-user-avatar">{(u.full_name || '?')[0].toUpperCase()}</div>
                      <div className="org-user-info">
                        <span className="org-user-name">{u.full_name}</span>
                        <span className="org-user-email">{u.email}</span>
                      </div>
                      <div className="org-user-right">
                        <span className={`role-tag role-tag--${u.role}`}>{u.role.replace(/_/g, ' ')}</span>
                        {!u.is_active && <span className="org-badge org-badge--inactive">off</span>}
                        {u.must_change_password && <span className="org-badge org-badge--warn">setup</span>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
"""
with open(path("frontend/src/pages/OrgManager.jsx"), 'a', encoding='utf-8') as f:
    f.write(ORG_MANAGER_RENDER3)
print("  appended: OrgManager.jsx — render part 3 (team list + close)")

# ─────────────────────────────────────────────────────────────────────────────
# 9. OrgManager.css — feature toggle styles
# ─────────────────────────────────────────────────────────────────────────────
print("\n[9] OrgManager.css — feature toggle styles")

css_additions = """
/* Feature toggle section */
.org-features-section {
  border-top: 1px solid var(--border-subtle);
  padding-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.org-features-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.org-features-title {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.org-features-status {
  font-size: 0.72rem;
  font-weight: 500;
  color: var(--text-secondary);
  background: var(--bg-surface, rgba(255,255,255,0.04));
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  padding: 1px 7px;
}

.org-features-status--all {
  color: #5ce87c;
  background: rgba(92,232,124,0.08);
  border-color: rgba(92,232,124,0.25);
}

.org-features-grant-all {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--signal-blue);
  background: transparent;
  border: 1px solid var(--signal-blue);
  border-radius: 6px;
  padding: 3px 10px;
  cursor: pointer;
  transition: background 0.15s;
  white-space: nowrap;
}
.org-features-grant-all:hover { background: var(--signal-blue-dim); }

.org-features-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 12px;
}

.org-feature-checkbox {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8rem;
  color: var(--text-primary);
  cursor: pointer;
  user-select: none;
}
.org-feature-checkbox input[type="checkbox"] {
  width: 14px;
  height: 14px;
  accent-color: var(--signal-blue);
  cursor: pointer;
  flex-shrink: 0;
}

.org-features-save {
  align-self: flex-end;
  padding: 7px 16px;
  background: var(--signal-blue);
  color: #fff;
  border: none;
  border-radius: 7px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.org-features-save:hover { opacity: 0.85; }
.org-features-save:disabled { opacity: 0.5; cursor: not-allowed; }
"""

org_css_content = read("frontend/src/pages/OrgManager.css")
if "org-features-section" not in org_css_content:
    with open(path("frontend/src/pages/OrgManager.css"), 'a', encoding='utf-8') as f:
        f.write(css_additions)
    print("  patched: OrgManager.css — feature toggle styles added")
else:
    print("  skip: feature toggle CSS already present")

# ─────────────────────────────────────────────────────────────────────────────
# 10. Overview.jsx — respect campaigns feature flag on Quick Actions
# ─────────────────────────────────────────────────────────────────────────────
print("\n[10] Overview.jsx — respect campaigns feature flag")

# 10a. Add getBranding to import
replace_once(
    "frontend/src/pages/Overview.jsx",
    "import { api, getCurrentUser } from '../api/client'",
    "import { api, getCurrentUser, getBranding } from '../api/client'",
    "add getBranding import to Overview.jsx"
)

# 10b. Add enabledFeatures after user declaration
replace_once(
    "frontend/src/pages/Overview.jsx",
    "  const user = getCurrentUser()\n  const navigate = useNavigate()",
    "  const user = getCurrentUser()\n  const navigate = useNavigate()\n  // Respect per-org feature flags set by super admin\n  const _branding = getBranding()\n  const _enabledFeatures = _branding?.enabled_features ?? null\n  const _isEnabled = (key) => !_enabledFeatures || _enabledFeatures.includes(key)",
    "add feature flag check to Overview"
)

# 10c. Filter 'Send campaign' and 'Lead cleanup' quick actions by feature flag
old_quick = """            { label: 'Import leads',     icon: '\\U0001f4e5', path: '/leads',          desc: 'Upload CSV or Excel' },
              { label: 'Send campaign',    icon: '\\U0001f4e3', path: '/campaigns',      desc: 'AI-powered outreach' },
              { label: 'Review replies',   icon: '\\U0001f4ac', path: '/replies',        desc: `${hotReplies} waiting` },
              { label: 'Email queue',      icon: '\\U0001f4e7', path: '/email-queue',    desc: 'Draft & send emails' },
              { label: 'Work queue',       icon: '\\u2705', path: '/work-queue',     desc: 'Today\\'s action items' },
              { label: 'Lead cleanup',     icon: '\\U0001f9f9', path: '/lead-cleanup',   desc: 'Merge duplicates' },"""

new_quick = """            { label: 'Import leads',     icon: '\\U0001f4e5', path: '/leads',          desc: 'Upload CSV or Excel',    feature: null },
              { label: 'Send campaign',    icon: '\\U0001f4e3', path: '/campaigns',      desc: 'AI-powered outreach',    feature: 'campaigns' },
              { label: 'Review replies',   icon: '\\U0001f4ac', path: '/replies',        desc: `${hotReplies} waiting`,  feature: null },
              { label: 'Email queue',      icon: '\\U0001f4e7', path: '/email-queue',    desc: 'Draft & send emails',    feature: null },
              { label: 'Work queue',       icon: '\\u2705', path: '/work-queue',     desc: 'Today\\'s action items', feature: null },
              { label: 'Lead cleanup',     icon: '\\U0001f9f9', path: '/lead-cleanup',   desc: 'Merge duplicates',       feature: 'lead_cleanup' },"""

overview_content = read("frontend/src/pages/Overview.jsx")

# Use a simpler targeted replacement: just the Send campaign line
replace_once(
    "frontend/src/pages/Overview.jsx",
    "              { label: 'Send campaign',    icon: '\\U0001f4e3', path: '/campaigns',      desc: 'AI-powered outreach' },",
    "              ..._isEnabled('campaigns') ? [{ label: 'Send campaign', icon: '\\U0001f4e3', path: '/campaigns', desc: 'AI-powered outreach' }] : [],",
    "hide Send campaign if campaigns feature disabled"
)
replace_once(
    "frontend/src/pages/Overview.jsx",
    "              { label: 'Lead cleanup',     icon: '\\U0001f9f9', path: '/lead-cleanup',   desc: 'Merge duplicates' },",
    "              ..._isEnabled('lead_cleanup') ? [{ label: 'Lead cleanup', icon: '\\U0001f9f9', path: '/lead-cleanup', desc: 'Merge duplicates' }] : [],",
    "hide Lead cleanup if lead_cleanup feature disabled"
)

print("\nAll patches complete.")
print("Run: cd to project root, then: python patch_feature_flags.py")
