"""Dependency-free tests: an AI employee cannot claim human/platform provenance
for memory it writes via the `memory.remember` tool (it would otherwise appear
in the unfenced trusted_memory block as "A person on the customer's team
recorded ...").

Run: python3 -m unittest tests.test_ai_workforce_memory_provenance

Evidence level: BEHAVIOURAL on the real tool_impls.memory_remember handler and
memory.model_source / memory._render with stubbed imports. NOT proven: DB
persistence, the full run loop.
"""
import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace as NS
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WF = os.path.join(ROOT, "app", "services", "workforce")


def _spec(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(WF, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load():
    names = ["sqlalchemy", "sqlalchemy.orm", "app", "app.models",
             "app.models.models", "app.models.workforce_models", "app.services",
             "app.services.workforce"]
    sibs = ["booking", "constants", "handoff", "knowledge", "memory", "outbound",
            "performance", "policy", "queue", "tools"]
    allnames = names + ["app.services.workforce." + s for s in sibs]
    saved = {k: sys.modules.get(k) for k in allnames}
    for n in names:
        sys.modules[n] = types.ModuleType(n)
    sys.modules["sqlalchemy.orm"].Session = object
    wm = sys.modules["app.models.workforce_models"]
    wm.AIEmployee = wm.AIEmployeeMemory = object
    mm = sys.modules["app.models.models"]
    for n in ("BookingLink", "CRMContact", "EmailMessage", "Lead", "Message",
              "Reply", "SuppressionEntry", "SuppressionSource"):
        setattr(mm, n, object)
    pkg = sys.modules["app.services.workforce"]
    try:
        for s in ("constants", "memory"):
            mod = _spec("wf_%s_t" % s, s + ".py")
            sys.modules["app.services.workforce." + s] = mod
            setattr(pkg, s, mod)
        for s in ("booking", "handoff", "knowledge", "outbound", "performance",
                  "policy", "queue"):
            mod = mock.MagicMock()
            sys.modules["app.services.workforce." + s] = mod
            setattr(pkg, s, mod)
        tools = types.ModuleType("app.services.workforce.tools")
        tools.ToolRefusal = Exception
        tools.register = lambda key: (lambda f: f)
        sys.modules["app.services.workforce.tools"] = tools
        pkg.tools = tools
        return (_spec("wf_tool_impls_t", "tool_impls.py"),
                sys.modules["app.services.workforce.memory"])
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


T, M = _load()


class Q:
    def filter(self, *a):
        return self

    def first(self):
        return None


class DB:
    def __init__(self):
        self.added = []

    def query(self, *a):
        return Q()

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


class Row:
    """Stands in for AIEmployeeMemory; class-level attrs allow ORM-style
    column comparisons in the (stubbed) query filter."""
    organization_id = employee_id = scope = scope_id = key = None
    updated_at = NS(desc=lambda: None)

    def __init__(self, **kw):
        self.__dict__.update(kw)


class MemoryProvenance(unittest.TestCase):
    def _remember(self, source):
        db = DB()
        ctx = NS(db=db, employee=NS(id="e1", organization_id="o1"),
                 work_item=NS(subject_id="lead-1"))
        with mock.patch.object(M, "AIEmployeeMemory", Row):
            out = T.memory_remember(ctx, {"key": "burial_pref",
                                          "value": "cremation",
                                          "source": source}, None)
        return out, db.added[0]

    def test_model_cannot_claim_human_or_platform_provenance(self):
        for claimed in (M.SOURCE_HUMAN, M.SOURCE_PLATFORM, "anything"):
            out, row = self._remember(claimed)
            self.assertEqual(out["source"], M.SOURCE_EMPLOYEE, claimed)
            self.assertEqual(row.source, M.SOURCE_EMPLOYEE)
            self.assertNotIn("customer's team", M._render(row))
            self.assertNotIn("customer's own records", M._render(row))

    def test_legitimate_model_sources_preserved(self):
        for ok in (M.SOURCE_CONTACT, M.SOURCE_EMPLOYEE):
            self.assertEqual(self._remember(ok)[0]["source"], ok)

    def test_missing_source_defaults_to_employee(self):
        self.assertEqual(self._remember(None)[0]["source"], M.SOURCE_EMPLOYEE)

    def test_code_paths_can_still_write_human_source(self):
        db = DB()
        with mock.patch.object(M, "AIEmployeeMemory", Row):
            row = M.remember(db, NS(id="e1", organization_id="o1"), "k", "v",
                             source=M.SOURCE_HUMAN)
        self.assertEqual(row.source, M.SOURCE_HUMAN)


if __name__ == "__main__":
    unittest.main()
