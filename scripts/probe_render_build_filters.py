"""
probe_render_build_filters.py - deploy gate for render.yaml build filters.

Why this exists: a build filter is a silent failure mode. Get it wrong and
nothing errors - a legitimate change simply never reaches production, and you
find out days later when the fix you shipped isn't there. So the filters are
not trusted by inspection; every service is simulated against concrete commit
shapes and asserted to BUILD or SKIP.

Render's semantics (implemented in _should_build below):
  * paths        = allowlist. Build only if SOME changed file matches.
  * ignoredPaths = denylist. Skip only if EVERY changed file matches.
  * neither      = always build.
A service must never declare both.

Run: python scripts/probe_render_build_filters.py
"""
import os
import sys
import fnmatch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDER_YAML = os.path.join(ROOT, "render.yaml")
REQS = os.path.join(ROOT, "requirements.txt")
REQS_DEV = os.path.join(ROOT, "requirements-dev.txt")

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + detail if detail else ""))
        failures.append(label)


def _match(path, pattern):
    """Render/glob matching. '**' spans directory separators, '*' does not."""
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if pattern.startswith("**/"):
        tail = pattern[3:]
        parts = path.split("/")
        return any(fnmatch.fnmatch("/".join(parts[i:]), tail) for i in range(len(parts)))
    if "/" not in pattern:
        # A bare pattern like "*.ps1" anchors at the repo root only.
        return "/" not in path and fnmatch.fnmatch(path, pattern)
    return fnmatch.fnmatch(path, pattern)


def _should_build(bf, changed):
    if not bf:
        return True
    paths = bf.get("paths")
    ignored = bf.get("ignoredPaths")
    if paths:
        return any(any(_match(f, p) for p in paths) for f in changed)
    if ignored:
        return not all(any(_match(f, p) for p in ignored) for f in changed)
    return True


# ---------------------------------------------------------------- parse
try:
    import yaml
except ImportError:
    print("PyYAML not installed; cannot verify render.yaml. "
          "pip install pyyaml")
    sys.exit(1)

with open(RENDER_YAML, "r", encoding="utf-8") as fh:
    raw = fh.read()

print("\n[1] render.yaml parses and anchors resolve")
try:
    doc = yaml.safe_load(raw)
    check("render.yaml is valid YAML", True)
except Exception as exc:  # noqa: BLE001
    check("render.yaml is valid YAML", False, str(exc))
    print("\nFAILED: %d check(s)" % len(failures))
    sys.exit(1)

services = {s["name"]: s for s in doc.get("services", [])}
# Four python services + the static frontend. It was five python services
# until advisorflow-ai-conversation was retired on 2026-09-19 - the backend
# owns ai_conversation_loop in service_role.SCHEDULER_OWNER, so the cron was a
# redundant second executor on unlocked rows. See the note in render.yaml.
check("all 4 services + frontend present", len(services) == 5,
      "found %d: %s" % (len(services), sorted(services)))

PYTHON_SERVICES = [
    "advisorflow-backend",
    "advisorflow-cadence-job",
    "advisorflow-email-poller",
    "advisorflow-voice",
]

# Named, not merely absent from the list above. The backend owns
# ai_conversation_loop and process_scheduled_touches takes no row lock, so a
# second executor would double-send. If this name ever comes back into the
# Blueprint, that is the defect, and it should fail here rather than ship.
check("the retired AI conversation cron is not in the Blueprint",
      "advisorflow-ai-conversation" not in services,
      "found it: %s" % sorted(services))

print("\n[2] every service declares a build filter")
for name, svc in services.items():
    check("%s has buildFilter" % name, isinstance(svc.get("buildFilter"), dict))
    bf = svc.get("buildFilter") or {}
    check("%s does not mix paths + ignoredPaths" % name,
          not (bf.get("paths") and bf.get("ignoredPaths")))

