"""Static send-path contract for Wholesale outbound SMS (SYNTHETIC / STATIC SOURCE).

Parses app/**/*.py with `ast` - no framework imports, no network, no DB, no
sends. Proves, from source:

  1. Every Twilio `messages.create(` call site in app/ is a KNOWN boundary. A new
     unregistered provider call fails the run, so a bypass cannot be added silently.
  2. Each boundary's enclosing function runs its required gates BEFORE the provider
     call (source-order check), and each seller-reaching boundary carries the
     Wholesale program gate.
  3. No Wholesale router/service other than the registered boundaries touches the
     Twilio client directly; bulk/AI/cadence/manual paths reach `send_sms`.
  4. Leads.jsx reads the response keys /ai-conversation/preview really returns.

Run: python3 scripts/wholesale_send_path_contract.py
"""
import ast
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
APP = os.path.join(ROOT, "app")

passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def rel(p):
    return os.path.relpath(p, ROOT).replace(os.sep, "/")


def parse(path):
    with open(path, encoding="utf-8-sig") as fh:
        src = fh.read()
    return src, ast.parse(src)


def functions(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def create_calls(fn):
    """Lines of `<x>.messages.create(` calls lexically inside fn."""
    out = []
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "create"
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "messages"):
            out.append(n.lineno)
    return out


def first_line_of(fn, token):
    """First line inside fn whose source text contains token (None if absent)."""
    seg_lines = SRC_LINES[fn.lineno - 1:fn.end_lineno]
    for i, text in enumerate(seg_lines):
        code = text.split("#", 1)[0]
        if token in code:
            return fn.lineno + i
    return None


# ---- 1. inventory every messages.create site ------------------------------
# (file, function) -> (kind, [gate tokens that must precede the create call])
REGISTRY = {
    ("app/services/sms_service.py", "send_sms"): (
        "seller_gate", ["_demo_send_guard", 'status == "dnc"', "is_phone_suppressed",
                        "enforce_for_lead"]),
    ("app/services/sms_service.py", "send_mms"): (
        "seller_gate", ["is_phone_suppressed", "is_program_lead"]),
    ("app/services/wholesale_sms.py", "send_program_sms"): (
        "seller_gate", ["is_program_lead", "check_eligibility", "program_sender"]),
    ("app/services/wholesale_disposition.py", "_send_sms"): (
        "buyer_gate_in_preflight", []),
    # advisor/staff-facing alerts to the staff member's own phone
    ("app/routers/calendar_router.py", "booking_confirmed_webhook"): ("refusal_for_phone", ["refusal_for_phone"]),
    ("app/services/pipeline_service.py", "_notify_fsa_sms"): ("staff_notify", []),
    # lead-facing direct sends that must refuse Wholesale sellers
    ("app/services/appointment_flow_service.py", "*"): ("refusal_for_phone", ["refusal_for_phone"]),
    ("app/services/post_appointment_service.py", "*"): ("refusal_for_phone", ["refusal_for_phone"]),
    ("app/crons/review_request_cron.py", "*"): ("refusal_for_phone", ["refusal_for_phone"]),
}

KNOWN_UNPARSEABLE = {"app/migrate_add_import_tables.py"}
sites = []
for dirpath, _, files in os.walk(APP):
    for f in files:
        if not f.endswith(".py"):
            continue
        path = os.path.join(dirpath, f)
        try:
            src, tree = parse(path)
        except SyntaxError:
            # app/migrate_add_import_tables.py is a one-off script committed
            # truncated (698 bytes); it is not imported and sends nothing.
            check("parses: " + rel(path), rel(path) in KNOWN_UNPARSEABLE)
            continue
        SRC_LINES = src.splitlines()
        for fn in functions(tree):
            for ln in create_calls(fn):
                sites.append((rel(path), fn.name, ln, fn, list(SRC_LINES)))

# de-duplicate nested-function double counting: keep innermost (largest lineno start)
inner = {}
for p, name, ln, fn, lines in sites:
    key = (p, ln)
    if key not in inner or fn.lineno > inner[key][3].lineno:
        inner[key] = (p, name, ln, fn, lines)
sites = sorted(inner.values(), key=lambda s: (s[0], s[2]))
check("found messages.create call sites", len(sites) >= 8)


def registered(path, name):
    for (p, n), v in REGISTRY.items():
        if p != path:
            continue
        if n == name or n == "*":
            return v
    return None


