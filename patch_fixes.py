"""
patch_fixes.py — fixes the 3 warning cases from patch_feature_flags.py
"""
import os

BASE = r"C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"

def path(rel):
    return os.path.join(BASE, rel)

def read(rel):
    with open(path(rel), 'r', encoding='utf-8') as f:
        return f.read()

def write(rel, content):
    with open(path(rel), 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"  saved: {rel}")

# ─────────────────────────────────────────────────────────────────────────────
# Fix 1: Layout.jsx ADMIN_NAV_ITEMS.map — add .filter(isFeatureEnabled)
# The map had extra indentation vs what the patch expected
# ─────────────────────────────────────────────────────────────────────────────
print("[fix1] Layout.jsx — add isFeatureEnabled filter to ADMIN_NAV_ITEMS render")
layout = read("frontend/src/components/Layout.jsx")

old = "         {ADMIN_NAV_ITEMS.map((item) => (\n                <NavLink key={item.to} to={item.to}\n                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}\n                  onClick={closeSidebar}\n                >\n                  <Icon name={item.icon} />{item.label}\n                </NavLink>\n              ))}"
new = "         {ADMIN_NAV_ITEMS.filter(item => isFeatureEnabled(item.featureKey)).map((item) => (\n                <NavLink key={item.to} to={item.to}\n                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}\n                  onClick={closeSidebar}\n                >\n                  <Icon name={item.icon} />{item.label}\n                </NavLink>\n              ))}"

if old in layout:
    layout = layout.replace(old, new, 1)
    write("frontend/src/components/Layout.jsx", layout)
    print("  patched: Layout.jsx — ADMIN_NAV_ITEMS now filtered by isFeatureEnabled")
else:
    print("  WARNING: Layout.jsx target not found — checking current content...")
    idx = layout.find("ADMIN_NAV_ITEMS.map")
    print(f"  Context: {repr(layout[idx-5:idx+250])}")


# ─────────────────────────────────────────────────────────────────────────────
# Fix 2 & 3: Overview.jsx — hide Send campaign / Lead cleanup per feature flag
# Use string search without emoji repr issues
# ─────────────────────────────────────────────────────────────────────────────
print("\n[fix2+3] Overview.jsx — campaign + lead_cleanup feature gates")
overview = read("frontend/src/pages/Overview.jsx")

# Find and replace the quick action items array entries
# We'll locate each line by a stable substring and do a surgical replacement

# Fix 'Send campaign' line
send_campaign_old = "{ label: 'Send campaign',    icon: '\U0001f4e3', path: '/campaigns',      desc: 'AI-powered outreach' },"
send_campaign_new = "...(_isEnabled('campaigns') ? [{ label: 'Send campaign', icon: '\U0001f4e3', path: '/campaigns', desc: 'AI-powered outreach' }] : []),"

if send_campaign_old in overview:
    overview = overview.replace(send_campaign_old, send_campaign_new, 1)
    print("  patched: Send campaign gated by campaigns feature")
else:
    # Try with different spacing
    idx = overview.find("Send campaign")
    if idx >= 0:
        line_start = overview.rfind("\n", 0, idx) + 1
        line_end = overview.find("\n", idx)
        current_line = overview[line_start:line_end]
        print(f"  WARNING: Send campaign line not matched. Current: {repr(current_line)}")
    else:
        print("  WARNING: 'Send campaign' not found in Overview.jsx")

# Fix 'Lead cleanup' line
lead_cleanup_old = "{ label: 'Lead cleanup',     icon: '\U0001f9f9', path: '/lead-cleanup',   desc: 'Merge duplicates' },"
lead_cleanup_new = "...(_isEnabled('lead_cleanup') ? [{ label: 'Lead cleanup', icon: '\U0001f9f9', path: '/lead-cleanup', desc: 'Merge duplicates' }] : []),"

if lead_cleanup_old in overview:
    overview = overview.replace(lead_cleanup_old, lead_cleanup_new, 1)
    print("  patched: Lead cleanup gated by lead_cleanup feature")
else:
    idx = overview.find("Lead cleanup")
    if idx >= 0:
        line_start = overview.rfind("\n", 0, idx) + 1
        line_end = overview.find("\n", idx)
        current_line = overview[line_start:line_end]
        print(f"  WARNING: Lead cleanup line not matched. Current: {repr(current_line)}")
    else:
        print("  WARNING: 'Lead cleanup' not found in Overview.jsx")

write("frontend/src/pages/Overview.jsx", overview)
print("\nAll fixes applied.")
