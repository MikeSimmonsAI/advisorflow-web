"""SCI POC readiness harness: stdlib only, no pytest / FastAPI / SQLAlchemy / network.

Runs deterministic scenarios against the REAL production decision modules
(these are imported, not re-implemented here):

    app/services/programs/regional_pools.py   pools, routing decision
    app/services/programs/alias_rules.py      entity -> alias slug, address validation
    app/services/programs/reply_rules.py      HOT/ACTIVE/LOW, opt-out/wrong-person/bad-data,
                                              cadence, alert, duplicate, draft-reply decisions
    app/services/optout_parser.py             global opt-out parser
    scripts/sci_campuses.csv                  the locked 30-campus / 39-entity grouping

Run:  python3 -I scripts/sci_readiness_harness.py [--md PATH]
Exit status 0 only if every scenario passes.

WHAT THIS IS NOT: it does not replace the dependency-backed pytest suites or
the DB integration tests (webhook -> DB rows, Twilio guard, real PhoneNumber
rows, cadence rows). Those remain unrun in this environment. See
handoff/SCI_READINESS_HARNESS.md for the explicit list.
"""
import ast
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.services import optout_parser as op                      # noqa: E402
from app.services.programs import alias_rules as ar               # noqa: E402
from app.services.programs import readiness_check as rc           # noqa: E402
from app.services.programs import regional_pools as rp            # noqa: E402
from app.services.programs import reply_rules as rr               # noqa: E402
from app.services.programs import webhook_simulation as ws        # noqa: E402
from app.utils import twilio_signature as tsig                    # noqa: E402

with open(os.path.join(ROOT, "scripts", "sci_campuses.csv"), encoding="utf-8") as _f:
    ROWS = list(csv.DictReader(_f))
BY_NAME = {r["Location"]: r for r in ROWS}
AREAS = ["205", "334", "850", "251", "706", "318"]
SCENARIOS = []          # (group, name, fn)


def scenario(group, name):
    def deco(fn):
        SCENARIOS.append((group, name, fn))
        return fn
    return deco


def add(group, name, fn):
    SCENARIOS.append((group, name, fn))


def eq(got, want):
    assert got == want, "got %r, want %r" % (got, want)


# ── pools (all six) ──────────────────────────────────────────────────────────
for _a in AREAS:
    def _pool(a=_a):
        pool = rp.pool_for_area_code(a)
        assert pool and rp.sender_pool(a) == pool["pool_id"]
        assert rp.pool_members(ROWS)[pool["pool_id"]], "pool has no entities"
    add("pools", "pool %s resolves and has entities" % _a, _pool)

    def _label(a=_a):
        rec = type("N", (), {"label": rp.POOL_LABEL_PREFIX + rp.POOLS[a]["pool_id"]})()
        eq(rp.pool_for_phone_number(rec), rp.POOLS[a])
    add("pools", "pool %s number label round-trips (no location in the number)" % _a, _label)

eq_pools = {a: rp.POOLS[a]["pool_id"] for a in AREAS}
add("pools", "exactly six pools, unique ids", lambda: eq((len(rp.POOLS), len(set(eq_pools.values()))), (6, 6)))
add("pools", "every pooled entity belongs to exactly one pool",
    lambda: eq(sum(len(v) for v in rp.pool_members(ROWS).values()), 38))
add("pools", "foreign/missing pool label is not a pool",
    lambda: eq([rp.pool_for_phone_number(type("N", (), {"label": l})()) for l in ("pool:pool-999-x", None, "main")],
               [None, None, None]))


@scenario("pools", "Pine Crest Cemetery West is verified and in the 251 (Mobile) pool")
def _():
    r = BY_NAME["Pine Crest Cemetery West"]
    eq((r["Area Code"], r["Address Status"]), ("251", "verified"))
    assert "Pine Crest Cemetery West" in rp.pool_members(ROWS)["pool-251-mobile"]


@scenario("pools", "Oaklawn is unresolved: no area code, no pool, no sender")
def _():
    r = BY_NAME["Oaklawn Central Care Center"]
    eq((r["Area Code"], r["Address Status"]), ("", "unverified"))
    assert all(r["Location"] not in v for v in rp.pool_members(ROWS).values())
    try:
        rp.sender_pool(r["Area Code"])
    except LookupError:
        return
    raise AssertionError("Oaklawn must have no sender pool")


@scenario("pools", "844 toll-free is overflow only: never a pool, never a sender")
def _():
    eq(rp.BACKUP_TOLL_FREE, "+18449172171")
    eq(rp.pool_for_area_code("844"), None)
    assert not any("844" in p["pool_id"] for p in rp.POOLS.values())
    try:
        rp.sender_pool("844")
    except LookupError:
        return
    raise AssertionError("844 must not resolve as a sender")


