"""
probe_prod_deps_sufficient.py - prove requirements.txt alone still runs the app.

A grep for "import pytest" is not proof. pandas loads openpyxl dynamically
inside read_excel and never names it in an import statement - that is exactly
how openpyxl looked removable when it is not. So instead of trusting a scan,
this gate makes the removed packages genuinely unavailable (a meta_path finder
that raises ImportError for them, which catches dynamic __import__ too) and
then boots the real FastAPI app and exercises the Excel import path.

Positive control included: with openpyxl blocked the same Excel path MUST fail.
If it passed, the test would be proving nothing.

Run: python scripts/probe_prod_deps_sufficient.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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


class Blocker:
    """Meta-path finder that makes named top-level packages unimportable."""

    def __init__(self, names):
        self.names = set(names)

    def find_module(self, fullname, path=None):
        return self if fullname.split(".")[0] in self.names else None

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError(
                "blocked by probe_prod_deps_sufficient: %s is not in "
                "requirements.txt and must not be needed at runtime" % fullname)
        return None

    def load_module(self, fullname):
        raise ImportError("blocked: " + fullname)


def _purge(names):
    for mod in list(sys.modules):
        if mod.split(".")[0] in names:
            del sys.modules[mod]


REMOVED = {"pytest", "alembic", "_pytest"}

print("\n[1] the removed packages really are blocked (control on the control)")
_purge(REMOVED)
blocker = Blocker(REMOVED)
sys.meta_path.insert(0, blocker)
for name in ("pytest", "alembic"):
    try:
        __import__(name)
        check("%s is blocked" % name, False, "it imported anyway")
    except ImportError:
        check("%s is blocked" % name, True)

print("\n[2] the production app boots with pytest + alembic unavailable")
# Throwaway values generated per run. They are never persisted, never valid
# anywhere, and never touch production - the point is only to get past the
# app's own startup validation so that an ImportError is the ONLY thing that
# can fail this section.
import secrets as _secrets
from cryptography.fernet import Fernet as _Fernet

os.environ.setdefault("DATABASE_URL", "sqlite:///./advisorflow.db")
os.environ.setdefault("JWT_SECRET", _secrets.token_hex(32))
os.environ.setdefault("ENCRYPTION_KEY", _Fernet.generate_key().decode())
try:
    import app.main as appmain
    check("import app.main succeeds", True)
    check("FastAPI app object exists", getattr(appmain, "app", None) is not None)
    routes = len(getattr(appmain.app, "routes", []))
    check("app registered routes (%d)" % routes, routes > 50,
          "only %d routes - the app did not fully load" % routes)
except ImportError as exc:
    check("import app.main succeeds", False, str(exc))
except Exception as exc:  # noqa: BLE001
    check("import app.main succeeds", False,
          "%s: %s" % (type(exc).__name__, exc))

print("\n[3] every cron entrypoint imports with them unavailable")
for job in ("run_cadence_job", "run_email_poller", "run_ai_conversation_job"):
    path = os.path.join(ROOT, "app", "jobs", job + ".py")
    if not os.path.exists(path):
        check("app/jobs/%s.py exists" % job, False, "missing")
        continue
    try:
        __import__("app.jobs." + job)
        check("app.jobs.%s imports" % job, True)
    except ImportError as exc:
        check("app.jobs.%s imports" % job, False, str(exc))
    except Exception as exc:  # noqa: BLE001
        # A job that runs work at import time can fail for DB reasons; only an
        # ImportError would indicate a missing package, which is what we test.
        check("app.jobs.%s imports (non-import error tolerated)" % job, True,
              "%s: %s" % (type(exc).__name__, exc))

sys.meta_path.remove(blocker)

print("\n[4] openpyxl IS required - Excel lead import works with it present")
xlsx_path = os.path.join(ROOT, ".probe_leads.xlsx")
made = False
try:
    import pandas as pd
    pd.DataFrame({"first_name": ["Probe"], "last_name": ["Row"],
                  "phone": ["+15555550100"]}).to_excel(xlsx_path, index=False)
    made = True
    df = pd.read_excel(xlsx_path, sheet_name=0, dtype=str)
    check("pd.read_excel round-trips a .xlsx", len(df) == 1,
          "got %d rows" % len(df))
except Exception as exc:  # noqa: BLE001
    check("pd.read_excel round-trips a .xlsx", False,
          "%s: %s" % (type(exc).__name__, exc))

print("\n[5] POSITIVE CONTROL - blocking openpyxl must BREAK that same path")
_purge({"openpyxl"})
ob = Blocker({"openpyxl"})
sys.meta_path.insert(0, ob)
try:
    import pandas as pd  # already imported; read_excel resolves openpyxl lazily
    pd.read_excel(xlsx_path, sheet_name=0, dtype=str)
    check("Excel import fails without openpyxl", False,
          "it SUCCEEDED - this gate cannot prove openpyxl is needed; "
          "investigate before trusting section 4")
except Exception as exc:  # noqa: BLE001
    check("Excel import fails without openpyxl", True,
          "%s (expected)" % type(exc).__name__)
finally:
    sys.meta_path.remove(ob)
    if made and os.path.exists(xlsx_path):
        os.remove(xlsx_path)

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
if failures:
    for f in failures:
        print("  FAILED: " + f)
    sys.exit(1)
print("PRODUCTION DEPENDENCY SET IS SUFFICIENT")
