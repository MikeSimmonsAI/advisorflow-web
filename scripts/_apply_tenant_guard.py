"""Apply require_tenant_user to the customer-tenant routers.

Phase 3 gave /leads and /pipeline this guard and stopped there. The gate 24
sweep showed the consequence: a brand sales manager gets 200 + empty body from
/crm, /campaigns and friends instead of a refusal, and POST /crm/contacts
crashes on a NOT NULL violation. Same "defense by schema, not by guard" the
require_tenant_user docstring was written about - just in twelve more routers.

Mechanical: swap Depends(get_current_user) -> Depends(require_tenant_user) and
fix the import. Routes listed in KEEP are left alone because a brand-sales user
legitimately needs them (their own name, photo, notification address).
"""
import os
import re
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = os.path.join(REPO, "app", "routers")

# Whole routers that are customer-tenant surfaces, top to bottom.
TENANT_ROUTERS = [
    "cadence_router.py", "cadence_template_router.py", "campaign_router.py",
    "case_file_router.py", "compliance_router.py", "contacts_router.py",
    "crm_native_router.py", "crm_router.py", "dlc_router.py",
    "email_router.py", "fiber_leads_router.py", "sms_router.py",
]

# settings_router is mixed: some routes are a person's own account.
SETTINGS = "settings_router.py"
SETTINGS_KEEP = ("get_profile", "update_own_profile", "update_notification_config",
                 "update_profile_photo", "delete_profile_photo")


def write(p, s):
    for _ in range(20):
        try:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(s)
            return
        except PermissionError:
            time.sleep(0.5)
    raise SystemExit("could not write %s" % p)


def fix_import(src):
    if "require_tenant_user" in src.split("\n\n")[0] or re.search(
            r"from app\.deps import[^\n]*require_tenant_user", src):
        return src
    m = re.search(r"^from app\.deps import (.+)$", src, re.M)
    if not m:
        return None
    names = m.group(1)
    if "require_tenant_user" in names:
        return src
    return src[:m.start()] + "from app.deps import " + names.rstrip() + \
        ", require_tenant_user" + src[m.end():]


total = 0
for name in TENANT_ROUTERS:
    p = os.path.join(R, name)
    if not os.path.exists(p):
        print("  missing: %s" % name)
        continue
    src = open(p, encoding="utf-8").read()
    n = src.count("Depends(get_current_user)")
    if not n:
        print("  %-30s no bare get_current_user deps" % name)
        continue
    out = src.replace("Depends(get_current_user)", "Depends(require_tenant_user)")
    out2 = fix_import(out)
    if out2 is None:
        print("  %-30s !! no 'from app.deps import' line - SKIPPED" % name)
        continue
    write(p, out2)
    total += n
    print("  %-30s %d guards swapped" % (name, n))

# settings_router, route by route.
p = os.path.join(R, SETTINGS)
src = open(p, encoding="utf-8").read()
lines = src.split("\n")
current_fn = None
changed = 0
for i, line in enumerate(lines):
    m = re.match(r"^(async )?def (\w+)\(", line)
    if m:
        current_fn = m.group(2)
    if "Depends(get_current_user)" in line and current_fn not in SETTINGS_KEEP:
        lines[i] = line.replace("Depends(get_current_user)", "Depends(require_tenant_user)")
        changed += 1
out = "\n".join(lines)
out = fix_import(out)
if out is None:
    print("  %-30s !! no 'from app.deps import' line - SKIPPED" % SETTINGS)
else:
    write(p, out)
    total += changed
    print("  %-30s %d guards swapped (kept: %s)" % (SETTINGS, changed, ", ".join(SETTINGS_KEEP)))

print("\ntotal guards swapped: %d" % total)