for _bad in (None, "", "   ", "999", "214"):
    def _unv(b=_bad):
        eq(rp.pool_for_area_code(b), None)
        try:
            rp.sender_pool(b)
        except LookupError:
            return
        raise AssertionError("no sender expected")
    add("pools", "unverified/foreign area code %r has no sender (844 not a silent default)" % (_bad,), _unv)

# ── exact entity and alias preservation ──────────────────────────────────────
add("entities", "30 campuses / 39 entities locked",
    lambda: eq((len(ROWS), len({r["Campus"] for r in ROWS})), (39, 30)))
add("entities", "entity names are unique (no merging)",
    lambda: eq(len({r["Location"] for r in ROWS}), 39))


@scenario("entities", "alias slugs are unique and non-empty for all 39 entities")
def _():
    slugs = ar.slugs_for([r["Location"] for r in ROWS])
    eq(len(slugs), 39)
    eq(len(set(slugs.values())), 39)
    assert all(slugs.values())


@scenario("entities", "alias slugs are valid local parts and not reserved")
def _():
    bad = {n: ar.validate_local(s) for n, s in ar.slugs_for([r["Location"] for r in ROWS]).items()
           if ar.validate_local(s)}
    eq(bad, {})


@scenario("entities", "shared-campus twins keep distinct entity names AND distinct aliases")
def _():
    by_campus = {}
    for r in ROWS:
        by_campus.setdefault(r["Campus"], []).append(r["Location"])
    twins = [v for v in by_campus.values() if len(v) > 1]
    assert twins, "expected multi-entity campuses"
    slugs = ar.slugs_for([r["Location"] for r in ROWS])
    for v in twins:
        eq(len({slugs[n] for n in v}), len(v))


@scenario("entities", "Pine Crest Cemetery / Funeral Home share a campus, Cemetery West is its own")
def _():
    eq(BY_NAME["Pine Crest Cemetery"]["Campus"], BY_NAME["Pine Crest Funeral Home"]["Campus"])
    assert BY_NAME["Pine Crest Cemetery West"]["Campus"] != BY_NAME["Pine Crest Cemetery"]["Campus"]


@scenario("entities", "alias casing/apostrophes normalised without changing the entity name")
def _():
    s = ar.slugs_for(["Pine Crest Cemetery West"])
    eq(s["Pine Crest Cemetery West"], "pinecrestwest")
    for bad in ("Admin", "a..b", "support", "-x"):
        assert ar.validate_local(bad), bad
    eq(ar.validate_local("pinecrestwest"), None)


@scenario("entities", "alias domain extraction")
def _():
    eq((ar.domain_of("Hello@Example.COM"), ar.domain_of("nope"), ar.domain_of(None)), ("example.com", None, None))


# ── known-contact exact-entity routing / unknown review routing ──────────────
for _a in AREAS:
    def _known(a=_a):
        ent = next(r["Location"] for r in ROWS if r["Area Code"] == a)
        d = rp.route_inbound(ent, rp.POOLS[a]["pool_id"])
        eq((d["location"], d["queue"], d["pool_id"]), (ent, None, rp.POOLS[a]["pool_id"]))
    add("routing", "pool %s: known contact routes to own exact entity" % _a, _known)

    def _unknown(a=_a):
        pid = rp.POOLS[a]["pool_id"]
        d = rp.route_inbound(None, pid)
        eq((d["location"], d["queue"]), (None, "regional_review:" + pid))
    add("routing", "pool %s: unknown sender/caller goes to regional review, no location guessed" % _a, _unknown)


@scenario("routing", "same-campus twin: funeral-home contact is NOT re-homed to the cemetery")
def _():
    d = rp.route_inbound("Pine Crest Funeral Home", rp.POOLS["251"]["pool_id"])
    eq(d["location"], "Pine Crest Funeral Home")


@scenario("routing", "a pool number never supplies identity (empty-string sender is unknown)")
def _():
    eq(rp.route_inbound("", rp.POOLS["205"]["pool_id"])["queue"], "regional_review:pool-205-birmingham")


# ── opt-outs and "stop by" false-positive protection ─────────────────────────
for _t in ("STOP", "stop", "Stop.", "unsubscribe", "Please STOP", "QUIT", "cancel", "stopall", "END", "OPT OUT"):
    add("opt-out", "clear opt-out suppresses: %r" % _t, lambda t=_t: eq(op.contains_hard_stop_language(t), True))
for _t in ("remove me from your list", "Please take me off this list", "stop texting me", "Stop calling me",
           "stop sending these messages", "unsubscribe me please"):
    add("opt-out", "explicit opt-out phrase suppresses: %r" % _t,
        lambda t=_t: eq(op.contains_hard_stop_language(t), True))
