from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.deps import get_db, require_admin
from app.models.models import User, Lead, Message, Reply, LeadStatus

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
