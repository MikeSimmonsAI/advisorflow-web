"""
Fiber Internet Service Provider — Customer Intake Form
-------------------------------------------------------
Public-facing form where a fiber ISP's potential customers fill out their
info. Submissions create a Lead record in the org's BookaBoost CRM with
status='new', tier='prospect', and the fiber-specific fields stored in
service_address and extra_data (JSON).

Routes:
  GET  /intake/fiber/{org_token}   — renders the HTML intake form
  POST /intake/fiber/{org_token}   — processes submission, creates lead,
                                     returns thank-you HTML page

The org_token is the org's social_webhook_token (auto-generated the first
time an advisor visits Settings → Social/Intake). This keeps the URL
shareable but not guessable.
"""

import html
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import Lead, Organization

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/intake", tags=["fiber-intake"])

# ── Shared CSS / styles used by both pages ─────────────────────────────────────
_BASE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: #f0f4ff;
  min-height: 100vh;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 32px 16px 64px;
}
.card {
  background: #fff;
  border-radius: 16px;
  box-shadow: 0 4px 32px rgba(0,0,0,0.10);
  max-width: 540px;
  width: 100%;
  padding: 40px 36px 36px;
}
.logo-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 28px;
}
.logo-icon {
  width: 44px; height: 44px;
  background: linear-gradient(135deg, #1565c0, #42a5f5);
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 22px;
}
.logo-text { font-size: 20px; font-weight: 700; color: #0d1b3e; }
.logo-sub  { font-size: 12px; color: #6b7a99; font-weight: 500; }
h1 { font-size: 22px; font-weight: 700; color: #0d1b3e; margin-bottom: 6px; }
.subtitle { font-size: 14px; color: #6b7a99; margin-bottom: 28px; line-height: 1.5; }
.form-row { margin-bottom: 18px; }
.form-row-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 18px; }
label { display: block; font-size: 13px; font-weight: 600; color: #374151; margin-bottom: 5px; }
input, select {
  width: 100%;
  padding: 10px 13px;
  border: 1.5px solid #d1d9f0;
  border-radius: 8px;
  font-size: 14px;
  color: #111;
  background: #f8faff;
  transition: border-color 0.15s;
  outline: none;
}
input:focus, select:focus { border-color: #1565c0; background: #fff; }
.consent-box {
  background: #f0f4ff;
  border: 1.5px solid #c5d3f0;
  border-radius: 10px;
  padding: 14px 16px;
  margin: 20px 0;
  font-size: 13px;
  color: #374151;
  line-height: 1.55;
}
.consent-row {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-top: 10px;
}
.consent-row input[type=checkbox] {
  width: 17px; height: 17px;
  flex-shrink: 0;
  margin-top: 2px;
  accent-color: #1565c0;
  cursor: pointer;
}
.btn-submit {
  width: 100%;
  padding: 13px;
  background: linear-gradient(135deg, #1565c0, #1976d2);
  color: #fff;
  font-size: 15px;
  font-weight: 700;
  border: none;
  border-radius: 10px;
  cursor: pointer;
  margin-top: 8px;
  transition: opacity 0.15s;
}
.btn-submit:hover { opacity: 0.92; }
.required { color: #e53e3e; }
.footer {
  text-align: center;
  margin-top: 22px;
  font-size: 12px;
  color: #9aa3b8;
}
.footer a { color: #1565c0; text-decoration: none; }
@media (max-width: 480px) {
  .card { padding: 28px 18px 24px; }
  .form-row-2 { grid-template-columns: 1fr; }
}
"""


def _get_org(db: Session, org_token: str) -> Organization:
    org = db.query(Organization).filter(
        Organization.social_webhook_token == org_token
    ).first()
    return org


def _render_form(org_name: str, org_token: str, error: str = "") -> str:
    # HTML-escape org_name and error so stored values can't inject markup.
    safe_org_name = html.escape(org_name)
    safe_error = html.escape(error)
    error_html = f'<div style="color:#c0392b;background:#fff5f5;border:1px solid #feb2b2;border-radius:8px;padding:10px 14px;margin-bottom:18px;font-size:13px;">{safe_error}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Get Connected — {safe_org_name}</title>
<style>{_BASE_CSS}</style>
</head>
<body>
<div class="card">
  <div class="logo-row">
    <div class="logo-icon">📡</div>
    <div>
      <div class="logo-text">{safe_org_name}</div>
      <div class="logo-sub">Fiber Internet Services</div>
    </div>
  </div>

  <h1>Check Availability in Your Area</h1>
  <p class="subtitle">Fill out the form below and a member of our team will reach out to confirm service availability and walk you through our plans.</p>

  {error_html}

  <form method="POST" action="/intake/fiber/{org_token}">

    <div class="form-row-2">
      <div class="form-row">
        <label for="first_name">First Name <span class="required">*</span></label>
        <input type="text" id="first_name" name="first_name" required placeholder="Jane">
      </div>
      <div class="form-row">
        <label for="last_name">Last Name <span class="required">*</span></label>
        <input type="text" id="last_name" name="last_name" required placeholder="Smith">
      </div>
    </div>

    <div class="form-row-2">
      <div class="form-row">
        <label for="phone">Phone Number <span class="required">*</span></label>
        <input type="tel" id="phone" name="phone" required placeholder="(555) 000-0000">
      </div>
      <div class="form-row">
        <label for="email">Email Address</label>
        <input type="email" id="email" name="email" placeholder="jane@email.com">
      </div>
    </div>

    <div class="form-row">
      <label for="service_address">Service Address <span class="required">*</span></label>
      <input type="text" id="service_address" name="service_address" required placeholder="123 Main St, City, State 00000">
    </div>

    <div class="form-row-2">
      <div class="form-row">
        <label for="current_provider">Current Internet Provider</label>
        <input type="text" id="current_provider" name="current_provider" placeholder="e.g. Spectrum, AT&T">
      </div>
      <div class="form-row">
        <label for="current_speed">Current Speed</label>
        <select id="current_speed" name="current_speed">
          <option value="">Not sure</option>
          <option value="under_100mb">Under 100 Mbps</option>
          <option value="100_250mb">100–250 Mbps</option>
          <option value="250_500mb">250–500 Mbps</option>
          <option value="500mb_1gb">500 Mbps–1 Gig</option>
          <option value="over_1gb">Over 1 Gig</option>
        </select>
      </div>
    </div>

    <div class="form-row-2">
      <div class="form-row">
        <label for="interested_tier">Interested Plan Speed</label>
        <select id="interested_tier" name="interested_tier">
          <option value="">Not sure yet</option>
          <option value="500mb">500 Mbps</option>
          <option value="1gb">1 Gig</option>
          <option value="2gb">2 Gig</option>
          <option value="business">Business Plan</option>
        </select>
      </div>
      <div class="form-row">
        <label for="best_contact_time">Best Time to Reach You</label>
        <select id="best_contact_time" name="best_contact_time">
          <option value="">No preference</option>
          <option value="morning">Morning (8am–12pm)</option>
          <option value="afternoon">Afternoon (12pm–5pm)</option>
          <option value="evening">Evening (5pm–8pm)</option>
        </select>
      </div>
    </div>

    <div class="consent-box">
      <strong>SMS Communication Consent</strong><br>
      By checking the box below, you consent to receive SMS text messages from {safe_org_name} regarding your service inquiry, availability updates, and scheduling. Message frequency varies. Message and data rates may apply. Reply STOP to opt out at any time.
      <div class="consent-row">
        <input type="checkbox" id="sms_consent" name="sms_consent" value="yes" required>
        <label for="sms_consent" style="margin:0;font-weight:400;">
          I agree to receive SMS messages from {safe_org_name}. I understand I can reply STOP at any time to opt out.
        </label>
      </div>
    </div>

    <button type="submit" class="btn-submit">Check My Availability →</button>
  </form>

  <div class="footer">
    By submitting this form you agree to our
    <a href="/privacy-policy" target="_blank">Privacy Policy</a> &amp;
    <a href="/terms" target="_blank">Terms</a>.
    Your information is never sold or shared.
  </div>
</div>
</body>
</html>"""


def _render_thankyou(org_name: str) -> str:
    safe_org_name = html.escape(org_name)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Thank You — {safe_org_name}</title>
<style>{_BASE_CSS}
.check {{ font-size: 56px; text-align: center; margin-bottom: 16px; }}
h1 {{ text-align: center; }}
.subtitle {{ text-align: center; }}
</style>
</head>
<body>
<div class="card" style="text-align:center;padding-top:52px;">
  <div class="check">✅</div>
  <h1>You're on our list!</h1>
  <p class="subtitle" style="margin-bottom:28px;">
    Thanks for your interest in {safe_org_name} fiber service.<br>
    A member of our team will reach out shortly to confirm availability at your address and walk you through the best plan for your needs.
  </p>
  <p style="font-size:13px;color:#9aa3b8;">You can close this window. We'll be in touch soon!</p>
</div>
</body>
</html>"""


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/fiber/{org_token}", response_class=HTMLResponse)
def fiber_intake_form(org_token: str, db: Session = Depends(get_db)):
    """Render the public-facing fiber customer intake form."""
    org = _get_org(db, org_token)
    org_name = org.name if org else "Our Fiber Service"
    return HTMLResponse(content=_render_form(org_name, org_token))


@router.post("/fiber/{org_token}", response_class=HTMLResponse)
def fiber_intake_submit(
    org_token: str,
    db: Session = Depends(get_db),
    first_name: str = Form(...),
    last_name: str = Form(...),
    phone: str = Form(...),
    email: str = Form(None),
    service_address: str = Form(...),
    current_provider: str = Form(None),
    current_speed: str = Form(None),
    interested_tier: str = Form(None),
    best_contact_time: str = Form(None),
    sms_consent: str = Form(None),
):
    """Process fiber intake form submission and create a lead."""
    org = _get_org(db, org_token)
    org_name = org.name if org else "Our Fiber Service"

    # Validate required fields
    if not first_name.strip() or not phone.strip() or not service_address.strip():
        return HTMLResponse(content=_render_form(
            org_name, org_token,
            error="Please fill in all required fields (name, phone, service address)."
        ))

    if sms_consent != "yes":
        return HTMLResponse(content=_render_form(
            org_name, org_token,
            error="Please check the SMS consent box to continue."
        ))

    if not org:
        logger.warning("fiber_intake: invalid org_token %s", org_token)
        return HTMLResponse(content=_render_thankyou("Our Team"))

    # Dedup by phone within org
    existing = db.query(Lead).filter(
        Lead.organization_id == org.id,
        Lead.phone == phone.strip(),
    ).first()

    if not existing:
        # Find the first advisor in the org to assign to
        row = db.execute(
            text(
                "SELECT id FROM users "
                "WHERE organization_id = :org_id AND role IN ('advisor','admin') "
                "ORDER BY created_at ASC LIMIT 1"
            ),
            {"org_id": org.id},
        ).fetchone()
        assigned_user_id = row[0] if row else None

        extra = {
            k: v for k, v in {
                "current_provider": current_provider,
                "current_speed": current_speed,
                "interested_tier": interested_tier,
                "best_contact_time": best_contact_time,
                "sms_consent": sms_consent,
                "consent_timestamp": datetime.utcnow().isoformat(),
            }.items() if v
        }

        lead = Lead(
            id=str(uuid.uuid4()),
            organization_id=org.id,
            user_id=assigned_user_id,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            phone=phone.strip(),
            email=email.strip() if email else None,
            service_address=service_address.strip(),
            extra_data=json.dumps(extra),
            source="fiber_intake",
            status="new",
            tier="prospect",
            message_track="new_inquiry_intro",
        )
        db.add(lead)
        db.commit()
        logger.info("fiber_intake: created lead %s for org %s", lead.id, org.id)
    else:
        # Update service address + extra data if they re-submit
        existing.service_address = service_address.strip()
        db.commit()
        logger.info("fiber_intake: deduped to existing lead %s", existing.id)

    return HTMLResponse(content=_render_thankyou(org_name))
