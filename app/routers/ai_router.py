from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import json

from app.deps import get_db, get_current_user
from app.models.models import User, Lead
from app.services.ai_analysis_service import analyze_lead, analyze_batch

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/analyze/{lead_id}")
def analyze_single_lead(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Runs AI quality analysis on one lead and stores the result on
    lead.ai_lead_quality_note. Called on-demand from the Lead Detail
    page rather than automatically on every import, since running this
    on hundreds of leads at once would be slow and burns through the
    OpenAI key's rate limit fast (see DEPLOY.md note on the 429 issue).
    """
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    result = analyze_lead(db, lead)
    return result


@router.post("/analyze-batch")
def analyze_lead_batch(lead_ids: list[str], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Analyzes a batch of leads at once - e.g. for a 'analyze all needs-review
    leads' button. Capped at 25 per call to avoid hammering the rate-limited
    OpenAI key in one request; call again for more.
    """
    if len(lead_ids) > 25:
        raise HTTPException(status_code=400, detail="Batch limited to 25 leads per call to avoid rate limit issues.")

    leads = db.query(Lead).filter(
        Lead.id.in_(lead_ids), Lead.organization_id == current_user.organization_id
    ).all()
    results = analyze_batch(db, leads)
    return {"analyzed_count": len(results), "results": results}


@router.get("/quality/{lead_id}")
def get_lead_quality(lead_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Returns the most recently stored AI analysis for a lead, without re-running it."""
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.organization_id == current_user.organization_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    if not lead.ai_lead_quality_note:
        return {"analyzed": False}
    try:
        parsed = json.loads(lead.ai_lead_quality_note)
        return {"analyzed": True, **parsed}
    except json.JSONDecodeError:
        return {"analyzed": True, "raw": lead.ai_lead_quality_note}
