"""Local preview of the Acquisition Control Center + client portal (SQLite).
Fictional sample businesses only; nothing is sent anywhere."""
import json, sys, uuid
from datetime import datetime, timedelta
sys.path.insert(0, "/home/claude/wt-api")
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.deps import SessionLocal  # noqa: E402
from app.models.models import BookingLink, Organization, User  # noqa: E402
from app.models.sales_models import Membership  # noqa: E402
from app.models.acquisition_models import AcquisitionOpportunity  # noqa: E402
from app.services.auth_service import create_access_token, hash_password  # noqa: E402

import app.models.registry  # noqa
from app.models.models import Base
from app.deps import engine
Base.metadata.create_all(engine)
c = TestClient(fastapi_app); db = SessionLocal()

def org(name):
    o = Organization(name=name, slug="aq-%s" % uuid.uuid4().hex[:8], plan="trial", industry="cleaning", is_active=True,
                     enabled_features=json.dumps(["leads"])); db.add(o); db.commit(); return o

def user(email, name, role, o=None, member=None):
    u = User(organization_id=o.id if o else None, email=email, full_name=name, password_hash=hash_password("not-used-x9"),
             role=role, is_active=True, must_change_password=False); db.add(u); db.commit()
    if o and member:
        db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=o.id, role=member, is_active=True)); db.commit()
    return u

god = user("platform@preview.test", "Mike Simmons", "god_admin")
G = {"Authorization": "Bearer " + create_access_token(god, db)}

def company(name, contact, email):
    o = org(name)
    r = c.post("/god/acquisition/%s/enable" % o.id, headers=G); assert r.status_code == 200, r.text
    # the way "Invite client" makes the login: home organization, NO membership
    u = user(email, contact, "org_admin", o, None)
    return o, u

A, ua = company("Sparkle Path Cleaning (TEST)", "Alicia Moreno", "alicia@preview.test")      # setup not started
B, ub = company("Brightline Janitorial (TEST)", "Ben Carter", "ben@preview.test")             # setup done, no appointments
Cc, uc = company("Michelle's Cleaning Services (TEST)", "Michelle", "michelle@preview.test")   # active
H = lambda u, o: {"Authorization": "Bearer " + create_access_token(u, db), "X-Workspace-Id": o.id}
for o in (A, B, Cc):
    c.put("/acquisition-ops/%s/profile" % o.id, headers=G, json={"offer_type": "free_pilot",
          "offer_terms": "EvoSys books 3 appointments in 30 days at no cost. After that we talk about staying on."})
hb = H(ub, B)
for body in ({"business_name": "Brightline Janitorial", "contact_phone": "214-555-0101", "contact_email": "ben@preview.test",
              "mailing_address": "100 Main St, Dallas, TX 75201", "setup_step": "business"},
             {"services": ["Office cleaning", "Floor care"], "setup_step": "services"},
             {"segments": ["office", "medical_dental"], "setup_step": "customers"},
             {"service_area": ["Dallas", "Plano"], "service_states": ["TX"], "setup_step": "area"},
             {"never_contact": ["oldclient.com"], "setup_step": "never"}):
    r = c.put("/acquisition/profile", headers=hb, json=body); assert r.status_code == 200, r.text
db.query(User).filter(User.id == ub.id).update({"google_calendar_connected": True})
from app.models.acquisition_models import AcquisitionProfile
db.query(AcquisitionProfile).filter(AcquisitionProfile.organization_id == B.id).update({"booking_user_id": ub.id})
db.commit()
print("accept", c.post("/acquisition/setup/accept-offer", headers=hb).status_code,
      "complete", c.post("/acquisition/setup/complete", headers=hb).status_code)
mich = Cc
r = c.put("/acquisition-ops/%s/profile" % mich.id, headers=G, json={
    "business_name": "Michelle's Cleaning Services", "contact_name": "Michelle", "contact_phone": "901-555-0100",
    "contact_email": "michelle@preview.test", "service_area": ["Memphis", "Germantown", "Cordova", "Bartlett"],
    "segments": ["office", "medical_dental", "property_management"]})
print("profile", r.status_code)

def add(name, seg, city):
    r = c.post("/acquisition-ops/%s/prospects" % mich.id, headers=G, json={"business_name": name, "segment": seg,
        "website": "https://%s.example" % uuid.uuid4().hex[:6], "is_practice": False, "source_url": "https://maps.example/x", "city": city})
    assert r.status_code == 200, r.text; return r.json()["id"]

def stage(opp, st, **kw):
    r = c.post("/acquisition-ops/%s/prospects/%s/stage" % (mich.id, opp), headers=G, json=dict(stage=st, **kw)); assert r.status_code == 200, r.text

def book(opp, days, hour):
    o = db.get(AcquisitionOpportunity, opp)
    bl = BookingLink(lead_id=o.lead_id, user_id=god.id, status="booked", appt_label="Walkthrough", appt_duration=45,
                     booked_time=(datetime.utcnow() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0),
                     calendar_event_id="evt-prev-%s" % uuid.uuid4().hex[:6])
    db.add(bl); db.commit(); o.booking_link_id = bl.id; db.commit()
    stage(opp, "interested", note="Wants a walkthrough"); stage(opp, "appointment_booked")

for name, seg, city, d, h in (("Poplar Avenue Medical Plaza (TEST)", "medical_dental", "Memphis", 1, 16),
                              ("Riverside Professional Center (TEST)", "office", "Germantown", 2, 15),
                              ("Cordova Property Group (TEST)", "property_management", "Cordova", 3, 17)):
    book(add(name, seg, city), d, h)
M = H(uc, mich)
held, est, rec, one = (add(n, s, ci) for n, s, ci in (("Union Ave Dental (TEST)", "medical_dental", "Memphis"),
    ("Midtown Offices (TEST)", "office", "Memphis"), ("Bartlett Family Dental (TEST)", "medical_dental", "Bartlett"),
    ("Collierville Lofts (TEST)", "property_management", "Collierville")))
for o in (held, est, rec, one):
    book(o, -5, 15)
post = lambda o, b: c.post("/acquisition/prospects/%s/outcome" % o, headers=M, json=b).status_code
print("held", post(held, {"outcome": "appointment_held"}),
      "est", post(est, {"outcome": "quote_issued", "quote_amount": 1800}),
      "rec", post(rec, {"outcome": "appointment_held"}), post(rec, {"outcome": "won", "contract_type": "recurring", "monthly_value": 2500, "contract_start": "2026-12-01"}),
      "one", post(one, {"outcome": "appointment_held"}), post(one, {"outcome": "won", "contract_type": "one_time", "contract_value": 4200, "contract_start": "2026-12-05"}))
owner = uc
tok = lambda u: create_access_token(u, db)
ju = lambda u, o: {"id": u.id, "full_name": u.full_name, "name": u.full_name, "role": "org_admin", "email": u.email, "organization_id": o.id}
json.dump({"god": G["Authorization"][7:], "owner": tok(uc), "a": tok(ua), "b": tok(ub), "mich": mich.id, "A": A.id, "B": B.id,
           "a_user": ju(ua, A), "b_user": ju(ub, B),
           "god_user": {"id": god.id, "full_name": "Mike Simmons", "name": "Mike Simmons", "role": "god_admin", "email": god.email},
           "owner_user": {"id": owner.id, "full_name": "Michelle", "name": "Michelle", "role": "org_admin", "email": owner.email, "organization_id": mich.id}},
          open("/home/claude/qa/acq_preview/tokens.json", "w"))
print("seeded")
