# -*- coding: utf-8 -*-
"""P0 SWEEP - the six defect classes that stop an audit.

  1. UNAUTHENTICATED ROUTES        no auth dependency of any kind
  2. DESTRUCTIVE OPERATIONS        delete/purge/execute, and whether audited
  3. SECRET EXPOSURE               a credential value in a response body
  4. CROSS-TENANT READS            a path org_id used without a scope check
  5. PRIVILEGE                     role writes and grant paths
  6. PAYMENT INTEGRITY             webhook verification, idempotency

Findings are CANDIDATES. Every one is read by hand before it is called a
defect - a scanner that declares vulnerabilities is worse than no scanner.

Writes _audit_p0.txt.
"""
import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
O = []


def w(s=""):
    O.append(s)


def read(p):
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


def routers():
    base = os.path.join(ROOT, "app", "routers")
    return sorted(os.path.join(base, f) for f in os.listdir(base)
                  if f.endswith(".py") and f != "__init__.py")


DEC = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*["\']([^"\']*)["\']')
PREFIX = re.compile(r'APIRouter\([^)]*prefix\s*=\s*["\']([^"\']*)["\']', re.S)

# ANY dependency whose name starts require_/get_current_/verify_ counts as a
# gate. The first version of this listed gate names by hand and reported 45
# routes as unauthenticated; reading them showed `require_import_leads` and
# `require_sales_manager` doing exactly their job. A scanner that names its
# own blind spot as a vulnerability wastes the reader's trust, so the rule is
# now structural rather than a list somebody has to keep current.
AUTH_RE = re.compile(
    r"Depends\(\s*(require_\w+|get_current\w*|verify_\w+|_require\w+|"
    r"resolve_\w*user\w*|current_\w+)")


def has_auth(body):
    return bool(AUTH_RE.search(body))

PUBLIC_OK = ("webhook", "/health", "/ping", "/version", "/public", "/demo",
             "/survey", "/intake", "/activate", "/booking", "/track",
             "/deal-room", "/setup", "/onboarding", "/10dlc", "/login",
             "/auth", "/branding", "/optin", "/opt-in", "/unsubscribe")


def func_blocks(src):
    """(decorator_line, method, path, function source) per route."""
    lines = src.splitlines()
    out = []
    for i, ln in enumerate(lines):
        m = DEC.search(ln)
        if not m:
            continue
        # function body: from here to the next @router or EOF
        j = i + 1
        while j < len(lines) and not lines[j].lstrip().startswith("@router."):
            j += 1
        out.append((i + 1, m.group(1).upper(), m.group(2),
                    "\n".join(lines[i:j])))
    return out


w("=" * 78)
w("P0 SWEEP")
w("=" * 78)

# ── 1. unauthenticated ──────────────────────────────────────────────────────
w()
w("-" * 78)
w("1. ROUTES WITH NO AUTH DEPENDENCY")
w("   public surfaces excluded by path; each remaining line is read by hand")
w("-" * 78)
n = 0
for p in routers():
    src = read(p)
    pre = PREFIX.search(src)
    pre = pre.group(1) if pre else ""
    for lineno, meth, path, body in func_blocks(src):
        full = pre + path
        if any(h in full.lower() for h in PUBLIC_OK):
            continue
        if has_auth(body):
            continue
        w("   %-42s :%-5d %-6s %s" % (os.path.basename(p), lineno, meth, full))
        n += 1
w("   TOTAL: %d" % n)

# ── 2. destructive ──────────────────────────────────────────────────────────
w()
w("-" * 78)
w("2. DESTRUCTIVE OPERATIONS  (delete / purge / execute / wipe)")
w("   flag = does the handler audit, and does it require confirmation")
w("-" * 78)
DESTR = re.compile(r"(delete|purge|wipe|destroy|execute|hard_delete|truncate)",
                   re.I)
n = 0
for p in routers():
    src = read(p)
    pre = PREFIX.search(src)
    pre = pre.group(1) if pre else ""
    for lineno, meth, path, body in func_blocks(src):
        full = pre + path
        if not (meth == "DELETE" or DESTR.search(path)):
            continue
        audited = "log_action" in body or "audit" in body.lower()
        confirm = ("confirm" in body.lower() or "preview" in body.lower()
                   or "dry_run" in body.lower())
        god = "require_god" in body
        w("   %-40s %-6s %-46s audit=%-5s confirm=%-5s god=%s"
          % (os.path.basename(p), meth, full, audited, confirm, god))
        n += 1
