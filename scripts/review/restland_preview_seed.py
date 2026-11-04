"""LOCAL PREVIEW for Issue #507 - SCI (3 synthetic locations) beside Restland (1).

Writes ONLY a local SQLite file. Refuses any other DATABASE_URL. Every person
and number is synthetic (555 numbers, example.com addresses).

    python scripts/review/restland_preview_seed.py
    DATABASE_URL=sqlite:///./restland_preview.db uvicorn app.main:app --port 8099
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(REPO, "restland_preview.db")
existing = (os.environ.get("DATABASE_URL") or "").strip()
if existing and not existing.startswith("sqlite"):
    print("REFUSING TO RUN: DATABASE_URL is not a local SQLite file.")
    sys.exit(2)
os.environ["DATABASE_URL"] = "sqlite:///" + DB_PATH
os.environ.setdefault("JWT_SECRET", "preview" + "0" * 60)
sys.path.insert(0, REPO)

PW = "Preview1234!"
SCI_HOMES = ["Eastern Gate Memorial Gardens", "Striffler-Hamby Mortuary", "Alabama Heritage Funeral Home"]
FIRST = ["Avery", "Jordan", "Morgan", "Casey", "Riley", "Quinn", "Drew", "Parker", "Rowan", "Hayden",
         "Emerson", "Sawyer", "Reese", "Finley", "Blake", "Kendall", "Logan", "Peyton", "Skyler", "Dakota"]
LAST = ["Sample", "Testwell", "Placeholder", "Demoson", "Fictional", "Mockford", "Synthwood", "Notreal"]


def main():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    from app.deps import SessionLocal, engine
    from app.models.models import Base, Lead, Organization, User
    import app.models.registry  # noqa: F401 - every table
    Base.metadata.create_all(bind=engine)
    from app.models.program_models import ProgramSourceRecord
    from app.models.sales_models import Membership
    from app.services import sms_programs
    from app.services.auth_service import hash_password
    from app.services.programs import setup, single_location_cutover as C

    db = SessionLocal()
    sci = Organization(name=sms_programs.SCI_ORG_NAME, slug="sci-preview", plan="standard", industry="funeral")
    rest = Organization(name="Restland Cemetery and Funeral Home", slug="restland", plan="standard", industry="funeral",
                        org_phone="+14695537417", org_twilio_phone_number="+15550001155", org_twilio_number_type="10dlc")
    db.add_all([sci, rest])
    db.commit()
    admin = User(organization_id=rest.id, email="preview@example.com", password_hash=hash_password(PW),
                 full_name="Preview Admin", role="org_admin", is_active=True, must_change_password=False)
    db.add(admin)
    db.commit()
    for org in (sci, rest):
        db.add(Membership(user_id=admin.id, scope_type="customer_org", scope_id=org.id, role="org_admin", is_active=True))
    db.commit()

    # SCI: program + three homes + synthetic families mapped across them.
    setup.ensure_program(db, sci, name=sci.name, primary_contact_name="Preview Contact")
    profs = setup.ensure_locations(db, sci, admin, SCI_HOMES)
    setup.ensure_campaign_families(db, sci)
    db.commit()
    homes = [p for p in profs.values() if not p.is_review_bucket]
    for i in range(45):
        lead = Lead(organization_id=sci.id, first_name=FIRST[i % 20], last_name=LAST[i % 8],
                    phone="+1205555%04d" % i, email="sci%d@example.com" % i, status="new")
        db.add(lead)
        db.flush()
        h = homes[i % len(homes)]
        db.add(ProgramSourceRecord(organization_id=sci.id, source_lead_id="SCI-%03d" % i, raw_json="{}",
                                   first_name=lead.first_name, last_name=lead.last_name, phone=lead.phone,
                                   email=lead.email, location_id=h.location_id, location_status="mapped",
                                   source_location_name=h.official_name, campaign_family="veteran_planning_guide",
                                   lead_id=lead.id))
    db.commit()

    # Restland: existing families first (as in production), then the cutover.
    for i in range(60):
        db.add(Lead(organization_id=rest.id, first_name=FIRST[(i * 3) % 20], last_name=LAST[(i * 5) % 8],
                    phone="+1214555%04d" % i, email=("rest%d@example.com" % i) if i % 3 else None,
                    status="dnc" if i % 11 == 0 else "new", tier="pre_need" if i % 2 else "partial"))
    db.commit()
    out = C.apply(db, rest, hero_title="Restland", contact_name="Mike Simmons", actor_id=admin.id)
    print("restland cutover:", out["reconciliation"])
    print("login: preview@example.com /", PW)
    print("sci org:", sci.id, "restland org:", rest.id)
    db.close()


if __name__ == "__main__":
    main()
