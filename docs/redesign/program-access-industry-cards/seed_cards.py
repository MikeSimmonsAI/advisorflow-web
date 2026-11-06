import os, sys, json, uuid
sys.path.insert(0, os.getcwd())
from app.deps import SessionLocal
import app.models.registry  # noqa
from app.models.models import Organization, User
from app.models.sales_models import Membership
from app.services.auth_service import hash_password, create_access_token
db = SessionLocal()
seed = json.load(open('/home/claude/qa/local_seed.json'))
p = User(email="platform-%s@local.test" % uuid.uuid4().hex[:4], full_name="EvoSys Platform User", role="org_admin",
         password_hash=hash_password("x"), is_active=True, must_change_password=False)
db.add(p); db.commit()
specs = [("Atlantis Light & Power", "energy", ["leads"]), ("Vincent Greene Agency", "insurance", ["leads", "insurance_agency"]),
         ("Restland Cemetery and Funeral Home", "funeral", ["leads"]), ("Commercial Cleaning Blueprint", "cleaning", ["leads"]),
         ("Countryside Land Partners", "land", ["leads"]), ("EvoSys Pro Roofing", "roofing", ["leads", "roofing_program"])]
ids = [seed["org"]]
for name, ind, feats in specs:
    o = Organization(name=name, slug="c-%s" % uuid.uuid4().hex[:6], plan="enterprise", industry=ind, is_active=True,
                     timezone="America/Chicago", enabled_features=json.dumps(feats))
    db.add(o); db.commit(); ids.append(o.id)
p.organization_id = ids[0]; db.commit()
for oid in ids:
    db.add(Membership(user_id=p.id, scope_type="customer_org", scope_id=oid, role="org_admin", is_active=True))
db.commit()
seed["platform"] = {"token": create_access_token(p, db), "user": {"id": p.id, "email": p.email, "full_name": p.full_name, "role": p.role, "organization_id": p.organization_id}}
json.dump(seed, open('/home/claude/qa/local_seed.json', 'w'))
print("ok", len(ids))
