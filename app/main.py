"""
BookaBoost Web - Main Application Entry Point

Run locally:
    uvicorn app.main:app --reload --port 8000

Deploy target: Render or Railway (see DEPLOY.md)
Required env vars: DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, BOOKING_BASE_URL
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from app.deps import engine
from app.models.models import Base
from app.routers import (
    auth_router, leads_router, sms_router, admin_router,
    cadence_router, email_router, calendar_router, notification_router,
    settings_router, templates_router, ai_router, outcomes_router, microsoft_router,
    compliance_router, audit_log_router, sample_data_router,
    health_router, workqueue_router, campaign_router,
    google_contacts_router,
)
from app.routers.objection_router import router as objection_router

app = FastAPI(title="BookaBoost", version="0.1.0-phase1")

ALLOWED_ORIGINS = [
    "https://advisorflow-frontend.onrender.com",
    "https://bookaboost.com",
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Public compliance pages - registered FIRST so nothing else intercepts them.
# Required for Twilio A2P 10DLC campaign registration.

@app.get("/privacy-policy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy - BookaBoost</title>
<style>
  body{font-family:Arial,sans-serif;max-width:800px;margin:40px auto;padding:0 24px;color:#222;line-height:1.7}
  h1{color:#0a0a1a}h2{color:#1a2a4a;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:6px}
  .brand{color:#1565c0;font-weight:800}
</style>
</head>
<body>
<h1><span class="brand">BookaBoost</span> Privacy Policy</h1>
<p><strong>Last updated: July 2026</strong></p>
<p>This Privacy Policy describes how BookaBoost collects, uses, and protects personal information in connection with our SMS appointment scheduling and outreach messaging program.</p>
<h2>Information We Collect</h2>
<p>We collect your name and mobile phone number when you voluntarily provide them to a BookaBoost advisor during an in-person consultation, phone inquiry, or scheduled appointment.</p>
<h2>How We Use Your Information</h2>
<p>We use your mobile phone number solely to send SMS messages related to appointment scheduling, reminders, and follow-up communications regarding services you have expressed interest in.</p>
<h2>SMS Messaging Program</h2>
<p>By providing your mobile phone number to a BookaBoost advisor, you consent to receive SMS text messages regarding your account, appointments, and related services. Message frequency varies. Standard message and data rates may apply.</p>
<p><strong>To opt out:</strong> Reply STOP to any message at any time.</p>
<p><strong>For help:</strong> Reply HELP to any message or contact us at info@bookaboost.com.</p>
<h2>Data Sharing</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for marketing or promotional purposes. Your mobile opt-in data and consent will not be sold, rented, or transferred to any third party at any time.</strong></p>
<h2>Data Security</h2>
<p>We implement appropriate technical and organizational measures to protect your personal information against unauthorized access, alteration, disclosure, or destruction.</p>
<h2>Contact Us</h2>
<p>BookaBoost | Dallas, TX | Phone: 469-553-7417 | Email: info@bookaboost.com | bookaboost.com</p>
</body>
</html>""")


