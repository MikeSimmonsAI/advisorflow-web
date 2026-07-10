from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from pydantic import BaseModel, EmailStr
import secrets
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.deps import get_db, require_admin
from app.models.models import User, Lead, Message, Reply, LeadOutcome, LeadStatus, ReplyClassification, CadenceState, ContactRegistry, Organization
from app.services.auth_service import hash_password
from app.services.dedup_service import normalize_phone, normalize_last_name
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/dashboard")
def master_dashboard(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Mike's master view - KPIs across every advisor in the organization.
    org_admin sees their own org; super_admin (Mike, eventually) can be
    extended to cross-org once North Star Memorial Group comes online.
    """
    org_id = current_user.organization_id

    advisors = db.query(User).filter(User.organization_id == org_id, User.role == "advisor").all()

    per_advisor_stats = []
    for advisor in advisors:
        sent_count = db.query(func.count(Message.id)).filter(Message.sender_id == advisor.id).scalar()
        lead_count = db.query(func.count(Lead.id)).filter(Lead.assigned_to_id == advisor.id).scalar()
        hot_count = (
            db.query(func.count(Reply.id))
            .join(Lead, Reply.lead_id == Lead.id)
            .filter(Lead.assigned_to_id == advisor.id, Reply.is_hot == True)
            .scalar()
        )
        per_advisor_stats.append({
            "advisor_id": advisor.id,
            "advisor_name": advisor.full_name,
            "leads_owned": lead_count,
            "messages_sent": sent_count,
            "hot_replies": hot_count,
        })

    total_leads = db.query(func.count(Lead.id)).filter(Lead.organization_id == org_id).scalar()
    total_duplicates = (
        db.query(func.count(Lead.id))
        .filter(Lead.organization_id == org_id, Lead.is_duplicate == True)
        .scalar()
    )

    return {
        "organization_id": org_id,
        "total_leads": total_leads,
        "total_duplicates_prevented": total_duplicates,
        "advisors": per_advisor_stats,
    }


@router.get("/leads")
def all_org_leads(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Full lead list across all advisors in the org - master view. Joins in
    the assigned advisor's name rather than returning a bare
    assigned_to_id, since a raw foreign key UUID is meaningless on the
    admin dashboard - Mike needs to see WHO owns each lead at a glance.
    """
    leads = (
        db.query(Lead)
        .filter(Lead.organization_id == current_user.organization_id)
        .order_by(Lead.created_at.desc())
        .limit(1000)
        .all()
    )

    advisor_ids = {lead.assigned_to_id for lead in leads if lead.assigned_to_id}
    advisors_by_id = {
        u.id: u.full_name
        for u in db.query(User).filter(User.id.in_(advisor_ids)).all()
    } if advisor_ids else {}

    results = []
    for lead in leads:
        lead_dict = {
            "id": lead.id,
            "first_name": lead.first_name,
            "last_name": lead.last_name,
            "phone": lead.phone,
            "email": lead.email,
            "tier": lead.tier if lead.tier else None,
            "status": lead.status if lead.status else None,
            "assigned_to_id": lead.assigned_to_id,
            "assigned_to_name": advisors_by_id.get(lead.assigned_to_id, "Unassigned"),
            "created_at": lead.created_at,
        }
        results.append(lead_dict)
    return results




# ---------------------------------------------------------------------------
# Manager Command Dashboard - quality metrics, not just volume counts.
# These endpoints intentionally sit beside /admin/dashboard instead of
# replacing it, so the existing Master Dashboard contract stays stable.
# ---------------------------------------------------------------------------

HOT_REPLY_CLASSIFICATIONS = (ReplyClassification.INTERESTED, ReplyClassification.CALLBACK)


def _safe_rate(numerator: int, denominator: int) -> float:
    """Return a percentage rounded to 2 decimals, with 0 for empty denominators."""
    if not denominator:
        return 0
    return round((numerator / denominator) * 100, 2)


def _advisor_metrics(db: Session, organization_id: str, advisor: User) -> dict:
    """
    Build quality metrics for one advisor using only existing tables.

    Notes on definitions:
    - messages_sent: SMS Message rows sent by this advisor.
    - replies/hot_replies: Reply rows on leads currently owned by this advisor.
    - hot reply = AI/manual classification interested or callback OR legacy is_hot=True.
    - booking/dnc rates use total leads owned as denominator.
    - duplicate_leads_prevented follows the existing project convention:
      Lead.is_duplicate=True, set by the ContactRegistry/dedup flow.
    """
    leads_owned = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == False,
    ).scalar() or 0

    messages_sent = db.query(func.count(Message.id)).join(Lead, Message.lead_id == Lead.id).filter(
        Lead.organization_id == organization_id,
        Message.sender_id == advisor.id,
        Lead.is_duplicate == False,
    ).scalar() or 0

    replies = db.query(func.count(Reply.id)).join(Lead, Reply.lead_id == Lead.id).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == False,
    ).scalar() or 0

    hot_replies = db.query(func.count(Reply.id)).join(Lead, Reply.lead_id == Lead.id).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == False,
        ((Reply.classification.in_(HOT_REPLY_CLASSIFICATIONS)) | (Reply.is_hot == True)),
    ).scalar() or 0

    booked_leads = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.status == "booked",
        Lead.is_duplicate == False,
    ).scalar() or 0

    dnc_leads = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.status == "dnc",
        Lead.is_duplicate == False,
    ).scalar() or 0

    duplicate_leads_prevented = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == organization_id,
        Lead.assigned_to_id == advisor.id,
        Lead.is_duplicate == True,
    ).scalar() or 0

    return {
        "advisor_id": advisor.id,
        "advisor_name": advisor.full_name,
        "leads_owned": leads_owned,
        "messages_sent": messages_sent,
        "replies": replies,
        "hot_replies": hot_replies,
        "booked_leads": booked_leads,
        "dnc_leads": dnc_leads,
        "duplicate_leads_prevented": duplicate_leads_prevented,
        "reply_rate": _safe_rate(replies, messages_sent),
        "hot_reply_rate": _safe_rate(hot_replies, messages_sent),
        "booking_rate": _safe_rate(booked_leads, leads_owned),
        "dnc_rate": _safe_rate(dnc_leads, leads_owned),
    }


