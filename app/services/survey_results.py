"""Survey answers, for the people who act on them.

Every answer is kept on the person's record (GET /survey/results/{lead_id}).
For an outreach program (SCI) the dashboard also shows the totals, the latest
answers, and ALERTS: 1-3 star answers nobody has marked handled yet.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

LOW_RATING = 3   # 1-3 stars is an alert


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None


def _item(r, lead, loc_names):
    name = ""
    if lead is not None:
        name = ("%s %s" % (lead.first_name or "", lead.last_name or "")).strip().title()
    return {
        "id": r.id, "lead_id": r.lead_id, "name": name or None,
        "location_id": getattr(r, "location_id", None),
        "location": loc_names.get(getattr(r, "location_id", None)),
        "rating": r.rating, "feedback": r.feedback,
        "share_ok": bool(getattr(r, "share_ok", False)),
        "submitted_at": _iso(r.submitted_at),
        "handled_at": _iso(getattr(r, "handled_at", None)),
        "alert": bool(r.rating is not None and r.rating <= LOW_RATING
                      and getattr(r, "handled_at", None) is None),
    }


def _query(db: Session, org_id: str, location_id: Optional[str]):
    from app.models.models import Lead, SurveyResponse
    q = (db.query(SurveyResponse, Lead).join(Lead, Lead.id == SurveyResponse.lead_id)
         .filter(Lead.organization_id == org_id))
    if location_id:
        q = q.filter(SurveyResponse.location_id == location_id)
    return q


def _location_names(db: Session, org_id: str) -> dict:
    from app.models.program_models import LocationProfile
    return {p.location_id: p.official_name for p in
            db.query(LocationProfile).filter(LocationProfile.organization_id == org_id).all()}


def for_org(db: Session, org_id: str, location_id: Optional[str] = None, limit: int = 25) -> dict:
    from app.models.models import SurveyResponse
    rows = _query(db, org_id, location_id).order_by(SurveyResponse.submitted_at.desc()).all()
    names = _location_names(db, org_id)
    items = [_item(r, lead, names) for r, lead in rows]
    rated = [i["rating"] for i in items if i["rating"]]
    return {
        "summary": {
            "count": len(items),
            "average": round(sum(rated) / len(rated), 2) if rated else None,
            "stars": {str(s): sum(1 for x in rated if x == s) for s in range(1, 6)},
            "share_ok": sum(1 for i in items if i["share_ok"]),
        },
        "alerts": [i for i in items if i["alert"]],
        "recent": items[:limit],
    }


def alert_count(db: Session, org_id: str, location_id: Optional[str] = None) -> int:
    from app.models.models import SurveyResponse
    return (_query(db, org_id, location_id)
            .filter(SurveyResponse.rating.isnot(None), SurveyResponse.rating <= LOW_RATING,
                    SurveyResponse.handled_at.is_(None)).count())


def mark_handled(db: Session, org_id: str, response_id: str, user_id: str) -> Optional[dict]:
    from app.models.models import SurveyResponse
    hit = _query(db, org_id, None).filter(SurveyResponse.id == response_id).first()
    if hit is None:
        return None
    r, lead = hit
    if r.handled_at is None:
        r.handled_at = datetime.utcnow()
        r.handled_by = user_id
        db.commit()
    return _item(r, lead, _location_names(db, org_id))
