"""Shared helpers for the insurance-agency vertical. Org-scoped, no globals."""
import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.agency_models import AgencyAgentProfile, AgencyDistributionConfig, AgencyProspectProfile
from app.models.models import Lead, User

ALL_FACTORS = ["jurisdiction", "specialization", "availability", "workload",
               "response_performance", "prior_decline"]
DEFAULT_RECRUIT_STAGES = ["lead", "candidate", "interview", "licensing", "onboarding",
                          "training", "active_agent"]
NEED_LABELS = {
    "family_protection": "Family Protection",
    "retirement": "Retirement Planning",
    "living_benefits": "Living Benefits",
    "business_owner": "Business Owner Planning",
    "final_expense": "Final Expense",
    "mortgage_protection": "Mortgage Protection",
    "wealth_transfer": "Wealth Transfer",
}
CLOSED_LEAD_STATUSES = ("dnc", "dead", "lost", "closed")


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(ts) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.replace(microsecond=0).isoformat() + "Z"
    if isinstance(ts, date):
        return ts.isoformat()
    return str(ts)


def jload(raw, default=None):
    if raw is None or raw == "":
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def jdump(val) -> Optional[str]:
    return None if val is None else json.dumps(val)


def lead_name(lead: Lead) -> str:
    n = " ".join(p for p in ((lead.first_name or "").strip(), (lead.last_name or "").strip()) if p)
    return n or (lead.email or lead.phone or "Unnamed prospect")


def lead_is_demo(lead: Lead, profile: Optional[AgencyProspectProfile] = None) -> bool:
    return bool(getattr(lead, "is_test", False) or (profile is not None and profile.is_demo))


def get_config(db: Session, org_id: str, create: bool = True) -> AgencyDistributionConfig:
    cfg = db.query(AgencyDistributionConfig).filter(
        AgencyDistributionConfig.organization_id == org_id).first()
    if cfg is None:
        cfg = AgencyDistributionConfig(organization_id=org_id, acceptance_timeout_minutes=30,
                                       max_active_per_agent=25, stalled_days=7,
                                       response_target_minutes=60, review_window_days=30,
                                       workload_alert_pct=90,
                                       factors_enabled=jdump(ALL_FACTORS),
                                       recruit_stages=jdump(DEFAULT_RECRUIT_STAGES))
        if create:
            db.add(cfg)
            db.flush()
    return cfg


def config_dict(cfg: AgencyDistributionConfig) -> Dict[str, Any]:
    return {
        "acceptance_timeout_minutes": cfg.acceptance_timeout_minutes,
        "escalation_user_id": cfg.escalation_user_id,
        "max_active_per_agent": cfg.max_active_per_agent,
        "factors_enabled": jload(cfg.factors_enabled, list(ALL_FACTORS)),
        "stalled_days": cfg.stalled_days,
        "response_target_minutes": cfg.response_target_minutes,
        "review_window_days": cfg.review_window_days,
        "workload_alert_pct": cfg.workload_alert_pct,
        "recruit_stages": jload(cfg.recruit_stages, list(DEFAULT_RECRUIT_STAGES)),
        "available_factors": list(ALL_FACTORS),
    }


def profile_for(db: Session, org_id: str, lead_id: str) -> Optional[AgencyProspectProfile]:
    return db.query(AgencyProspectProfile).filter(
        AgencyProspectProfile.organization_id == org_id,
        AgencyProspectProfile.lead_id == lead_id).first()


def org_agents(db: Session, org_id: str) -> List[User]:
    """Users of this org who can work prospects (advisors + managers), active or not."""
    return (db.query(User).filter(User.organization_id == org_id,
                                  User.role.in_(("advisor", "org_admin", "super_admin")))
            .order_by(User.full_name).all())


def agent_profiles(db: Session, org_id: str) -> Dict[str, AgencyAgentProfile]:
    return {p.user_id: p for p in db.query(AgencyAgentProfile).filter(
        AgencyAgentProfile.organization_id == org_id).all()}


def user_names(db: Session, ids) -> Dict[str, str]:
    ids = [i for i in set(ids) if i]
    if not ids:
        return {}
    return {u.id: u.full_name for u in db.query(User.id, User.full_name).filter(User.id.in_(ids))}


def ref(uid: Optional[str], names: Dict[str, str]):
    return {"id": uid, "name": names.get(uid)} if uid else None


def paginate(items: List[Any], page: int, per_page: int) -> Dict[str, Any]:
    per_page = max(1, min(int(per_page or 50), 200))
    page = max(1, int(page or 1))
    start = (page - 1) * per_page
    return {"items": items[start:start + per_page], "total": len(items),
            "page": page, "per_page": per_page}