for _t in ("Can I stop by Friday?", "I'll stop by the office tomorrow", "Stop by anytime after 3, we can talk then",
           "we can't stop by", "I need to cancel my appointment", "see you this weekend", "Please remove my old address"):
    add("opt-out", "scheduling/ordinary language does NOT suppress: %r" % _t,
        lambda t=_t: eq(op.contains_hard_stop_language(t), False))
add("opt-out", "empty / None body is not an opt-out",
    lambda: eq((op.contains_hard_stop_language(""), op.contains_hard_stop_language(None)), (False, False)))
add("opt-out", "program classifier: 'Can I stop by Tuesday?' is HOT, not OPT-OUT",
    lambda: eq(rr.classify("Can I stop by Tuesday?")["class"], rr.HOT))
for _t in ("STOP", "unsubscribe", "Stop!"):
    add("opt-out", "program classifier: %r is OPT-OUT, closed, no alert, no draft" % _t,
        lambda t=_t: (eq(rr.classify(t)["class"], rr.OPT_OUT), eq(rr.alert_plan(rr.OPT_OUT), None),
                      eq(rr.suggested_reply(rr.OPT_OUT, [], first_name="A", contact="C", location="L",
                                            channel="sms"), None)))
add("opt-out", "program classifier: 'do not text me' is OPT-OUT",
    lambda: eq(rr.classify("Please do not text me again")["class"], rr.OPT_OUT))

# ── HOT / ACTIVE / LOW ───────────────────────────────────────────────────────
for _t, _c in (("Yes please, I'm interested", rr.HOT), ("How much does a plan cost?", rr.HOT),
               ("Please call me tomorrow", rr.HOT), ("Can we schedule a visit?", rr.HOT),
               ("What does the package include?", rr.ACTIVE), ("Can you send me a brochure", rr.ACTIVE),
               ("ok", rr.LOW), ("Thanks!", rr.LOW), ("", rr.LOW)):
    add("classify", "%r -> %s" % (_t, _c), lambda t=_t, c=_c: eq(rr.classify(t)["class"], c))
add("classify", "bereavement reply is HOT and routed to a personal reply",
    lambda: eq(rr.classify("My husband passed away last month")["class"], rr.HOT))
add("classify", "upstream 'interested' classification promotes a neutral-looking reply to HOT",
    lambda: eq(rr.classify("sounds fine", "interested")["class"], rr.HOT))
add("classify", "urgency mapping: HOT/ACTIVE high, terminal lanes LOW (never an emergency page)",
    lambda: eq([rr.urgency(c) for c in (rr.HOT, rr.ACTIVE, rr.LOW, rr.OPT_OUT, rr.BAD_DATA, rr.WRONG_PERSON)],
               ["HOT", "ACTIVE", "LOW", "LOW", "LOW", "LOW"]))
add("classify", "intents: pricing + appointment detected, ack for 'ok'",
    lambda: (eq(sorted(rr.intents("What's the price? Can I schedule a visit?", rr.HOT))[:2],
                sorted([rr.APPOINTMENT, rr.PRICING])[:2]),
             eq(rr.intents("ok", rr.LOW), [rr.ACKNOWLEDGMENT])))

# ── wrong person / bad data ──────────────────────────────────────────────────
for _t, _c in (("This is not him, wrong person", rr.WRONG_PERSON), ("you have the wrong number", rr.BAD_DATA),
               ("who is this?", rr.BAD_DATA), ("she doesn't live here", rr.WRONG_PERSON)):
    add("data", "%r -> %s (held for Data Review, not sold to)" % (_t, _c),
        lambda t=_t, c=_c: eq(rr.classify(t)["class"], c))
add("data", "wrong person/bad data raise an in-app alert only (no external)",
    lambda: eq([rr.alert_plan(c)["external"] for c in (rr.WRONG_PERSON, rr.BAD_DATA)], [False, False]))
add("data", "wrong-person draft acknowledges and promises no further contact",
    lambda: assert_in("won't hear from us again",
                      rr.suggested_reply(rr.WRONG_PERSON, [rr.I_WRONG_PERSON], first_name="Pat", contact="Sam",
                                         location="Pine Crest", channel="sms")))


def assert_in(needle, hay):
    assert needle in hay, "%r not in %r" % (needle, hay)


# ── cadence pause ────────────────────────────────────────────────────────────
add("cadence", "any reply pauses an ACTIVE cadence",
    lambda: eq(rr.cadence_action(rr.ACTIVE, "active"), {"new_status": "paused", "paused": True}))
add("cadence", "HOT reply pauses an ACTIVE cadence",
    lambda: eq(rr.cadence_action(rr.HOT, "active")["new_status"], "paused"))
add("cadence", "opt-out ENDS an active cadence (stopped_dnc), not just pauses",
    lambda: eq(rr.cadence_action(rr.OPT_OUT, "active")["new_status"], "stopped_dnc"))
