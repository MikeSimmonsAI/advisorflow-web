"""
Survey router — post-appointment satisfaction survey.

GET  /survey/{token}              → Branded HTML survey page (no auth)
POST /survey/{token}              → Store rating + feedback
GET  /survey/results/{lead_id}    → Survey results for a lead (auth required)

Success page logic:
  4-5 stars → "Thank you! Here's how to spread the word" + Google/social links
  1-3 stars → "We're sorry — we'll reach out to make it right"
  No rating → generic thank-you
"""

import html
import logging
import re
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
    rating: Optional[int] = None
    feedback: Optional[str] = None
    facebook_handle: Optional[str] = None
    instagram_handle: Optional[str] = None


def _get_survey_context(db: Session, token: str):
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


_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?$')


def _safe_url(url: str | None) -> str | None:
    """Return url only if it starts with http/https; else None."""
    if url and url.lower().startswith(("http://", "https://")):
        return html.escape(url, quote=True)
    return None


def _safe_color(color: str | None) -> str:
    """Return color only if it's a valid hex code; else a safe default."""
    if color and _HEX_COLOR_RE.match(color):
        return color
    return "#2fb6ff"


def _review_links_html(org: Organization) -> str:
    """High-rating: show Google + social links. All URLs are scheme-validated."""
    if not org:
        return ""
    links = []
    google_url = _safe_url(getattr(org, "google_review_url", None))
    if google_url:
        links.append(
            f'<a href="{google_url}" target="_blank" rel="noopener noreferrer" style="'
            'display:inline-block;margin:6px;padding:12px 22px;background:#ea4335;'
            'color:#fff;text-decoration:none;border-radius:8px;font-size:15px;font-weight:600;">⭐ Leave a Google Review</a>'
        )
    facebook_url = _safe_url(getattr(org, "facebook_url", None))
    if facebook_url:
        links.append(
            f'<a href="{facebook_url}" target="_blank" rel="noopener noreferrer" style="'
            'display:inline-block;margin:6px;padding:12px 22px;background:#1877f2;'
            'color:#fff;text-decoration:none;border-radius:8px;font-size:15px;">👍 Facebook</a>'
        )
    instagram_url = _safe_url(getattr(org, "instagram_url", None))
    if instagram_url:
        links.append(
            f'<a href="{instagram_url}" target="_blank" rel="noopener noreferrer" style="'
            'display:inline-block;margin:6px;padding:12px 22px;background:#e1306c;'
            'color:#fff;text-decoration:none;border-radius:8px;font-size:15px;">📸 Instagram</a>'
        )
    if not links:
        return ""
    return (
        '<div style="margin-top:20px;text-align:center;">'
        '<p style="color:#64748b;font-size:14px;margin-bottom:12px;">'
        'Help others find us — it only takes a second:</p>'
        + "".join(links) + "</div>"
    )


@router.get("/results/{lead_id}")
def get_survey_results(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
            }
            for r in responses
        ],
    }


@router.get("/{token}/context")
def get_survey_context_json(token: str, db: Session = Depends(get_db)):
    """The same survey, as JSON, for the branded public page.

    Public - the token is the whole authorization, exactly as for the HTML
    page below. That page stays: links already sent to families point at it,
    and breaking a live link to tidy an architecture is not a trade worth
    making. New links go to the branded route on the organization's own
    domain, which renders from this.

    Deliberately narrow. A family needs their first name, the business's name
    and its colour. Nothing about the advisor, the lead record, the
    appointment history or the organization's internals is in this payload,
    so a mistake in the page cannot expose what was never sent.
    """
    followup, lead, advisor, org = _get_survey_context(db, token)

    from app.services.public_identity import identity_for_org, public_branding
    _org_id = (org.id if org else None) or getattr(lead, "organization_id", None)
    ident = identity_for_org(db, _org_id)
    branding = public_branding(db, _org_id)

    return {
        "token": token,
        "already_submitted": _already_submitted(db, followup.id),
        "first_name": (lead.first_name if lead else "") or "there",
        # The BUSINESS, never the platform.
        "business_name": ident.customer_facing_name or (org.name if org else "our team"),
        "business_phone": ident.business_phone,
        # Same resolved block the booking page uses, so the two pages a family
        # sees in one week cannot end up branded differently.
        "branding": branding,
        "brand_color": _safe_color(branding["brand_color"]
                                   or (getattr(org, "brand_color_primary", None) if org else None)),
        "review_url": getattr(org, "google_review_url", None) if org else None,
        "facebook_url": getattr(org, "facebook_url", None) if org else None,
        "instagram_url": getattr(org, "instagram_url", None) if org else None,
    }


