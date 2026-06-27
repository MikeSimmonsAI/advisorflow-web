"""
Seed script - run this ONCE after deployment to create:
  - Restland Cemetery & Funeral Home organization
  - Mike's super_admin account
  - 5 advisor accounts for the Northwood proof-of-concept team

Usage:
    python -m app.seed

Edit the ADVISORS list below with real names/emails before running.
Every account created here has must_change_password=True (the model
default), so the frontend will prompt for a password change on first
login - see /auth/change-password and the ChangePassword screen.
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.deps import SessionLocal, engine
from app.models.models import Base, Organization, User
from app.services.auth_service import hash_password

Base.metadata.create_all(bind=engine)
db = SessionLocal()

# --- 1. Organization ---
org = db.query(Organization).filter(Organization.slug == "restland").first()
if not org:
    org = Organization(
        name="Restland Cemetery & Funeral Home",
        slug="restland",
        plan="standard",
    )
    db.add(org)
    db.commit()
    print(f"Created organization: {org.name} ({org.id})")
else:
    print(f"Organization already exists: {org.name} ({org.id})")

# --- 1b. Tier definitions - the real, per-org configuration system that
# replaces the old hardcoded LeadTier/MessageTrack/TIER_TO_TRACK setup.
# Idempotent (see seed_default_tier_definitions's docstring) - safe to
# run against the real, already-existing production database to
# backfill Restland's org, which predates this system entirely.
from app.services.tier_config_service import seed_default_tier_definitions
created_tiers = seed_default_tier_definitions(db, org.id)
if created_tiers:
    print(f"Seeded {len(created_tiers)} tier definitions for {org.name}")
else:
    print(f"Tier definitions already exist for {org.name}")

# --- 2. Mike's super_admin account ---
mike_email = "michael.simmons@nsmg.com"
mike = db.query(User).filter(User.email == mike_email).first()
if not mike:
    mike = User(
        organization_id=org.id,
        email=mike_email,
        password_hash=hash_password("ChangeMe123!"),  # CHANGE on first login
        full_name="Mike Simmons",
        role="super_admin",
    )
    db.add(mike)
    db.commit()
    print(f"Created super_admin: {mike.email} (temp password: ChangeMe123!)")
else:
    print(f"Super admin already exists: {mike.email}")

# --- 3. Five advisor accounts for Northwood proof of concept ---
# EDIT THESE before running for real - placeholders shown here.
ADVISORS = [
    {"full_name": "Advisor One", "email": "advisor1@restland-poc.com"},
    {"full_name": "Advisor Two", "email": "advisor2@restland-poc.com"},
    {"full_name": "Advisor Three", "email": "advisor3@restland-poc.com"},
    {"full_name": "Advisor Four", "email": "advisor4@restland-poc.com"},
    {"full_name": "Advisor Five", "email": "advisor5@restland-poc.com"},
]

for advisor_data in ADVISORS:
    existing = db.query(User).filter(User.email == advisor_data["email"]).first()
    if existing:
        print(f"Advisor already exists: {advisor_data['email']}")
        continue
    advisor = User(
        organization_id=org.id,
        email=advisor_data["email"],
        password_hash=hash_password("Welcome123!"),  # each should change on first login
        full_name=advisor_data["full_name"],
        role="advisor",
    )
    db.add(advisor)
    print(f"Created advisor: {advisor_data['email']} (temp password: Welcome123!)")

db.commit()
db.close()
print("\nSeed complete. Everyone will be prompted to change their temp password")
print("automatically on first login (must_change_password=True by default).")
print("Also: each advisor should set their own Twilio credentials via the")
print("Settings screen (PUT /settings/twilio) after logging in.")