add("cadence", "opt-out ENDS an already-paused cadence",
    lambda: eq(rr.cadence_action(rr.OPT_OUT, "paused")["new_status"], "stopped_dnc"))
add("cadence", "no cadence: nothing to pause",
    lambda: eq(rr.cadence_action(rr.HOT, None), {"new_status": None, "paused": False}))
add("cadence", "a stopped cadence is never resumed by a reply",
    lambda: eq(rr.cadence_action(rr.HOT, "stopped_dnc")["new_status"], "stopped_dnc"))

# ── duplicate / idempotency ──────────────────────────────────────────────────
add("idempotency", "retried MessageSid with stored reply -> duplicate, no second pipeline",
    lambda: eq(rr.duplicate_inbound_result("SM1", "r-1"), {"status": "duplicate", "reply_id": "r-1"}))
add("idempotency", "new MessageSid -> processed",
    lambda: eq(rr.duplicate_inbound_result("SM2", None), None))
add("idempotency", "no MessageSid cannot be deduped (processed)",
    lambda: eq(rr.duplicate_inbound_result("", "r-1"), None))
add("idempotency", "same input classifies identically twice (deterministic)",
    lambda: eq(rr.classify("Can I schedule a visit?"), rr.classify("Can I schedule a visit?")))

# ── alerts ───────────────────────────────────────────────────────────────────
add("alerts", "HOT: management + external alert, kind hot",
    lambda: eq(rr.alert_plan(rr.HOT), {"kind": "hot", "management": True, "external": True}))
add("alerts", "ACTIVE: external, no management",
    lambda: eq(rr.alert_plan(rr.ACTIVE), {"kind": "active", "management": False, "external": True}))
add("alerts", "LOW: in-app only",
    lambda: eq(rr.alert_plan(rr.LOW)["external"], False))
add("alerts", "staff SMS/email is OFF by default: attempt refused with reason",
    lambda: eq(rr.staff_alert_gate("+15555550100", "sms", "primary", False),
               (False, "staff alerts are switched off for this program")))
add("alerts", "missing recipient is recorded, never guessed",
    lambda: eq(rr.staff_alert_gate(None, "email", "management", True),
               (False, "no email configured for management alerts")))
add("alerts", "enabled + recipient configured -> allowed",
    lambda: eq(rr.staff_alert_gate("+15555550100", "sms", "primary", True), (True, None)))

# ── no AI auto-send ──────────────────────────────────────────────────────────
add("no-auto-send", "suggested reply is a DRAFT string; terminal lanes get none",
    lambda: (assert_in("Hi Pat,", rr.suggested_reply(rr.HOT, [rr.APPOINTMENT], first_name="pat", contact="Sam",
                                                     location="Pine Crest", channel="sms")),
             eq(rr.suggested_reply(rr.LOW, [], first_name="p", contact="c", location="l", channel="sms"), None)))
add("no-auto-send", "draft never asks the family to call",
    lambda: eq("call" in rr.suggested_reply(rr.HOT, [rr.APPOINTMENT, rr.PRICING], first_name="P", contact="S",
                                            location="L", channel="sms").lower(), False))

# ── Controlled Test console (app/services/programs/readiness_check.py) ───────
# The console's ten synthetic checks are executed here too, so the harness and
# the in-app "Run controlled readiness test" button can never disagree.
for _k, _label, _fn, _fix in rc.CHECKS:
    add("console", "console check: %s" % _label, _fn)


@scenario("console", "console run_all reports 10 PASS / 0 FAIL, nothing sent, no failed gate")
def _():
    out = rc.run_all()
    eq((out["pass_count"], out["fail_count"], out["failed_gate"], out["sent"]), (10, 0, None, 0))


@scenario("console", "console reports the exact failed gate when a check breaks")
def _():
    def boom():
        raise AssertionError("injected")
    saved = rc.CHECKS[:]
    rc.CHECKS[:] = [(k, l, boom if k == "oaklawn_hold" else f, x) for k, l, f, x in saved]
    try:
        out = rc.run_all()
    finally:
        rc.CHECKS[:] = saved
    eq((out["fail_count"], out["failed_gate"]), (1, saved[6][1]))
    eq(rc.verdict(out)["status"], "BLOCKED")


@scenario("console", "verdict: not run = BLOCKED; all pass with external blockers = CONDITIONAL (never READY)")
def _():
    eq(rc.verdict(None)["status"], "BLOCKED")
    eq(rc.verdict(rc.run_all())["status"], "CONDITIONAL")


def _imports_of(path):
    tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            out.add(n.module or "")
    return out


