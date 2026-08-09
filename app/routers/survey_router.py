"""
Survey router — post-appointment satisfaction survey.

GET  /survey/{token}  → Returns a branded HTML survey page (no auth needed)
POST /survey/{token}  → Stores the lead's responses in survey_responses table
GET  /survey/results/{lead_id} → Returns survey results for a lead (advisor auth required)
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user
from app.models.models import (
    BookingFollowup, Lead, Organization, SurveyResponse, User, gen_uuid
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/survey", tags=["survey"])


class SurveySubmission(BaseModel):
    rating: Optional[int] = None          # 1-5
    feedback: Optional[str] = None
    facebook_handle: Optional[str] = None
    instagram_handle: Optional[str] = None


def _get_survey_context(db: Session, token: str):
    """Resolve token → followup, lead, advisor, org. Raises 404 if invalid."""
    followup = db.query(BookingFollowup).filter(
        BookingFollowup.survey_token == token
    ).first()
    if not followup:
        raise HTTPException(status_code=404, detail="Survey not found")

    lead = db.query(Lead).filter(Lead.id == followup.lead_id).first()
    advisor = db.query(User).filter(User.id == followup.advisor_id).first()
    org = db.query(Organization).filter(
        Organization.id == (advisor.organization_id if advisor else None)
    ).first() if advisor else None

    return followup, lead, advisor, org


def _already_submitted(db: Session, followup_id: str) -> bool:
    return db.query(SurveyResponse).filter(
        SurveyResponse.booking_followup_id == followup_id
    ).first() is not None


def _social_links_html(org: Organization) -> str:
    """Build social links row using org-level social URLs."""
    if not org:
        return ""
    links = []
    if getattr(org, "facebook_url", None):
        links.append(
            f'<a href="{org.facebook_url}" target="_blank" style="'
            'display:inline-block;margin:0 8px;padding:10px 20px;background:#1877f2;'
            'color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">👍 Facebook</a>'
        )
    if getattr(org, "google_review_url", None):
        links.append(
            f'<a href="{org.google_review_url}" target="_blank" style="'
            'display:inline-block;margin:0 8px;padding:10px 20px;background:#ea4335;'
            'color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">⭐ Google Review</a>'
        )
    if getattr(org, "instagram_url", None):
        links.append(
            f'<a href="{org.instagram_url}" target="_blank" style="'
            'display:inline-block;margin:0 8px;padding:10px 20px;background:#e1306c;'
            'color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">📸 Instagram</a>'
        )
    if getattr(org, "linkedin_url", None):
        links.append(
            f'<a href="{org.linkedin_url}" target="_blank" style="'
            'display:inline-block;margin:0 8px;padding:10px 20px;background:#0a66c2;'
            'color:#fff;text-decoration:none;border-radius:6px;font-size:14px;">💼 LinkedIn</a>'
        )
    if not links:
        return ""
    return (
        '<div style="margin:24px 0;text-align:center;">'
        '<p style="color:#64748b;font-size:14px;margin-bottom:12px;">Connect with us:</p>'
        + "".join(links) +
        "</div>"
    )


@router.get("/results/{lead_id}")
def get_survey_results(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return all survey responses for a lead. Advisor auth required."""
    lead = db.query(Lead).filter(
        Lead.id == lead_id,
        Lead.organization_id == current_user.organization_id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    responses = (
        db.query(SurveyResponse)
        .filter(SurveyResponse.lead_id == lead_id)
        .order_by(SurveyResponse.submitted_at.desc())
        .all()
    )

    return {
        "lead_id": lead_id,
        "responses": [
            {
                "id": r.id,
                "rating": r.rating,
                "feedback": r.feedback,
                "facebook_handle": r.facebook_handle,
                "instagram_handle": r.instagram_handle,
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            }
            for r in responses
        ],
    }


@router.get("/{token}", response_class=HTMLResponse)
def get_survey_page(token: str, db: Session = Depends(get_db)):
    """Serve the branded survey HTML page to the lead."""
    followup, lead, advisor, org = _get_survey_context(db, token)
    already_done = _already_submitted(db, followup.id)

    org_name = org.name if org else "our team"
    advisor_name = advisor.full_name if advisor else "Your Advisor"
    first_name = lead.first_name if lead else "there"
    primary_color = (org.brand_color_primary if org else None) or "#2fb6ff"
    social_html = _social_links_html(org)

    if already_done:
        body_content = f"""
        <div style="text-align:center;padding:40px 0;">
          <div style="font-size:48px;margin-bottom:16px;">✅</div>
          <h2 style="color:#1e293b;">Thanks, {first_name}!</h2>
          <p style="color:#64748b;">Your feedback has already been submitted. We really appreciate it!</p>
          {social_html}
        </div>"""
    else:
        body_content = f"""
        <h2 style="color:#1e293b;margin-bottom:4px;">How did we do, {first_name}?</h2>
        <p style="color:#64748b;margin-bottom:28px;font-size:15px;">
          Your feedback helps {advisor_name} and {org_name} serve families better.
          It only takes 30 seconds.
        </p>

        <form id="survey-form" action="/survey/{token}" method="POST" onsubmit="submitSurvey(event)">

          <!-- Star rating -->
          <div style="margin-bottom:24px;">
            <label style="display:block;font-weight:600;color:#1e293b;margin-bottom:10px;">
              Overall experience
            </label>
            <div id="stars" style="font-size:36px;cursor:pointer;letter-spacing:4px;">
              <span onclick="setRating(1)">☆</span>
              <span onclick="setRating(2)">☆</span>
              <span onclick="setRating(3)">☆</span>
              <span onclick="setRating(4)">☆</span>
              <span onclick="setRating(5)">☆</span>
            </div>
            <input type="hidden" id="rating-input" name="rating" value="">
          </div>

          <!-- Feedback -->
          <div style="margin-bottom:24px;">
            <label style="display:block;font-weight:600;color:#1e293b;margin-bottom:8px;">
              Any comments? (optional)
            </label>
            <textarea name="feedback" rows="3" placeholder="Tell us about your experience..."
              style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;
              font-size:14px;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
          </div>

          <!-- Social handles (soft ask) -->
          <div style="margin-bottom:24px;background:#f8fafc;border-radius:10px;padding:16px;">
            <p style="margin:0 0 12px;font-size:14px;color:#64748b;">
              Want to stay connected? Drop your social handle and we'll follow you back! (totally optional)
            </p>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
              <input type="text" name="facebook_handle" placeholder="Facebook name"
                style="flex:1;min-width:140px;padding:8px 12px;border:1px solid #e2e8f0;
                border-radius:6px;font-size:13px;" />
              <input type="text" name="instagram_handle" placeholder="@instagram"
                style="flex:1;min-width:140px;padding:8px 12px;border:1px solid #e2e8f0;
                border-radius:6px;font-size:13px;" />
            </div>
          </div>

          {social_html}

          <button type="submit" style="width:100%;padding:14px;background:{primary_color};
            color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;
            cursor:pointer;transition:opacity .2s;" onmouseover="this.style.opacity='.85'"
            onmouseout="this.style.opacity='1'">
            Submit Feedback
          </button>
        </form>

        <div id="success-msg" style="display:none;text-align:center;padding:32px 0;">
          <div style="font-size:48px;margin-bottom:16px;">🙏</div>
          <h2 style="color:#1e293b;">Thank you, {first_name}!</h2>
          <p style="color:#64748b;">Your feedback means the world to us.</p>
          {social_html}
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>How did we do? — {org_name}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
         background:#f1f5f9;min-height:100vh;display:flex;align-items:flex-start;
         justify-content:center;padding:32px 16px; }}
  .card {{ background:#fff;border-radius:16px;padding:36px;max-width:500px;width:100%;
           box-shadow:0 4px 24px rgba(0,0,0,.08); }}
  .brand {{ color:{primary_color};font-size:13px;font-weight:700;letter-spacing:.5px;
            text-transform:uppercase;margin-bottom:20px; }}
</style>
</head>
<body>
<div class="card">
  <div class="brand">{org_name}</div>
  {body_content}
</div>
<script>
let currentRating = 0;
function setRating(n) {{
  currentRating = n;
  document.getElementById('rating-input').value = n;
  const stars = document.querySelectorAll('#stars span');
  stars.forEach((s, i) => s.textContent = i < n ? '★' : '☆');
}}
async function submitSurvey(e) {{
  e.preventDefault();
  const form = e.target;
  const data = new FormData(form);
  const payload = {{
    rating: parseInt(data.get('rating')) || null,
    feedback: data.get('feedback') || null,
    facebook_handle: data.get('facebook_handle') || null,
    instagram_handle: data.get('instagram_handle') || null,
  }};
  await fetch('/survey/{token}', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify(payload),
  }});
  form.style.display = 'none';
  document.getElementById('success-msg').style.display = 'block';
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/{token}")
def submit_survey(token: str, payload: SurveySubmission, db: Session = Depends(get_db)):
    """Store survey response from a lead. Idempotent — silently ignores duplicates."""
    followup, lead, advisor, _ = _get_survey_context(db, token)

    if _already_submitted(db, followup.id):
        return {"success": True, "note": "already_submitted"}

    if payload.rating is not None and not (1 <= payload.rating <= 5):
        raise HTTPException(status_code=422, detail="Rating must be 1-5")

    response = SurveyResponse(
        id=gen_uuid(),
        booking_followup_id=followup.id,
        lead_id=followup.lead_id,
        advisor_id=followup.advisor_id,
        rating=payload.rating,
        feedback=payload.feedback,
        facebook_handle=payload.facebook_handle,
        instagram_handle=payload.instagram_handle,
    )
    db.add(response)
    db.commit()
    logger.info("Survey submitted lead=%s rating=%s", followup.lead_id, payload.rating)
    return {"success": True}
