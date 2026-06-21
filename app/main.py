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
    compliance_router,
)

app = FastAPI(title="AdvisorFlow Web", version="0.1.0-phase1")

# Allow the frontend (deployed separately, e.g. on Vercel/Render static site)
# to call this API. Tighten allow_origins to the real frontend domain before
# going live with real advisor data.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict to actual frontend domain before launch
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


@app.on_event("startup")
def on_startup():
    # Creates tables if they don't exist. For production, replace with
    # Alembic migrations (see migrations/ folder) instead of this auto-create.
    Base.metadata.create_all(bind=engine)


@app.get("/health")
def health_check():
    return {"status": "ok", "phase": "1"}
