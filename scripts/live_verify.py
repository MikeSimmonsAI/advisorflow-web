"""LIVE VERIFICATION — the real product, over HTTP, as four different people.

Not the test suite. The test suite proves the units behave; this drives the
RUNNING SERVICE the way a browser does — real login, real tokens, real
sessions, real database — and asserts the things a screen would show.

Every control on every new screen posts to one of the calls below, so a green
run here is the API half of the button audit. The other half is clicking them.
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8011"
PASSWORD = "LiveVerify!2026"

PASS, FAIL = [], []


def call(method, path, token=None, body=None, expect=200):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            code, payload = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, payload = e.code, e.read().decode()
    except Exception as e:
        return None, "TRANSPORT %s" % e
    try:
        parsed = json.loads(payload) if payload else None
    except ValueError:
        parsed = payload
    return code, parsed


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(("  PASS  " if ok else "  FAIL  ") + label + (("  — " + str(detail)[:220]) if detail and not ok else ""))


def login(email):
    body = "username=%s&password=%s" % (urllib.parse.quote(email),
                                        urllib.parse.quote(PASSWORD))
    req = urllib.request.Request(BASE + "/auth/login", data=body.encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())["access_token"]


import urllib.parse  # noqa: E402  (used by login above)

print("\n=== SIGN IN ===")
god = login("owner@example.invalid")
rep = login("j.pike@example.invalid")
nobody = login("nobody@example.invalid")
christina = login("c.torres@example.invalid")
check("four identities sign in against the running service", True)

# ── the brands ──────────────────────────────────────────────────────────────
code, envs = call("GET", "/god/demo-suite/environments", god)
check("GET /god/demo-suite/environments", code == 200, envs)
evo = next(e for e in envs["environments"] if e["platform_slug"] == "evosyspro")
boost = next(e for e in envs["environments"] if e["platform_slug"] == "bookaboost")
EVO, BOOST = evo["platform_id"], boost["platform_id"]
check("EvoSys Pro environment is READY with a seeded world",
      evo["ready"] and evo["counts"]["leads"] >= 10, evo)
check("BookaBoost has no environment and says so, rather than looking broken",
      boost["exists"] is False)

print("\n=== 1. GOD MODE / MANAGE ACCESS ===")
code, directory = call("GET", "/god/access/directory", god)
check("directory returns NAMED brands, orgs, roles and templates",
      code == 200 and directory["platforms"] and directory["templates"], code)

code, users = call("GET", "/god/users?scope=all&limit=200", god)
CHRISTINA = next(u["id"] for u in users["users"]
                 if u["email"] == "c.torres@example.invalid")
REP = next(u["id"] for u in users["users"] if u["email"] == "j.pike@example.invalid")

code, fp = call("GET", "/god/access/users/" + CHRISTINA, god)
check("footprint loads and reads as sentences",
      code == 200 and fp["summary"] and fp["workspaces"], code)
check("her wrong placement is visible by NAME",
      any(w["name"] == "Northgate Memorial" for w in fp["workspaces"]), fp["workspaces"])

PLAN = {"operations": [
    {"op": "move_membership",
     "from": {"scope_type": "customer_org",
              "scope_id": fp["workspaces"][0]["scope_id"]},
     "to": {"scope_type": "platform", "scope_id": BOOST, "role": "brand_executive"}},
    {"op": "apply_template", "template": "sales_manager",
     "scope_id": directory["sales_organizations"][0]["id"]},
    {"op": "grant_demo", "platform_id": EVO},
    {"op": "assign_training", "path_key": "running_a_demo"},
    {"op": "set_home_organization", "organization_id": None},
]}

code, prev = call("POST", "/god/access/users/%s/preview" % CHRISTINA, god, PLAN)
check("PREVIEW works", code == 200, prev)
check("preview names what is added, removed and PRESERVED",
      bool(prev["adding"]) and bool(prev["removing"]) and
      any("login identity" in p.lower() for p in prev["preserved"]), prev)

code, after = call("GET", "/god/access/users/" + CHRISTINA, god)
check("preview wrote NOTHING",
      after["identity"]["home_organization_name"] == "Northgate Memorial")

code, applied = call("POST", "/god/access/users/%s/apply" % CHRISTINA, god, PLAN)
check("CONFIRM applies the plan", code == 200, applied)

code, fp2 = call("GET", "/god/access/users/" + CHRISTINA, god)
check("same identity, same login after the correction",
      fp2["identity"]["user_id"] == CHRISTINA and
      fp2["identity"]["email"] == "c.torres@example.invalid")
check("she is now BookaBoost Executive",
      any(b["name"] == "BookaBoost" and b["is_active"]
          for b in fp2["brand_contexts"]), fp2["brand_contexts"])
check("and a Sales Manager in the back office",
      any(b["role_label"] == "Sales Manager" and b["is_active"]
          for b in fp2["back_office"]), fp2["back_office"])
check("the wrong workspace access is revoked, not deleted",
      any(w["state"] == "revoked" for w in fp2["workspaces"]), fp2["workspaces"])
check("Demo Suite access granted for EvoSys Pro",
      any(d["platform_name"] == "EvoSys Pro" for d in fp2["demo"]["brands"]))
check("training assigned", any(t["path_key"] == "running_a_demo"
                               for t in fp2["training"]))

code, audit = call("GET", "/god/access/users/%s/audit" % CHRISTINA, god)
check("the change is in the audit trail with a before and an after",
      code == 200 and any(e["action"] == "access.manage" and e["before"] and e["after"]
                          for e in audit["entries"]), audit)
blob = json.dumps(audit).lower()
check("no secrets in the audit trail",
      not any(w in blob for w in ("password", "token", "secret", "hash")))

# she can still sign in — the identity was never recreated
try:
    login("c.torres@example.invalid")
    check("she can still sign in with the same password", True)
except Exception as e:
    check("she can still sign in with the same password", False, e)

print("\n=== 2. NOBODY BELOW GOD REACHES IT ===")
for who, tok in (("salesperson", rep), ("wrongly-provisioned admin", christina),
                 ("no-access user", nobody)):
    code, _ = call("GET", "/god/access/users/" + CHRISTINA, tok)
    check("%s is refused Manage Access (403)" % who, code == 403, code)
    code, _ = call("POST", "/god/access/users/%s/apply" % CHRISTINA, tok,
                   {"operations": [{"op": "set_platform_role", "role": "super_admin"}]})
    check("%s cannot apply an access change (403)" % who, code == 403, code)
    code, _ = call("GET", "/god/demo-suite/environments", tok)
    check("%s cannot administer demo environments (403)" % who, code == 403, code)
    code, _ = call("POST", "/god/training/assign", tok,
                   {"user_id": CHRISTINA, "path_key": "running_a_demo"})
    check("%s cannot assign training (403)" % who, code == 403, code)

code, r = call("POST", "/god/access/users/%s/apply" % REP, god,
               {"operations": [{"op": "set_platform_role", "role": "god_admin"}]})
check("god_admin cannot be granted from the provisioning surface (400)",
      code == 400 and "root authority" in str(r).lower(), r)

print("\n=== 3. THE DEMO SUITE, AS A SALESPERSON ===")
code, me = call("GET", "/demo-suite/me", rep)
check("the presenter is offered exactly the brand they hold",
      code == 200 and [b["platform_name"] for b in me["brands"]] == ["EvoSys Pro"], me)
check("and is told the environment is ready", me["brands"][0]["environment_ready"])

code, _ = call("GET", "/demo-suite/%s/world" % BOOST, rep)
check("a brand they do not hold is refused (403)", code == 403, code)
code, _ = call("GET", "/demo-suite/%s/world" % EVO, nobody)
check("somebody with no entitlement is refused (403)", code == 403, code)

code, world = call("GET", "/demo-suite/%s/world" % EVO, rep)
check("the world loads", code == 200, world)
p = world["panels"]
for key in ("priority", "leads", "conversation", "calendar", "pipeline",
            "team", "revenue", "customers", "launch"):
    check("panel renders: %s" % key, bool(p.get(key)), key)
check("the priority queue is populated and ordered", len(p["priority"]["queue"]) > 5)
check("a do-not-contact record is present and excluded",
      any(l["blocked"] for l in p["leads"]["leads"]) and bool(p["priority"]["excluded"]))
check("the pipeline has deals in several stages",
      sum(1 for c in p["pipeline"]["columns"] if c["count"]) >= 4)
check("revenue is computed and its weights are stated",
      p["revenue"]["open_pipeline"] > 0 and bool(p["revenue"]["weights"]))

print("\n=== 4. EVERY DEMO ACTION ===")
def act(action, params, label, scenario=None, step=None):
    code, out = call("POST", "/demo-suite/%s/action" % EVO, rep,
                     {"action": action, "params": params,
                      "scenario": scenario, "step": step})
    check("action works: %s" % label, code == 200 and bool(out.get("narration")),
          out)
    return out

before = call("GET", "/demo-suite/%s/world" % EVO, rep)[1]["panels"]
act("qualify_all", {}, "qualify & prioritise the whole book")
o = act("qualify_lead", {"target": "terrence"}, "qualify one record")
o = act("send_sms", {"target": "terrence"}, "send the first response")
check("the send is SIMULATED and says so", o.get("simulated") is True, o)
act("simulate_reply", {"target": "terrence"}, "the family replies")
o = act("ai_follow_up", {"target": "terrence"}, "AI drafts a follow-up")
check("the draft waits for approval rather than sending",
      o.get("requires_approval") is True, o)
act("approve_draft", {"target": "terrence"}, "the advisor approves it")
o = act("book_appointment", {"target": "terrence"}, "book the consultation")
check("the appointment has a real time", bool(o.get("when")), o)
act("move_stage", {"target": "cordova", "to_stage": "closing"}, "move a deal")
act("complete_task", {"target": "halverson"}, "complete a next action")

code, out = call("POST", "/demo-suite/%s/action" % EVO, rep,
                 {"action": "make_it_up", "params": {}})
check("an unknown action is REFUSED, not silently accepted", code == 400, code)

code, out = call("POST", "/demo-suite/%s/action" % EVO, rep,
                 {"action": "send_sms", "params": {"target": "not-a-demo-record"}})
check("a demo action cannot be pointed at anything outside the demo (404)",
      code == 404, code)

after = call("GET", "/demo-suite/%s/world" % EVO, rep)[1]["panels"]
check("the world changed as a result of the actions",
      after["calendar"]["upcoming"] > before["calendar"]["upcoming"] and
      any(d["company"] == "Cordova Dental Partners"
          for c in after["pipeline"]["columns"] if c["stage"] == "closing"
          for d in c["deals"]))

print("\n=== 5. A COMPLETE GUIDED SCENARIO ===")
code, scen = call("GET", "/demo-suite/%s/scenarios/lead_to_appointment" % EVO, rep)
check("the scenario loads with its running order", code == 200 and scen["steps"], code)
missing = [s["key"] for s in scen["steps"]
           if not (s["what_we_show"] and s["where_to_click"] and s["what_happens"]
                   and s["why_it_matters"] and s["prospect_should_notice"]
                   and s["presenter_says"])]
check("every step carries what to show, say and why", not missing, missing)

for s in scen["steps"]:
    if s["action"]:
        params = dict(s["action"]); action = params.pop("action")
        code, out = call("POST", "/demo-suite/%s/action" % EVO, rep,
                         {"action": action, "params": params,
                          "scenario": "lead_to_appointment", "step": s["key"]})
        ok = code == 200
    else:
        code, out = call("POST",
                         "/demo-suite/%s/scenarios/lead_to_appointment/step" % EVO,
                         rep, {"step": s["key"]})
        ok = code == 200
    check("scenario step runs: %s" % s["key"], ok, out)

code, scen2 = call("GET", "/demo-suite/%s/scenarios/lead_to_appointment" % EVO, rep)
check("the scenario completes", scen2["session"]["status"] == "complete", scen2["session"])

print("\n=== 6. CONTEXTUAL HELP AND THE COACH ===")
code, topics = call("GET", "/demo-suite/help", rep)
check("help topics load", code == 200 and len(topics["topics"]) >= 10, code)
bad = [t["key"] for t in topics["topics"]
       if not (t["what_it_is"] and t["what_it_does"] and t["why_customer_cares"]
               and t["what_happens_next"] and t["presenter_says"])]
check("every topic answers the same five questions", not bad, bad)
code, one = call("GET", "/demo-suite/help/ai_prioritisation", rep)
check("one topic opens", code == 200 and one["presenter_says"], code)
code, _ = call("GET", "/demo-suite/help/nope", rep)
check("an unknown topic 404s rather than rendering blank", code == 404, code)

print("\n=== 7. RESET ===")
code, _ = call("POST", "/demo-suite/%s/scenarios/lead_to_appointment/reset" % EVO, rep)
check("a presenter can reset their own run-through", code == 200, code)
code, _ = call("POST", "/demo-suite/%s/rebuild" % EVO, rep)
check("a presenter WITHOUT demo_admin cannot rebuild the shared world (403)",
      code == 403, code)
code, built = call("POST", "/god/demo-suite/environments/%s/build" % EVO, god)
check("the owner rebuilds it", code == 200, built)
code, w = call("GET", "/demo-suite/%s/world" % EVO, rep)
board = {c["stage"]: [d["company"] for d in c["deals"]] for c in w["panels"]["pipeline"]["columns"]}
check("rebuild restored canonical state",
      "Cordova Dental Partners" in board.get("discovery", []) and
      "Cordova Dental Partners" not in board.get("closing", []), board)
check("and the presenter's booked demo appointment is gone",
      w["panels"]["calendar"]["upcoming"] == 1, w["panels"]["calendar"]["upcoming"])

print("\n=== 8. TRAINING ===")
code, mine = call("GET", "/training/me", rep)
check("the learner sees what was assigned to them",
      code == 200 and any(a["key"] == "running_a_demo" for a in mine["assigned"]), mine)
code, path = call("GET", "/training/paths/running_a_demo", rep)
check("the path opens with its steps", code == 200 and path["steps"], code)
code, _ = call("GET", "/training/paths/executive_basics", rep)
check("an unassigned path is not workable (404)", code == 404, code)

practice = [s for s in path["steps"] if s["requires_practice"]]
check("the presenter path has practice steps", bool(practice))
code, r = call("POST", "/training/paths/running_a_demo/complete", rep,
               {"step": practice[0]["key"]})
check("a practice step CANNOT be completed by reading (409)", code == 409, r)

for s in path["steps"]:
    if s["requires_practice"]:
        code, sc = call("GET", "/demo-suite/%s/scenarios/%s"
                        % (EVO, s["practice_scenario"]), rep)
        for st in sc["steps"]:
            call("POST", "/demo-suite/%s/scenarios/%s/step" % (EVO, s["practice_scenario"]),
                 rep, {"step": st["key"]})
    code, done = call("POST", "/training/paths/running_a_demo/complete", rep,
                      {"step": s["key"]})
    if code != 200:
        check("training step completes: %s" % s["key"], False, done)
check("the whole Demo Presenter path completes",
      done["assignment"]["status"] == "complete", done["assignment"])

code, ready = call("GET", "/god/training/readiness", god)
check("the owner can see who is ready",
      code == 200 and ready["complete"] >= 1, ready)

print("\n=== 9. THE SAME AUTHORITY TRUTH, WEB AND MOBILE ===")
code, ctx = call("GET", "/auth/my-contexts", rep)
check("/auth/my-contexts carries the demo entitlement",
      code == 200 and ctx["has_demo_access"] and
      [d["platform_name"] for d in ctx["demo_contexts"]] == ["EvoSys Pro"], ctx)
check("and the training state", ctx["training"]["assigned"] >= 1)
check("demo entitlement is NOT a context — it does not put anybody anywhere",
      all(c.get("type") != "demo" for c in ctx["contexts"]))
code, ctx2 = call("GET", "/auth/my-contexts", nobody)
check("somebody with no entitlement gets an empty demo list, not an error",
      code == 200 and ctx2["has_demo_access"] is False, ctx2)

print("\n=== 10. THE BOUNDARY ===")
code, orgs = call("GET", "/god/orgs", god)
names = [o["name"] for o in orgs["orgs"]]
check("the demonstration workspace is NOT in the customer list",
      "Northgate Memorial" in names and "Lakemont Family Services" not in names, names)
code, orgs2 = call("GET", "/god/orgs?include_demo=true", god)
check("...and is reachable for the one screen that wants it",
      "Lakemont Family Services" in [o["name"] for o in orgs2["orgs"]])
code, ev = call("GET", "/god/demo-suite/events", god)
check("the demo keeps its own trail, and it survived the rebuild",
      code == 200 and any(e["action"] == "send_sms" for e in ev["events"]),
      [e["action"] for e in (ev.get("events") or [])][:12])

print("\n" + "=" * 70)
print("PASSED %d   FAILED %d" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
print("=" * 70)
sys.exit(1 if FAIL else 0)
