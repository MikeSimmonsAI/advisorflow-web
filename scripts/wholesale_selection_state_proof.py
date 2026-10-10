"""Dependency-free proof: buyer-selection state consistency.

Executes the real pure module app/services/wholesale_selection.py, simulates the
select -> replace -> response path over it, and asserts router wiring at source
level (no fastapi/sqlalchemy needed).

Run: python3 scripts/wholesale_selection_state_proof.py
"""
import importlib.util
import itertools
import os
import sys

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


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


sel = load("app/services/wholesale_selection.py", "sel")
models = src("app/models/wholesale_models.py")

# every status the module writes must be in the existing vocabulary
vocab = ("not_contacted", "sent", "delivered", "opened", "replied", "interested",
         "needs_info", "offer_submitted", "selected", "passed", "rejected",
         "no_response")
check("vocab unchanged in models", all('"%s"' % v in models for v in vocab))
check("replaced statuses in vocabulary",
      sel.REPLACED_WITH_OFFER in vocab and sel.REPLACED_NO_OFFER in vocab)
check("replaced w/ offer", sel.replaced_status(250000) == "offer_submitted")
check("replaced w/o offer", sel.replaced_status(None) == "interested")
check("replaced zero offer", sel.replaced_status(0) == "interested")
check("replaced never 'selected'", sel.replaced_status(1) != "selected")

# ── check_select: exhaustive over status x flags ──
for status, is_sel, dnc, active in itertools.product(
        vocab + ("queued", "failed", "blocked"), (False, True), (False, True), (False, True)):
    d, code, label = sel.check_select(status, is_sel, dnc, active)
    tag = "select(%s,sel=%s,dnc=%s,act=%s)" % (status, is_sel, dnc, active)
    if dnc:
        check(tag + " DNC first", (d, code) == ("refuse", "buyer_opted_out"))
    elif not active:
        check(tag + " inactive", (d, code) == ("refuse", "buyer_inactive"))
    elif is_sel and status == "selected":
        check(tag + " idempotent", d == "noop" and code is None)
    elif status in ("passed", "rejected"):
        check(tag + " passed refused", (d, code) == ("refuse", "buyer_passed"))
    else:
        check(tag + " ok", d == "select")
    check(tag + " label iff refuse", bool(label) == (d == "refuse"))

# ── check_status_write: exhaustive ──
all_status = vocab + ("queued", "failed", "blocked", "requested_info", "accepted")
for cur, is_sel, new in itertools.product(all_status, (False, True), all_status):
    d, code, label = sel.check_status_write(cur, is_sel, new)
    tag = "write(%s,sel=%s -> %s)" % (cur, is_sel, new)
    if new == cur:
        check(tag + " noop", d == "noop")
    elif new == "selected":
        check(tag + " never via write", (d, code) == ("refuse", "select_via_select_buyer"))
    elif is_sel or cur == "selected":
        if new in ("passed", "rejected"):
            check(tag + " releases", d == "release")
        elif new in ("offer_submitted", "accepted"):
            check(tag + " offer keeps selected", d == "keep")
        else:
            check(tag + " locked", (d, code) == ("refuse", "selected_row_is_locked"))
    else:
        check(tag + " free", d == "allow")
check("None status noop", sel.check_status_write("sent", False, None)[0] == "noop")
check("every refusal code has a label",
      all(sel.REFUSALS[c] for c in sel.REFUSALS))

# ── simulate the router sequence over the pure rules ──
def make(i, status="sent", offer=None):
    return {"id": i, "status": status, "sel": False, "offer": offer}


def do_select(rows, target, deal):
    row = rows[target]
    d, code, _ = sel.check_select(row["status"], row["sel"], False, True)
    if d == "refuse":
        return code
    if d == "noop" and deal["buyer"] == target:
        return "already"
    for k, o in rows.items():
        if k != target and (o["sel"] or o["status"] == "selected"):
            was = o["status"]
            o["sel"] = False
            if was == "selected":
                o["status"] = sel.replaced_status(o["offer"])
            deal["events"].append(("replaced", k, was, o["status"]))
    row["sel"], row["status"], deal["buyer"] = True, "selected", target
    deal["events"].append(("selected", target))
    return "ok"


rows = {"a": make("a", "offer_submitted", 100), "b": make("b", "interested"),
        "c": make("c", "replied")}
deal = {"buyer": None, "events": []}
check("select a", do_select(rows, "a", deal) == "ok")
check("retry a idempotent", do_select(rows, "a", deal) == "already")
check("retry adds no event", deal["events"] == [("selected", "a")])
check("select b ok", do_select(rows, "b", deal) == "ok")
check("a no longer selected", not rows["a"]["sel"] and rows["a"]["status"] == "offer_submitted")
check("exactly one selected", sum(r["sel"] for r in rows.values()) == 1)
check("status/flag agree",
      all((r["status"] == "selected") == r["sel"] for r in rows.values()))
check("audit has replacement", ("replaced", "a", "selected", "offer_submitted") in deal["events"])
check("deal buyer is b", deal["buyer"] == "b")
rows["c"]["status"] = "passed"
check("passed c refused", do_select(rows, "c", deal) == "buyer_passed")
# drift repair: stale flag-less status "selected" is cleaned up too
rows["a"]["status"] = "selected"
do_select(rows, "b", {"buyer": None, "events": []})
check("stale 'selected' status repaired", rows["a"]["status"] == "offer_submitted")

# ── router source wiring ──
r = src("app/routers/wholesale_buyers_router.py")
check("router imports selection module", "wholesale_selection as sel" in r)
check("select uses check_select", "sel.check_select(" in r)
check("old flag-only update removed",
      '.update({"is_selected": False}' not in r)
check("replacement locks rows", ".with_for_update()" in r)
check("replacement audit event", '"buyer.selection_replaced"' in r)
check("release audit event", '"buyer.selection_released"' in r)
check("update_outreach gated", "_apply_status_write(db, org_id, user, row, payload.status)" in r)
check("record_response gated", "_apply_status_write(db, org_id, user, row, value)" in r)
check("no raw status assignment in update_outreach",
      "row.status = payload.status" not in r)
check("refusal detail carries code", '"%s [%s]"' in r)
check("offer cannot demote selected", "sel.offer_may_set_status(" in r)
# DNC/inactive refusal occurs in select before any mutation
i_sel = r.index("sel.check_select(")
i_mut = r.index("old.is_selected = False")
check("refusal precedes mutation", i_sel < i_mut)
check("tenant scoping on replaced query",
      "WholesaleBuyerOutreach.organization_id == org_id" in r[i_sel:i_mut])

print("wholesale_selection_state_proof: %d passed, %d failed" % (passed, failed))
sys.exit(1 if failed else 0)