@router.get("/{token}", response_class=HTMLResponse)
def get_survey_page(token: str, db: Session = Depends(get_db)):
    """The original backend-rendered survey. Kept deliberately.

    Links already sent to families point here. Retiring it to tidy the
    architecture would break live links in messages already delivered, which
    is a worse outcome than serving two pages for a while. New links go to
    the branded route on the organization's own domain.
    """
    followup, lead, advisor, org = _get_survey_context(db, token)
    already_done = _already_submitted(db, followup.id)

    # HTML-escape user-derived strings before interpolating into the page.
    org_name = html.escape(org.name if org else "our team")
    first_name = html.escape(lead.first_name if lead else "there")
    # Validate hex color to prevent CSS injection via brand_color_primary.
    primary_color = _safe_color(getattr(org, "brand_color_primary", None) if org else None)
    review_links = _review_links_html(org)

    if already_done:
        body_content = f"""
        <div style="text-align:center;padding:40px 0;">
          <div style="font-size:48px;margin-bottom:16px;">✅</div>
          <h2 style="color:#1e293b;">Thanks, {first_name}!</h2>
          <p style="color:#64748b;">Your feedback has already been submitted. We really appreciate it!</p>
          {review_links}
        </div>"""
    else:
        body_content = f"""
        <h2 style="color:#1e293b;margin-bottom:4px;">How did we do, {first_name}?</h2>
        <p style="color:#64748b;margin-bottom:28px;font-size:15px;">
          Your feedback helps {org_name} serve you better. Takes 30 seconds.
        </p>
        <div id="survey-form">
          <div style="margin-bottom:24px;">
            <label style="display:block;font-weight:600;color:#1e293b;margin-bottom:10px;">
              Overall experience
            </label>
            <div id="stars" style="font-size:40px;cursor:pointer;letter-spacing:6px;user-select:none;">
              <span onclick="setRating(1)" onmouseover="hoverRating(1)" onmouseout="hoverRating(0)">☆</span>
              <span onclick="setRating(2)" onmouseover="hoverRating(2)" onmouseout="hoverRating(0)">☆</span>
              <span onclick="setRating(3)" onmouseover="hoverRating(3)" onmouseout="hoverRating(0)">☆</span>
              <span onclick="setRating(4)" onmouseover="hoverRating(4)" onmouseout="hoverRating(0)">☆</span>
              <span onclick="setRating(5)" onmouseover="hoverRating(5)" onmouseout="hoverRating(0)">☆</span>
            </div>
          </div>
          <div style="margin-bottom:24px;">
            <label style="display:block;font-weight:600;color:#1e293b;margin-bottom:8px;">
              Comments? (optional)
            </label>
            <textarea id="feedback-box" rows="3" placeholder="Tell us about your experience..."
              style="width:100%;padding:10px;border:1px solid #e2e8f0;border-radius:8px;
              font-size:14px;font-family:inherit;resize:vertical;box-sizing:border-box;"></textarea>
          </div>
          <button onclick="submitSurvey()" style="width:100%;padding:14px;background:{primary_color};
            color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:600;
            cursor:pointer;transition:opacity .2s;" onmouseover="this.style.opacity='.85'"
            onmouseout="this.style.opacity='1'">
            Submit Feedback
          </button>
        </div>
        <div id="success-high" style="display:none;text-align:center;padding:32px 0;">
          <div style="font-size:48px;margin-bottom:12px;">🙏</div>
          <h2 style="color:#1e293b;">Thank you, {first_name}!</h2>
          <p style="color:#64748b;margin-bottom:8px;">We're so glad you had a great experience.</p>
          {review_links}
        </div>
        <div id="success-low" style="display:none;text-align:center;padding:32px 0;">
          <div style="font-size:48px;margin-bottom:12px;">😔</div>
          <h2 style="color:#1e293b;">We're sorry to hear that, {first_name}.</h2>
          <p style="color:#64748b;">Your feedback means a lot to us. A member of our team
          will follow up with you shortly to make things right.</p>
        </div>
        <div id="success-any" style="display:none;text-align:center;padding:32px 0;">
          <div style="font-size:48px;margin-bottom:12px;">✅</div>
          <h2 style="color:#1e293b;">Thanks, {first_name}!</h2>
          <p style="color:#64748b;">Your feedback has been received. We appreciate you!</p>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>How did we do? — {org_name}</title>
<style>
  * {{ box-sizing:border-box; }}
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
function hoverRating(n) {{
  if (currentRating > 0) return;
  const stars = document.querySelectorAll('#stars span');
  stars.forEach((s, i) => s.textContent = i < n ? '★' : '☆');
}}
function setRating(n) {{
  currentRating = n;
  const stars = document.querySelectorAll('#stars span');
  stars.forEach((s, i) => s.textContent = i < n ? '★' : '☆');
}}
async function submitSurvey() {{
  const rating = currentRating;
  const feedback = document.getElementById('feedback-box')?.value || null;
  const payload = {{ rating: rating || null, feedback: feedback || null }};
  try {{
    await fetch('/survey/{token}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(payload),
    }});
  }} catch(e) {{}}
  document.getElementById('survey-form').style.display = 'none';
  if (rating >= 4) {{
    document.getElementById('success-high').style.display = 'block';
  }} else if (rating > 0) {{
    document.getElementById('success-low').style.display = 'block';
  }} else {{
    document.getElementById('success-any').style.display = 'block';
  }}
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/{token}")
def submit_survey(token: str, payload: SurveySubmission, db: Session = Depends(get_db)):
    """Store survey response. Idempotent — silently ignores duplicates."""
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
