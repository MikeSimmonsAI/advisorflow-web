"""THE TEAM BOARD - build list #19. Appointments set and run, and contracts
signed, by rep and by week (weeks start Monday, Central time).

Counts come straight from the appointment and offer records - nothing is
estimated. Who appears follows the same rule as the appointments
themselves: admins and full-access logins see everyone, managers and team
leads see their part of the tree, a field login sees only themselves.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.models import User
from app.models.wholesale_ops_models import WholesaleFieldAppointment as Appt, WholesaleFieldOffer as Offer
from app.services import wholesale_appointments as A

METRICS = ("booked", "ran", "no_show", "signed", "follow_up", "dead", "offers", "waiting")


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _utc(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=A.CENTRAL).astimezone(timezone.utc).replace(tzinfo=None)


def board(db: Session, org_id: str, user: User, weeks: int = 4, include_test: bool = False) -> Dict[str, Any]:
    weeks = max(1, min(int(weeks or 4), 12))
    this_week = _week_start(datetime.now(A.CENTRAL).date())
    starts = [this_week - timedelta(weeks=i) for i in range(weeks)][::-1]
    lo, hi = _utc(starts[0]), _utc(this_week + timedelta(days=7))
    visible = A.visible_user_ids(db, org_id, user)

    q = db.query(Appt).filter(Appt.organization_id == org_id, Appt.starts_at >= lo, Appt.starts_at < hi,
                              Appt.status != "cancelled")
    if not include_test:
        q = q.filter(Appt.is_test.is_(False))
    if visible is not None:
        q = q.filter(Appt.assigned_to_id.in_(list(visible)))
    appts = q.all()
    by_id = {a.id: a for a in appts}

    def wk(dt: datetime) -> str:
        local = dt.replace(tzinfo=timezone.utc).astimezone(A.CENTRAL).date()
        return _week_start(local).isoformat()

    cells: Dict[str, Dict[str, Dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: dict.fromkeys(METRICS, 0)))
    for a in appts:
        rep = a.assigned_to_id or "unassigned"
        c = cells[rep][wk(a.starts_at)]
        c["booked"] += 1
        if a.status == "completed":
            c["ran"] += 1
        if a.status == "no_show":
            c["no_show"] += 1
        if a.outcome in ("signed", "follow_up", "dead"):
            c[a.outcome] += 1
    if by_id:
        for o in db.query(Offer).filter(Offer.organization_id == org_id, Offer.appointment_id.in_(list(by_id))).all():
            a = by_id[o.appointment_id]
            c = cells[a.assigned_to_id or "unassigned"][wk(a.starts_at)]
            c["offers"] += 1
            if o.status == "pending":
                c["waiting"] += 1

    # Everyone on the team who could have had appointments shows, even with zeros.
    people = {r["user_id"]: r for r in A.reps(db, org_id, user)}
    for uid in list(people):
        cells[uid]  # noqa: B018 - creates the empty row
    rows: List[Dict[str, Any]] = []
    for uid, weeks_map in cells.items():
        p = people.get(uid)
        name = p["name"] if p else (A._name(db, uid) if uid != "unassigned" else "No rep yet")
        per_week = [{"week": s.isoformat(), **weeks_map.get(s.isoformat(), dict.fromkeys(METRICS, 0))} for s in starts]
        total = {m: sum(w[m] for w in per_week) for m in METRICS}
        total["signed_rate"] = round(100.0 * total["signed"] / total["ran"]) if total["ran"] else None
        rows.append({"user_id": uid if uid != "unassigned" else None, "name": name,
                     "role_label": (p or {}).get("role_label"), "weeks": per_week, "total": total})
    rows.sort(key=lambda r: (-r["total"]["signed"], -r["total"]["ran"], (r["name"] or "").lower()))
    team = {m: sum(r["total"][m] for r in rows) for m in METRICS}
    team["signed_rate"] = round(100.0 * team["signed"] / team["ran"]) if team["ran"] else None
    return {"weeks": [s.isoformat() for s in starts], "rows": rows, "team": team}