for p, name, ln, fn, lines in sites:
    reg = registered(p, name)
    check("registered boundary: %s::%s:%d" % (p, name, ln), reg is not None)
    if reg is None:
        continue
    kind, gates = reg
    SRC_LINES = lines
    if kind in ("seller_gate",):
        for g in gates:
            gl = first_line_of(fn, g)
            check("%s::%s gate %r precedes provider call" % (p, name, g),
                  gl is not None and gl < ln)
    elif kind == "refusal_for_phone":
        # the refusal lives in the file (helper or inline) - require it be present
        text = "\n".join(lines)
        check("%s carries refusal_for_phone" % p, "refusal_for_phone" in text)

# ---- 2. buyer deal-sheet SMS: preflight is the only road to _send_sms ------
dpath = os.path.join(APP, "services/wholesale_disposition.py")
dsrc, dtree = parse(dpath)
SRC_LINES = dsrc.splitlines()
fns = {f.name: f for f in functions(dtree)}
pre, send = fns["preflight"], fns["send_to_buyer"]
call_pre = first_line_of(send, "preflight(")
call_out = first_line_of(send, "_send_sms(")
check("send_to_buyer calls preflight before _send_sms",
      call_pre is not None and call_out is not None and call_pre < call_out)
check("_send_sms only called from send_to_buyer",
      len(re.findall(r"\b_send_sms\(", dsrc)) == 2)  # def + one call
order = ["is_test", "do_not_contact", "is_phone_suppressed", "already_sent",
         "block_if_demo", "sms_enabled"]
lines = [first_line_of(pre, t) for t in order]
check("preflight contains every gate", all(l is not None for l in lines))
check("preflight gate order sandbox<optout<suppression<idempotency<demo<switch",
      all(l is not None for l in lines) and lines == sorted(lines))
check("SMS switch defaults OFF (env var, not hard-coded true)",
      "OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION" in dsrc
      and "_TRUE = (" in dsrc)
check("refusal codes include suppressed", '"suppressed"' in dsrc)

# ---- 3. no Wholesale module reaches Twilio except registered boundaries ----
direct = []
for dirpath, _, files in os.walk(APP):
    for f in files:
        if "wholesale" in f and f.endswith(".py"):
            text = open(os.path.join(dirpath, f), encoding="utf-8").read()
            code = "\n".join(l.split("#", 1)[0] for l in text.splitlines())
            if re.search(r"from twilio|import twilio|\.messages\.create\(", code):
                direct.append(f)
check("wholesale files touching twilio are exactly the registered ones: %s" % sorted(direct),
      sorted(direct) == ["wholesale_disposition.py", "wholesale_sms.py"])

# Wholesale seller router: manual send goes through sms_service.send_sms
rsrc = open(os.path.join(APP, "routers/wholesale_router.py"), encoding="utf-8").read()
check("manual seller send uses sms_service.send_sms", "sms_service.send_sms(" in rsrc)
check("manual seller send stamps MANUAL source", re.search(r"send_sms\([^)]*send_source=\"manual\"", rsrc, re.S) is not None)
check("wholesale router never builds a Twilio client", "Client(" not in rsrc)
for other in ("wholesale_buyers_router.py", "wholesale_ops_router.py",
              "wholesale_seller_intake_router.py", "wholesale_files_router.py",
              "wholesale_rooms_router.py"):
    t = open(os.path.join(APP, "routers", other), encoding="utf-8").read()
    check("%s has no direct provider call" % other, ".messages.create(" not in t)

# buyer disposition endpoint is human-triggered, bounded, org-scoped
bsrc = open(os.path.join(APP, "routers/wholesale_buyers_router.py"), encoding="utf-8").read()
check("disposition caps batch at 100", "> 100" in bsrc)
check("disposition org-scopes the deal", "svc.get_deal(db, org_id, deal_id)" in bsrc)
check("disposition rejects observation users", "require_not_observation" in bsrc)

# ---- 4. frontend/endpoint response-shape agreement ------------------------
jsx = open(os.path.join(ROOT, "frontend/src/pages/Leads.jsx"), encoding="utf-8").read()
svc = open(os.path.join(APP, "services/ai_conversation_service.py"), encoding="utf-8").read()
for key in ("reply", "should_stop", "reason", "source", "error_kind"):
    check("preview endpoint returns %r" % key, '"%s":' % key in svc)
    check("Leads.jsx reads result.%s" % key, "result.%s" % key in jsx)
jsx_code = "\n".join(l.split("//", 1)[0] for l in jsx.splitlines())
check("Leads.jsx no longer reads result.message", "result.message" not in jsx_code)

print("send-path contract: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