def _no_send_surface(path):
    imps = _imports_of(path)
    banned = ("twilio", "sms_service", "email_service", "requests", "httpx", "openai", "ai_gateway",
              "sqlalchemy", "smtplib", "socket")
    hits = {i for i in imps if any(b in i for b in banned)}
    eq(hits, set())


for _p in ("app/services/programs/reply_rules.py", "app/services/optout_parser.py",
           "app/services/programs/regional_pools.py", "app/services/programs/alias_rules.py"):
    add("no-auto-send", "decision module has no send/network/DB imports: %s" % _p,
        lambda p=_p: _no_send_surface(p))


@scenario("no-auto-send", "responses.py reply path never calls a customer-send function")
def _():
    src = open(os.path.join(ROOT, "app/services/programs/responses.py"), encoding="utf-8").read()
    # the only outbound in this module is _deliver_staff_alert (staff, gated by staff_alert_gate)
    tree = ast.parse(src)
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not (calls & {"send_sms", "send_message", "send_sms_via_provider", "send_to_lead"}), calls
    assert "staff_alert_gate" in calls and "_deliver_staff_alert" in calls


# ── Signed inbound SMS / voice webhook simulation (gate T4) ──────────────────
# Real signature primitives (app/utils/twilio_signature.py, shared with the
# FastAPI guard) plus real routing/reply rules; run-local synthetic token.
for _k, _label, _fn in ws._scenarios(ws.new_token()):
    add("webhook-sim", "webhook sim: %s" % _label, _fn)


@scenario("webhook-sim", "signature primitive matches an independent HMAC-SHA1 computation")
def _():
    import base64
    import hashlib
    import hmac
    tok, url, params = "run-local-" + ws.new_token(), "https://staging.synthetic.invalid/x", {"B": "2", "A": "1"}
    want = base64.b64encode(hmac.new(tok.encode(), (url + "A1B2").encode(), hashlib.sha1).digest()).decode()
    eq(tsig.compute_signature(tok, url, params), want)
    eq(tsig.signature_matches("", [url], params, want), False)
    eq(tsig.signature_matches(tok, [url], params, ""), False)


@scenario("webhook-sim", "twilio_security delegates to the shared pure signature module")
def _():
    src = open(os.path.join(ROOT, "app/utils/twilio_security.py"), encoding="utf-8").read()
    assert "from app.utils.twilio_signature import" in src


@scenario("webhook-sim", "simulation and signature module have no send/network/DB imports")
def _():
    # "twilio_signature" is the pure helper itself (name contains "twilio"); allow only that import
    imps = _imports_of("app/services/programs/webhook_simulation.py") - {"app.utils.twilio_signature"}
    eq({i for i in imps if any(b in i for b in ("twilio", "sms_service", "requests", "httpx", "sqlalchemy",
                                                  "smtplib", "socket", "openai", "ai_gateway", "fastapi"))}, set())
    _no_send_surface("app/utils/twilio_signature.py")


@scenario("webhook-sim", "webhook_proof run_all: all PASS, zero outbound")
def _():
    out = ws.run_all()
    eq((out["fail_count"], out["failed_gate"], out["sent"], out["outbound"]), (0, None, 0, 0))
    assert out["pass_count"] >= 12


@scenario("webhook-sim", "Launch Readiness run_all carries webhook_proof and verdict blocks on its failure")
def _():
    out = rc.run_all()
    eq(out["webhook_proof"]["fail_count"], 0)
    bad = dict(out, webhook_proof=dict(out["webhook_proof"], fail_count=1, failed_gate="x"))
    eq(rc.verdict(bad)["status"], "BLOCKED")


# ── Dependency-free gap coverage (continuation run) ─────────────────────────
@scenario("webhook-url", "proxy reconstruction: forwarded proto+host, host-only https, configured base, raw URL, deduped")
def _():
    h = {"x-forwarded-proto": "https", "x-forwarded-host": "api.example.invalid", "host": "10.0.0.1:10000"}
    got = tsig.candidate_urls("/sms/in", "a=1", h, "https://base.example.invalid/", "http://10.0.0.1:10000/sms/in?a=1")
    eq(got, ["https://api.example.invalid/sms/in?a=1", "https://base.example.invalid/sms/in?a=1",
             "http://10.0.0.1:10000/sms/in?a=1"])
    eq(tsig.candidate_urls("/x", "", {"host": "h.invalid"}, None, "http://h.invalid/x"),
       ["https://h.invalid/x", "http://h.invalid/x"])


@scenario("webhook-url", "comma-chained forwarded headers use the first hop only")
def _():
    h = {"x-forwarded-proto": "https, http", "x-forwarded-host": "a.invalid, b.invalid"}
    eq(tsig.candidate_urls("/p", "", h, None, "http://z/p")[0], "https://a.invalid/p")


