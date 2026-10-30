"""
Survey router — post-appointment satisfaction survey.

GET  /survey/{token}              → Branded HTML survey page (no auth)
POST /survey/{token}              → Store rating + feedback
GET  /survey/results/{lead_id}    → Survey results for a lead (auth required)

After an answer (#405):
  every rating sees the SAME neutral, optional public-review invitation (no
  review gating: a public review is never asked of happy families only);
  1-3 stars also alert the responsible team (survey_dispatch.notify_low_rating);
  the page never promises a follow-up the system does not create and track.
"""

import html
import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.deps import get_db, get_current_user, require_admin
from app.models.models import (
    BookingFollowup, Lead, Organization, SurveyResponse, User, gen_uuid
)
from app.services.lead_scope import (authorized_lead_query, load_lead_in_scope, assert_leads_in_scope, reject_ownership_fields)
from app.utils.time_fmt import iso_utc  # S19: explicit-UTC timestamps

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/survey", tags=["survey"])
from app.limiter import limiter


class SurveySubmission(BaseModel):
    rating: Optional[int] = None
    feedback: Optional[str] = None
    facebook_handle: Optional[str] = None
    instagram_handle: Optional[str] = None
    share_ok: Optional[bool] = None


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


def public_links(db: Session, org: Organization, lead) -> dict:
    """The public review/social links for THIS family's business. A program
    contact (SCI) sees only its own location's review page - never the
    corporation's pages, which name the wrong place. Unvalidated -> absent."""
    from app.services.survey_identity import location_identity
    loc = location_identity(db, lead) if lead is not None else None
    if loc is not None:
        url = None
        if loc.get("location_id"):
            from app.models.program_models import LocationProfile
            prof = (db.query(LocationProfile)
                    .filter(LocationProfile.organization_id == lead.organization_id,
                            LocationProfile.location_id == loc["location_id"]).first())
            url = getattr(prof, "review_url", None) if prof is not None else None
        return {"review_url": url if _safe_url(url) else None, "facebook_url": None, "instagram_url": None}
    out = {}
    for k, attr in (("review_url", "google_review_url"), ("facebook_url", "facebook_url"),
                    ("instagram_url", "instagram_url")):
        v = getattr(org, attr, None) if org else None
        out[k] = v if _safe_url(v) else None
    return out


def _review_links_html(links: dict) -> str:
    """Optional public links, shown the SAME way whatever the rating."""
    links = links or {}
    out = []
    google_url = _safe_url(links.get("review_url"))
    if google_url:
        out.append(
            f'<a href="{google_url}" target="_blank" rel="noopener noreferrer" style="'
            'display:inline-block;margin:6px;padding:12px 22px;background:#ea4335;'
            'color:#fff;text-decoration:none;border-radius:8px;font-size:15px;font-weight:600;">Google</a>'
        )
    facebook_url = _safe_url(links.get("facebook_url"))
    if facebook_url:
        out.append(
            f'<a href="{facebook_url}" target="_blank" rel="noopener noreferrer" style="'
            'display:inline-block;margin:6px;padding:12px 22px;background:#1877f2;'
            'color:#fff;text-decoration:none;border-radius:8px;font-size:15px;">👍 Facebook</a>'
        )
    instagram_url = _safe_url(links.get("instagram_url"))
    if instagram_url:
        out.append(
            f'<a href="{instagram_url}" target="_blank" rel="noopener noreferrer" style="'
            'display:inline-block;margin:6px;padding:12px 22px;background:#e1306c;'
            'color:#fff;text-decoration:none;border-radius:8px;font-size:15px;">📸 Instagram</a>'
        )
    if not out:
        return ""
    return (
        '<div style="margin-top:20px;text-align:center;">'
        '<p style="color:#64748b;font-size:14px;margin-bottom:12px;">'
        'If you would like, you can also share your experience publicly:</p>'
        + "".join(out) + "</div>"
    )