@router.get("/dashboard/metrics")
def dashboard_quality_metrics(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Org-scoped advisor quality metrics for the upgraded Manager Command Dashboard."""
    org_id = current_user.organization_id
    advisors = (
        db.query(User)
        .filter(User.organization_id == org_id, User.role == "advisor")
        .order_by(User.full_name.asc())
        .all()
    )

    advisor_rows = [_advisor_metrics(db, org_id, advisor) for advisor in advisors]

    totals = {
        "advisor_id": "org_total",
        "advisor_name": "Organization total",
        "leads_owned": sum(row["leads_owned"] for row in advisor_rows),
        "messages_sent": sum(row["messages_sent"] for row in advisor_rows),
        "replies": sum(row["replies"] for row in advisor_rows),
        "hot_replies": sum(row["hot_replies"] for row in advisor_rows),
        "booked_leads": sum(row["booked_leads"] for row in advisor_rows),
        "dnc_leads": sum(row["dnc_leads"] for row in advisor_rows),
        "duplicate_leads_prevented": db.query(func.count(Lead.id)).filter(
            Lead.organization_id == org_id,
            Lead.is_duplicate == True,
        ).scalar() or 0,
    }
    totals["reply_rate"] = _safe_rate(totals["replies"], totals["messages_sent"])
    totals["hot_reply_rate"] = _safe_rate(totals["hot_replies"], totals["messages_sent"])
    totals["booking_rate"] = _safe_rate(totals["booked_leads"], totals["leads_owned"])
    totals["dnc_rate"] = _safe_rate(totals["dnc_leads"], totals["leads_owned"])

    return {
        "organization_id": org_id,
        "totals": totals,
        "advisors": advisor_rows,
    }


@router.get("/dashboard/funnel")
def dashboard_funnel(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Org-wide lead funnel counts from existing Lead/Message/Reply/LeadOutcome data."""
    org_id = current_user.organization_id

    total_leads = db.query(func.count(Lead.id)).filter(Lead.organization_id == org_id).scalar() or 0

    sent = db.query(func.count(distinct(Lead.id))).join(Message, Message.lead_id == Lead.id).filter(
        Lead.organization_id == org_id,
    ).scalar() or 0

    replied = db.query(func.count(distinct(Lead.id))).join(Reply, Reply.lead_id == Lead.id).filter(
        Lead.organization_id == org_id,
    ).scalar() or 0

    hot_interested = db.query(func.count(distinct(Lead.id))).join(Reply, Reply.lead_id == Lead.id).filter(
        Lead.organization_id == org_id,
        ((Reply.classification.in_(HOT_REPLY_CLASSIFICATIONS)) | (Reply.is_hot == True)),
    ).scalar() or 0

    booked = db.query(func.count(Lead.id)).filter(
        Lead.organization_id == org_id,
        Lead.status == "booked",
    ).scalar() or 0

    sold = db.query(func.count(distinct(Lead.id))).join(LeadOutcome, LeadOutcome.lead_id == Lead.id).filter(
        Lead.organization_id == org_id,
        LeadOutcome.resulted_in_sale == True,
    ).scalar() or 0

    stages = [
        {"key": "total_leads", "label": "Total leads", "count": total_leads},
        {"key": "sent", "label": "Sent", "count": sent},
        {"key": "replied", "label": "Replied", "count": replied},
        {"key": "hot_interested", "label": "Hot / interested", "count": hot_interested},
        {"key": "booked", "label": "Booked", "count": booked},
        {"key": "sold", "label": "Sold", "count": sold},
    ]

    return {
        "organization_id": org_id,
        "total_leads": total_leads,
        "sent": sent,
        "replied": replied,
        "hot_interested": hot_interested,
        "booked": booked,
        "sold": sold,
        "stages": stages,
    }


@router.get("/dashboard/revenue")
def dashboard_revenue(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Master Control Board / revenue analytics - step 6 of the original
    8-step build plan, never started until now.

    IMPORTANT DESIGN CONSTRAINT, matching LeadOutcome.sale_amount's own
    column comment: sale_amount is a free-text sales note field an
    advisor types in (e.g. "$3,200" or "approx 2800 plus marker"), NOT a
    structured currency column. This endpoint deliberately reports SALE
    COUNTS, not dollar totals - summing or parsing sale_amount strings as
    real currency would produce a number that looks precise and
    authoritative but isn't reliable financial data. Real revenue
    accounting belongs in Restland's actual accounting system, not here.
    What this CAN do reliably: how many sales, by whom, of what kind, and
    when - all of which come from structured boolean/date fields.
    """
    org_id = current_user.organization_id

    sale_outcomes = (
        db.query(LeadOutcome)
        .join(Lead, LeadOutcome.lead_id == Lead.id)
        .filter(Lead.organization_id == org_id, LeadOutcome.resulted_in_sale == True)
        .all()
    )

    total_sales = len(sale_outcomes)

    # Per-advisor sale counts - the actual gap _advisor_metrics didn't cover.
    sales_by_advisor: dict[str, int] = defaultdict(int)
    for outcome in sale_outcomes:
        sales_by_advisor[outcome.recorded_by_id] += 1

    advisor_ids = list(sales_by_advisor.keys())
    advisors_by_id = {}
    if advisor_ids:
        advisors_by_id = {
            a.id: a.full_name
            for a in db.query(User).filter(User.id.in_(advisor_ids)).all()
        }

    by_advisor = sorted(
        [
            {"advisor_id": advisor_id, "advisor_name": advisors_by_id.get(advisor_id, "Unknown"), "sale_count": count}
            for advisor_id, count in sales_by_advisor.items()
        ],
        key=lambda row: row["sale_count"],
        reverse=True,
    )

    # What's being sold - the structured checklist fields are reliable
    # counts; sale_items is free text and only shown as a recent-notes
    # list, never aggregated, since advisors don't write it in any
    # consistent structured format.
    product_mix = {
        "funeral_arrangement": sum(1 for o in sale_outcomes if o.has_funeral_arrangement),
        "cemetery_property": sum(1 for o in sale_outcomes if o.has_cemetery_property),
        "marker": sum(1 for o in sale_outcomes if o.has_marker),
        "memorial": sum(1 for o in sale_outcomes if o.has_memorial),
    }

    # Monthly trend, grouped in Python rather than via a DB-specific
    # date-trunc function, since this app runs on SQLite in tests/dev and
    # Postgres in production - a portable approach beats a function that
    # only works on one of the two.
    monthly_counts: dict[str, int] = defaultdict(int)
    for outcome in sale_outcomes:
        when = outcome.appointment_date or outcome.created_at
        if when:
            monthly_counts[when.strftime("%Y-%m")] += 1
    monthly_trend = [
        {"month": month, "sale_count": count}
        for month, count in sorted(monthly_counts.items())
    ]

    recent_sale_notes = [
        {
            "lead_id": outcome.lead_id,
            "advisor_name": advisors_by_id.get(outcome.recorded_by_id) or (
                db.query(User.full_name).filter(User.id == outcome.recorded_by_id).scalar()
            ),
            "sale_items": outcome.sale_items,
            "sale_amount": outcome.sale_amount,  # shown verbatim as the advisor's own note text, never parsed/summed
            "date": outcome.appointment_date or outcome.created_at,
        }
        for outcome in sorted(sale_outcomes, key=lambda o: o.created_at or datetime.min, reverse=True)[:20]
    ]

    return {
        "total_sales": total_sales,
        "by_advisor": by_advisor,
        "product_mix": product_mix,
        "monthly_trend": monthly_trend,
        "recent_sale_notes": recent_sale_notes,
    }


# ---------------------------------------------------------------------------
# User management - lets an org_admin/super_admin create and manage advisor
# accounts directly from the app, instead of running the seed.py script by
# hand. This was a real gap Mike specifically flagged: the only way to add
# an advisor was a one-time backend script, not a real in-app workflow.
# ---------------------------------------------------------------------------

class CreateUserRequest(BaseModel):
    email: EmailStr
    full_name: str
    role: str = "advisor"  # advisor, org_admin (super_admin is reserved, not creatable here)


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    must_change_password: bool
    temp_password: str | None = None  # only populated once, right after creation


def _generate_temp_password() -> str:
    """
    Generates a random temporary password for a new account, readable
    enough to type/copy but not guessable. Always paired with
    must_change_password=True so it's never the account's permanent
    password.
    """
    return secrets.token_urlsafe(9) + "!1"


@router.get("/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Lists every user in the current admin's organization - the real account management screen."""
    users = (
        db.query(User)
        .filter(User.organization_id == current_user.organization_id)
        .order_by(User.created_at.asc())
        .all()
    )
    return [
        UserResponse(
            id=u.id, email=u.email, full_name=u.full_name, role=u.role,
            is_active=u.is_active, must_change_password=u.must_change_password,
        )
        for u in users
    ]


@router.post("/users", response_model=UserResponse)
def create_user(
    req: CreateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Creates a new advisor (or org_admin) account in the current admin's
    organization. Generates a temporary password, returned ONCE in this
    response only - never retrievable again afterward, same security
    pattern as how Twilio/OpenAI show API keys only at creation time.
    The new account is forced to change that password on first login.
    """
    existing = db.query(User).filter(User.email == req.email).first()
    if existing:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="A user with this email already exists.")

    if req.role not in ("advisor", "org_admin"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Role must be 'advisor' or 'org_admin'.")

    temp_password = _generate_temp_password()
    new_user = User(
        organization_id=current_user.organization_id,
        email=req.email,
        password_hash=hash_password(temp_password),
        full_name=req.full_name,
        role=req.role,
        must_change_password=True,
    )
    db.add(new_user)
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.create", target_type="user", target_id=new_user.id,
        details={"email": new_user.email, "role": new_user.role, "created_by": current_user.full_name},
    )

    return UserResponse(
        id=new_user.id, email=new_user.email, full_name=new_user.full_name,
        role=new_user.role, is_active=new_user.is_active,
        must_change_password=new_user.must_change_password,
        temp_password=temp_password,
    )


@router.patch("/users/{user_id}/deactivate")
def deactivate_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Deactivates (not deletes) an advisor account - they can no longer log
    in, but their leads/messages/history stay intact for record-keeping.
    Deletion isn't offered here on purpose: removing a user shouldn't
    silently orphan or destroy their lead history.
    """
    from fastapi import HTTPException
    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")
    if target.role == "super_admin":
        raise HTTPException(status_code=400, detail="Cannot deactivate a super_admin account.")

    target.is_active = False
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.deactivate", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return {"success": True}


@router.patch("/users/{user_id}/reactivate")
def reactivate_user(user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """Re-enables a previously deactivated account."""
    from fastapi import HTTPException
    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    target.is_active = True
    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.reactivate", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return {"success": True}


# ---------------------------------------------------------------------------
# Password reset - SUPER ADMIN ONLY, by Mike's explicit instruction.
# Org admins can deactivate/reactivate accounts (above) but must NOT be
# able to reset passwords - that's a more sensitive action reserved for
# the super_admin role alone.
# ---------------------------------------------------------------------------

class ResetPasswordResponse(BaseModel):
    email: str
    temp_password: str


def require_super_admin(current_user: User = Depends(require_admin)) -> User:
    """
    Stricter than require_admin - only super_admin passes. Layered on
    top of require_admin (not a replacement) so org_admins still get a
    clean 403 rather than this function needing its own duplicate auth
    plumbing.
    """
    from fastapi import HTTPException
    if current_user.role != "super_admin":
        raise HTTPException(status_code=403, detail="Only the super admin can perform this action.")
    return current_user


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def reset_user_password(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Resets any user's password to a new temp password, forcing them to
    set a real one on next login. Deliberately restricted to super_admin
    only (see require_super_admin above) - an org_admin should never be
    able to take over another advisor's account by resetting their
    password, even within the same organization.
    """
    from fastapi import HTTPException
    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    temp_password = _generate_temp_password()
    target.password_hash = hash_password(temp_password)
    target.must_change_password = True
    db.commit()

    # CRITICAL: never include temp_password in the audit details - the
    # audit log is the one thing that should outlive this response, and a
    # password (even temporary) has no business living in a log table.
    log_action(
        db, current_user.organization_id, current_user.id,
        action="user.reset_password", target_type="user", target_id=target.id,
        details={"email": target.email},
    )

    return ResetPasswordResponse(email=target.email, temp_password=temp_password)


# ---------------------------------------------------------------------------
# Edit user details - SUPER ADMIN ONLY, same reasoning as password reset
# above: a typo'd name or wrong email on an existing account is something
# the org owner needs to be able to fix directly, without it being
# delegated to every org_admin. Does NOT touch password or allow promoting
# to super_admin (role changes here are limited to advisor/org_admin, same
# restriction as create_user above).
# ---------------------------------------------------------------------------

class UpdateUserRequest(BaseModel):
    full_name: str | None = None
    email: EmailStr | None = None
    role: str | None = None  # 'advisor' or 'org_admin' only - see validation below


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Edits an existing user's name, email, and/or role. Fields omitted from
    the request are left unchanged - this is a partial update, not a
    full replace, so the frontend doesn't have to resend everything.
    """
    from fastapi import HTTPException

    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    before = {"full_name": target.full_name, "email": target.email, "role": target.role}

    if req.email is not None and req.email != target.email:
        existing = db.query(User).filter(User.email == req.email, User.id != target.id).first()
        if existing:
            raise HTTPException(status_code=400, detail="A user with this email already exists.")
        target.email = req.email

    if req.full_name is not None:
        cleaned_name = req.full_name.strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="full_name cannot be blank.")
        target.full_name = cleaned_name

    if req.role is not None:
        if target.role == "super_admin":
            raise HTTPException(status_code=400, detail="Cannot change the super_admin account's role.")
        if req.role not in ("advisor", "org_admin"):
            raise HTTPException(status_code=400, detail="Role must be 'advisor' or 'org_admin'.")
        target.role = req.role

    db.commit()
    db.refresh(target)

    after = {"full_name": target.full_name, "email": target.email, "role": target.role}
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="user.update", target_type="user", target_id=target.id,
            details=changed,
        )

    return UserResponse(
        id=target.id, email=target.email, full_name=target.full_name, role=target.role,
        is_active=target.is_active, must_change_password=target.must_change_password,
    )


# ---------------------------------------------------------------------------
# Per-user detail page - this didn't exist before. Clicking a name in User
# Management went nowhere; there was no way to see what a user had actually
# done (lead counts, performance, recent activity) without going through
# the org-wide Master Dashboard and manually finding their row. Reuses
# _advisor_metrics (already computes leads_owned/messages_sent/replies/
# booking_rate/etc. for the Master Dashboard) rather than recomputing those
# numbers a second way.
# ---------------------------------------------------------------------------

def _recent_activity_for_advisor(db: Session, organization_id: str, advisor_id: str, limit: int = 12) -> list[dict]:
    """
    Merges the advisor's most recent sent messages and the replies received
    on leads they own into one chronological feed. Two separate queries
    (not a SQL UNION) since Message and Reply have different columns and
    different join paths to Lead - simpler and clearer to merge in Python
    for a feed this small (limit defaults to 12) than to fight an ORM
    UNION across mismatched column sets.
    """
    messages = (
        db.query(Message, Lead.first_name, Lead.last_name, Lead.id)
        .join(Lead, Message.lead_id == Lead.id)
        .filter(Lead.organization_id == organization_id, Message.sender_id == advisor_id)
        .order_by(Message.sent_at.desc())
        .limit(limit)
        .all()
    )
    replies = (
        db.query(Reply, Lead.first_name, Lead.last_name, Lead.id)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == organization_id, Lead.assigned_to_id == advisor_id)
        .order_by(Reply.received_at.desc())
        .limit(limit)
        .all()
    )

    feed = []
    for message, first_name, last_name, lead_id in messages:
        feed.append({
            "type": "sent",
            "timestamp": message.sent_at,
            "lead_id": lead_id,
            "lead_name": f"{first_name or ''} {last_name or ''}".strip() or "Unknown lead",
            "body": message.body,
        })
    for reply, first_name, last_name, lead_id in replies:
        feed.append({
            "type": "reply",
            "timestamp": reply.received_at,
            "lead_id": lead_id,
            "lead_name": f"{first_name or ''} {last_name or ''}".strip() or "Unknown lead",
            "body": reply.body,
            "classification": reply.classification.value if reply.classification else None,
        })

    feed.sort(key=lambda item: item["timestamp"] or datetime.min, reverse=True)
    return feed[:limit]


@router.get("/users/{user_id}/detail")
def get_user_detail(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Full detail view for one user: profile fields, performance metrics
    (same numbers as the Master Dashboard, scoped to just this person),
    and a recent activity feed. org_admin and super_admin can view any
    user in their org; this is read-only regardless of role - editing
    still goes through PATCH /users/{user_id} (super_admin only) above.
    """
    target = db.query(User).filter(
        User.id == user_id, User.organization_id == current_user.organization_id
    ).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    metrics = _advisor_metrics(db, current_user.organization_id, target)
    activity = _recent_activity_for_advisor(db, current_user.organization_id, target.id)

    return {
        "id": target.id,
        "email": target.email,
        "full_name": target.full_name,
        "role": target.role,
        "is_active": target.is_active,
        "must_change_password": target.must_change_password,
        "last_login_at": target.last_login_at,
        "metrics": metrics,
        "recent_activity": activity,
    }


# ---------------------------------------------------------------------------
# Lead reassignment - the manual routing capability Mike specifically
# asked for: look at the full pool of leads and direct specific ones to
# specific advisors (e.g. "memorial-interested leads go to this person").
# ---------------------------------------------------------------------------

class ReassignLeadRequest(BaseModel):
    lead_ids: list[str]
    new_assigned_to_id: str | None = None  # None = unassign, leave in the pool


class ReassignResultResponse(BaseModel):
    reassigned_count: int
    skipped_count: int
    skipped_ids: list[str]


@router.post("/leads/reassign", response_model=ReassignResultResponse)
def reassign_leads(
    req: ReassignLeadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Reassigns one or more leads to a different advisor (or unassigns them
    back to the pool if new_assigned_to_id is None). Both the leads and
    the target advisor must belong to the current admin's organization -
    enforced explicitly below rather than trusted from the request body.
    """
    from fastapi import HTTPException

    if req.new_assigned_to_id:
        target_advisor = db.query(User).filter(
            User.id == req.new_assigned_to_id,
            User.organization_id == current_user.organization_id,
            User.is_active == True,
        ).first()
        if not target_advisor:
            raise HTTPException(status_code=404, detail="Target advisor not found or inactive in this organization.")

    leads = db.query(Lead).filter(
        Lead.id.in_(req.lead_ids), Lead.organization_id == current_user.organization_id
    ).all()
    found_ids = {l.id for l in leads}
    skipped_ids = [lid for lid in req.lead_ids if lid not in found_ids]

    for lead in leads:
        lead.assigned_to_id = req.new_assigned_to_id

    db.commit()

    if leads:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.reassign", target_type="lead_batch", target_id=",".join(found_ids) if len(found_ids) <= 20 else f"{len(found_ids)}_leads",
            details={
                "lead_ids": sorted(found_ids),
                "new_assigned_to_id": req.new_assigned_to_id,
                "count": len(leads),
            },
        )

    return ReassignResultResponse(
        reassigned_count=len(leads),
        skipped_count=len(skipped_ids),
        skipped_ids=skipped_ids,
    )


@router.get("/leads/unassigned")
def list_unassigned_leads(db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    """
    Returns every lead in the org's pool that has no advisor assigned yet -
    the queue an admin works through when manually routing leads out to
    the team, rather than every lead defaulting to whoever happened to
    import it.
    """
    leads = (
        db.query(Lead)
        .filter(Lead.organization_id == current_user.organization_id, Lead.assigned_to_id.is_(None))
        .order_by(Lead.created_at.desc())
        .limit(500)
        .all()
    )
    return [
        {
            "id": l.id, "first_name": l.first_name, "last_name": l.last_name,
            "phone": l.phone, "email": l.email,
            "tier": l.tier if l.tier else None,
            "engagement_temperature": l.engagement_temperature.value if l.engagement_temperature else None,
            "created_at": l.created_at,
        }
        for l in leads
    ]


# ---------------------------------------------------------------------------
# Lead Cleanup Center - potential duplicate discovery, safe merge, contact fixes.
# ---------------------------------------------------------------------------

class LeadSummary(BaseModel):
    id: str
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None
    assigned_to_id: str | None = None
    is_duplicate: bool | None = None


class PotentialDuplicateGroup(BaseModel):
    match_type: str
    match_key: str
    leads: list[LeadSummary]


class MergeLeadsRequest(BaseModel):
    keep_lead_id: str
    merge_lead_ids: list[str]


class MergeLeadsResponse(BaseModel):
    keep_lead_id: str
    merged_count: int
    moved_messages: int
    moved_replies: int
    moved_cadence_states: int
    moved_outcomes: int
    deleted_lead_ids: list[str]


class FixContactInfoRequest(BaseModel):
    phone: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None


def _lead_summary(lead: Lead) -> dict[str, Any]:
    return {
        "id": lead.id,
        "first_name": lead.first_name,
        "last_name": lead.last_name,
        "phone": lead.phone,
        "email": lead.email,
        "status": lead.status if lead.status else None,
        "assigned_to_id": lead.assigned_to_id,
        "is_duplicate": bool(lead.is_duplicate),
    }


def _delete_merged_lead_records(db: Session, merge_leads: list[Lead]) -> None:
    """
    Small seam for transaction tests: route calls this after related rows are
    moved but before commit. If this raises, the caller rolls back everything.

    Uses a bulk delete instead of ORM db.delete(lead) so SQLAlchemy does not
    try to null out one-to-one relationship children such as CadenceState after
    their lead_id has already been reassigned to the kept lead.

    Must delete child records with non-cascading FKs first (booking_links,
    lead_outcomes, messages, replies) before deleting the lead rows.
    """
    from app.models.models import BookingLink, LeadOutcome, Message, Reply

    merge_ids = [lead.id for lead in merge_leads]
    if not merge_ids:
        return

    # Delete child records that reference lead_id without CASCADE
    db.query(BookingLink).filter(BookingLink.lead_id.in_(merge_ids)).delete(synchronize_session=False)
    db.query(LeadOutcome).filter(LeadOutcome.lead_id.in_(merge_ids)).delete(synchronize_session=False)
    db.query(Message).filter(Message.lead_id.in_(merge_ids)).delete(synchronize_session=False)
    db.query(Reply).filter(Reply.lead_id.in_(merge_ids)).delete(synchronize_session=False)

    # Clear self-referential duplicate_of_lead_id on any leads pointing to ones being deleted
    db.query(Lead).filter(Lead.duplicate_of_lead_id.in_(merge_ids)).update(
        {"duplicate_of_lead_id": None, "is_duplicate": False},
        synchronize_session=False
    )

    # Now safe to delete the lead rows
    db.query(Lead).filter(Lead.id.in_(merge_ids)).delete(synchronize_session=False)


def _apply_contact_registry_after_contact_fix(db: Session, lead: Lead) -> None:
    """
    Re-run the existing dedup normalization after a manual phone correction.

    If the corrected phone + normalized last name already belongs to another
    registry entry in the same org, mark this lead as duplicate of that original.
    Otherwise, update/create this lead's registry footprint.
    """
    normalized_phone = normalize_phone(lead.phone or "")
    normalized_last = normalize_last_name(lead.last_name or "")

    if not normalized_phone or not normalized_last:
        lead.is_duplicate = False
        lead.duplicate_of_lead_id = None
        return

    existing = (
        db.query(ContactRegistry)
        .filter(
            ContactRegistry.organization_id == lead.organization_id,
            ContactRegistry.normalized_phone == normalized_phone,
            ContactRegistry.normalized_last_name == normalized_last,
            ContactRegistry.first_seen_lead_id != lead.id,
        )
        .first()
    )
    if existing:
        lead.is_duplicate = True
        lead.duplicate_of_lead_id = existing.first_seen_lead_id
        return

    own_entry = (
        db.query(ContactRegistry)
        .filter(ContactRegistry.organization_id == lead.organization_id, ContactRegistry.first_seen_lead_id == lead.id)
        .first()
    )
    if own_entry:
        own_entry.normalized_phone = normalized_phone
        own_entry.normalized_last_name = normalized_last
        own_entry.owning_user_id = lead.assigned_to_id
    else:
        db.add(
            ContactRegistry(
                organization_id=lead.organization_id,
                normalized_phone=normalized_phone,
                normalized_last_name=normalized_last,
                first_seen_lead_id=lead.id,
                owning_user_id=lead.assigned_to_id,
            )
        )
    lead.is_duplicate = False
    lead.duplicate_of_lead_id = None


@router.get("/leads/potential-duplicates", response_model=list[PotentialDuplicateGroup])
def potential_duplicate_leads(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Find likely duplicate leads using tiered confidence matching:

    Tier 1 — PHONE match: same normalized phone number.
              Strongest signal. Always grouped.

    Tier 2 — PHONE + LAST NAME: same phone AND same last name.
              Redundant with Tier 1 in most cases, but surfaces conflicts.

    Tier 3 — LAST NAME + SOURCE YEAR: same normalized last name AND same
              source_year. Only grouped if source_year is set on both leads.
              Avoids grouping every "Jones" across all years.

    Last name only (no phone, no year) is intentionally excluded — too noisy.

    Existing import-caught duplicates (Lead.is_duplicate=True) are excluded
    since they're already flagged.
    """
    leads = (
        db.query(Lead)
        .filter(
            Lead.organization_id == current_user.organization_id,
            (Lead.is_duplicate == False) | (Lead.is_duplicate.is_(None)),
        )
        .order_by(Lead.created_at.desc())
        .limit(5000)
        .all()
    )

    # Tier 1: group by phone
    phone_groups: dict[str, list[Lead]] = defaultdict(list)
    # Tier 3: group by last_name + source_year (only when year is set)
    name_year_groups: dict[tuple[str, int], list[Lead]] = defaultdict(list)

    for lead in leads:
        phone_key = normalize_phone(lead.phone or lead.phone_raw or "")
        last_key = normalize_last_name(lead.last_name or "")

        if phone_key:
            phone_groups[phone_key].append(lead)

        if last_key and lead.source_year:
            name_year_groups[(last_key, lead.source_year)].append(lead)

    results = []
    # Track which lead IDs are already in a phone group to avoid
    # showing the same leads again in a name+year group
    leads_in_phone_groups: set[str] = set()

    # Tier 1 — phone matches (highest confidence)
    for phone_key, group_leads in phone_groups.items():
        if len(group_leads) < 2:
            continue
        for l in group_leads:
            leads_in_phone_groups.add(l.id)
        results.append({
            "match_type": "phone",
            "match_key": phone_key,
            "leads": [_lead_summary(l) for l in group_leads],
        })

    # Tier 3 — last name + source year (only leads NOT already in a phone group)
    for (last_key, year), group_leads in name_year_groups.items():
        # Filter out leads already surfaced by phone grouping
        new_leads = [l for l in group_leads if l.id not in leads_in_phone_groups]
        if len(new_leads) < 2:
            continue
        results.append({
            "match_type": "name_year",
            "match_key": f"{last_key} ({year})",
            "leads": [_lead_summary(l) for l in new_leads],
        })

    # Sort: phone matches first (most actionable), then name+year
    results.sort(key=lambda r: (0 if r["match_type"] == "phone" else 1, r["match_key"]))

    return results


@router.post("/leads/merge", response_model=MergeLeadsResponse)
def merge_leads(
    req: MergeLeadsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Merge duplicate leads by moving history to the kept lead, then deleting the
    duplicate lead rows. All work is committed once at the end. Any error rolls
    the session back so no partial merge state is left behind.
    """
    if not req.merge_lead_ids:
        raise HTTPException(status_code=400, detail="At least one lead must be selected to merge.")
    if req.keep_lead_id in req.merge_lead_ids:
        raise HTTPException(status_code=400, detail="A lead cannot be merged into itself.")
    if len(set(req.merge_lead_ids)) != len(req.merge_lead_ids):
        raise HTTPException(status_code=400, detail="Duplicate merge lead ids are not allowed.")

    try:
        keep_lead = (
            db.query(Lead)
            .filter(Lead.id == req.keep_lead_id, Lead.organization_id == current_user.organization_id)
            .first()
        )
        if not keep_lead:
            raise HTTPException(status_code=404, detail="Lead to keep was not found in this organization.")

        merge_leads = (
            db.query(Lead)
            .filter(Lead.id.in_(req.merge_lead_ids), Lead.organization_id == current_user.organization_id)
            .all()
        )
        found_ids = {lead.id for lead in merge_leads}
        missing_ids = [lead_id for lead_id in req.merge_lead_ids if lead_id not in found_ids]
        if missing_ids:
            raise HTTPException(status_code=404, detail="One or more merge leads were not found in this organization.")

        merge_ids = [lead.id for lead in merge_leads]

        keep_has_cadence = db.query(CadenceState).filter(CadenceState.lead_id == keep_lead.id).first() is not None
        merge_cadence_states = db.query(CadenceState).filter(CadenceState.lead_id.in_(merge_ids)).all()
        if (keep_has_cadence and merge_cadence_states) or len(merge_cadence_states) > 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot merge cadence history because CadenceState is one-to-one and multiple cadence records would point to the kept lead.",
            )

        moved_messages = db.query(Message).filter(Message.lead_id.in_(merge_ids)).update(
            {Message.lead_id: keep_lead.id}, synchronize_session=False
        )
        moved_replies = db.query(Reply).filter(Reply.lead_id.in_(merge_ids)).update(
            {Reply.lead_id: keep_lead.id}, synchronize_session=False
        )
        moved_outcomes = db.query(LeadOutcome).filter(LeadOutcome.lead_id.in_(merge_ids)).update(
            {LeadOutcome.lead_id: keep_lead.id}, synchronize_session=False
        )

        moved_cadence_states = 0
        for cadence_state in merge_cadence_states:
            cadence_state.lead_id = keep_lead.id
            moved_cadence_states += 1

        # Registry rows pointing at merged leads should follow the kept survivor.
        db.query(ContactRegistry).filter(ContactRegistry.first_seen_lead_id.in_(merge_ids)).update(
            {ContactRegistry.first_seen_lead_id: keep_lead.id}, synchronize_session=False
        )

        _delete_merged_lead_records(db, merge_leads)
        db.flush()
        db.commit()

        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.merge", target_type="lead", target_id=keep_lead.id,
            details={
                "kept_lead_id": keep_lead.id,
                "merged_lead_ids": merge_ids,
                "moved_messages": moved_messages,
                "moved_replies": moved_replies,
                "moved_outcomes": moved_outcomes,
                "moved_cadence_states": moved_cadence_states,
            },
        )

        return MergeLeadsResponse(
            keep_lead_id=keep_lead.id,
            merged_count=len(merge_leads),
            moved_messages=moved_messages,
            moved_replies=moved_replies,
            moved_cadence_states=moved_cadence_states,
            moved_outcomes=moved_outcomes,
            deleted_lead_ids=merge_ids,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Lead merge failed and was rolled back: {exc}") from exc


@router.patch("/leads/{lead_id}/fix-contact-info")
def fix_lead_contact_info(
    lead_id: str,
    req: FixContactInfoRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Correct a lead's phone/email/name while respecting org isolation and
    dedup normalization.

    Name fields were added alongside phone/email per Mike's explicit
    feedback that Lead Cleanup didn't actually let him "clean up anything"
    about a lead - a misspelled name matters here specifically, since
    duplicate-group matching (see /admin/leads/potential-duplicates) keys
    on normalized last_name. A typo'd last name could both cause a false
    duplicate match against an unrelated lead, AND prevent a real
    duplicate from being caught in the first place.
    """
    if req.phone is None and req.email is None and req.first_name is None and req.last_name is None:
        raise HTTPException(status_code=400, detail="Provide at least one field to update.")

    lead = (
        db.query(Lead)
        .filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id)
        .first()
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found in this organization.")

    before = {"first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone, "email": lead.email}

    # Track whether the registry needs re-syncing - true if EITHER phone
    # or last_name changes, since the registry's match key is built from
    # both together. Re-syncing only on phone change (the original
    # behavior) left a stale registry entry any time just the name was
    # corrected.
    registry_needs_resync = False

    if req.phone is not None:
        normalized = normalize_phone(req.phone)
        if not normalized:
            raise HTTPException(status_code=400, detail="Phone could not be normalized.")
        lead.phone_raw = req.phone
        lead.phone = normalized
        registry_needs_resync = True

    if req.email is not None:
        lead.email = req.email.strip() or None

    if req.first_name is not None:
        cleaned_first = req.first_name.strip()
        lead.first_name = cleaned_first or None

    if req.last_name is not None:
        cleaned_last = req.last_name.strip()
        if not cleaned_last:
            raise HTTPException(status_code=400, detail="last_name cannot be blank.")
        lead.last_name = cleaned_last
        registry_needs_resync = True

    if registry_needs_resync:
        _apply_contact_registry_after_contact_fix(db, lead)

    db.commit()
    db.refresh(lead)

    after = {"first_name": lead.first_name, "last_name": lead.last_name, "phone": lead.phone, "email": lead.email}
    changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
    if changed:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="lead.fix_contact_info", target_type="lead", target_id=lead.id,
            details=changed,
        )

    return _lead_summary(lead)


# ---------------------------------------------------------------------------
# Provision Client — super_admin only
# Creates a new Organization + supervisor (org_admin) account in one shot.
# ---------------------------------------------------------------------------

class ProvisionClientRequest(BaseModel):
    org_name: str
    org_slug: str           # url-safe identifier e.g. "acme-funeral"
    industry: str = "funeral"
    plan: str = "trial"
    supervisor_full_name: str
    supervisor_email: EmailStr
    supervisor_password: str | None = None  # if omitted, a temp password is generated


class ProvisionClientResponse(BaseModel):
    org_id: str
    org_name: str
    supervisor_id: str
    supervisor_email: str
    temp_password: str | None   # returned only when we generated one
    message: str


@router.post("/provision-client", response_model=ProvisionClientResponse)
def provision_client(
    req: ProvisionClientRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    One-shot endpoint: creates the org and its first supervisor account.
    Restricted to super_admin (Mike) only.
    """
    # Slug uniqueness check
    existing = db.query(Organization).filter(Organization.slug == req.org_slug).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Slug '{req.org_slug}' is already taken.")

    # Email uniqueness check
    existing_user = db.query(User).filter(User.email == req.supervisor_email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail=f"Email '{req.supervisor_email}' is already registered.")

    # Create org
    new_org = Organization(
        name=req.org_name,
        slug=req.org_slug,
        industry=req.industry,
        plan=req.plan,
        is_active=True,
    )
    db.add(new_org)
    db.flush()  # get new_org.id before creating user

    # Password — use provided or generate temp
    generated_password = None
    if req.supervisor_password:
        raw_password = req.supervisor_password
    else:
        generated_password = _generate_temp_password()
        raw_password = generated_password

    new_supervisor = User(
        organization_id=new_org.id,
        email=req.supervisor_email,
        password_hash=hash_password(raw_password),
        full_name=req.supervisor_full_name,
        role="org_admin",
        is_active=True,
        must_change_password=True,
    )
    db.add(new_supervisor)
    db.commit()
    db.refresh(new_org)
    db.refresh(new_supervisor)

    try:
        log_action(
            db, current_user.organization_id, current_user.id,
            action="provision_client",
            target_type="organization",
            target_id=new_org.id,
            details={
                "org_name": new_org.name,
                "org_slug": new_org.slug,
                "supervisor_email": new_supervisor.email,
                "created_by": current_user.full_name,
            },
        )
    except Exception:
        pass  # audit log failure should never block provisioning

    return ProvisionClientResponse(
        org_id=new_org.id,
        org_name=new_org.name,
        supervisor_id=new_supervisor.id,
        supervisor_email=new_supervisor.email,
        temp_password=generated_password,
        message=f"Client '{req.org_name}' provisioned successfully.",
    )


@router.get("/organizations")
def list_organizations(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """List all orgs — super_admin only, for the provision client page."""
    orgs = db.query(Organization).order_by(Organization.created_at.desc()).all()
    return [
        {
            "id": o.id,
            "name": o.name,
            "slug": o.slug,
            "industry": o.industry,
            "plan": o.plan,
            "is_active": o.is_active,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in orgs
    ]