w("   TOTAL: %d" % n)

# ── 3. secret exposure ──────────────────────────────────────────────────────
w()
w("-" * 78)
w("3. CREDENTIAL VALUES REACHING A RESPONSE BODY")
w("-" * 78)
SECRET = re.compile(
    r'["\'](\w*(auth_token|api_key|secret|password|access_token|private_key)\w*)'
    r'["\']\s*:\s*(?!None|\"\"|\'\')', re.I)
n = 0
for base in ("app/routers", "app/services"):
    d = os.path.join(ROOT, base)
    for dp, dn, fn in os.walk(d):
        dn[:] = [x for x in dn if x != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            src = read(os.path.join(dp, f))
            for m in SECRET.finditer(src):
                line = src[:m.start()].count("\n") + 1
                ctx = src.splitlines()[line - 1].strip()
                # masked / redacted forms are the correct pattern
                if any(k in ctx.lower() for k in
                       ("last4", "mask", "redact", "***", "bool(", "is not None",
                        "encrypted", "has_", "_set", "configured", "presence")):
                    continue
                rel = os.path.relpath(os.path.join(dp, f), ROOT).replace("\\", "/")
                w("   %s:%d  %s" % (rel, line, ctx[:110]))
                n += 1
w("   TOTAL: %d" % n)

# ── 4. cross-tenant: path org id used without a scope check ─────────────────
w()
w("-" * 78)
w("4. HANDLERS TAKING AN ORGANIZATION ID FROM THE PATH")
w("   flag = is there any authorization on it")
w("-" * 78)
n = 0
for p in routers():
    src = read(p)
    pre = PREFIX.search(src)
    pre = pre.group(1) if pre else ""
    for lineno, meth, path, body in func_blocks(src):
        if not re.search(r"\{(org_id|organization_id|orgId)\}", path):
            continue
        checked = any(k in body for k in (
            "require_god", "platform_id ==", "may_view_org", "authorized_org",
            "_require", "assert_org", "organization_id ==", "scope",
            "require_capability", "workspace_access", "get_platform_org_ids"))
        if checked:
            continue
        w("   %-40s %-6s %-44s  NO VISIBLE SCOPE CHECK"
          % (os.path.basename(p), meth, pre + path))
        n += 1
w("   TOTAL UNCHECKED: %d" % n)

# ── 5. privilege ────────────────────────────────────────────────────────────
w()
w("-" * 78)
w("5. ROLE AND GRANT WRITES")
w("-" * 78)
n = 0
for p in routers():
    src = read(p)
    for lineno, meth, path, body in func_blocks(src):
        if not re.search(r"\.role\s*=|role\s*=\s*[\"']god|Membership\(", body):
            continue
        god = "require_god" in body
        w("   %-40s %-6s %-40s require_god=%s"
          % (os.path.basename(p), meth, path, god))
        n += 1
w("   TOTAL: %d" % n)

# ── 6. payment integrity ────────────────────────────────────────────────────
w()
w("-" * 78)
w("6. PAYMENT INTEGRITY")
w("-" * 78)
for f in ("billing_router.py", "god_billing_router.py"):
    src = read(os.path.join(ROOT, "app", "routers", f))
    if not src:
        continue
    w("   %s" % f)
    for k, label in (("construct_event", "signature verification"),
                     ("STRIPE_WEBHOOK_SECRET", "webhook secret required"),
                     ("stripe_event_id", "event idempotency key"),
                     ("collection_reference", "collection idempotency"),
                     ("503", "fail-closed when unconfigured")):
        w("      %-28s %s" % (label, "present" if k in src else "ABSENT"))
svc = read(os.path.join(ROOT, "app", "services", "billing_webhook.py")) or ""
if svc:
    w("   billing_webhook.py")
    for k, label in (("construct_event", "signature verification"),
                     ("stripe_event_id", "event idempotency key")):
        w("      %-28s %s" % (label, "present" if k in svc else "ABSENT"))

io.open(os.path.join(ROOT, "_audit_p0.txt"), "w",
        encoding="utf-8", newline="\n").write("\n".join(O))
print("wrote _audit_p0.txt")
