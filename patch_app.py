import re

app_path = r'C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web\frontend\src\App.jsx'
layout_path = r'C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web\frontend\src\components\Layout.jsx'

# ── Patch App.jsx ──────────────────────────────────────────────────────────────
with open(app_path, 'r', encoding='utf-8') as f:
    c = f.read()

if 'FiberLeadCapture' not in c:
    c = c.replace(
        "import DLCRegistration from './pages/DLCRegistration'",
        "import DLCRegistration from './pages/DLCRegistration'\nimport FiberLeadCapture from './pages/FiberLeadCapture'"
    )
    c = c.replace(
        "        <Route path=\"*\" element={<Navigate to=\"/\" replace />} />",
        "        <Route path=\"/fiber-capture\" element={<ProtectedRoute><FiberLeadCapture /></ProtectedRoute>} />\n        <Route path=\"*\" element={<Navigate to=\"/\" replace />} />"
    )
    with open(app_path, 'w', encoding='utf-8') as f:
        f.write(c)
    print('App.jsx patched')
else:
    print('App.jsx already has FiberLeadCapture')

# ── Patch Layout.jsx ───────────────────────────────────────────────────────────
with open(layout_path, 'r', encoding='utf-8') as f:
    c = f.read()

if 'fiber-capture' not in c:
    # Add fiber-capture to NAV_ITEMS before the closing bracket
    c = c.replace(
        "  { to: '/availability', label: 'Availability', icon: 'calendar' },\n]",
        "  { to: '/availability', label: 'Availability', icon: 'calendar' },\n  { to: '/fiber-capture', label: 'Fiber Lead', icon: 'zap', fiberOnly: true },\n]"
    )
    # In the nav render section, filter fiber-only items based on branding.industry
    c = c.replace(
        "          {NAV_ITEMS.map((item) => (",
        "          {NAV_ITEMS.filter(item => !item.fiberOnly || (branding && branding.industry === 'fiber')).map((item) => ("
    )
    with open(layout_path, 'w', encoding='utf-8') as f:
        f.write(c)
    print('Layout.jsx patched')
else:
    print('Layout.jsx already has fiber-capture')