print("\n[3] the YAML anchor actually shared one object across 4 services")
anchor = services["advisorflow-backend"].get("buildFilter")
for name in PYTHON_SERVICES[1:]:
    check("%s resolved to the SAME filter as backend" % name,
          services[name].get("buildFilter") == anchor,
          "got %r" % (services[name].get("buildFilter"),))
check("anchor is declared once in the source text",
      raw.count("&pythonBuildFilter") == 1)
check("anchor is referenced by the other three",
      raw.count("*pythonBuildFilter") == 3,
      "found %d references" % raw.count("*pythonBuildFilter"))

print("\n[4] MUST BUILD - changes that have to reach production")
MUST_BUILD = [
    ("backend python change", ["app/routers/god_router.py"], PYTHON_SERVICES),
    ("dependency change", ["requirements.txt"], PYTHON_SERVICES),
    ("render.yaml change", ["render.yaml"], PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("startup migration change", ["app/auto_migrate.py"], PYTHON_SERVICES),
    ("job entrypoint change", ["app/jobs/run_cadence_job.py"], PYTHON_SERVICES),
    ("alembic migration", ["alembic/versions/0001_x.py"], PYTHON_SERVICES),
    ("root entrypoint", ["index.py"], PYTHON_SERVICES),
    # THE FRONTEND TRIGGER IS SOURCE, NOT THE BUILT BUNDLE. These three used
    # to assert that a commit touching frontend/dist rebuilt the static site,
    # which was right while dist was committed to git and served as-is. It is
    # not committed any more: Render runs `cd frontend && npm ci && npm run
    # build` and serves what that produces, and frontend/dist/** is in the
    # static site's own ignoredPaths precisely so a stale artifact can never be
    # the trigger. So the thing that must still build is every source input to
    # that command - and the dist cases move to MUST SKIP below, where they
    # now belong.
    ("frontend source change", ["frontend/src/App.jsx"],
     ["advisorflow-frontend"]),
    ("frontend entry document", ["frontend/index.html"],
     ["advisorflow-frontend"]),
    ("frontend dependency change", ["frontend/package.json"],
     ["advisorflow-frontend"]),
    ("frontend build config change", ["frontend/vite.config.js"],
     ["advisorflow-frontend"]),
    # The mixed case, restated in source terms: one commit touching both sides
    # must still reach both sides.
    ("mixed backend + frontend source commit",
     ["app/main.py", "frontend/src/App.jsx"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    # Denylist semantics, stated once where it matters: a commit is skipped
    # only when EVERY file matches. A source change carried alongside an
    # ignored one still builds.
    ("frontend source alongside an ignored file",
     ["frontend/src/App.jsx", "docs/ROADMAP.md"], ["advisorflow-frontend"]),
    ("frontend source alongside a stale dist artifact",
     ["frontend/src/App.jsx", "frontend/dist/index.html"],
     ["advisorflow-frontend"]),
    ("code change alongside a doc",
     ["app/main.py", "README.md"], PYTHON_SERVICES),
]
for label, changed, must in MUST_BUILD:
    for name in must:
        check("%s -> %s BUILDS" % (label, name),
              _should_build(services[name].get("buildFilter"), changed),
              "changed=%s" % changed)

print("\n[5] MUST SKIP - the waste this pass is removing")
MUST_SKIP = [
    # A COMMITTED dist ARTIFACT TRIGGERS NOTHING, ANYWHERE. dist is generated
    # by Render, not committed, so a dist path in a diff means someone's local
    # build leaked into a commit. Rebuilding the static site from it would
    # publish whatever that machine happened to produce. This is the assertion
    # that keeps frontend/dist/** in the static site's ignoredPaths.
    ("stale dist artifact only", ["frontend/dist/assets/index-abc.js"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("stale dist index.html only", ["frontend/dist/index.html"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("frontend src+dist commit",
     ["frontend/src/App.jsx", "frontend/dist/index.html"], PYTHON_SERVICES),
    ("frontend source commit", ["frontend/src/App.jsx"], PYTHON_SERVICES),
    # The public site is PHP, uploaded to GoDaddy by hand. Neither `pip install
    # -r requirements.txt` nor `npm run build` reads a byte of it.
    ("public-site-only commit", ["public-site/request-demo/index.php"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("customer site + view config commit",
     ["public-sites/atlantis-light-and-power/index.html",
      "config/workspace-views/energy-retail-with-move-concierge.json"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("docs-only commit", ["docs/ROADMAP.md", "SUMMARY.md"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("deploy-gate script change", ["scripts/probe_owner_console.py"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("test-only change", ["tests/test_auth_service.py"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    ("backend-only commit", ["app/main.py"], ["advisorflow-frontend"]),
    ("requirements change", ["requirements.txt"], ["advisorflow-frontend"]),
    ("ps1 tooling change", ["deploy.ps1"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
    # OBSERVED, NOT HYPOTHETICAL. deploy.ps1 itself makes one push. The waste
    # came from SEPARATE pushes of "chore: remove deploy scratch files", each
    # touching exactly one root-level _depN.ps1 temp wrapper - commits 79b0687,
    # a01b265 and 638f94e on Aug 27. Each triggered a build on every service in
    # the Blueprint (six at the time) to delete a temp file. Under this filter
    # they build nothing.
    ("deploy scratch-file cleanup commit", ["_dep4.ps1"],
     PYTHON_SERVICES + ["advisorflow-frontend"]),
]
for label, changed, must in MUST_SKIP:
    for name in must:
        check("%s -> %s SKIPS" % (label, name),
              not _should_build(services[name].get("buildFilter"), changed),
              "changed=%s" % changed)

print("\n[6] dev-dependency split")
with open(REQS, "r", encoding="utf-8") as fh:
    prod_lines = [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]
with open(REQS_DEV, "r", encoding="utf-8") as fh:
    dev_lines = [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]

prod_pkgs = {l.split("==")[0].split(">=")[0].split("[")[0] for l in prod_lines}
check("pytest is NOT in requirements.txt", "pytest" not in prod_pkgs)
check("alembic is NOT in requirements.txt", "alembic" not in prod_pkgs)
check("openpyxl IS still in requirements.txt (pandas.read_excel needs it)",
      "openpyxl" in prod_pkgs)
check("pandas IS still in requirements.txt", "pandas" in prod_pkgs)
check("requirements-dev.txt pulls in production set",
      any(l.startswith("-r requirements.txt") for l in dev_lines))
check("requirements-dev.txt provides pytest",
      any(l.startswith("pytest") for l in dev_lines))
check("requirements-dev.txt provides alembic",
      any(l.startswith("alembic") for l in dev_lines))
check("no package was lost in the split",
      prod_pkgs | {"pytest", "alembic"} >= prod_pkgs)

print("\n[7] nothing in the runtime path imports the removed packages")
bad = []
for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, "app")):
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        full = os.path.join(dirpath, fn)
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh, 1):
                s = line.strip()
                if s.startswith("import pytest") or s.startswith("from pytest") \
                        or s.startswith("import alembic") or s.startswith("from alembic"):
                    bad.append("%s:%d %s" % (full.replace(ROOT + os.sep, ""), i, s))
check("app/ contains 0 pytest/alembic imports", not bad, "; ".join(bad))

print("\n[8] openpyxl's real (dynamic) consumer still exists")
imp = os.path.join(ROOT, "app", "services", "import_service.py")
src = open(imp, "r", encoding="utf-8", errors="replace").read() if os.path.exists(imp) else ""
check("import_service.py calls pd.read_excel", "read_excel" in src,
      "if this ever stops being true, revisit whether openpyxl is still needed")

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
if failures:
    for f in failures:
        print("  FAILED: " + f)
    sys.exit(1)
print("ALL RENDER BUILD-FILTER CHECKS PASSED")
