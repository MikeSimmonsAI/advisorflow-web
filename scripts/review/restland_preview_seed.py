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
    for i in range(312):
        db.add(Lead(organization_id=rest.id, first_name=FIRST[(i * 3) % 20], last_name=LAST[(i * 5) % 8] + str(i % 13),
                    phone="+1214555%04d" % i, email=("rest%d@example.com" % i) if i % 3 else None,
                    status="dnc" if i % 11 == 0 else ("needs_tier_review" if i % 4 == 0 else "new"),
                    tier="pre_need" if i % 2 else "partial"))
    db.commit()
    out = C.apply(db, rest, hero_title="Restland", contact_name="Mike Simmons", actor_id=admin.id)
    # Restland's own theme for the preview (production uses its org id).
    import json as _json
    from app.models.program_models import LocationProfile
    lp = db.query(LocationProfile).filter_by(organization_id=rest.id, is_review_bucket=False).one()
    lp.brand_settings = _json.dumps({"program_theme": "restland"})
    # Earlier work, as Restland has it in production (synthetic content).
    from datetime import datetime as _dt, timedelta as _td
    from sqlalchemy import text as _text
    from app.models.models import BookingLink, PipelineConversation, Reply
    fams = db.query(Lead).filter(Lead.organization_id == rest.id).order_by(Lead.created_at, Lead.id).all()
    for k, l in enumerate(fams[5:11]):
        for j in range(1 + k % 3):
            db.add(Reply(lead_id=l.id, body=["Yes, I'd like to come look at my file.", "Can we do a Saturday?",
                                             "Please call me after 3."][j], source="sms",
                         received_at=_dt(2026, 8, 6 + k, 14 + j, 5)))
        db.add(PipelineConversation(organization_id=rest.id, lead_id=l.id, advisor_id=admin.id, stage="replied",
                                    touch_number=3, messages_sent=3, replies_received=1 + k % 3,
                                    last_inbound_at=_dt(2026, 8, 6 + k, 14, 5)))
    bl = []
    for k, (l, when, st) in enumerate([(fams[5], _dt(2026, 8, 12, 10, 0), "booked"), (fams[6], _dt(2026, 8, 19, 13, 30), "booked"),
                                       (fams[7], _dt(2026, 8, 22, 11, 0), "cancelled"), (fams[8], _dt(2026, 8, 31, 9, 30), "booked")]):
        b = BookingLink(lead_id=l.id, user_id=admin.id, status=st, booked_time=when, appt_label="File review")
        db.add(b)
        bl.append(b)
    db.commit()
    db.execute(_text("CREATE TABLE IF NOT EXISTS appointment_case_files (id VARCHAR PRIMARY KEY, lead_id VARCHAR, organization_id VARCHAR, recorded_by_id VARCHAR, booking_link_id VARCHAR, appointment_date TIMESTAMP, appointment_type VARCHAR, outcome_type VARCHAR, products_discussed TEXT, products_sold TEXT, advisor_notes TEXT, objections_raised TEXT, client_concerns TEXT, referral_potential BOOLEAN, case_status VARCHAR, next_action VARCHAR, next_action_date TIMESTAMP, next_action_notes TEXT, created_at TIMESTAMP, updated_at TIMESTAMP)"))
    for k, b in enumerate(bl):
        db.execute(_text("INSERT INTO appointment_case_files (id, lead_id, organization_id, booking_link_id, appointment_date, appointment_type, case_status, advisor_notes, next_action) VALUES (:i, :l, :o, :b, :d, 'file_review', :s, :n, :a)"),
                   {"i": "cf%d" % k, "l": b.lead_id, "o": rest.id, "b": b.id, "d": b.booked_time,
                    "s": "void" if b.status == "cancelled" else "open", "n": "Synthetic note: reviewed property file.",
                    "a": "Follow up on options" if k % 2 == 0 else None})
    staff = User(organization_id=rest.id, email="staff@example.com", password_hash=hash_password(PW),
                 full_name="Preview Staff", role="advisor", is_active=True, must_change_password=False)
    db.add(staff)
    db.commit()
    db.add(Membership(user_id=staff.id, scope_type="customer_org", scope_id=rest.id, role="advisor", is_active=True))
    db.commit()
    # The recorded clean-up + Batch 1 draft (Issue #507), exactly as production runs it.
    from app.services.programs import workspace_reset as WR
    for k, l in enumerate(db.query(Lead).filter(Lead.organization_id == rest.id).order_by(Lead.id).limit(40).all()):
        if k < 8:
            l.status, l.tier = "new", "pre_need"
    db.commit()
    for l in db.query(Lead).filter(Lead.organization_id == rest.id).all():
        if l.status == "new" and l.tier == "partial":
            l.tier = "pre_need"
        l.phone = (l.phone or "").lstrip("+")
    db.commit()
    res = WR.apply(db, rest, "preview-1")
    print("workspace reset:", res["summary"]["changes"])
    # Issue #507 (Nov 7): booking account, appointment rules, 14-day campaign.
    # The preview uses a stand-in address for the booking account.
    from app.services.programs import workspace_configure as WC
    bu = User(email="mike.preview@example.com", full_name="Jerome Simmons", password_hash=hash_password(PW),
              organization_id=rest.id, role="org_admin", is_active=True)
    db.add(bu)
    db.commit()
    cfg = WC.configure(db, rest, "preview-cfg-1", probe=False, booking_user_id=bu.id)
    print("configure:", cfg["summary"]["changes"])
    from app.services.sms_service import get_or_create_booking_link
    staged = db.query(Lead).filter(Lead.organization_id == rest.id, Lead.status == "new").first()
    link = get_or_create_booking_link(db, staged, bu)
    db.commit()
    print("booking page: /book/%s" % link.token)
    print("restland cutover:", out["reconciliation"])
    print("login: preview@example.com /", PW)
    print("sci org:", sci.id, "restland org:", rest.id)
    db.close()


if __name__ == "__main__":
    main()