@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms_and_conditions():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Terms and Conditions - BookaBoost</title>
<style>
  body{font-family:Arial,sans-serif;max-width:800px;margin:40px auto;padding:0 24px;color:#222;line-height:1.7}
  h1{color:#0a0a1a}h2{color:#1a2a4a;margin-top:32px;border-bottom:1px solid #ddd;padding-bottom:6px}
  .brand{color:#1565c0;font-weight:800}
</style>
</head>
<body>
<h1><span class="brand">BookaBoost</span> SMS Program - Terms and Conditions</h1>
<p><strong>Last updated: July 2026</strong></p>
<p>These Terms and Conditions govern your participation in the BookaBoost SMS appointment scheduling and outreach messaging program.</p>
<h2>Program Description</h2>
<p>BookaBoost operates an SMS messaging program to send appointment scheduling messages, reminders, and follow-up communications to customers and prospects who have provided their mobile phone number to a BookaBoost advisor.</p>
<h2>Consent to Receive Messages</h2>
<p>By providing your mobile phone number to a BookaBoost advisor, you consent to receive recurring SMS text messages related to your account, appointments, and related services. Consent is not required as a condition of any purchase.</p>
<h2>Message Frequency</h2>
<p>Message frequency varies. You may receive multiple messages per month.</p>
<h2>Message and Data Rates</h2>
<p><strong>Message and data rates may apply.</strong> Check with your mobile carrier for details.</p>
<h2>How to Opt Out</h2>
<p><strong>Reply STOP</strong> to any message at any time. You will receive one final confirmation and no further messages will be sent.</p>
<h2>How to Get Help</h2>
<p><strong>Reply HELP</strong> to any message, or contact BookaBoost: Phone: 469-553-7417 | Email: info@bookaboost.com</p>
<h2>Carriers</h2>
<p>Mobile carriers are not liable for delayed or undelivered messages.</p>
<h2>Privacy</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for marketing or promotional purposes at any time.</strong></p>
<p>See our full <a href="/privacy-policy">Privacy Policy</a> for complete details.</p>
<h2>Contact</h2>
<p>BookaBoost | Dallas, TX | Phone: 469-553-7417 | Email: info@bookaboost.com | bookaboost.com</p>
</body>
</html>""")


@app.get("/sms-consent-evidence", response_class=HTMLResponse, include_in_schema=False)
def sms_consent_evidence():
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SMS Consent Evidence - Restland Cemetery &amp; Funeral Home</title>
<style>
  body{font-family:Arial,sans-serif;max-width:900px;margin:40px auto;padding:0 24px;color:#222;line-height:1.7}
  h1{color:#0a0a1a}h2{color:#1a2a4a;margin-top:32px;border-bottom:2px solid #1565c0;padding-bottom:6px}
  .box{background:#f5f8ff;border:1px solid #c0d0ee;border-radius:8px;padding:20px 24px;margin:16px 0}
  .box p{margin:6px 0}
  .label{font-weight:700;color:#1a2a4a}
  .consent-text{background:#fff;border:2px solid #1565c0;border-radius:6px;padding:16px;margin:12px 0;font-size:15px}
  .checkbox-row{display:flex;align-items:flex-start;gap:10px;margin:10px 0}
  .checkbox-row input{margin-top:4px;width:16px;height:16px;flex-shrink:0}
  .step{background:#e8f0fe;border-left:4px solid #1565c0;padding:12px 16px;margin:12px 0;border-radius:0 6px 6px 0}
  table{width:100%;border-collapse:collapse;margin:12px 0}
  td,th{border:1px solid #ddd;padding:10px 14px;text-align:left}
  th{background:#1a2a4a;color:#fff}
</style>
</head>
<body>
<h1>SMS Consent Evidence</h1>
<p><strong>Business:</strong> Restland Cemetery &amp; Funeral Home, Dallas, TX</p>
<p><strong>SMS Program:</strong> Appointment scheduling and follow-up outreach via Restland Family Service Advisors</p>
<p><strong>Document purpose:</strong> This page documents all opt-in paths used for the A2P 10DLC SMS campaign and is provided for TCR campaign review purposes.</p>

<h2>Opt-In Path 1 — Website Form</h2>
<div class="box">
  <p class="label">How it works:</p>
  <p>Prospective customers visit the Restland booking page and submit a contact form. The form includes an <strong>unchecked</strong> SMS consent checkbox that the user must actively check before submitting.</p>
  <p class="label">Form URL:</p>
  <p><a href="https://advisorflow-backend.onrender.com/book">https://advisorflow-backend.onrender.com/book</a></p>
  <p class="label">Consent language displayed on the form:</p>
  <div class="consent-text">
    <div class="checkbox-row">
      <input type="checkbox" disabled>
      <span>I agree to receive SMS text messages from Restland Cemetery &amp; Funeral Home regarding appointment scheduling and funeral or cemetery planning services. Message frequency varies. Message and data rates may apply. Reply STOP to opt out at any time. Reply HELP for assistance. View our <a href="https://advisorflow-backend.onrender.com/privacy-policy">Privacy Policy</a> and <a href="https://advisorflow-backend.onrender.com/terms">Terms &amp; Conditions</a>.</span>
    </div>
  </div>
  <p><em>The checkbox is unchecked by default. The user must actively check it to provide consent. Form cannot be submitted without completing this field.</em></p>
</div>

<h2>Opt-In Path 2 — Verbal Consent (In-Person or Phone)</h2>
<div class="box">
  <p class="label">How it works:</p>
  <p>Restland Family Service Advisors collect verbal consent from customers during in-person consultations, phone inquiries, or scheduled file review appointments.</p>
  <p class="label">Verbal disclosure script used by advisors:</p>
  <div class="step">
    "With your permission, we'd like to send you follow-up text messages regarding your appointment and our services. These messages are sent by our scheduling system and message and data rates may apply. You can opt out at any time by replying STOP. Do you consent to receive these text messages?"
  </div>
  <p class="label">Consent documentation:</p>
  <p>Advisor records verbal consent in the BookaBoost platform at the time of collection. The timestamp and advisor ID are logged in the system.</p>
</div>

<h2>Required Disclosures Present in Both Opt-In Paths</h2>
<table>
  <tr><th>Required Element</th><th>Present</th><th>Location</th></tr>
  <tr><td>Business name identified</td><td>✅ Yes</td><td>Both paths</td></tr>
  <tr><td>Message frequency disclosure</td><td>✅ Yes</td><td>Both paths</td></tr>
  <tr><td>Message and data rates may apply</td><td>✅ Yes</td><td>Both paths</td></tr>
  <tr><td>STOP opt-out instruction</td><td>✅ Yes</td><td>Both paths</td></tr>
  <tr><td>HELP instruction</td><td>✅ Yes</td><td>Both paths</td></tr>
  <tr><td>Privacy Policy link</td><td>✅ Yes</td><td>Website form + below</td></tr>
  <tr><td>Terms &amp; Conditions link</td><td>✅ Yes</td><td>Website form + below</td></tr>
  <tr><td>Checkbox unchecked by default</td><td>✅ Yes</td><td>Website form</td></tr>
  <tr><td>No third-party data sharing</td><td>✅ Yes</td><td>Privacy Policy</td></tr>
</table>

<h2>Legal Pages</h2>
<div class="box">
  <p><strong>Privacy Policy:</strong> <a href="https://advisorflow-backend.onrender.com/privacy-policy">https://advisorflow-backend.onrender.com/privacy-policy</a></p>
  <p><strong>Terms &amp; Conditions:</strong> <a href="https://advisorflow-backend.onrender.com/terms">https://advisorflow-backend.onrender.com/terms</a></p>
</div>

<h2>Contact</h2>
<div class="box">
  <p><strong>Restland Cemetery &amp; Funeral Home</strong></p>
  <p>13005 Greenville Ave, Dallas, TX 75243</p>
  <p>Phone: 469-553-7417 | Email: info@bookaboost.com</p>
</div>
</body>
</html>""")


# ── Public endpoint for landing page demo requests (no auth required)
@app.get("/leads/demo-request")
def demo_request_docs():
    return {"message": "POST to this endpoint to submit a demo request"}


# ── All app routers
app.include_router(auth_router.router)
app.include_router(leads_router.router)
app.include_router(sms_router.router)
app.include_router(admin_router.router)
app.include_router(cadence_router.router)
app.include_router(email_router.router)
app.include_router(calendar_router.router)
app.include_router(notification_router.router)
app.include_router(settings_router.router)
app.include_router(templates_router.router)
app.include_router(ai_router.router)
app.include_router(outcomes_router.router)
app.include_router(microsoft_router.router)
app.include_router(compliance_router.router)
app.include_router(audit_log_router.router)
app.include_router(sample_data_router.router)
app.include_router(health_router.router)
app.include_router(workqueue_router.router)
app.include_router(campaign_router.router)
app.include_router(google_contacts_router.router)
app.include_router(objection_router)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "1"}
