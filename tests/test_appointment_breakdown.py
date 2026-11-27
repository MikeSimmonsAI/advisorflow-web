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


def test_appointments_set_spans_count_by_booking_day():
    now_utc = datetime(2026, 11, 27, 13, 0)                       # 7:00 AM Central
    def bk(days_ago, res="booked", appt_day=30):
        return {"lead_id": "x", "at": now_utc - timedelta(days=days_ago), "result": res,
                "appt_ct": datetime(2026, 11 if appt_day <= 30 else 12, min(appt_day, 30), 10, tzinfo=S.CT)}
    L = _L([bk(0), bk(3), bk(20, "completed"), bk(40, "no_show"), bk(50, "booked", 1), bk(100, "completed")],
           [{"lead_id": "y", "at": now_utc - timedelta(days=2)}])
    orig = S.rows
    S.rows = lambda L_, m, w_, f=None: [r for r in L_.bookings if S._in(r["at"], w_)] if m == "appointments" else []
    try:
        t = {r["label"]: r for r in S.appointments_set(L, {}, now=now_utc)}
    finally:
        S.rows = orig
    assert [t[k]["set"] for k in ("Today", "7 days", "30 days", "45 days", "60 days", "All time")] == [1, 3, 4, 5, 6, 7]
    assert t["All time"]["kept"] == 6 and t["All time"]["cancelled"] == 1 and t["All time"]["showed"] == 2


def test_bookit_appointments_set_route_managers_only(monkeypatch):
    import pytest
    from fastapi import HTTPException, Response
    from app.routers import restland_bookit_router as BR

    class _W:
        def __init__(self, m): self.manager = m
    prog = type("P", (), {"organization_id": "org1"})()
    monkeypatch.setattr(S, "ledger", lambda db, org: _L([], []))
    monkeypatch.setattr(S, "rows", lambda L_, m, w_, f=None: [])
    monkeypatch.setattr(BR, "_ctx", lambda db, user: (prog, _W(True)))
    out = BR.appointments_set(Response(), db=None, user=None)
    assert [r["label"] for r in out["spans"]] == ["Today", "7 days", "30 days", "45 days", "60 days", "All time"]
    monkeypatch.setattr(BR, "_ctx", lambda db, user: (prog, _W(False)))
    with pytest.raises(HTTPException) as e:
        BR.appointments_set(Response(), db=None, user=None)
    assert e.value.status_code == 403
