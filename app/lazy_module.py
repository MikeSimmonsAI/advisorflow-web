"""
Heavy third-party modules, imported on first use instead of at startup.

WHY THIS EXISTS — MEASURED, NOT GUESSED.

The backend restarted out of memory on a 512 MB Render instance. The
diagnosis found a ~306 MB structural baseline: the process was already
two-thirds full before it served a single request, so the ~4.3 MB/hour of
ordinary request-driven retention had only ~200 MB of runway and reached the
ceiling in about two days. The `advisorflow-voice` service - same image, same
imports, almost no traffic - sat at 306 MB flat for 62 hours, which is what
proved the baseline is import-time and not traffic.

Importing `app.main` and measuring RSS per library gives the ranking:

    fastapi                     +34.3 MB    every request needs it
    sqlalchemy                  +10.7 MB    every request needs it
    stripe                      +45.9 MB    billing only
    pandas                      +44.8 MB    CSV/XLSX import only
    googleapiclient.discovery   +20.0 MB    Google Calendar sync only
    openai                       +8.2 MB    AI paths only

The bottom three cost ~111 MB and are touched by a handful of endpoints. The
web service's actual traffic is ~53% `/notifications/`, ~27% `/ping` and the
rest dashboard reads - none of which import a spreadsheet, charge a card or
talk to Google Calendar. Every worker paid for all three anyway, and so did
all three cron services and the voice service.

WHY A PROXY AND NOT `import x` INSIDE EACH FUNCTION.

Moving the import into the function body is the usual advice and it is fine
when there are one or two call sites. `stripe` has thirteen across two
modules, including `except stripe.error.SignatureVerificationError` in the
webhook handler. Deleting the module-level import and missing one site turns
into a NameError on a live billing request - the code path least likely to be
exercised by a test run and most expensive to get wrong. The proxy keeps every
existing `stripe.Customer.create(...)` call site working exactly as written,
so there is no site to miss.

WHAT IT DOES NOT DO.

It does not make anything faster, it does not avoid the import - the first
billing request still pays stripe's ~1.3s import - and it is not a general
tool for deferring project imports. It is for large third-party packages with
narrow call sites. `openai` is deliberately NOT wrapped: it is 8.2 MB against
nine importing modules, and the AI conversation loop touches it on a schedule,
so it would be loaded on most processes anyway.

ROLLBACK: replace `stripe = lazy("stripe")` with `import stripe` in the two
billing modules and `pd = lazy("pandas")` with `import pandas as pd` in
import_service. Nothing else changes.
"""

from __future__ import annotations

import importlib
from typing import Any


class LazyModule:
    """Stands in for a module until something actually reads an attribute.

    Attribute reads and writes both force the real import first, so
    `stripe.api_key = key` followed by `stripe.Customer.create(...)` behaves
    exactly as it would with a plain `import stripe` - the assignment lands on
    the real module, not on a placeholder that a later import would discard.
    (That discard is the specific trap in `importlib.util.LazyLoader`, whose
    `_LazyModule` replaces its own `__dict__` wholesale on first read; it is
    why this is a small explicit class rather than the stdlib helper.)
    """

    __slots__ = ("_lazy_name", "_lazy_mod")

    def __init__(self, name: str) -> None:
        object.__setattr__(self, "_lazy_name", name)
        object.__setattr__(self, "_lazy_mod", None)

    # ── loading ──────────────────────────────────────────────────────────
    def _lazy_load(self):
        mod = object.__getattribute__(self, "_lazy_mod")
        if mod is None:
            mod = importlib.import_module(object.__getattribute__(self, "_lazy_name"))
            object.__setattr__(self, "_lazy_mod", mod)
        return mod

    @property
    def lazy_loaded(self) -> bool:
        """True once the real module has been imported. For tests."""
        return object.__getattribute__(self, "_lazy_mod") is not None

    # ── forwarding ───────────────────────────────────────────────────────
    def __getattr__(self, item: str) -> Any:
        # Only reached when normal lookup fails, i.e. for anything that is not
        # a slot or a method of this class.
        return getattr(self._lazy_load(), item)

    def __setattr__(self, item: str, value: Any) -> None:
        setattr(self._lazy_load(), item, value)

    def __delattr__(self, item: str) -> None:
        delattr(self._lazy_load(), item)

    def __dir__(self):
        return dir(self._lazy_load())

    def __repr__(self) -> str:
        name = object.__getattribute__(self, "_lazy_name")
        state = "loaded" if self.lazy_loaded else "not loaded"
        return f"<LazyModule {name!r} ({state})>"


def lazy(name: str) -> LazyModule:
    """Return a stand-in for `name` that imports it on first attribute access."""
    return LazyModule(name)
