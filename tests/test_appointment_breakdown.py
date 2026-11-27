"""Performance > Appointments card: what became of the booked appointments (owner, Nov 27)."""
from datetime import datetime, timedelta

from app.services.programs import sci_analytics as S


class _L:
    def __init__(self, bookings, cancelled):
        self.attempts, self.bookings, self.cancelled = [], bookings, cancelled


def test_breakdown_splits_booked_and_keeps_cancels_out():
    now = datetime(2026, 11, 27, 7, 0, tzinfo=S.CT)
    at = datetime(2026, 11, 20)
    b = lambda res, day: {"lead_id": "x", "at": at, "result": res, "appt_ct": datetime(2026, 11, day, 10, tzinfo=S.CT)}  # noqa: E731
    L = _L([b("booked", 27), b("booked", 30), b("completed", 24), b("no_show", 25), b("booked", 21)],
           [{"lead_id": "y", "at": at}])
    w = {"start": at - timedelta(days=1), "end": at + timedelta(days=30)}
    orig = S.rows
    S.rows = lambda L_, m, w_, f=None: [r for r in L_.bookings if S._in(r["at"], w_)] if m == "appointments" else []
    try:
        out = S.appointment_breakdown(L, w, {}, now=now)
    finally:
        S.rows = orig
    assert out == {"upcoming": 2, "showed": 1, "no_show": 1, "no_result": 1, "cancelled": 1}
