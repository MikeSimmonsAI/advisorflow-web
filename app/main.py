"""
BookaBoost Web - Main Application Entry Point

Run locally:
    uvicorn app.main:app --reload --port 8000

Deploy target: Render or Railway (see DEPLOY.md)
Required env vars: DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, BOOKING_BASE_URL
"""

import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from sqlalchemy import text as _text
from app.deps import engine
from app.models.models import Base
from app.routers import (
    auth_router, leads_router, sms_router, admin_router,
    cadence_router, email_router, calendar_router, notification_router,
    settings_router, templates_router, ai_router, outcomes_router, microsoft_router,
    compliance_router, audit_log_router, sample_data_router,
    health_router, workqueue_router, campaign_router,
    google_contacts_router, availability_router, voice_router,
    concierge_router,
)
from app.routers.objection_router import router as objection_router
from app.routers import onboarding_router, ai_conversation_router, cadence_template_router, auto_send_router, org_settings_router
from app.routers import reports_router, crm_router
from app.routers.survey_router import router as survey_router
from app.routers.pipeline_router import router as pipeline_router
from app.routers.tier_definitions_router import router as tier_definitions_router
from app.routers.dlc_router import router as dlc_router
from app.routers.case_file_router import router as case_file_router
from app.routers.social_webhooks_router import router as social_webhooks_router
from app.routers.fiber_leads_router import router as fiber_leads_router
from app.routers.setup_router import router as setup_router
from app.routers.contacts_router import router as contacts_router
from app.routers.timeline_router import router as timeline_router
from app.routers.activity_router import router as activity_router
from app.routers.branding_router import router as branding_router
from app.routers.email_tracking_router import router as email_tracking_router

app = FastAPI(title="BookaBoost", version="0.1.0-phase1")

