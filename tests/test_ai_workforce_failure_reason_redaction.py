"""Dependency-free tests: a failure reason written to the customer-visible
work-item timeline is redacted (credentials, emails, phone numbers).

Run: python3 -m unittest tests.test_ai_workforce_failure_reason_redaction

Evidence level: BEHAVIOURAL on the real queue.record_failure with stubbed
sqlalchemy/model imports. NOT proven: DB persistence, HTTP rendering.
"""
import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace as NS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
WF = os.path.join(ROOT, "app", "services", "workforce")


def _spec(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(WF, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Event:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _load():
    names = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.exc", "app",
             "app.models", "app.models.workforce_models", "app.services",
             "app.services.workforce", "app.services.workforce.constants",
             "app.services.workforce.run_evidence")
    stubs = {n: types.ModuleType(n) for n in names}
    stubs["sqlalchemy"].or_ = lambda *a: None
    stubs["sqlalchemy.orm"].Session = object
    stubs["sqlalchemy.exc"].IntegrityError = Exception
    wm = stubs["app.models.workforce_models"]
    wm.AIEmployee = wm.AIWorkItem = object
    wm.AIWorkItemEvent = Event
    saved = {k: sys.modules.get(k) for k in names}
    sys.modules.update(stubs)
    try:
        C = _spec("wf_constants_t", "constants.py")
        RE = _spec("wf_run_evidence_t", "run_evidence.py")
        stubs["app.services.workforce"].constants = C
        stubs["app.services.workforce"].run_evidence = RE
        sys.modules["app.services.workforce.constants"] = C
        sys.modules["app.services.workforce.run_evidence"] = RE
        return _spec("wf_queue_t", "queue.py")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


Q = _load()


class DB:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


def item():
    return NS(id="w1", organization_id="o1", employee_id="e1", state="working",
              consecutive_failures=0, next_action_at=None, updated_at=None)


class FailureReasonRedaction(unittest.TestCase):
    def reason_for(self, text):
        db = DB()
        Q.record_failure(db, item(), text)
        return db.added[0].reason

    def test_credentials_emails_phones_removed(self):
        r = self.reason_for(
            "401 from provider api_key=sk_live_ABCDEFGH12345 for "
            "jane.doe@example.com phone +1 (555) 123-4567")
        for leaked in ("sk_live", "ABCDEFGH12345", "jane.doe@example.com",
                       "555) 123-4567"):
            self.assertNotIn(leaked, r)
        self.assertTrue(r.startswith("failure: "))

    def test_bearer_header_removed(self):
        r = self.reason_for("Authorization: Bearer abcdefghijklmnop1234")
        self.assertNotIn("abcdefghijklmnop1234", r)

    def test_empty_reason_not_blank_and_counts_failure(self):
        db = DB()
        it = item()
        Q.record_failure(db, it, "")
        self.assertEqual(db.added[0].reason, "failure: unspecified error")
        self.assertEqual(it.consecutive_failures, 1)

    def test_plain_reason_preserved(self):
        self.assertEqual(self.reason_for("provider timed out"),
                         "failure: provider timed out")

    def test_exhaustion_signal_unchanged(self):
        db, it = DB(), item()
        results = [Q.record_failure(db, it, "x") for _ in range(3)]
        self.assertEqual(results, [False, False, True])


if __name__ == "__main__":
    unittest.main()
