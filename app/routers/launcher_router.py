"""Program Access launcher: per-account pins and the Needs Attention row.

PINS are a preference, never access. The launcher only shows a pinned card
while /auth/my-contexts still lists it for this login, so a removed grant
removes the card even if the pin row is still here.

NEEDS ATTENTION returns at most three real, actionable items, each read from
records this person may already open, and each pointing at the queue or record
where the work is done. Totals (pipeline, roster, compensation) stay in Back
Office. Every source is read independently; one failing source is skipped,
never the whole row, and never the launcher.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.launcher_models import LauncherPin
from app.models.models import User
from app.deps import get_current_user, get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/launcher", tags=["launcher"])

MAX_PINS = 24
MAX_ITEMS = 3


class PinsIn(BaseModel):
    keys: List[str]


def _clean_keys(keys: List[str]) -> List[str]:
    out: List[str] = []
    for k in keys or []:
        k = (k or "").strip()
        if not k or len(k) > 160 or k in out:
            continue
        out.append(k)
    if len(out) > MAX_PINS:
        raise HTTPException(status_code=400, detail="You can pin up to %d workspaces." % MAX_PINS)
    return out


@router.get("/pins")
def get_pins(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (db.query(LauncherPin).filter(LauncherPin.user_id == user.id)
            .order_by(LauncherPin.position, LauncherPin.created_at).all())
    return {"keys": [r.item_key for r in rows]}


@router.put("/pins")
def put_pins(body: PinsIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    keys = _clean_keys(body.keys)
    db.query(LauncherPin).filter(LauncherPin.user_id == user.id).delete()
    for i, k in enumerate(keys):
        db.add(LauncherPin(user_id=user.id, item_key=k, position=i))
    db.commit()
    return {"keys": keys}


# ── Needs Attention ──────────────────────────────────────────────────────────

def _item(kind: str, title: str, detail: str, href: str, count: Optional[int] = None) -> Dict[str, Any]:
    return {"kind": kind, "title": title, "detail": detail, "href": href, "count": count}


def _plural(n: int, one: str, many: str) -> str:
    return "%d %s" % (n, one if n == 1 else many)


def _sales_items(db: Session, user: User) -> List[Dict[str, Any]]:
    from app.models.sales_models import (DEMO_PENDING_REVIEW, BrandSalesOrg, Opportunity,
                                         demo_is_outstanding)
    from app.models.sales_team_models import ONB_SUBMITTED, SalesOnboarding
    from app.services import sales_team
    from app.services.sales_access import is_god, sales_org_ids

    items: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    brand_ids = list(sales_org_ids(user, db) or [])
    program = sales_team.program_brand(db)
    if is_god(user) and program is not None and program.id not in brand_ids:
        brand_ids.append(program.id)
    if not brand_ids:
        return items

    # 1. Rep approvals waiting (owners and God Mode only - they approve).
    try:
        rights = sales_team.actor_rights(db, user)
        if rights.get("can_approve") and program is not None:
            n = (db.query(SalesOnboarding)
                 .filter(SalesOnboarding.brand_sales_org_id == program.id,
                         SalesOnboarding.status == ONB_SUBMITTED).count())
            if n:
                items.append(_item("approvals", _plural(n, "salesperson waiting", "salespeople waiting")
                                   + " for approval", "Onboarding submitted — review and approve",
                                   "/sales/my-team", n))
    except Exception:
        log.exception("attention: approvals failed for user=%s", user.id)

    # 2/3. Demo decisions and overdue builds, within this person's scope.
    try:
        decide = 0
        overdue = 0
        mine_overdue = 0
        for bid in brand_ids:
            scope = sales_team.opp_scope(db, user, bid)
            leads = scope is None or len(scope) > 1
            q = db.query(Opportunity).filter(Opportunity.brand_sales_org_id == bid,
                                             Opportunity.status == "open")
            if scope is not None:
                q = q.filter(or_(Opportunity.owner_user_id.in_(scope),
                                 Opportunity.demo_owner_user_id == user.id))
            for o in q.all():
                if leads and o.demo_status == DEMO_PENDING_REVIEW:
                    decide += 1
                if demo_is_outstanding(o) and o.demo_due_at and o.demo_due_at < now:
                    if leads:
                        overdue += 1
                    elif o.demo_owner_user_id == user.id:
                        mine_overdue += 1
        if decide:
            items.append(_item("demo_decisions", _plural(decide, "custom demo request", "custom demo requests")
                               + " to decide", "Route to an existing demo or approve a build",
                               "/sales/demo-requests", decide))
        if overdue or mine_overdue:
            n = overdue or mine_overdue
            items.append(_item("overdue_builds", _plural(n, "demo build", "demo builds") + " overdue",
                               "Past the target date", "/sales/demo-requests?view=overdue", n))
    except Exception:
        log.exception("attention: demos failed for user=%s", user.id)

    # 4. The salesperson's own next appointment and follow-ups due.
    try:
        from app.models.scheduling_models import (APPT_SCHEDULED, AppointmentParticipant,
                                                  SalesAppointment)
        appt = (db.query(SalesAppointment)
                .join(AppointmentParticipant, AppointmentParticipant.appointment_id == SalesAppointment.id)
                .filter(AppointmentParticipant.user_id == user.id,
                        SalesAppointment.status == APPT_SCHEDULED,
                        SalesAppointment.starts_at >= now,
                        SalesAppointment.starts_at <= now + timedelta(days=7))
                .order_by(SalesAppointment.starts_at).first())
        if appt is not None:
            who = appt.prospect_company or appt.prospect_name or appt.title
            items.append({**_item("next_appointment", "Next appointment: " + (who or "appointment"),
                                  appt.title or "", "/sales/opportunities/%s" % appt.opportunity_id
                                  if appt.opportunity_id else "/sales/appointments"),
                          "at": appt.starts_at.isoformat() + "Z"})
    except Exception:
        log.exception("attention: appointment failed for user=%s", user.id)

    try:
        due = (db.query(Opportunity)
               .filter(Opportunity.brand_sales_org_id.in_(brand_ids),
                       Opportunity.owner_user_id == user.id,
                       Opportunity.status == "open",
                       Opportunity.next_action_due_at.isnot(None),
                       Opportunity.next_action_due_at <= now).count())
        if due:
            items.append(_item("follow_ups", _plural(due, "follow-up", "follow-ups") + " due",
                               "Due today or earlier", "/sales/follow-ups", due))
    except Exception:
        log.exception("attention: follow-ups failed for user=%s", user.id)
    return items


@router.get("/attention")
def attention(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        items = _sales_items(db, user)
    except Exception:  # never let this row break the launcher
        log.exception("attention failed for user=%s", user.id)
        items = []
    return {"items": items[:MAX_ITEMS]}