@router.get("/results/{lead_id}")
def get_survey_results(
    lead_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    lead = authorized_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    responses = (
        db.query(SurveyResponse)
        .filter(SurveyResponse.lead_id == lead_id)
        .order_by(SurveyResponse.submitted_at.desc())
        .all()
    )
    _loc_names = {}
    if any(r.location_id for r in responses):
        from app.models.program_models import LocationProfile
        _loc_names = {p.location_id: p.official_name for p in db.query(LocationProfile).filter(
            LocationProfile.organization_id == lead.organization_id).all()}
    return {
        "lead_id": lead_id,
        "responses": [
            {
                "id": r.id,
                "rating": r.rating,
                "feedback": r.feedback,
                "submitted_at": iso_utc(r.submitted_at),
                "share_ok": bool(r.share_ok),
                "location": _loc_names.get(r.location_id),
                "handled_at": iso_utc(r.handled_at) if r.handled_at else None,
            }
            for r in responses
        ],
    }


def _workspace_org(db: Session, user: User, request: Request) -> str:
    from app.services.lead_scope import active_workspace_org_id
    org_id = active_workspace_org_id(user, db, request) or user.organization_id
    if not org_id:
        raise HTTPException(status_code=400, detail="Open a workspace first")
    return org_id


@router.get("/admin/attention")
def surveys_needing_attention(request: Request, db: Session = Depends(get_db),
                              user: User = Depends(require_admin)):
    """#405: held, failed, stuck or undelivered surveys for a person to review."""
    from app.services import survey_dispatch
    org_id = _workspace_org(db, user, request)
    return {"sending_enabled": survey_dispatch.sending_enabled(),
            "surveys": survey_dispatch.attention(db, org_id)}


@router.post("/admin/attention/{followup_id}/retry")
def retry_survey(followup_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    """#405: controlled recovery - only after checking nothing reached the family."""
    from app.services import survey_dispatch
    org_id = _workspace_org(db, user, request)
    out = survey_dispatch.retry(db, followup_id, org_id)
    if not out.get("ok") and out.get("reason") == "not found":
        raise HTTPException(status_code=404, detail="Survey not found")
    return out


@limiter.limit("60/minute")
@router.get("/{token}/context")
def get_survey_context_json(request: Request, token: str, db: Session = Depends(get_db)):
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
    business_name = ident.customer_facing_name or (org.name if org else "our team")
    business_phone = ident.business_phone
    # OUTREACH PROGRAM (SCI): the family dealt with one funeral home or
    # cemetery. Its name and ITS phone only - never the corporate name or a
    # person's number. Unresolved location: no name, no number.
    from app.services.survey_identity import location_identity, proper_name
    loc = location_identity(db, lead)
    if loc is not None:
        business_name = loc["name"] or ""
        business_phone = loc["phone"]
        branding = dict(branding, name=business_name, phone=business_phone or "", logo_url="",
                        address="", tagline="", document_title=business_name)

    return {
        "token": token,
        "already_submitted": _already_submitted(db, followup.id),
        "first_name": proper_name(lead.first_name if lead else "") or "there",
        # The BUSINESS, never the platform.
        "business_name": business_name,
        "business_phone": business_phone,
        # Same resolved block the booking page uses, so the two pages a family
        # sees in one week cannot end up branded differently.
        "branding": branding,
        "brand_color": _safe_color(branding["brand_color"]
                                   or (getattr(org, "brand_color_primary", None) if org else None)),
        # Neutral and the same for every rating (#405); a program contact
        # gets only its own location's review page.
        **public_links(db, org, lead),
    }


@limiter.limit("60/minute")
@router.get("/{token}", response_class=HTMLResponse)
def get_survey_page(request: Request, token: str, db: Session = Depends(get_db)):
    """The original backend-rendered survey. Kept deliberately.

    Links already sent to families point here. Retiring it to tidy the
    architecture would break live links in messages already delivered, which
    is a worse outcome than serving two pages for a while. New links go to
    the branded route on the organization's own domain.
    """
    followup, lead, advisor, org = _get_survey_context(db, token)
    # Links already delivered land here. The branded page is the one that is
    # kept current (location name, location phone), so send them there.
    try:
        from fastapi.responses import RedirectResponse
        from app.services.public_identity import survey_url as _branded_survey_url
        _org_id = (org.id if org else None) or getattr(lead, "organization_id", None)
        _to = _branded_survey_url(db, _org_id, token)
        if _to:
            return RedirectResponse(_to, status_code=302)
    except Exception as exc:   # the page below is still a working fallback
        logger.warning("survey: branded redirect failed for followup %s: %s", followup.id, type(exc).__name__)
    already_done = _already_submitted(db, followup.id)

    # HTML-escape user-derived strings before interpolating into the page.
    # A program contact (SCI): its own funeral home / cemetery, never the corporation.
    from app.services.survey_identity import location_identity as _loc_ident
    _loc = _loc_ident(db, lead) if lead is not None else None
    org_name = html.escape((_loc or {}).get("name") or "our team") if _loc is not None \
        else html.escape(org.name if org else "our team")
    first_name = html.escape(lead.first_name if lead else "there")
    # Validate hex color to prevent CSS injection via brand_color_primary.
    primary_color = _safe_color(getattr(org, "brand_color_primary", None) if org else None)
    review_links = _review_links_html(public_links(db, org, lead))

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
        <p id="survey-error" style="display:none;color:#b42318;margin-top:12px;"></p>
        <div id="success-any" style="display:none;text-align:center;padding:32px 0;">
          <div style="font-size:48px;margin-bottom:12px;">✅</div>
          <h2 style="color:#1e293b;">Thank you, {first_name}.</h2>
          <p style="color:#64748b;">Your answer went straight to our team at {org_name}.</p>
          {review_links}
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
let sending = false;
async function submitSurvey() {{
  if (sending) return;
  const rating = currentRating;
  const err = document.getElementById('survey-error');
  if (!rating) {{ err.textContent = 'Please choose 1 to 5 stars.'; err.style.display = 'block'; return; }}
  const feedback = document.getElementById('feedback-box')?.value || null;
  const payload = {{ rating: rating, feedback: feedback || null }};
  sending = true;
  err.style.display = 'none';
  try {{
    const res = await fetch('/survey/{token}', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify(payload),
    }});
    if (!res.ok) throw new Error('save failed');
  }} catch(e) {{
    // Never show "thank you" for an answer that was not saved.
    sending = false;
    err.textContent = 'We could not save your answer. Please try again in a moment.';
    err.style.display = 'block';
    return;
  }}
  document.getElementById('survey-form').style.display = 'none';
  document.getElementById('success-any').style.display = 'block';
}}
</script>
</body>
</html>"""
    return HTMLResponse(content=html)


@limiter.limit("60/minute")
@router.post("/{token}")
def submit_survey(request: Request, token: str, payload: SurveySubmission, db: Session = Depends(get_db)):
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
        share_ok=bool(payload.share_ok) if payload.share_ok is not None else None,
    )
    try:
        from app.services.survey_identity import location_identity
        _loc = location_identity(db, lead)
        if _loc and _loc.get("location_id"):
            response.location_id = _loc["location_id"]
    except Exception as exc:   # never lose the answer over the location lookup
        logger.warning("survey: location lookup failed lead=%s: %s", followup.lead_id, exc)
    db.add(response)
    db.commit()
    logger.info("Survey submitted lead=%s rating=%s", followup.lead_id, payload.rating)
    notified = False
    try:
        from app.services import survey_dispatch
        notified = survey_dispatch.notify_low_rating(db, response)
    except Exception as exc:   # the answer is saved; the alert failure is logged
        db.rollback()
        logger.error("survey: low-rating alert failed response=%s: %s", response.id, type(exc).__name__)
    return {"success": True, "team_notified": notified}
