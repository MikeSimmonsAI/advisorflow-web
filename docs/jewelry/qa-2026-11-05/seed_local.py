import os, sys, json, uuid
sys.path.insert(0, os.getcwd())
from app.deps import engine, SessionLocal
import app.models.registry  # noqa
from app.models.models import Base, Organization, User
from app.models.sales_models import Membership
from app.services.auth_service import hash_password, create_access_token
Base.metadata.create_all(bind=engine)
db = SessionLocal()
feats = ["leads","imports","booking","calendar","availability","reports","compliance","users","branding_settings","sms","email"]
org = Organization(name="Blue Ridge Gold & Jewelry", slug="br-local-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                   industry="generic", is_active=True, timezone="America/New_York", enabled_features=json.dumps(feats))
other = Organization(name="Other Test Shop", slug="other-%s" % uuid.uuid4().hex[:6], plan="enterprise",
                   industry="jewelry", is_active=True, timezone="America/New_York", enabled_features=json.dumps(feats+["jewelry_program"]))
db.add_all([org, other]); db.commit()
god = User(email="god@local.test", full_name="Platform Owner", role="god_admin", password_hash=hash_password("x"), is_active=True, must_change_password=False)
owner = User(organization_id=org.id, email="owner@local.test", full_name="Store Owner", role="org_admin", password_hash=hash_password("x"), is_active=True, must_change_password=False)
staff = User(organization_id=org.id, email="staff@local.test", full_name="Sam Staff", role="advisor", password_hash=hash_password("x"), is_active=True, must_change_password=False)
db.add_all([god, owner, staff]); db.commit()
for u, r in ((owner, "org_admin"), (staff, "advisor")):
    db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=org.id, role=r, is_active=True))
db.commit()
from app.services.jewelry import setup as S
S.setup(db, org, god.id, grant_features=True)
out = {"org": org.id, "other": other.id}
for k, u in (("god", god), ("owner", owner), ("staff", staff)):
    out[k] = {"token": create_access_token(u, db), "user": {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "organization_id": u.organization_id}}
json.dump(out, open("/home/claude/qa/local_seed.json", "w"))
print("seeded", org.id)