@scenario("webhook-url", "signature signed over https verifies behind an http-reporting proxy; http-signed does not match wrong host")
def _():
    tok, params = "run-local-" + ws.new_token(), {"From": "+12055550111", "Body": "hi"}
    h = {"x-forwarded-proto": "https", "x-forwarded-host": "api.example.invalid"}
    cands = tsig.candidate_urls("/sms/in", "", h, None, "http://10.0.0.1/sms/in")
    good = tsig.compute_signature(tok, "https://api.example.invalid/sms/in", params)
    eq(tsig.signature_matches(tok, cands, params, good), True)
    evil = tsig.compute_signature(tok, "https://attacker.invalid/sms/in", params)
    eq(tsig.signature_matches(tok, cands, params, evil), False)
    eq(tsig.signature_matches(tok, cands, dict(params, Body="x"), good), False)
    eq(tsig.signature_matches("other-" + tok, cands, params, good), False)


@scenario("webhook-url", "twilio_security and voice guard fail closed in source (no pass-through return on missing token/signature)")
def _():
    src = open(os.path.join(ROOT, "app/utils/twilio_security.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for fn in tree.body:
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in (
                "verify_signature_or_403", "validate_twilio_webhook"):
            for node in ast.walk(fn):
                if isinstance(node, ast.If) and ast.unparse(node.test) in ("not auth_token", "not token", "not twilio_sig"):
                    kinds = [type(b).__name__ for b in node.body]
                    assert kinds and kinds[-1] == "Raise", (fn.name, ast.unparse(node.test), kinds)


def _load_voice_guard():
    """Import telephony_webhook_guard with fastapi stubbed (no real dependency)."""
    import importlib.util
    import types

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            self.status_code, self.detail = status_code, detail
    saved = {k: sys.modules.get(k) for k in ("fastapi",)}
    stub = types.ModuleType("fastapi")
    stub.HTTPException, stub.Request = HTTPException, object
    sys.modules["fastapi"] = stub
    try:
        spec = importlib.util.spec_from_file_location(
            "_tg_stub", os.path.join(ROOT, "app/services/telephony_webhook_guard.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved["fastapi"] is None:
            sys.modules.pop("fastapi", None)
        else:
            sys.modules["fastapi"] = saved["fastapi"]
    return mod, HTTPException


@scenario("cross-org", "voice guard: tenant-signed request cannot act on another org; no-org tenant fails closed")
def _():
    mod, HTTPException = _load_voice_guard()
    V = mod.VerifiedVoiceRequest
    mod.assert_org_matches(V({}, "AC1", "org-A", False), "org-A")
    mod.assert_org_matches(V({}, "AC1", "org-A", False), None)
    mod.assert_org_matches(V({}, "ACp", None, True), "org-B")

    def denied(v, org):
        try:
            mod.assert_org_matches(v, org)
        except HTTPException as e:
            return e.status_code
        return None
    eq(denied(V({}, "AC1", "org-A", False), "org-B"), 403)
    eq(denied(V({}, "AC1", None, False), "org-B"), 403)
    eq(denied(V({}, "AC1", "", False), "org-B"), 403)


@scenario("cross-org", "voice guard verifies the tenant-token signature before returning a verified request; platform fallback present")
def _():
    src = open(os.path.join(ROOT, "app/services/telephony_webhook_guard.py"), encoding="utf-8").read()
    assert src.index("verify_signature_or_403(request, resolved.auth_token") < src.index("return VerifiedVoiceRequest(params, account_sid, org_id")
    assert "await validate_twilio_webhook(request)" in src


@scenario("no-auto-send", "voice/sms webhook guards and signature modules import no send/AI surface")
def _():
    for p in ("app/services/telephony_webhook_guard.py", "app/utils/twilio_security.py"):
        imps = _imports_of(p)
        eq({i for i in imps if any(b in i for b in ("sms_service", "email_service", "openai", "ai_gateway", "smtplib"))}, set())


# ── Persisted finalizer coverage: inbound email decisions (dependency-free) ──
import contextlib   # noqa: E402
import datetime as _dt   # noqa: E402
import types       # noqa: E402


@contextlib.contextmanager
def _mailbox_stubs():
    """Stub httpx / sqlalchemy / ORM models so the pure mailbox functions import. Restored on exit."""
    names = ("httpx", "sqlalchemy", "sqlalchemy.orm", "app.models", "app.models.models",
             "app.models.inbound_mailbox_models", "_mbx_stub")
    saved = {n: sys.modules.get(n) for n in names}

    class Col:
        def in_(self, _v):
            return None

    def model(*cols):
        return type("M", (), {c: Col() for c in cols})
    sa = types.ModuleType("sqlalchemy")
    sa.func = types.SimpleNamespace(lower=lambda x: x)
    orm = types.ModuleType("sqlalchemy.orm")
    orm.Session = object
    sa.orm = orm
    pkg = types.ModuleType("app.models")
    pkg.__path__ = []
    mm = types.ModuleType("app.models.models")
    mm.Lead = model("id", "organization_id", "email")
    mm.EmailMessage = model("lead_id", "sent_at", "subject")
    im = types.ModuleType("app.models.inbound_mailbox_models")
    im.InboundMailbox = im.InboundMailboxMessage = object
    stubs = {"httpx": types.ModuleType("httpx"), "sqlalchemy": sa, "sqlalchemy.orm": orm,
             "app.models": pkg, "app.models.models": mm, "app.models.inbound_mailbox_models": im}
    sys.modules.update(stubs)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_mbx_stub", os.path.join(ROOT, "app/services/inbound_mailbox_service.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod, mm
    finally:
        for n, v in saved.items():
            if v is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = v


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a):
        return self

    def all(self):
        return list(self.rows)


class _FakeDB:
    def __init__(self, mm, leads, emails):
        self.mm, self.leads, self.emails = mm, leads, emails

    def query(self, *cols):
        return _FakeQuery(self.leads if cols and cols[0] is self.mm.Lead else self.emails)


def _lead(i, org, email="jane@x.invalid"):
    return types.SimpleNamespace(id=i, organization_id=org, email=email)


_T0 = _dt.datetime(2026, 1, 1)
_DAY = _dt.timedelta(days=1)


@scenario("inbound-email", "STOP typed in an email still suppresses after clean_body strips HTML/quotes")
def _():
    with _mailbox_stubs() as (mod, _mm):
        for raw in ("<p>STOP</p>\n<div>On Mon, A wrote:</div>", "Stop\r\n\r\n> earlier text", "<div>Unsubscribe</div>"):
            cleaned = mod.clean_body(raw)
            eq(op.contains_hard_stop_language(cleaned), True)
            eq(rr.classify(cleaned)["class"], rr.OPT_OUT)


@scenario("inbound-email", "quoted 'Reply STOP' in our own text does not suppress a genuine reply")
def _():
    with _mailbox_stubs() as (mod, _mm):
        raw = "Yes, please call me Tuesday.\n\nOn Mon, Sales wrote:\nReply STOP to opt out of texts"
        cleaned = mod.clean_body(raw)
        eq(cleaned, "Yes, please call me Tuesday.")
        eq(op.contains_hard_stop_language(cleaned), False)
        assert rr.classify(cleaned)["class"] != rr.OPT_OUT


@scenario("inbound-email", "clean_body: None/empty are safe and output is capped at 4000")
def _():
    with _mailbox_stubs() as (mod, _mm):
        eq((mod.clean_body(None), mod.clean_body(""), mod.clean_body("   \n ")), ("", "", ""))
        eq(len(mod.clean_body("a" * 9000)), 4000)
        eq(len(mod.clean_body("a" * 4000)), 4000)


@scenario("inbound-email", "subject normalization strips RE/FWD/FW/AW/SV (stacked, numbered, any case)")
def _():
    with _mailbox_stubs() as (mod, _mm):
        n = mod._norm_subject
        for s in ("Hello World", "RE: Hello World", "re: FWD: hello  world", "AW: Hello World",
                  "SV: Hello World", "Fw: Re[2]: Hello World", "  RE:RE: hello world "):
            eq(n(s), "hello world")
        eq((n(None), n(""), n("RE:")), ("", "", ""))


@scenario("inbound-email", "recipients_of: null/blank/missing recipients are skipped; To+Cc lower-cased")
def _():
    with _mailbox_stubs() as (mod, _mm):
        eq(mod.recipients_of({}), [])
        eq(mod.recipients_of({"toRecipients": None, "ccRecipients": None}), [])
        m = {"toRecipients": [None, {}, {"emailAddress": None}, {"emailAddress": {"address": "  "}},
                              {"emailAddress": {"address": " A@X.Invalid "}}],
             "ccRecipients": [{"emailAddress": {"address": "B@Y.invalid"}}]}
        eq(mod.recipients_of(m), ["a@x.invalid", "b@y.invalid"])


@scenario("inbound-email", "mailbox service has no send/Twilio/SMTP import surface; no_lead/ambiguous paths return no lead")
def _():
    imps = _imports_of("app/services/inbound_mailbox_service.py")
    eq({i for i in imps if any(b in i for b in ("twilio", "sms_service", "email_service", "smtplib",
                                                  "openai", "ai_gateway"))}, set())
    src = open(os.path.join(ROOT, "app/services/inbound_mailbox_service.py"), encoding="utf-8").read()
    for outcome in ('"no_lead"', '"ambiguous"'):
        assert "return None, " + outcome in src, outcome


@scenario("inbound-email", "empty/None reply body is not an opt-out and not HOT")
def _():
    for t in (None, "", "   ", "(Replied with no text.)"):
        c = rr.classify(t)["class"]
        assert c not in (rr.OPT_OUT, rr.HOT), (t, c)
        eq(op.contains_hard_stop_language(t), False)


@scenario("controlled-readiness", "evidence is synthetic, no-send and carries no secret-shaped values")
def _():
    import re as _re
    out = rc.run_all()
    eq((out["synthetic_only"], out["sent"], out["webhook_proof"]["sent"], out["webhook_proof"]["outbound"]), (True, 0, 0, 0))
    blob = repr(out)
    assert not _re.search(r"\bAC[0-9a-f]{32}\b|\bSK[0-9a-f]{32}\b|sk-[A-Za-z0-9]{20,}|Bearer\s+\S+", blob), "secret-shaped value"
    for r in out["results"]:
        eq(set(r), {"key", "label", "status", "error", "next_action"})
        assert r["status"] in ("PASS", "FAIL")


@scenario("inbound-email", "route(): no workspaces / no matching lead fails closed with a fake session")
def _():
    with _mailbox_stubs() as (mod, mm):
        eq(mod.route(_FakeDB(mm, [], []), [], "jane@x.invalid")[:2], (None, "no_lead"))
        eq(mod.route(_FakeDB(mm, [], []), ["o1"], "jane@x.invalid")[:2], (None, "no_lead"))
        # shared mailbox, lead exists but nobody ever emailed them: not attached
        db = _FakeDB(mm, [_lead("L1", "o1")], [])
        eq(mod.route(db, ["o1", "o2"], "jane@x.invalid")[:2], (None, "no_lead"))
        # single org, two leads, none emailed: ambiguous, attached to nobody
        db = _FakeDB(mm, [_lead("L1", "o1"), _lead("L2", "o1")], [])
        eq(mod.route(db, ["o1"], "jane@x.invalid")[:2], (None, "ambiguous"))


@scenario("inbound-email", "shared mailbox: two tenants emailed the same address is ambiguous; subject beats recency")
def _():
    with _mailbox_stubs() as (mod, mm):
        leads = [_lead("LA", "orgA"), _lead("LB", "orgB")]
        emails = [("LA", _T0, "Spring Open House"), ("LB", _T0 + 2 * _DAY, "Summer Plan")]
        db = _FakeDB(mm, leads, emails)
        # no subject / unknown subject: never pick on recency alone
        eq(mod.route(db, ["orgA", "orgB"], "jane@x.invalid")[:2], (None, "ambiguous"))
        eq(mod.route(db, ["orgA", "orgB"], "jane@x.invalid", "Re: Something else")[:2], (None, "ambiguous"))
        # reply to A's older email: subject wins over B's newer send
        lead, outcome, _d = mod.route(db, ["orgA", "orgB"], "jane@x.invalid", "RE: Spring Open House")
        eq((lead.id, outcome), ("LA", "matched"))
        lead, outcome, _d = mod.route(db, ["orgA", "orgB"], "jane@x.invalid", "AW: summer plan")
        eq((lead.id, outcome), ("LB", "matched"))
        # same subject in both tenants stays ambiguous
        both = _FakeDB(mm, leads, [("LA", _T0, "Hello"), ("LB", _T0 + _DAY, "Hello")])
        eq(mod.route(both, ["orgA", "orgB"], "jane@x.invalid", "Re: Hello")[:2], (None, "ambiguous"))


# ── runner ───────────────────────────────────────────────────────────────────

def run(md_path=None):
    rows, failed = [], 0
    for i, (group, name, fn) in enumerate(SCENARIOS, 1):
        try:
            fn()
            status, note = "PASS", ""
        except Exception as exc:                                 # noqa: BLE001
            status, note, failed = "FAIL", "%s: %s" % (type(exc).__name__, exc), failed + 1
        rows.append((i, group, name, status, note))
        print("%3d  %-4s  [%s] %s%s" % (i, status, group, name, "  <- " + note if note else ""))
    total = len(rows)
    print("\nEXECUTED %d  PASS %d  FAIL %d" % (total, total - failed, failed))
    if md_path:
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# SCI readiness harness: executed results\n\n")
            f.write("Executed %d scenarios: **%d PASS, %d FAIL**. Stdlib only; real decision modules; "
                    "does not replace dependency-backed pytest or DB integration suites.\n\n"
                    % (total, total - failed, failed))
            f.write("| # | Group | Scenario | Result |\n|---|---|---|---|\n")
            for i, g, n, s, note in rows:
                f.write("| %d | %s | %s | %s%s |\n" % (i, g, n.replace("|", "/"), s, " " + note if note else ""))
    return failed


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--md") + 1] if "--md" in sys.argv else None
    sys.exit(1 if run(out) else 0)
