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
mich = org("Michelle's Cleaning Services")
test2 = org("TEST Second Cleaning Co (synthetic)")
owner = user("michelle@preview.test", "Michelle", "org_admin", mich, "org_admin")
G = {"Authorization": "Bearer " + create_access_token(god, db)}
for o in (mich, test2):
    r = c.post("/god/acquisition/%s/enable" % o.id, headers=G); assert r.status_code == 200, r.text
r = c.put("/acquisition-ops/%s/profile" % mich.id, headers=G, json={
    "business_name": "Michelle's Cleaning Services", "contact_name": "Michelle", "contact_phone": "901-555-0100",
    "contact_email": "michelle@preview.test", "service_area": ["Memphis", "Germantown", "Cordova", "Bartlett"],
    "states": ["TN"], "segments": ["office", "medical_dental", "property_management"]})
print("profile", r.status_code, r.text[:200])

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

ids = {}
for name, seg, city, d, h in (("Poplar Avenue Medical Plaza", "medical_dental", "Memphis", 1, 10),
                              ("Riverside Professional Center", "office", "Germantown", 1, 14),
                              ("Cordova Property Group", "property_management", "Cordova", 2, 11)):
    ids[name] = add(name, seg, city); book(ids[name], d, h)
won = add("Bartlett Family Dental", "medical_dental", "Bartlett"); book(won, -6, 9)
M = {"Authorization": "Bearer " + create_access_token(owner, db), "X-Workspace-Id": mich.id}
for body in ({"outcome": "appointment_held"}, {"outcome": "won", "contract_type": "recurring", "monthly_value": 2500, "contract_start": "2026-12-01"}):
    r = c.post("/acquisition/prospects/%s/outcome" % won, headers=M, json=body); print("outcome", r.status_code, r.text[:120])
json.dump({"god": G["Authorization"][7:], "owner": M["Authorization"][7:], "mich": mich.id, "test2": test2.id,
           "god_user": {"id": god.id, "full_name": "Mike Simmons", "name": "Mike Simmons", "role": "god_admin", "email": god.email},
           "owner_user": {"id": owner.id, "full_name": "Michelle", "name": "Michelle", "role": "org_admin", "email": owner.email, "organization_id": mich.id}},
          open("/home/claude/qa/acq_preview/tokens.json", "w"))
print("seeded")
