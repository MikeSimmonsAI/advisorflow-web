"""Dependency-free tests: a resolved handoff stays resolved; repeat submits
do not overwrite the first accept/resolve.

Run: python3 -m unittest tests.test_ai_workforce_handoff_finality

Evidence level: BEHAVIOURAL on the real handoff.accept/resolve functions with
stubbed sqlalchemy/model imports. NOT proven: HTTP 409 mapping, DB.
"""
import importlib.util
import os
import sys
import types
import unittest
from types import SimpleNamespace as NS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _load():
    stubs = {}
    for n in ("sqlalchemy", "sqlalchemy.orm", "app", "app.models",
              "app.models.models", "app.models.workforce_models",
              "app.services", "app.services.workforce",
              "app.services.workforce.constants"):
        stubs[n] = types.ModuleType(n)
    stubs["sqlalchemy.orm"].Session = object
    for n in ("BookingLink", "Lead", "User"):
        setattr(stubs["app.models.models"], n, object)
    for n in ("AIEmployee", "AIHandoff", "AIWorkItem"):
        setattr(stubs["app.models.workforce_models"], n, object)
    stubs["app.services.workforce"].constants = \
        stubs["app.services.workforce.constants"]
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "handoff_under_test",
            os.path.join(ROOT, "app", "services", "workforce", "handoff.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


H = _load()


class DB:
    def flush(self):
        pass


def row(status="open", **kw):
    d = dict(organization_id="o1", status=status, accepted_by=None,
             accepted_at=None, resolved_at=None, resolution_note=None)
    d.update(kw)
    return NS(**d)


A = NS(id="a", organization_id="o1", role="admin")
B = NS(id="b", organization_id="o1", role="admin")
OTHER = NS(id="x", organization_id="o2", role="admin")


class HandoffFinality(unittest.TestCase):
    def test_accept_open(self):
        r = H.accept(DB(), row(), A)
        self.assertEqual((r.status, r.accepted_by), ("accepted", "a"))

    def test_accept_resolved_does_not_reopen(self):
        r = row("resolved", resolved_at="t0")
        with self.assertRaises(H.HandoffStateError):
            H.accept(DB(), r, A)
        self.assertEqual(r.status, "resolved")
        self.assertIsNone(r.accepted_by)

    def test_second_person_cannot_take_over(self):
        r = H.accept(DB(), row(), A)
        with self.assertRaises(H.HandoffStateError):
            H.accept(DB(), r, B)
        self.assertEqual(r.accepted_by, "a")

    def test_repeat_accept_same_person_is_noop(self):
        r = H.accept(DB(), row(), A)
        first = r.accepted_at
        H.accept(DB(), r, A)
        self.assertEqual(r.accepted_at, first)

    def test_repeat_resolve_keeps_first_note_and_time(self):
        r = H.resolve(DB(), row(), A, note="first")
        t = r.resolved_at
        H.resolve(DB(), r, B, note="second")
        self.assertEqual((r.resolution_note, r.resolved_at), ("first", t))
        self.assertEqual(r.accepted_by, "a")

    def test_cross_org_still_refused(self):
        with self.assertRaises(PermissionError):
            H.accept(DB(), row(), OTHER)
        with self.assertRaises(PermissionError):
            H.resolve(DB(), row(), OTHER)


if __name__ == "__main__":
    unittest.main()
