"""
AdvisorFlow Web - Main Application Entry Point

Run locally:
    uvicorn app.main:app --reload --port 8000

Deploy target: Render or Railway (see DEPLOY.md)
Required env vars: DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, BOOKING_BASE_URL
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.deps import engine
from app.models.models import Base
from app.routers import (
    auth_router, leads_router, sms_router, admin_router,
    cadence_router, email_router, calendar_router, notification_router,
    settings_router, templates_router, ai_router, outcomes_router, microsoft_router,
    compliance_router, sample_data_router,
)

app = FastAPI(title="AdvisorFlow Web", version="0.1.0-phase1")

# Allow the frontend (deployed separately on Render) to call this API.
#
# REAL BUG FIXED HERE: this previously used allow_origins=["*"] combined
# with allow_credentials=True - browsers explicitly reject that exact
# combination as a security measure (wildcard + credentials is invalid
# per the CORS spec), which silently blocked EVERY authenticated request
# from the live frontend. This wasn't introduced by recent changes; it
# was a pre-existing gap flagged by the TODO that used to be here, which
# only became visible once enough pages were making authenticated calls
# for the pattern to be obvious in the browser console.
#
# Listing explicit origins instead - includes the production Render
# frontend domain plus localhost for local development.
ALLOWED_ORIGINS = [
    "https://advisorflow-frontend.onrender.com",
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
app.include_router(sample_data_router.router)


@app.on_event("startup")
def on_startup():
    # Creates tables if they don't exist. For production, replace with
    # Alembic migrations (see migrations/ folder) instead of this auto-create.
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "1"}


from fastapi.responses import HTMLResponse

@app.get("/privacy-policy", response_class=HTMLResponse, include_in_schema=False)
def privacy_policy():
    """
    Public privacy policy page - required for A2P 10DLC campaign
    registration with Twilio / The Campaign Registry (TCR). Must be
    publicly accessible with no login required so TCR's automated
    verification bot can screenshot it.
    """
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Privacy Policy – BookaBoost</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 24px; color: #222; line-height: 1.7; }
  h1 { color: #0a0a1a; font-size: 28px; } h2 { color: #1a2a4a; font-size: 18px; margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 6px; }
  p, li { font-size: 15px; } strong { color: #c0392b; }
  .brand { color: #1565c0; font-weight: 800; }
</style>
</head>
<body>
<h1><span class="brand">BookaBoost</span> Privacy Policy</h1>
<p><strong>Last updated: July 2026</strong></p>
<p>This Privacy Policy describes how BookaBoost ("we," "us," or "our") collects, uses, and protects personal information in connection with our SMS appointment scheduling and outreach messaging program.</p>

<h2>Information We Collect</h2>
<p>We collect your name and mobile phone number when you voluntarily provide them to a BookaBoost advisor or sales consultant during an in-person consultation, phone inquiry, or scheduled appointment. We may also collect appointment preferences and notes related to your service needs.</p>

<h2>How We Use Your Information</h2>
<p>We use your mobile phone number solely to send you SMS messages related to appointment scheduling, reminders, and follow-up communications regarding services you have expressed interest in. We do not use your information for unrelated marketing purposes.</p>

<h2>SMS Messaging Program</h2>
<p>By providing your mobile phone number to a BookaBoost advisor, you consent to receive SMS text messages regarding your account, appointments, and related services. Message frequency varies. Standard message and data rates may apply.</p>
<p><strong>To opt out:</strong> Reply STOP to any message at any time. You will receive one confirmation message and no further messages will be sent.</p>
<p><strong>For help:</strong> Reply HELP to any message or contact us directly at info@bookaboost.com.</p>

<h2>Data Sharing</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for marketing or promotional purposes. Your mobile opt-in data and consent will not be sold, rented, or transferred to any third party at any time.</strong></p>

<h2>Data Security</h2>
<p>We implement appropriate technical and organizational measures to protect your personal information against unauthorized access, alteration, disclosure, or destruction.</p>

<h2>Your Rights</h2>
<p>You may request access to, correction of, or deletion of your personal information at any time by contacting us at info@bookaboost.com.</p>

<h2>Contact Us</h2>
<p>BookaBoost<br>
Dallas, TX<br>
Phone: 469-553-7417<br>
Email: info@bookaboost.com<br>
Website: bookaboost.com</p>
</body>
</html>""")


@app.get("/terms", response_class=HTMLResponse, include_in_schema=False)
def terms_and_conditions():
    """
    Public terms and conditions page - required for A2P 10DLC campaign
    registration. Must be publicly accessible with no login required.
    """
    return HTMLResponse(content="""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Terms and Conditions – BookaBoost SMS Program</title>
<style>
  body { font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 24px; color: #222; line-height: 1.7; }
  h1 { color: #0a0a1a; font-size: 28px; } h2 { color: #1a2a4a; font-size: 18px; margin-top: 32px; border-bottom: 1px solid #ddd; padding-bottom: 6px; }
  p, li { font-size: 15px; } strong { color: #c0392b; }
  .brand { color: #1565c0; font-weight: 800; }
</style>
</head>
<body>
<h1><span class="brand">BookaBoost</span> SMS Program – Terms and Conditions</h1>
<p><strong>Last updated: July 2026</strong></p>
<p>These Terms and Conditions govern your participation in the BookaBoost SMS appointment scheduling and outreach messaging program ("Program").</p>

<h2>Program Description</h2>
<p>BookaBoost operates an SMS messaging program to send appointment scheduling messages, reminders, and follow-up communications to customers and prospects who have provided their mobile phone number to a BookaBoost advisor or sales consultant.</p>

<h2>Consent to Receive Messages</h2>
<p>By providing your mobile phone number to a BookaBoost advisor, you consent to receive recurring SMS text messages related to your account, appointments, and related services. Consent is not required as a condition of any purchase.</p>

<h2>Message Frequency</h2>
<p>Message frequency varies based on your account activity and advisor follow-up schedule. You may receive multiple messages per month.</p>

<h2>Message and Data Rates</h2>
<p><strong>Message and data rates may apply.</strong> Check with your mobile carrier for details regarding your plan's messaging rates.</p>

<h2>How to Opt Out</h2>
<p><strong>To stop receiving messages, reply STOP</strong> to any message at any time. After opting out, you will receive one final confirmation message and no further messages will be sent unless you provide consent again.</p>

<h2>How to Get Help</h2>
<p><strong>For help, reply HELP</strong> to any message, or contact BookaBoost directly:</p>
<p>Phone: 469-553-7417<br>Email: info@bookaboost.com</p>

<h2>Supported Carriers</h2>
<p>Mobile carriers are not liable for delayed or undelivered messages.</p>

<h2>Privacy</h2>
<p><strong>No mobile information will be shared with third parties or affiliates for marketing or promotional purposes at any time.</strong></p>
<p>See our full <a href="/privacy-policy">Privacy Policy</a> for complete details on how we handle your information.</p>

<h2>Contact</h2>
<p>BookaBoost<br>
Dallas, TX<br>
Phone: 469-553-7417<br>
Email: info@bookaboost.com<br>
Website: bookaboost.com</p>
</body>
</html>""")
