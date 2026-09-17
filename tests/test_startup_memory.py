"""The startup memory baseline, asserted as a property rather than a hope.

WHY THIS FILE EXISTS.

The backend restarted out of memory on a 512 MB Render instance. The diagnosis
found the process was already ~306 MB before it served a request, and that
`advisorflow-voice` - same image, nearly no traffic - sat at the same figure
flat for 62 hours. So the baseline was import-time, and three libraries
accounted for most of the removable part of it:

    stripe                      ~46 MB      billing only
    pandas                      ~45 MB      spreadsheet import only
    googleapiclient.discovery   ~20 MB      Google Calendar sync only

None of them is touched by the traffic the web service actually serves. They
are now behind `app.lazy_module` (stripe, pandas) and a function-level import
(googleapiclient). Measured effect on `import app.main`: 272.7 MB -> 188.5 MB,
2,684 modules -> 1,728.

WHAT THIS GUARDS AGAINST. Not the megabytes - RSS is not stable enough to
assert on across machines. It guards the thing that actually regresses: someone
adding `import pandas as pd` to the top of a service module because it is
convenient, and quietly handing 45 MB back to every process on the platform
including three cron jobs that will never read a spreadsheet. The failure mode
is invisible until an instance restarts at 3am, which is exactly the kind of
thing a test should be holding.

IF ONE OF THESE FAILS: find the new top-level import with the message's help,
and either move it inside the function that uses it or wrap it with
`app.lazy_module.lazy`. Do not delete the assertion.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# Libraries that must not be imported merely by starting the app. The comment
# on each is the reason it is worth the indirection.
DEFERRED = {
    "stripe": "~46 MB; only /billing touches it",
    "pandas": "~45 MB; only the CSV/XLSX importer touches it",
    "numpy": "arrives with pandas",
    "googleapiclient.discovery": "~20 MB; only Google Calendar sync touches it",
}


def _import_app_main_in_subprocess():
    """Import app.main in a clean interpreter and report what it loaded.

    A subprocess is required: by the time this test runs, the rest of the suite
    has already imported half the codebase into this process, so `sys.modules`
    here says nothing about what starting the app costs.
    """
    script = textwrap.dedent(
        """
        import json, sys
        import app.main  # noqa: F401
        json.dump({"modules": sorted(sys.modules)}, sys.stdout)
        """
    )
    # Inherit the environment rather than building one: the interpreter's own
    # site-packages location, its virtualenv and the platform's certificate
    # paths all live there, and a hand-built env turns this into a permanent
    # skip - a test that never runs and never says so.
    import os
    env = dict(os.environ)
    env.update({
        "JWT_SECRET": "0123456789abcdef0123456789abcdef0123456789abcdef",
        "DATABASE_URL": "sqlite://",
        "SKIP_STARTUP_MIGRATIONS": "1",
        "PYTHONPATH": str(REPO),
    })
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        # Deliberately a failure, not a skip. A skip here would mean the guard
        # silently stops guarding the moment anything about the import breaks -
        # which is precisely when it is needed.
        raise AssertionError(
            "could not import app.main in a clean subprocess, so the startup "
            "memory baseline is unverified:\n%s" % proc.stderr.strip()[-3000:])
    import json
    return set(json.loads(proc.stdout)["modules"])


@pytest.fixture(scope="module")
def startup_modules():
    return _import_app_main_in_subprocess()


@pytest.mark.parametrize("module_name", sorted(DEFERRED))
def test_heavy_library_is_not_imported_at_startup(startup_modules, module_name):
    assert module_name not in startup_modules, (
        "`%s` is imported just by starting the app (%s).\n"
        "Something added a top-level import of it. Move that import inside the "
        "function that uses it, or wrap the module with app.lazy_module.lazy."
        % (module_name, DEFERRED[module_name])
    )


def test_the_app_still_imports_the_things_it_does_need(startup_modules):
    """A sanity check, so the test above cannot pass by app.main failing to load."""
    for required in ("fastapi", "sqlalchemy", "app.main", "app.routers.billing_router"):
        assert required in startup_modules, required


# ── the proxy itself ────────────────────────────────────────────────────────

def test_lazy_module_does_not_import_until_touched():
    from app.lazy_module import lazy
    proxy = lazy("statistics")
    assert proxy.lazy_loaded is False
    assert proxy.mean([1, 2, 3]) == 2
    assert proxy.lazy_loaded is True


def test_lazy_module_setattr_lands_on_the_real_module():
    """The trap that made this a hand-written class instead of importlib's.

    `stripe.api_key = key` is followed later by `stripe.Customer.create(...)`.
    If the assignment goes to a placeholder that the first read then replaces,
    the key is silently lost and every card charge fails in production with an
    authentication error. It has to land on the real module.
    """
    import sys
    from app.lazy_module import lazy
    proxy = lazy("statistics")
    proxy._advisorflow_probe = "set-before-load"
    import statistics
    assert statistics._advisorflow_probe == "set-before-load"
    assert proxy._advisorflow_probe == "set-before-load"
    del statistics._advisorflow_probe
    assert "statistics" in sys.modules


def test_lazy_module_repr_says_whether_it_loaded():
    from app.lazy_module import lazy
    proxy = lazy("statistics")
    assert "not loaded" in repr(proxy)
    proxy.mean
    assert "not loaded" not in repr(proxy)


def test_billing_router_still_exposes_stripe_by_name():
    """Every `stripe.X` call site in billing_router must still resolve.

    The proxy exists so that removing the top-level import could not turn one
    of thirteen call sites into a NameError on a live billing request.
    """
    from app.routers import billing_router
    assert hasattr(billing_router, "stripe")
    assert billing_router.stripe.__class__.__name__ == "LazyModule"


def test_import_service_still_exposes_pd_by_name():
    from app.services import import_service
    assert hasattr(import_service, "pd")
    assert import_service.pd.__class__.__name__ == "LazyModule"
