"""The implementation lifecycle: owner, milestones, blockers, launch.

AUTHORITY MODEL
---------------
Three different questions, three different answers, and they are deliberately
not the same:

    read  (status, owner, dates, blocker headline)
        god, the implementation owner, the customer's own brand sales manager,
        and the rep who sold it. The last two get a PROJECTION - see
        `sales_projection` - not the record.

    manage (status, milestones, blockers, notes, owner)
        god, or the assigned implementation owner. A sales manager cannot move
        an implementation forward; selling it does not staff it.

    launch (Ready for Launch -> Live)
        god only. Marking a customer Live is the moment they start depending on
        the system in front of their own customers, and it is the one action in
        this file with no undo.

WHAT LAUNCH DOES NOT DO
-----------------------
It does not blindly refuse. An unfinished REQUIRED milestone produces a warning
that the actor must acknowledge; an unfinished optional one produces nothing at
all. A customer who has not imported historical leads is not a customer who
cannot go live, and a platform that thinks otherwise makes its operators lie to
it to get work done.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status as http_status
from sqlalchemy.orm import Session

from app.models.models import Organization, Platform, User, AuditLogEntry
from app.models.sales_models import (
    Opportunity, BrandSalesOrg, BrandPackage, DiscoveryRecord, STAGE_LIVE,
)
from app.models.implementation_models import (
    Implementation, ImplementationMilestone,
    IMPLEMENTATION_STATUSES, IMPLEMENTATION_STATUS_LABELS,
    IMPL_NOT_STARTED, IMPL_KICKOFF_SCHEDULED, IMPL_READY_FOR_LAUNCH,
    IMPL_LIVE, IMPL_BLOCKED,
    MILESTONE_STATUSES, MILESTONE_PENDING, MILESTONE_DONE, MILESTONE_SETTLED,
)
from app.services.sales_access import is_god, is_sales_manager
from app.routers.audit_log_router import log_action


# ── authority ───────────────────────────────────────────────────────────────

def can_manage(user: User, impl: Implementation, db: Session) -> bool:
    if is_god(user):
        return True
    return bool(impl.owner_user_id) and impl.owner_user_id == user.id


def assert_can_manage(user: User, impl: Implementation, db: Session) -> None:
    if not can_manage(user, impl, db):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN,
                            detail="Only god or the assigned implementation owner may change this implementation.")


def can_launch(user: User, impl: Implementation, db: Session) -> bool:
    return is_god(user)


def can_read(user: User, impl: Implementation, db: Session) -> bool:
    """Read authority for the PROJECTION, not for the record."""
    if is_god(user):
        return True
    if impl.owner_user_id and impl.owner_user_id == user.id:
        return True
    if impl.sold_by_user_id and impl.sold_by_user_id == user.id:
        return True
    return bool(impl.brand_sales_org_id) and is_sales_manager(user, db, impl.brand_sales_org_id)


def get_or_404(db: Session, implementation_id: str) -> Implementation:
    impl = db.query(Implementation).filter(Implementation.id == implementation_id).first()
    if impl is None:
        raise HTTPException(status_code=404, detail="Implementation not found.")
    return impl


# ── milestones ──────────────────────────────────────────────────────────────

def milestones(db: Session, impl: Implementation) -> List[ImplementationMilestone]:
    return (db.query(ImplementationMilestone)
              .filter(ImplementationMilestone.implementation_id == impl.id)
              .order_by(ImplementationMilestone.position,
                        ImplementationMilestone.created_at)
              .all())


def completion(db: Session, impl: Implementation) -> Dict[str, Any]:
    """Percentage settled, and what is still outstanding.

    `skipped` counts as settled. A customer who did not buy the voice module has
    not left the voice milestone unfinished; they have no voice milestone to
    finish, and treating it as outstanding makes every percentage on the god
    dashboard wrong for every customer who buys less than everything.
    """
    rows = milestones(db, impl)
    total = len(rows)
    settled = sum(1 for m in rows if m.status in MILESTONE_SETTLED)
    required_open = [m for m in rows
                     if m.is_required and m.status not in MILESTONE_SETTLED]
    return {
        "total": total,
        "settled": settled,
        "percent": int(round(100.0 * settled / total)) if total else 0,
        "required_open": [{"key": m.key, "label": m.label, "status": m.status}
                          for m in required_open],
        "blocked": [{"key": m.key, "label": m.label} for m in rows
                    if m.status == "blocked"],
    }


def set_milestone(db: Session, impl: Implementation, actor: User, key: str,
                  new_status: Optional[str] = None,
                  notes: Optional[str] = None) -> ImplementationMilestone:
    if new_status is not None and new_status not in MILESTONE_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown milestone status '%s'." % new_status)
    m = (db.query(ImplementationMilestone)
           .filter(ImplementationMilestone.implementation_id == impl.id,
                   ImplementationMilestone.key == key).first())
    if m is None:
        raise HTTPException(status_code=404, detail="Milestone '%s' not found on this implementation." % key)

    before = {"status": m.status, "notes": m.notes}
    now = datetime.utcnow()
    if new_status is not None and new_status != m.status:
        m.status = new_status
        if new_status == MILESTONE_DONE:
            m.completed_at = now
            m.completed_by = actor.id
        else:
            m.completed_at = None
            m.completed_by = None
    if notes is not None:
        m.notes = notes.strip() or None
    impl.last_activity_at = now

    _audit(db, impl, actor, "implementation_milestone_changed",
           target_type="implementation_milestone", target_id=m.id,
           before=before, after={"status": m.status, "notes": m.notes},
           details={"key": m.key, "label": m.label})
    db.commit()
    db.refresh(m)
    return m


def add_milestone(db: Session, impl: Implementation, actor: User, *,
                  key: str, label: str, description: Optional[str] = None,
                  is_required: bool = False,
                  position: Optional[int] = None) -> ImplementationMilestone:
    """Milestones are configurable per customer, per §13.

    The template is a starting point, not a cage: a customer who bought Starter
    but needs a data migration gets one added here rather than being pushed onto
    a package they did not buy.
    """
    key = (key or "").strip().lower()
    if not key or not (label or "").strip():
        raise HTTPException(status_code=400, detail="A milestone needs a key and a label.")
    if (db.query(ImplementationMilestone)
          .filter(ImplementationMilestone.implementation_id == impl.id,
                  ImplementationMilestone.key == key).first()) is not None:
        raise HTTPException(status_code=409, detail="Milestone '%s' already exists here." % key)
    if position is None:
        last = (db.query(ImplementationMilestone)
                  .filter(ImplementationMilestone.implementation_id == impl.id)
                  .order_by(ImplementationMilestone.position.desc()).first())
        position = (last.position + 1) if last else 0
    m = ImplementationMilestone(
        implementation_id=impl.id, key=key, label=label.strip(),
        description=(description or None), position=position,
        is_required=bool(is_required), status=MILESTONE_PENDING,
    )
    db.add(m)
    impl.last_activity_at = datetime.utcnow()
    _audit(db, impl, actor, "implementation_milestone_added",
           target_type="implementation_milestone", target_id=key,
           after={"key": key, "label": m.label, "is_required": m.is_required})
    db.commit()
    db.refresh(m)
    return m


# ── owner, status, blockers ─────────────────────────────────────────────────

def assign_owner(db: Session, impl: Implementation, actor: User,
                 owner_user_id: Optional[str]) -> Implementation:
    """Assign or clear the implementation owner.

    Never defaults to the salesperson. `sold_by_user_id` is already on the row
    for the read-only post-Won view; making the rep the owner would quietly move
    delivery accountability onto somebody staffed to sell.
    """
    before = {"owner_user_id": impl.owner_user_id}
    now = datetime.utcnow()
    if owner_user_id:
        u = db.query(User).filter(User.id == owner_user_id, User.is_active.is_(True)).first()
        if u is None:
            raise HTTPException(status_code=404, detail="User not found or inactive.")
        impl.owner_user_id = u.id
        impl.owner_assigned_at = now
        impl.owner_assigned_by = actor.id
    else:
        impl.owner_user_id = None
        impl.owner_assigned_at = None
        impl.owner_assigned_by = None
    impl.last_activity_at = now
    _audit(db, impl, actor, "implementation_owner_assigned",
           before=before, after={"owner_user_id": impl.owner_user_id})
    db.commit()
    db.refresh(impl)
    return impl


def set_status(db: Session, impl: Implementation, actor: User, new_status: str,
               blocker_note: Optional[str] = None,
               target_launch_date: Optional[datetime] = None,
               note: Optional[str] = None) -> Implementation:
    """Move the implementation along. Live is NOT reachable from here.

    `launch()` is the only way to Live, because Live has a different authority
    and writes a different set of fields. Allowing it through the generic status
    setter would make the launch audit trail depend on which endpoint somebody
    happened to call.
    """
    if new_status not in IMPLEMENTATION_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown implementation status '%s'." % new_status)
    if new_status == IMPL_LIVE:
        raise HTTPException(status_code=400,
                            detail="Use the launch action to mark a customer Live.")
    if impl.status == IMPL_LIVE:
        raise HTTPException(status_code=409,
                            detail="This customer is already Live. Reopening a live customer is not supported.")

    now = datetime.utcnow()
    before = {"status": impl.status, "blocker_note": impl.blocker_note,
              "target_launch_date": impl.target_launch_date}

    impl.status = new_status
    if new_status == IMPL_BLOCKED:
        if not (blocker_note or "").strip():
            raise HTTPException(status_code=400,
                                detail="Blocking an implementation requires a reason.")
        impl.blocker_note = blocker_note.strip()
        impl.blocked_at = now
    else:
        # Leaving blocked clears the blocker but keeps nothing stale behind.
        impl.blocker_note = None
        impl.blocked_at = None

    if new_status == IMPL_KICKOFF_SCHEDULED and impl.kickoff_at is None:
        impl.kickoff_at = now
    if new_status == IMPL_READY_FOR_LAUNCH:
        impl.ready_for_launch_at = now
    elif impl.ready_for_launch_at and new_status != IMPL_READY_FOR_LAUNCH:
        impl.ready_for_launch_at = None

    if target_launch_date is not None:
        impl.target_launch_date = target_launch_date
    impl.last_activity_at = now

    action = ("implementation_ready_for_launch" if new_status == IMPL_READY_FOR_LAUNCH
              else "implementation_status_changed")
    _audit(db, impl, actor, action, before=before,
           after={"status": impl.status, "blocker_note": impl.blocker_note,
                  "target_launch_date": impl.target_launch_date},
           note=note)
    db.commit()
    db.refresh(impl)
    return impl


def launch_warnings(db: Session, impl: Implementation) -> List[str]:
    w: List[str] = []
    c = completion(db, impl)
    for m in c["required_open"]:
        w.append("Required milestone not complete: %s" % m["label"])
    if impl.status == IMPL_BLOCKED:
        w.append("Implementation is currently blocked: %s" % (impl.blocker_note or "no reason recorded"))
    if impl.owner_user_id is None:
        w.append("No implementation owner is assigned.")
    org = db.query(Organization).filter(Organization.id == impl.organization_id).first()
    if org is not None and not org.is_active:
        w.append("The customer organisation is currently suspended.")
    if not (db.query(User)
              .filter(User.organization_id == impl.organization_id,
                      User.is_active.is_(True)).first()):
        w.append("The customer has no active user account yet.")
    return w


def launch(db: Session, impl: Implementation, actor: User,
           acknowledge_warnings: bool = False,
           note: Optional[str] = None) -> Implementation:
    """Mark the customer Live. God only, explicit, audited, one-way.

    Warnings do not block on their own - the caller has to say it has seen them.
    That is the difference between a system that refuses to let work happen and
    one that refuses to let it happen ACCIDENTALLY.
    """
    if impl.status == IMPL_LIVE:
        return impl
    warnings = launch_warnings(db, impl)
    if warnings and not acknowledge_warnings:
        raise HTTPException(status_code=409, detail={
            "message": "This implementation has open warnings. Confirm to launch anyway.",
            "warnings": warnings,
        })

    now = datetime.utcnow()
    before = {"status": impl.status, "launched_at": impl.launched_at}
    impl.status = IMPL_LIVE
    impl.launched_at = now
    impl.launched_by = actor.id
    impl.blocker_note = None
    impl.blocked_at = None
    impl.last_activity_at = now

    # The sales record follows the customer into life. `status` stays "won" and
    # `won_at` is untouched: this is a lifecycle position, not a re-outcome.
    opp = db.query(Opportunity).filter(Opportunity.id == impl.opportunity_id).first()
    opp_before = opp.stage if opp else None
    if opp is not None and opp.stage != STAGE_LIVE:
        opp.stage = STAGE_LIVE
        opp.stage_changed_at = now

    _audit(db, impl, actor, "customer_marked_live",
           before={**before, "opportunity_stage": opp_before},
           after={"status": IMPL_LIVE, "launched_at": now,
                  "opportunity_stage": STAGE_LIVE},
           details={"warnings_acknowledged": warnings or None},
           note=note)
    db.commit()
    db.refresh(impl)
    return impl


# ── projections ─────────────────────────────────────────────────────────────

_SALES_VISIBLE_STATUS = {
    IMPL_NOT_STARTED: "Provisioned",
    IMPL_KICKOFF_SCHEDULED: "Kickoff scheduled",
    "configuration": "Implementation in progress",
    "data_migration": "Implementation in progress",
    "integrations": "Implementation in progress",
    "testing": "Implementation in progress",
    "training": "Implementation in progress",
    IMPL_READY_FOR_LAUNCH: "Ready for launch",
    IMPL_LIVE: "Live",
    IMPL_BLOCKED: "Blocked",
}


def sales_projection(db: Session, impl: Implementation) -> Dict[str, Any]:
    """What the rep who sold it, and their manager, are allowed to see (§15/§16).

    Coarse by design. The rep gets to know the customer is progressing and
    roughly where; they do not get the milestone detail, the internal notes, the
    blocker text, or anything at all inside the tenant. `blocked` is a boolean
    and a date, not a story - the story often names a customer's staffing
    problem, and the rep who sold the deal has no reason to hold it.
    """
    org = db.query(Organization).filter(Organization.id == impl.organization_id).first()
    owner = (db.query(User).filter(User.id == impl.owner_user_id).first()
             if impl.owner_user_id else None)
    c = completion(db, impl)

    # Added for the manager's team Won / Onboarding view. Not a widening of what
    # is sensitive: who sold it, what they sold and which company it was are
    # facts the selling rep already holds. The coarseness this docstring protects
    # is milestone detail, internal notes and blocker text, and none of that is
    # here.
    from app.models.sales_models import Opportunity, BrandPackage
    opp = (db.query(Opportunity).filter(Opportunity.id == impl.opportunity_id).first()
           if impl.opportunity_id else None)
    sold_by = (db.query(User).filter(User.id == impl.sold_by_user_id).first()
               if impl.sold_by_user_id else None)
    pkg = (db.query(BrandPackage)
           .filter(BrandPackage.id == opp.selected_package_id).first()
           if opp is not None and opp.selected_package_id else None)

    return {
        "implementation_id": impl.id,
        "opportunity_id": impl.opportunity_id,
        "customer_organization_name": org.name if org else None,
        "company_name": opp.company_name if opp else None,
        "sold_by_user_id": impl.sold_by_user_id,
        "sold_by_name": sold_by.full_name if sold_by else None,
        "package_name": pkg.name if pkg else None,
        "deal_value": float(opp.deal_value) if opp is not None and opp.deal_value is not None else None,
        "won_at": opp.won_at if opp else None,
        "brand_sales_org_id": impl.brand_sales_org_id,
        "status": impl.status,
        "status_label": _SALES_VISIBLE_STATUS.get(impl.status,
                                                  IMPLEMENTATION_STATUS_LABELS.get(impl.status, impl.status)),
        "implementation_owner": owner.full_name if owner else None,
        "target_launch_date": impl.target_launch_date,
        "is_blocked": impl.status == IMPL_BLOCKED,
        "blocked_since": impl.blocked_at,
        "percent_complete": c["percent"],
        "launched_at": impl.launched_at,
        "is_live": impl.is_live(),
    }


def handoff_context(db: Session, impl: Implementation) -> Dict[str, Any]:
    """Sales context for the person doing the implementation (§39).

    A whitelist, assembled here, of the things somebody configuring a tenant
    actually needs. It is NOT the Opportunity object: pipeline stage, deal
    value, price overrides, loss reasons, commission-relevant fields and the
    rest of the sales record stay on the sales side of the wall.
    """
    opp = db.query(Opportunity).filter(Opportunity.id == impl.opportunity_id).first()
    if opp is None:
        return {}
    disc = (db.query(DiscoveryRecord)
              .filter(DiscoveryRecord.opportunity_id == opp.id).first())
    pkg = (db.query(BrandPackage).filter(BrandPackage.id == impl.package_id).first()
           if impl.package_id else None)
    sold_by = (db.query(User).filter(User.id == impl.sold_by_user_id).first()
               if impl.sold_by_user_id else None)

    fields = {}
    if disc is not None:
        labels = dict(DiscoveryRecord.FIELDS)
        for f in ("business_description", "business_goals", "current_process",
                  "current_tools", "bottlenecks", "required_integrations",
                  "appointment_process", "follow_up_process", "desired_outcome",
                  "opportunity_notes"):
            v = getattr(disc, f, None)
            if v is not None and str(v).strip():
                fields[f] = {"label": labels.get(f, f), "value": v}

    return {
        "company": opp.company_name,
        "primary_contact": opp.contact_name,
        "contact_email": opp.email,
        "contact_phone": opp.phone,
        "website": opp.website,
        "industry": opp.industry,
        "timezone": opp.timezone,
        "package": {"key": pkg.key, "name": pkg.name} if pkg else None,
        "sold_by": sold_by.full_name if sold_by else None,
        "target_launch_date": impl.target_launch_date,
        "discovery": fields or None,
        "notes": impl.notes,
    }


def timeline(db: Session, impl: Implementation, limit: int = 100) -> List[Dict[str, Any]]:
    """Implementation history, read from the audit log (§38).

    Deliberately not a second activity table. Every action in this module is
    already audited with actor, before and after; a parallel timeline store
    would be a second source of truth that could disagree with the first.
    """
    rows = (db.query(AuditLogEntry)
              .filter(AuditLogEntry.target_type.in_(
                          ("implementation", "implementation_milestone", "customer_activation")),
                      AuditLogEntry.organization_id == impl.organization_id)
              .order_by(AuditLogEntry.created_at.desc())
              .limit(limit).all())
    out = []
    for r in rows:
        actor = db.query(User).filter(User.id == r.actor_user_id).first()
        out.append({
            "id": r.id, "action": r.action, "at": r.created_at,
            "actor": actor.full_name if actor else None,
            "target_type": r.target_type, "target_id": r.target_id,
            "before": r.before_state, "after": r.after_state,
            "details": r.details, "note": r.note,
        })
    return out


# ── internal ────────────────────────────────────────────────────────────────

def _audit(db: Session, impl: Implementation, actor: User, action: str, *,
           target_type: str = "implementation", target_id: Optional[str] = None,
           before: Any = None, after: Any = None,
           details: Any = None, note: Optional[str] = None) -> None:
    log_action(
        db, impl.organization_id, actor.id,
        action=action,
        target_type=target_type,
        target_id=target_id or impl.id,
        platform_id=impl.platform_id,
        brand_sales_org_id=impl.brand_sales_org_id,
        before=before, after=after, details=details, note=note,
        commit=False,
    )
