"""Dependency-free tests for workforce booking idempotency and scope.

Run: python3 -m unittest tests.test_ai_workforce_booking_idempotency

Evidence level: BEHAVIOURAL on the real booking.book_slot / reschedule with
stubbed sqlalchemy/model imports and a fake session. NOT proven: real DB,
tenant_scheduling availability, calendar push.
"""
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class _Col:
    def __getattr__(self, _n):
        return _Col()

    def __call__(self, *a, **k):
        return _Col()

    def __eq__(self, o):
        return True

    __hash__ = object.__hash__


def _load():
    stubs = {n: types.ModuleType(n) for n in (
        "sqlalchemy", "sqlalchemy.orm", "app", "app.models",
        "app.models.models", "app.services", "app.services.workforce")}
    stubs["sqlalchemy.orm"].Session = object
    for n in ("BookingLink", "Lead", "Organization", "User"):
        setattr(stubs["app.models.models"], n, _Col())
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "booking_under_test",
            os.path.join(ROOT, "app", "services", "workforce", "booking.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.added = []

    def query(self, *a):
        return _Q(self.rows)

    def add(self, o):
        self.added.append(o)


class BookingIdempotency(unittest.TestCase):
    def setUp(self):
        self.m = _load()
        self.now = datetime(2026, 10, 7, 12, 0)
        self.emp = NS(organization_id="org1", config=None, created_by=None)
        self.later = (self.now + timedelta(days=1)).isoformat() + "Z"

    def test_retry_returns_existing_booking_without_availability_read(self):
        existing = NS(id="b1", user_id="u1", booked_time=self.now)
        db = _DB([existing])
        self.m.available_slots = lambda *a, **k: self.fail("re-read ran")
        lead = NS(id="l1", organization_id="org1", status="booked")
        out = self.m.book_slot(db, employee=self.emp, lead=lead,
                               starts_at=self.later, now=self.now)
        self.assertTrue(out["already_booked"])
        self.assertEqual(out["booking_id"], "b1")
        self.assertEqual(db.added, [])

    def test_cross_tenant_lead_is_refused_before_any_write(self):
        db = _DB([NS(id="b1", user_id="u1", booked_time=self.now)])
        lead = NS(id="l1", organization_id="OTHER", status="new")
        with self.assertRaises(ValueError):
            self.m.book_slot(db, employee=self.emp, lead=lead,
                             starts_at=self.later, now=self.now)
        self.assertEqual(lead.status, "new")
        self.assertEqual(db.added, [])

    def test_reschedule_refuses_cancelled_booking_and_keeps_status(self):
        b = NS(id="b1", lead_id="l1", user_id="u1", status="cancelled",
               booked_time=self.now, confirmation_sent=True)
        db = _DB([b])
        with self.assertRaises(ValueError):
            self.m.reschedule(db, employee=self.emp,
                              lead=NS(id="l1"), booking_link_id="b1",
                              starts_at=self.later, now=self.now)
        self.assertEqual(b.status, "cancelled")
        self.assertEqual(b.booked_time, self.now)


if __name__ == "__main__":
    unittest.main()