ALLOWED_ORIGINS = [
    "https://advisorflow-frontend.onrender.com",
    "https://bookaboost.com",
    "https://bookaboost.live",
    "https://app.bookaboost.live",
    "https://evosyspro.live",
    "https://app.evosyspro.live",
    "http://localhost:5173",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Org-Override"],
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
<title>SMS Consent Evidence - BookaBoost Pilot Client</title>
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
<p><strong>Business:</strong> BookaBoost Pilot Client — Funeral Services, Dallas, TX</p>
<p><strong>SMS Program:</strong> Appointment scheduling and follow-up outreach via Family Service Advisors</p>
<p><strong>Document purpose:</strong> This page documents all opt-in paths used for the A2P 10DLC SMS campaign and is provided for TCR campaign review purposes.</p>

<h2>Opt-In Path 1 — Website Form</h2>
<div class="box">
  <p class="label">How it works:</p>
  <p>Prospective customers visit the booking page and submit a contact form. The form includes an <strong>unchecked</strong> SMS consent checkbox that the user must actively check before submitting.</p>
  <p class="label">Form URL:</p>
  <p><a href="https://advisorflow-backend.onrender.com/book">https://advisorflow-backend.onrender.com/book</a></p>
  <p class="label">Consent language displayed on the form:</p>
  <div class="consent-text">
    <div class="checkbox-row">
      <input type="checkbox" disabled>
      <span>I agree to receive SMS text messages from our scheduling team regarding appointment scheduling and related services. Message frequency varies. Message and data rates may apply. Reply STOP to opt out at any time. Reply HELP for assistance. View our <a href="https://advisorflow-backend.onrender.com/privacy-policy">Privacy Policy</a> and <a href="https://advisorflow-backend.onrender.com/terms">Terms &amp; Conditions</a>.</span>
    </div>
  </div>
  <p><em>The checkbox is unchecked by default. The user must actively check it to provide consent. Form cannot be submitted without completing this field.</em></p>
</div>

<h2>Opt-In Path 2 — Verbal Consent (In-Person or Phone)</h2>
<div class="box">
  <p class="label">How it works:</p>
  <p>Family Service Advisors collect verbal consent from customers during in-person consultations, phone inquiries, or scheduled file review appointments.</p>
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
  <p><strong>BookaBoost</strong></p>
  <p>Dallas, TX</p>
  <p>Phone: 469-553-7417 | Email: info@bookaboost.com</p>
</div>
</body>
</html>""")


# ── Public health / keep-alive endpoints (no auth required) ─────────────────
@app.get("/ping")
def ping():
    """Unauthenticated liveness probe. Used by the frontend keep-alive interval
    and external uptime monitors to prevent Render free-tier sleep."""
    return {"status": "ok"}


# ── Public endpoint for landing page demo requests (no auth required)
@app.get("/leads/demo-request")
def demo_request_docs():
    return {"message": "POST to this endpoint to submit a demo request"}


# ── All app routers
app.include_router(auth_router.router)
app.include_router(timeline_router)   # before leads_router — prevents /{lead_id} catch-all from shadowing /activity
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
app.include_router(concierge_router.router)
app.include_router(outcomes_router.router)
app.include_router(microsoft_router.router)
app.include_router(compliance_router.router)
app.include_router(audit_log_router.router)
app.include_router(sample_data_router.router)
app.include_router(health_router.router)
app.include_router(workqueue_router.router)
app.include_router(campaign_router.router)
app.include_router(pipeline_router)
app.include_router(google_contacts_router.router)
app.include_router(objection_router)
app.include_router(onboarding_router.router)
app.include_router(ai_conversation_router.router)
app.include_router(cadence_template_router.router)
app.include_router(auto_send_router.router)
app.include_router(org_settings_router.router)
app.include_router(availability_router.router)
app.include_router(voice_router.router)
app.include_router(reports_router.router)
app.include_router(crm_router.router)
app.include_router(survey_router)
app.include_router(tier_definitions_router)
app.include_router(dlc_router)
app.include_router(case_file_router)
app.include_router(social_webhooks_router)
app.include_router(fiber_leads_router)
app.include_router(setup_router)
app.include_router(contacts_router)
app.include_router(timeline_router)
app.include_router(activity_router)
app.include_router(branding_router)  # public — no auth, must stay after CORS middleware
app.include_router(email_tracking_router)


# ── Background asyncio loops ──────────────────────────────────────────────────

async def _review_request_loop():
    """Send Google review request SMS after appointments end. Runs every 30 min."""
    from app.crons.review_request_cron import run_review_request_cron
    import logging as _log
    _logger = _log.getLogger("review_request_cron")
    await asyncio.sleep(60)  # brief startup delay
    while True:
        try:
            sent = run_review_request_cron(engine)
            if sent:
                _logger.info("review_request_cron: sent %d messages", sent)
        except Exception as exc:
            _logger.error("review_request_cron error: %s", exc)
        await asyncio.sleep(1800)  # 30 minutes


async def _ai_conversation_loop():
    """Process AI conversation touches every 2 min — per-org isolated.

    Each org's touches run in its own try/except block so a failure,
    bad Twilio credential, or runaway query in one org cannot halt
    processing for any other org. The loop itself never crashes the
    web server process — all exceptions are caught and logged.
    """
    from app.routers.ai_conversation_router import process_scheduled_touches
    from app.deps import SessionLocal
    from app.models.models import Organization
    import logging as _log
    _logger = _log.getLogger("ai_conversation_loop")
    await asyncio.sleep(30)  # brief startup delay
    while True:
        db = SessionLocal()
        try:
            orgs = db.query(Organization).filter(Organization.is_active == True).all()
            org_ids = [o.id for o in orgs]
        except Exception as exc:
            _logger.error("ai_conversation_loop: failed to fetch orgs: %s", exc)
            org_ids = []
        finally:
            db.close()

        for org_id in org_ids:
            try:
                db = SessionLocal()
                try:
                    process_scheduled_touches(db, org_id=org_id)
                finally:
                    db.close()
            except Exception as exc:
                _logger.error("ai_conversation_loop: org=%s error: %s", org_id, exc)
                # Isolation: continue to next org regardless of this error

        await asyncio.sleep(120)  # 2 minutes


@app.on_event("startup")
async def on_startup():
    # 1. Create any brand-new tables
    Base.metadata.create_all(bind=engine)

    # 2. Safe column/enum migrations (idempotent — no-ops if already applied)
    from app.auto_migrate import run_auto_migrations
    run_auto_migrations(engine)

    # 3. AI-conversation columns on pipeline_conversations (IF NOT EXISTS)
    migration_sql = """
        ALTER TABLE pipeline_conversations
            ADD COLUMN IF NOT EXISTS touch_number            INTEGER     DEFAULT 0,
            ADD COLUMN IF NOT EXISTS next_send_at            TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS paused                  BOOLEAN     DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS paused_reason           VARCHAR     NULL,
            ADD COLUMN IF NOT EXISTS started_at              TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS completed_at            TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS messages_sent           INTEGER     DEFAULT 0,
            ADD COLUMN IF NOT EXISTS replies_received        INTEGER     DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ai_responses_sent       INTEGER     DEFAULT 0,
            ADD COLUMN IF NOT EXISTS ai_responses_flagged    INTEGER     DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_outbound_at        TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS last_inbound_at         TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS booking_link_sent_at    TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS booked_at               TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS confirmed_at            TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS appointment_kept_at     TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS sale_recorded_at        TIMESTAMP   NULL,
            ADD COLUMN IF NOT EXISTS booking_notification_sent      BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS confirmation_notification_sent BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS ai_direction            VARCHAR     NULL,
            ADD COLUMN IF NOT EXISTS updated_at              TIMESTAMP   NULL;
    """
    try:
        with engine.connect() as conn:
            conn.execute(_text(migration_sql))
            conn.commit()
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("pipeline_conversations migration note: %s", e)

    # 4. Ensure the master super_admin account has the correct role.
    #    NOTE: password_hash is intentionally NOT set here — it would overwrite
    #    any password change made through the app on every deploy.
    #    Email is read from SUPER_ADMIN_EMAIL env var (not hardcoded) so it
    #    can be changed without a code deploy and never leaks in source.
    import os as _os
    _super_admin_email = _os.environ.get("SUPER_ADMIN_EMAIL", "")
    if _super_admin_email:
        try:
            with engine.connect() as conn:
                conn.execute(_text(
                    "UPDATE users SET role='super_admin', must_change_password=FALSE "
                    "WHERE email=:email"
                ), {"email": _super_admin_email})
                conn.commit()
        except Exception as e:
            import logging as _logging
            _logging.getLogger(__name__).warning("Super admin role migration note: %s", e)
    else:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "SUPER_ADMIN_EMAIL env var not set — skipping super_admin role grant on startup."
        )

    # 5. Seed default Platform records (idempotent — ON CONFLICT DO NOTHING)
    _platform_seed_sql = """
        INSERT INTO platforms (id, name, slug, domain, support_email, is_active)
        VALUES
            ('plt-bookaboost',    'BookaBoost',    'bookaboost',    'app.bookaboost.live',   'support@bookaboost.live',   TRUE),
            ('plt-evosyspro',     'EvoSys Pro',    'evosyspro',     'app.evosyspro.live',    'support@evosyspro.live',    TRUE),
            ('plt-harmonyhustle', 'Harmony Hustle','harmonyhustle', 'app.harmonyhustle.com', 'support@harmonyhustle.com', TRUE)
        ON CONFLICT (slug) DO NOTHING;
    """
    try:
        with engine.connect() as conn:
            conn.execute(_text(_platform_seed_sql))
            conn.commit()
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning("Platform seed note: %s", e)

    # 5. Start background asyncio loops (fire-and-forget, run for app lifetime)
    asyncio.create_task(_review_request_loop())   # Google review SMS  — every 30 min
    asyncio.create_task(_ai_conversation_loop())  # AI lead touches    — every 2 min


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "1"}
# touched Thu Jul  9 12:08:59 UTC 2026
