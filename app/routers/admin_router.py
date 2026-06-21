from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, EmailStr
import secrets

from app.deps import get_db, require_admin
from app.models.models import User, Lead, Message, Reply, LeadStatus
from app.services.auth_service import hash_password

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
            "tier": lead.tier.value if lead.tier else None,
            "status": lead.status.value if lead.status else None,
            "assigned_to_id": lead.assigned_to_id,
            "assigned_to_name": advisors_by_id.get(lead.assigned_to_id, "Unassigned"),
            "created_at": lead.created_at,
        }
        results.append(lead_dict)
    return results


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

    return ResetPasswordResponse(email=target.email, temp_password=temp_password)


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
            "tier": l.tier.value if l.tier else None,
            "engagement_temperature": l.engagement_temperature.value if l.engagement_temperature else None,
            "created_at": l.created_at,
        }
        for l in leads
    ]
