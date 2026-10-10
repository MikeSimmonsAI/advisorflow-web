"""Dependency-free proof: CSV buyer-import identity/idempotency and outreach
mutation safety. Executes the real wholesale_import_identity module and the real
`preflight` extracted from wholesale_disposition.py; the router/service guards
are asserted at source level (no fastapi/sqlalchemy needed).

Run: python3 scripts/wholesale_import_outreach_proof.py
"""
import ast
import importlib.util
import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print("FAIL:", name)


def src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


spec = importlib.util.spec_from_file_location(
    "ident", os.path.join(ROOT, "app/services/wholesale_import_identity.py"))
ident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ident)


def B(i, e=None, p=None):
    return SimpleNamespace(id=i, email=e, phone=p)


# ── identity normalisation ──
check("email case/space", ident.norm_email("  A@B.com ") == "a@b.com")
check("bad email None", ident.norm_email("nope") is None)
check("phone formats equal",
      ident.norm_phone("(214) 555-0100") == ident.norm_phone("+1 214-555-0100") == "2145550100")
check("short phone None", ident.norm_phone("555-0100") is None)

index = ident.build_index([B("b1", "a@x.com", "214-555-0100"), B("b2", "c@x.com", None)])
seen = {}
d = ident.decide("A@X.com", None, index, seen, 2)
check("retry same email skipped", d["action"] == ident.SKIP and d["buyer_id"] == "b1")
d = ident.decide(None, "(214)555-0100", index, seen, 3)
check("retry same phone skipped", d["action"] == ident.SKIP and d["buyer_id"] == "b1")
d = ident.decide("new@x.com", None, index, seen, 4)
check("new buyer created", d["action"] == ident.CREATE)
idx2 = ident.build_index([B("b1", "a@x.com"), B("b9", None, "9725550111")])
d = ident.decide("a@x.com", "972-555-0111", idx2, {}, 5)
check("email/phone to different buyers rejected",
      d["action"] == ident.REJECT and "ambiguous" in d["reason"])
idx3 = ident.build_index([B("b1", "a@x.com"), B("b2", "a@x.com")])
check("duplicate existing email rejected",
      ident.decide("a@x.com", None, idx3, {}, 6)["action"] == ident.REJECT)
seen = {}
check("first row creates", ident.decide("n@x.com", None, {}, seen, 2)["action"] == ident.CREATE)
ident.claim("n@x.com", None, seen, 2)
d = ident.decide("N@x.com", None, {}, seen, 3)
check("in-file dup skipped w/ row ref", d["action"] == ident.SKIP and "row 2" in d["reason"])
ident.claim("m@x.com", "2145550199", seen, 4)
d = ident.decide("n@x.com", "2145550199", {}, seen, 5)
check("in-file split identity rejected", d["action"] == ident.REJECT)
check("empty org index creates",
      ident.decide("a@x.com", None, ident.build_index([]), {}, 2)["action"] == ident.CREATE)
check("name-only never dedupes", ident.identity_keys(None, None) == [])


def run_import(rows, existing):
    idx, seen_, out = ident.build_index(existing), {}, {"c": 0, "s": 0, "r": 0}
    for n, (e, p) in enumerate(rows, start=2):
        v = ident.decide(e, p, idx, seen_, n)
        if v["action"] == ident.CREATE:
            existing.append(B("id%d" % len(existing), e, p))
            ident.claim(e, p, seen_, n)
            out["c"] += 1
        elif v["action"] == ident.SKIP:
            out["s"] += 1
        else:
            out["r"] += 1
    return out


rows = [("a@x.com", None), (None, "2145550100"), ("b@x.com", "2145550101")]
store = []
first = run_import(rows, store)
second = run_import(rows, store)
check("first import creates 3", first == {"c": 3, "s": 0, "r": 0})
check("retry creates 0, skips 3", second == {"c": 0, "s": 3, "r": 0} and len(store) == 3)

# ── router wiring (source level) ──
r = src("app/routers/wholesale_buyers_router.py")
check("import builds org-scoped index",
      "ident.build_index(db.query(WholesaleBuyer).filter(\n"
      "        WholesaleBuyer.organization_id == org_id)" in r)
check("import returns rejected + counts",
      '"rejected": rejected' in r and '"skipped_count"' in r and '"rejected_count"' in r)
check("select-buyer 404 on missing buyer",
      "buyer is None:\n        raise HTTPException(status_code=404" in r)
check("select-buyer refuses inactive/passed",
      "sel.check_select(" in r
      and "marked inactive, so they cannot" in src("app/services/wholesale_selection.py")
      and '("passed", "rejected")' in src("app/services/wholesale_selection.py"))
check("negative offer guarded in both routes",
      r.count("An offer amount cannot be negative.") == 2)
check("disposition reports unknown buyers", "unknown = [bid" in r and "was not found" in r)
s = src("app/services/wholesale_service.py")
check("queue dedupes buyer ids",
      "dict.fromkeys(buyer_ids)" in s and "existing[buyer.id] = row" in s)

# ── real preflight: resend cooldown ──
tree = ast.parse(src("app/services/wholesale_disposition.py"))
ns = {"datetime": datetime, "Optional": __import__("typing").Optional, "WholesaleDeal": object,
      "WholesaleBuyer": object, "WholesaleBuyerOutreach": object}
for node in tree.body:
    if ((isinstance(node, ast.Assign)
         and getattr(node.targets[0], "id", None) == "RESEND_COOLDOWN_SECONDS")
            or (isinstance(node, ast.ClassDef) and node.name == "SendRefused")
            or (isinstance(node, ast.FunctionDef) and node.name == "preflight")):
        exec(compile(ast.Module([node], []), "disp", "exec"), ns)
pre, Refused = ns["preflight"], ns["SendRefused"]
deal = SimpleNamespace(is_test=False)
buyer = SimpleNamespace(is_test=False, do_not_contact=False, do_not_contact_reason=None,
                        is_active=True, email="b@x.com", phone=None)


def code(row, force):
    try:
        pre(None, "org", deal, buyer, row, "email", force_resend=force)
    except Refused as e:
        return e.code
    except Exception:
        return "past_guards"      # reached the demo/deployment checks (needs app deps)
    return "ok"


now = datetime.utcnow()
check("resend seconds after send refused",
      code(SimpleNamespace(sent_at=now - timedelta(seconds=5), status="sent"), True) == "recently_sent")
check("resend after cooldown passes guard",
      code(SimpleNamespace(sent_at=now - timedelta(minutes=10), status="sent"), True) == "past_guards")
check("plain re-send still already_sent",
      code(SimpleNamespace(sent_at=now - timedelta(minutes=10), status="sent"), False) == "already_sent")
buyer.do_not_contact = True
check("DNC refused before cooldown/resend",
      code(SimpleNamespace(sent_at=now - timedelta(seconds=5), status="sent"), True) == "opted_out")

print("wholesale_import_outreach_proof: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
