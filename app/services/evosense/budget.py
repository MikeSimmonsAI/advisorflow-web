"""Atomic paid-data budget.

THE RACE THIS PREVENTS. Daily budget $1.00, six workers each about to spend
$0.20. A read-then-write check ("spent so far is $0.80, so there is room")
lets two of them both see $0.80 and both spend. So nothing here reads a
balance and decides: each counter is moved by ONE conditional statement

    UPDATE evosense_budget_counters
       SET spent_cents = spent_cents + :c
     WHERE id = :id AND spent_cents + :c <= :limit

which the database applies atomically. If it touches zero rows, the money is
not there, and the paid call is refused BEFORE it is made. When several
counters apply (org day, org month, strategy day, strategy month) they are
moved inside one savepoint, and any refusal rolls all of them back.

A reservation is released (refunded) if the provider call fails, so a
timeout never costs budget. It is charged only on a completed call.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models.evosense_models import EvoSenseBudgetCounter, EvoSenseCostEntry
from app.services.evosense import common as C

PAID_DATA_PAUSED = "PAID DATA PAUSED"
DAILY_REACHED = "DAILY BUDGET REACHED"
MONTHLY_REACHED = "MONTHLY BUDGET REACHED"


def _scopes(db, org_id: str, strategy) -> List[Tuple[str, str, Optional[int], str]]:
    ctl = C.controls(db, org_id)
    out = [("org", C.day_key(), ctl.org_daily_budget_cents, DAILY_REACHED),
           ("org", C.month_key(), ctl.org_monthly_budget_cents, MONTHLY_REACHED)]
    if strategy is not None:
        sid = "strategy:%s" % strategy.id
        out.append((sid, C.day_key(), strategy.daily_budget_cents, DAILY_REACHED))
        out.append((sid, C.month_key(), strategy.monthly_budget_cents, MONTHLY_REACHED))
    return out


def _counter(db, org_id, scope, period, limit):
    row = (db.query(EvoSenseBudgetCounter)
           .filter(EvoSenseBudgetCounter.organization_id == org_id,
                   EvoSenseBudgetCounter.scope == scope,
                   EvoSenseBudgetCounter.period == period).first())
    if row is None:
        sp = db.begin_nested()
        try:
            row = EvoSenseBudgetCounter(organization_id=org_id, scope=scope, period=period,
                                        spent_cents=0, limit_cents=limit)
            db.add(row)
            db.flush()
            sp.commit()
        except IntegrityError:
            # another worker created it first - use theirs
            sp.rollback()
            row = (db.query(EvoSenseBudgetCounter)
                   .filter(EvoSenseBudgetCounter.organization_id == org_id,
                           EvoSenseBudgetCounter.scope == scope,
                           EvoSenseBudgetCounter.period == period).first())
    return row


def remaining(db, org_id: str, strategy) -> Optional[int]:
    """Smallest remaining allowance across the applicable limits (cents), or
    None when nothing limits spend. A READ for display and pre-checks only —
    the reservation itself never relies on it."""
    best = None
    for scope, period, limit, _ in _scopes(db, org_id, strategy):
        if limit is None:
            continue
        # populate_existing: the counter is moved by raw SQL, so an ORM copy
        # already in this session would otherwise show a stale balance.
        row = (db.query(EvoSenseBudgetCounter).populate_existing()
               .filter(EvoSenseBudgetCounter.organization_id == org_id,
                       EvoSenseBudgetCounter.scope == scope,
                       EvoSenseBudgetCounter.period == period).first())
        left = limit - (row.spent_cents if row else 0)
        best = left if best is None else min(best, left)
    return best


def reserve(db, org_id: str, strategy, cents: int, *, provider_key: str, connector_kind: str,
            capability: str, operation: str, property_id: str = None, owner_id: str = None,
            decision_id: str = None, is_test: bool = False
            ) -> Tuple[bool, Optional[str], Optional[EvoSenseCostEntry]]:
    """Atomically claim `cents`. Returns (ok, refusal_reason, ledger_entry)."""
    ctl = C.controls(db, org_id)
    if ctl.paused_paid_data or ctl.paused_all:
        return False, PAID_DATA_PAUSED, None
    if cents <= 0:
        entry = EvoSenseCostEntry(organization_id=org_id, provider_key=provider_key,
                                  connector_kind=connector_kind, capability=capability,
                                  operation=operation, strategy_id=getattr(strategy, "id", None),
                                  property_id=property_id, owner_id=owner_id,
                                  decision_id=decision_id, quantity=1, unit_cost_cents=0,
                                  total_cents=0, status="reserved", period_day=C.day_key(),
                                  period_month=C.month_key(), is_test=is_test)
        db.add(entry)
        db.flush()
        return True, None, entry

    sp = db.begin_nested()
    try:
        for scope, period, limit, label in _scopes(db, org_id, strategy):
            row = _counter(db, org_id, scope, period, limit)
            if limit is None:
                db.execute(text("UPDATE evosense_budget_counters SET spent_cents = spent_cents + :c "
                                "WHERE id = :id"), {"c": cents, "id": row.id})
                continue
            res = db.execute(text(
                "UPDATE evosense_budget_counters SET spent_cents = spent_cents + :c, "
                "limit_cents = :lim WHERE id = :id AND spent_cents + :c <= :lim"),
                {"c": cents, "id": row.id, "lim": limit})
            if res.rowcount == 0:
                sp.rollback()
                return False, label, None
        entry = EvoSenseCostEntry(organization_id=org_id, provider_key=provider_key,
                                  connector_kind=connector_kind, capability=capability,
                                  operation=operation, strategy_id=getattr(strategy, "id", None),
                                  property_id=property_id, owner_id=owner_id,
                                  decision_id=decision_id, quantity=1, unit_cost_cents=cents,
                                  total_cents=cents, status="reserved", period_day=C.day_key(),
                                  period_month=C.month_key(), is_test=is_test)
        db.add(entry)
        db.flush()
        sp.commit()
    except Exception:
        if sp.is_active:
            sp.rollback()
        raise
    return True, None, entry


def charge(db, entry: EvoSenseCostEntry, success: bool = True) -> None:
    entry.status = "charged"
    entry.success = success


def refund(db, entry: EvoSenseCostEntry, strategy=None) -> None:
    """Release a reservation for a call that did not complete."""
    if entry.total_cents:
        scopes = ["org"] + (["strategy:%s" % entry.strategy_id] if entry.strategy_id else [])
        for scope, period in [(s, p) for s in scopes for p in (entry.period_day, entry.period_month)]:
            db.execute(text("UPDATE evosense_budget_counters SET spent_cents = spent_cents - :c "
                            "WHERE organization_id = :o AND scope = :s AND period = :p"),
                       {"c": entry.total_cents, "o": entry.organization_id, "s": scope,
                        "p": period})
    entry.status = "failed_refunded"
    entry.success = False


def spent(db, org_id: str, period_key: str, strategy_id: str = None) -> int:
    from sqlalchemy import func
    q = db.query(func.coalesce(func.sum(EvoSenseCostEntry.total_cents), 0)).filter(
        EvoSenseCostEntry.organization_id == org_id,
        EvoSenseCostEntry.status == "charged")
    if period_key.startswith("d:"):
        q = q.filter(EvoSenseCostEntry.period_day == period_key)
    else:
        q = q.filter(EvoSenseCostEntry.period_month == period_key)
    if strategy_id:
        q = q.filter(EvoSenseCostEntry.strategy_id == strategy_id)
    return int(q.scalar() or 0)
