"""Static guarantees about the Demo Mode frontend.

WHY A PYTHON TEST FOR REACT CODE. This repo has no JS test runner, and adding
one to assert three things would be a larger change than the thing being
asserted. These checks read the source and the built bundle, which is enough to
prove the properties that actually matter here:

  * the banner and console decide from the BACKEND probe, never from the
    hostname, a query parameter or a build-time flag
  * neither renders any control affordance when the probe says production
  * the shipped bundle contains no hardcoded demo scenario data - if the demo
    screens show three scenarios, three scenarios came from the server

The last one is the one worth having. A React file that hardcoded the scenario
list would look identical in a screenshot and would drift from the engine the
day somebody adds a fourth.

    python scripts/smoke_demo_frontend.py
"""
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
FAILURES = []


def check(label, ok, detail=""):
    print("  %-4s %s%s" % ("PASS" if ok else "FAIL", label,
                           ("\n         -> " + str(detail)[:300]) if not ok else ""))
    if not ok:
        FAILURES.append(label)


def read(*p):
    path = os.path.join(ROOT, *p)
    if not os.path.exists(path):
        return None
    return open(path, encoding="utf-8", errors="replace").read()


def s1_source():
    print("\n[1] The environment answer comes from the backend")
    api = read("frontend", "src", "api", "demo.js")
    check("the demo api module exists", api is not None)
    check("IT ASKS THE BACKEND FOR THE ENVIRONMENT",
          "/demo/environment" in api)
    check("it does NOT read the hostname",
          "location.hostname" not in api and "window.location.host" not in api)
    check("it does NOT read a query parameter",
          "URLSearchParams" not in api and "searchParams" not in api)
    check("it does NOT read a build-time flag",
          "import.meta.env.VITE_DEMO" not in api and "NODE_ENV" not in api)
    check("A FAILED PROBE FALLS BACK TO PRODUCTION, NOT DEMO",
          "demo_mode: false" in api and ".catch(" in api)

    banner = read("frontend", "src", "components", "DemoBanner.jsx")
    check("the banner exists", banner is not None)
    check("the banner asks the same probe", "fetchEnvironment" in banner)
    check("THE BANNER RENDERS NOTHING WHEN NOT IN DEMO",
          "if (!envInfo || !envInfo.demo_mode) return null" in banner)
    check("the banner says what will not happen",
          "No real calls" in banner and "charges" in banner)
    check("the banner is responsive rather than a fixed desktop strip",
          "narrow" in banner and "innerWidth" in banner)


def s2_console():
    print("\n[2] The console holds no business logic")
    con = read("frontend", "src", "pages", "DemoConsole.jsx")
    check("the console exists", con is not None)
    check("IT REFUSES TO RENDER CONTROLS OUTSIDE THE DEMO",
          "if (!envInfo.demo_mode)" in con)
    check("it refuses anyone who is not a platform owner",
          "god_admin" in con and "super_admin" in con)

    # The scenario list, the step order and the narration must all arrive from
    # the server. A hardcoded copy would look right and be wrong.
    for banned in ("customer_reactivation", "speed_to_lead", "brand_sales",
                   "Cedar Hollow", "Brightwater", "Taffiney"):
        check("the console does not hardcode %r" % banned, banned not in con)

    check("it renders whatever steps the server returned",
          "s.steps.map" in con)
    check("and whatever narration the server returned",
          "next_step.narration" in con)
    check("THE NEXT ACTION IS THE HEADLINE OF THE PAGE",
          "WHAT TO SHOW NEXT" in con)
    check("it shows position in the story",
          "of {active.total_steps}" in con or "total_steps}" in con)
    check("it surfaces the firewall state to the operator",
          "firewall" in con and "default-deny" in con)

    css = read("frontend", "src", "pages", "DemoConsole.css")
    check("the console stylesheet is scoped", css and ".dc-scope" in css)
    check("EVERY RULE IS SCOPED - none can leak into the tenant or sales sheets",
          css and not re.search(r"(?m)^\s*(body|html|\*|button|input)\s*\{", css))
    check("it has a phone breakpoint", css and "max-width: 720px" in css)


def s3_wiring():
    print("\n[3] Wiring")
    app = read("frontend", "src", "App.jsx")
    check("the banner is mounted above every shell",
          "<DemoBanner />" in app)
    check("the console has a route", 'path="/demo"' in app)
    check("THE DEMO ROUTE STILL REQUIRES A LOGIN",
          re.search(r'path="/demo"[\s\S]{0,220}isAuthenticated\(\)', app) is not None)


def s4_bundle():
    print("\n[4] The shipped bundle")
    dist = os.path.join(ROOT, "frontend", "dist", "assets")
    if not os.path.isdir(dist):
        print("       (no build present - run deploy.ps1 to produce one; skipped)")
        return
    js = [f for f in os.listdir(dist) if f.endswith(".js")]
    if not js:
        print("       (no bundle found - skipped)")
        return

    # deploy.ps1 runs the smoke suites BEFORE it builds the frontend, so on the
    # deploy that first introduces a component the bundle on disk is older than
    # the source and cannot contain it. Asserting against a stale bundle would
    # fail for a reason that has nothing to do with correctness, so this
    # section skips itself and says so rather than lying in either direction.
    newest_src = max(
        os.path.getmtime(os.path.join(ROOT, "frontend", "src", *p))
        for p in (("pages", "DemoConsole.jsx"), ("components", "DemoBanner.jsx"),
                  ("api", "demo.js"), ("App.jsx",)))
    newest_bundle = max(os.path.getmtime(os.path.join(dist, f)) for f in js)
    if newest_bundle < newest_src:
        print("       (bundle predates the demo source - it will be rebuilt by")
        print("        this deploy's build step; skipped)")
        return

    blob = ""
    for f in js:
        blob += open(os.path.join(dist, f), encoding="utf-8", errors="replace").read()

    check("the bundle contains the environment probe", "/demo/environment" in blob)

    # Scenario NAMES, not keys. The key `brand_sales` is a substring of the
    # long-standing `brand_sales_org_id` field that legitimately appears all
    # over the sales workspace, so matching on the key reports a collision as a
    # violation. The display names are unique to the scenario engine.
    for banned in ("Lead Reactivation", "Speed to Lead",
                   "EvoSys Pro B2B Sales Cycle"):
        check("THE BUNDLE DOES NOT HARDCODE THE SCENARIO %r" % banned,
              banned not in blob)
    for banned in ("Cedar Hollow", "Brightwater", "Marguerite", "Taffiney:"):
        check("the bundle carries no seeded demo content (%r)" % banned,
              banned not in blob)
    check("NO DEMO PASSWORD IS IN THE SHIPPED BUNDLE",
          "EvoDemo2026" not in blob)


def main():
    s1_source()
    s2_console()
    s3_wiring()
    s4_bundle()
    print()
    if FAILURES:
        print("  %d FAILURE(S): %s" % (len(FAILURES), ", ".join(FAILURES[:8])))
        sys.exit(1)
    print("  ALL DEMO FRONTEND CHECKS PASSED")


if __name__ == "__main__":
    main()
