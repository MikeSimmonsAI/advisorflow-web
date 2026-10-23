"""GOD MODE DASHBOARD - daily trends from real rows, real and test kept apart.

Read-only. Every number is a COUNT of rows in a date range; nothing is
estimated, smoothed or extrapolated.

REAL vs TEST. A row is TEST when its organization is a demonstration tenant
(`organizations.is_demo`) or its lead is a test/practice record
(`leads.is_test`). Test rows are counted separately so the owner can see
synthetic activity without it inflating the business numbers.

COMPARISONS. A "vs previous period" change is returned only when the previous
period has a non-zero count; a percentage against zero would be a made-up
number. Delivery rate is returned only with at least MIN_SENT_FOR_RATE sent
messages in the window.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

MIN_SENT_FOR_RATE = 20
LIVE_BOOKING = ("booked", "confirmed", "completed")


def _days(now: datetime, n: int) -> List[str]:
    start = (now - timedelta(days=n - 1)).date()
    return [(start + timedelta(days=i)).isoformat() for i in range(n)]


def _series(rows, labels: List[str]) -> Dict[str, List[int]]:
    """rows: (date, is_test, count) -> {"real": [...], "test": [...]}."""
    idx = {d: i for i, d in enumerate(labels)}
    real = [0] * len(labels)
    test = [0] * len(labels)
    for day, is_test, count in rows:
        key = str(day)[:10] if day is not None else None
        if key in idx:
            (test if is_test else real)[idx[key]] += int(count or 0)
    return {"real": real, "test": test}


def _change(current: int, previous: int) -> Optional[Dict[str, Any]]:
    if not previous:
        return None
    return {"previous": previous,
            "pct": round(100.0 * (current - previous) / previous)}


def trends(db: Session, *, days: int = 14,
           now: Optional[datetime] = None) -> Dict[str, Any]:
    from app.models.models import (BookingLink, Lead, Message, Organization,
                                   Reply)
    now = now or datetime.utcnow()
    days = max(7, min(int(days or 14), 60))
    labels = _days(now, days)
    start = datetime.fromisoformat(labels[0])
    prev_start = start - timedelta(days=days)

    is_test = or_(Organization.is_demo.is_(True), Lead.is_test.is_(True))
    test_flag = func.coalesce(is_test, False)

    def grouped(ts_col, extra=(), base=Lead):
        q = db.query(func.date(ts_col), test_flag, func.count())
        if base is not Lead:
            q = q.select_from(base).join(Lead, Lead.id == base.lead_id)
        q = q.outerjoin(Organization, Organization.id == Lead.organization_id)
        q = q.filter(ts_col >= start, *extra)
        return q.group_by(func.date(ts_col), test_flag).all()

    def total(ts_col, lo, hi, extra=(), base=Lead):
        q = db.query(func.count())
        if base is not Lead:
            q = q.select_from(base).join(Lead, Lead.id == base.lead_id)
        else:
            q = q.select_from(Lead)
        q = q.outerjoin(Organization, Organization.id == Lead.organization_id)
        q = q.filter(ts_col >= lo, ts_col < hi, *extra,
                     Lead.is_test.isnot(True), Organization.is_demo.isnot(True))
        return int(q.scalar() or 0)

    out: Dict[str, Any] = {"days": labels, "window_days": days,
                           "generated_at": now.isoformat() + "Z",
                           "note": ("Real activity excludes demonstration "
                                    "accounts and test/practice leads; those "
                                    "are counted under test.")}
    specs = {
        "leads": (Lead.created_at, (), Lead),
        "messages_sent": (Message.sent_at, (), Message),
        "messages_delivered": (Message.sent_at,
                               (Message.delivery_status == "delivered",),
                               Message),
        "replies": (Reply.received_at, (), Reply),
        "appointments_booked": (BookingLink.created_at,
                                (BookingLink.status.in_(LIVE_BOOKING),),
                                BookingLink),
    }
    for key, (col, extra, base) in specs.items():
        try:
            s = _series(grouped(col, extra, base), labels)
            cur = sum(s["real"])
            prev = total(col, prev_start, start, extra, base)
            out[key] = {"real": s["real"], "test": s["test"],
                        "real_total": cur, "test_total": sum(s["test"]),
                        "vs_previous": _change(cur, prev)}
        except Exception as exc:                             # noqa: BLE001
            db.rollback()
            out[key] = {"unavailable": True, "reason": type(exc).__name__}

    sent = (out.get("messages_sent") or {}).get("real_total") or 0
    delivered = (out.get("messages_delivered") or {}).get("real_total") or 0
    out["delivery_rate"] = (
        {"pct": round(100.0 * delivered / sent), "sent": sent,
         "delivered": delivered}
        if sent >= MIN_SENT_FOR_RATE else
        {"pct": None, "sent": sent, "delivered": delivered,
         "why": "Needs at least %d real messages sent in the window."
                % MIN_SENT_FOR_RATE})

    try:
        upcoming = (db.query(func.count(BookingLink.id))
                    .join(Lead, Lead.id == BookingLink.lead_id)
                    .outerjoin(Organization,
                               Organization.id == Lead.organization_id)
                    .filter(BookingLink.status.in_(("booked", "confirmed")),
                            BookingLink.booked_time >= now,
                            BookingLink.booked_time < now + timedelta(days=7),
                            Lead.is_test.isnot(True),
                            Organization.is_demo.isnot(True))
                    .scalar())
        out["appointments_next_7_days"] = int(upcoming or 0)
    except Exception:                                        # noqa: BLE001
        db.rollback()
        out["appointments_next_7_days"] = None

    out["ai_runs"] = _ai_runs(db, start)
    return out


def _ai_runs(db: Session, start: datetime) -> Dict[str, Any]:
    try:
        from app.models.workforce_models import AIEmployeeRun
    except Exception:                                        # noqa: BLE001
        return {"unavailable": True}
    try:
        rows = (db.query(AIEmployeeRun.mode, AIEmployeeRun.status,
                         func.count())
                .filter(AIEmployeeRun.started_at >= start)
                .group_by(AIEmployeeRun.mode, AIEmployeeRun.status).all())
    except Exception:                                        # noqa: BLE001
        db.rollback()
        return {"unavailable": True}
    res = {"practice": {"completed": 0, "failed": 0},
           "real": {"completed": 0, "failed": 0}}
    for mode, status, n in rows:
        bucket = res["practice" if mode in ("simulation", "shadow", None)
                     else "real"]
        if status == "completed":
            bucket["completed"] += int(n)
        elif status in ("failed", "error"):
            bucket["failed"] += int(n)
    return res
