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
    compliance_router, audit_log_router, sample_data_router, health_router, workqueue_router,
    campaign_router, reports_router, email_tracking_router,
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
app.include_router(audit_log_router.router)
app.include_router(sample_data_router.router)
app.include_router(health_router.router)
app.include_router(workqueue_router.router)
app.include_router(campaign_router.router)
app.include_router(reports_router.router)
app.include_router(email_tracking_router.router)


@app.on_event("startup")
def on_startup():
    # Creates tables if they don't exist. For production, replace with
    # Alembic migrations (see migrations/ folder) instead of this auto-create.
    Base.metadata.create_all(bind=engine)

    # Auto-migration: adds any columns/enum values that exist in the
    # Python models but haven't reached the live database yet, since
    # create_all() above only creates brand-new tables - it never alters
    # an existing table or enum type. Runs on EVERY startup, safely (see
    # app/auto_migrate.py docstring for the full reasoning) - this is
    # what replaces the manual "SSH into Render's Shell tab and run a
    # migration command" step that broke the live site twice this
    # session when it got skipped. Mike was explicit he never wants to
    # do that by hand again.
    from app.auto_migrate import run_auto_migrations
    try:
        run_auto_migrations(engine)
    except Exception as e:
        # Never let a migration hiccup prevent the app from starting at
        # all - every statement inside run_auto_migrations already
        # catches its own errors per-statement; this outer catch is only
        # for something unexpected at the connection/engine level, and
        # even then the app should still come up rather than refuse to
        # boot entirely.
        print(f"[auto_migrate] Startup migration check failed unexpectedly: {e}")


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "1"}
