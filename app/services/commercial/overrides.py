"""ONBOARDING STEPS THAT WERE ALREADY SATISFIED.

THE PROBLEM, STATED PLAINLY
---------------------------
A customer was given a demo before this onboarding workflow existed. The
workflow now wants a demo ticked, and there are three ways to get there:

  1. Tick it and invent a date. The record then says something that did not
     happen on a day it did not happen.
  2. Make the customer sit through a second demo to satisfy a database.
  3. Record what is actually true: this was completed previously, here is who
     decided that, when they decided it, and why.

This module is the third one, generalised. It is not a demo feature — a step
can be marked completed previously, not applicable, or waived, and the same
four fields are demanded every time: a mode, a named decider, the moment of the
DECISION, and a reason in words.

WHAT IT WILL NOT DO
-------------------
Invent a completion date. `previously_completed_on` is nullable and sits beside
`previously_completed_date_known`, because "some time in August" is the honest
answer for most of these, and a NOT NULL date column is how a system ends up
holding a precise-looking lie.

WHAT IT MIRRORS, AND WHAT IT DOES NOT
-------------------------------------
A MILESTONE override is mirrored onto the milestone row, because `done` and
`skipped` are the launch engine's existing vocabulary for exactly these two
outcomes and leaving the row at `pending` would mean two different answers to
one question.

An INTEGRATION override is mirrored ONLY for `not_applicable`, which the launch
engine already models as a status of its own.

A CHECK or a TRAINING override is NOT mirrored. There is no honest existing
status for "waived" on a test that was never run, and writing `pass` on an
untested check to satisfy a progress bar is the failure this whole module is
here to avoid. The override is the record, and the onboarding view reads it.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.commercial_models import (
    ITEM_CHECK, ITEM_DEMO, ITEM_DISCOVERY, ITEM_INTAKE_STEP, ITEM_INTEGRATION,
    ITEM_MILESTONE, ITEM_TRAINING, MODE_COMPLETED_NORMALLY,
    MODE_COMPLETED_PREVIOUSLY, MODE_NOT_APPLICABLE, MODE_WAIVED,
    OVERRIDE_ITEM_KINDS, OVERRIDE_MODE_LABELS, OVERRIDE_MODES,
    OVERRIDE_SATISFYING_MODES, OnboardingMilestoneOverride,
)
from app.models.implementation_models import (
    Implementation, ImplementationMilestone, MILESTONE_DONE, MILESTONE_PENDING,
    MILESTONE_SKIPPED,
)
from app.models.launch_delivery_models import (
    INT_NOT_APPLICABLE, INT_REQUIRED, ImplementationIntegration,
)
from app.models.models import User
from app.services.commercial import audit as caudit

# Which milestone status each mode means, where a mirror is honest.
_MILESTONE_MIRROR = {
    MODE_COMPLETED_NORMALLY:   MILESTONE_DONE,
    MODE_COMPLETED_PREVIOUSLY: MILESTONE_DONE,
    MODE_NOT_APPLICABLE:       MILESTONE_SKIPPED,
    MODE_WAIVED:               MILESTONE_SKIPPED,
}


def list_for(db: Session, implementation_id: str,
             active_only: bool = True) -> List[OnboardingMilestoneOverride]:
    q = (db.query(OnboardingMilestoneOverride)
         .filter(OnboardingMilestoneOverride.implementation_id == implementation_id))
    if active_only:
        q = q.filter(OnboardingMilestoneOverride.is_active.is_(True))
    return q.order_by(OnboardingMilestoneOverride.decided_at.desc()).all()


def active_map(db: Session,
               implementation_id: str) -> Dict[Tuple[str, str],
                                               OnboardingMilestoneOverride]:
    return {(r.item_kind, r.item_key): r
            for r in list_for(db, implementation_id, active_only=True)}


def satisfied(db: Session, implementation_id: str, item_kind: str,
              item_key: str) -> Optional[OnboardingMilestoneOverride]:
    row = active_map(db, implementation_id).get((item_kind, item_key))
    if row is not None and row.mode in OVERRIDE_SATISFYING_MODES:
        return row
    return None


def public(row: Optional[OnboardingMilestoneOverride]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {
        "id": row.id,
        "item_kind": row.item_kind,
        "item_key": row.item_key,
        "item_label": row.item_label,
        "mode": row.mode,
        "mode_label": OVERRIDE_MODE_LABELS.get(row.mode, row.mode),
        "reason": row.reason,
        "note": row.note,
        "previously_completed_on": row.previously_completed_on,
        "previously_completed_date_known": bool(row.previously_completed_date_known),
        "decided_by_user_id": row.decided_by_user_id,
        "decided_at": row.decided_at,
        "is_active": bool(row.is_active),
    }


def _mirror(db: Session, impl: Implementation, actor: User,
            item_kind: str, item_key: str, mode: str) -> Optional[str]:
    """Carry the decision onto the row the launch engine already keeps.

    Returns a short description of what was mirrored, for the audit entry, or
    None where mirroring would assert something that did not happen.
    """
    if item_kind == ITEM_MILESTONE:
        row = (db.query(ImplementationMilestone)
               .filter(ImplementationMilestone.implementation_id == impl.id,
                       ImplementationMilestone.key == item_key).first())
        if row is None:
            return None
        target = _MILESTONE_MIRROR.get(mode)
        if target is None or row.status == target:
            return None
        row.status = target
        # The completion stamp records WHEN THE DECISION WAS MADE. It is not
        # backdated to the day the work happened, because that day is often
        # unknown and always unproven — `previously_completed_on` on the
        # override carries that separately, with a flag saying whether it is
        # known at all.
        if target == MILESTONE_DONE:
            row.completed_at = datetime.utcnow()
            row.completed_by = getattr(actor, "id", None)
        else:
            row.completed_at = None
            row.completed_by = None
        return "milestone:%s->%s" % (item_key, target)

    if item_kind == ITEM_INTEGRATION and mode == MODE_NOT_APPLICABLE:
        row = (db.query(ImplementationIntegration)
               .filter(ImplementationIntegration.implementation_id == impl.id,
                       ImplementationIntegration.key == item_key).first())
        if row is None or row.status == INT_NOT_APPLICABLE:
            return None
        row.status = INT_NOT_APPLICABLE
        return "integration:%s->not_applicable" % item_key

    return None


def apply(db: Session, impl: Implementation, actor: User, *,
          item_kind: str, item_key: str, mode: str, reason: str,
          note: Optional[str] = None,
          item_label: Optional[str] = None,
          previously_completed_on: Optional[date] = None,
          previously_completed_date_known: bool = False
          ) -> OnboardingMilestoneOverride:
    """Record that a step is satisfied other than by doing it now."""
    if item_kind not in OVERRIDE_ITEM_KINDS:
        raise HTTPException(status_code=400,
                            detail="Unknown onboarding item kind '%s'." % item_kind)
    if mode not in OVERRIDE_MODES:
        raise HTTPException(status_code=400,
                            detail="Unknown completion mode '%s'." % mode)
    key = (item_key or "").strip()
    if not key:
        raise HTTPException(status_code=400,
                            detail="An override needs the step it applies to.")
    if not (reason or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Marking a step complete any way other than doing it "
                   "requires a reason. It is the only record of why.")

    if mode != MODE_COMPLETED_PREVIOUSLY and previously_completed_on is not None:
        raise HTTPException(
            status_code=400,
            detail="A prior completion date only belongs on a step marked "
                   "completed previously.")
    if previously_completed_on is not None and not previously_completed_date_known:
        # The caller gave a date and said it is not known. One of those is
        # wrong, and guessing which would put an unverified date in the record.
        raise HTTPException(
            status_code=400,
            detail="A prior completion date was supplied but flagged as not "
                   "known. Give the date, or say it is not known — not both.")
    if previously_completed_on is not None and previously_completed_on > date.today():
        raise HTTPException(status_code=400,
                            detail="A prior completion cannot be in the future.")

    existing = (db.query(OnboardingMilestoneOverride)
                .filter(OnboardingMilestoneOverride.implementation_id == impl.id,
                        OnboardingMilestoneOverride.item_kind == item_kind,
                        OnboardingMilestoneOverride.item_key == key,
                        OnboardingMilestoneOverride.is_active.is_(True))
                .all())
    now = datetime.utcnow()
    for row in existing:
        row.is_active = False
        row.superseded_at = now
        row.superseded_by_user_id = getattr(actor, "id", None)

    row = OnboardingMilestoneOverride(
        implementation_id=impl.id,
        organization_id=impl.organization_id,
        item_kind=item_kind, item_key=key,
        item_label=(item_label or None),
        mode=mode, reason=reason.strip(), note=(note or None),
        previously_completed_on=previously_completed_on,
        previously_completed_date_known=bool(previously_completed_date_known
                                             and previously_completed_on is not None),
        decided_by_user_id=getattr(actor, "id", None),
        decided_at=now, is_active=True,
    )
    db.add(row)
    db.flush()

    mirrored = _mirror(db, impl, actor, item_kind, key, mode)

    caudit.record(db, None, actor, caudit.A_MILESTONE_OVERRIDDEN,
                  target_type="onboarding_milestone_override", target_id=row.id,
                  organization_id=impl.organization_id,
                  platform_id=impl.platform_id,
                  brand_sales_org_id=impl.brand_sales_org_id,
                  before={"superseded": [r.id for r in existing] or None},
                  after={"item_kind": item_kind, "item_key": key, "mode": mode,
                         "previously_completed_on": (
                             str(previously_completed_on)
                             if previously_completed_on else None),
                         "previously_completed_date_known":
                             bool(row.previously_completed_date_known)},
                  details={"mirrored": mirrored,
                           "implementation_id": impl.id},
                  note=reason.strip())
    db.flush()
    return row


def remove(db: Session, impl: Implementation, actor: User, override_id: str,
           reason: str) -> OnboardingMilestoneOverride:
    """Withdraw an override and put the step back where it was.

    The row is superseded, never deleted. Who waived what, and who later
    decided that was wrong, is the history this table is for.
    """
    if not (reason or "").strip():
        raise HTTPException(status_code=400,
                            detail="Withdrawing an override requires a reason.")
    row = (db.query(OnboardingMilestoneOverride)
           .filter(OnboardingMilestoneOverride.id == override_id,
                   OnboardingMilestoneOverride.implementation_id == impl.id)
           .first())
    if row is None:
        raise HTTPException(status_code=404,
                            detail="That override is not on this onboarding.")
    if not row.is_active:
        return row

    row.is_active = False
    row.superseded_at = datetime.utcnow()
    row.superseded_by_user_id = getattr(actor, "id", None)

    restored = None
    if row.item_kind == ITEM_MILESTONE:
        m = (db.query(ImplementationMilestone)
             .filter(ImplementationMilestone.implementation_id == impl.id,
                     ImplementationMilestone.key == row.item_key).first())
        if m is not None and m.status in (MILESTONE_DONE, MILESTONE_SKIPPED):
            m.status = MILESTONE_PENDING
            m.completed_at = None
            m.completed_by = None
            restored = "milestone:%s->pending" % row.item_key
    elif row.item_kind == ITEM_INTEGRATION and row.mode == MODE_NOT_APPLICABLE:
        i = (db.query(ImplementationIntegration)
             .filter(ImplementationIntegration.implementation_id == impl.id,
                     ImplementationIntegration.key == row.item_key).first())
        if i is not None and i.status == INT_NOT_APPLICABLE:
            i.status = INT_REQUIRED
            restored = "integration:%s->required" % row.item_key

    caudit.record(db, None, actor, caudit.A_MILESTONE_RESTORED,
                  target_type="onboarding_milestone_override", target_id=row.id,
                  organization_id=impl.organization_id,
                  platform_id=impl.platform_id,
                  brand_sales_org_id=impl.brand_sales_org_id,
                  before={"mode": row.mode, "item_key": row.item_key},
                  after={"is_active": False},
                  details={"restored": restored,
                           "implementation_id": impl.id},
                  note=reason.strip())
    db.flush()
    return row
