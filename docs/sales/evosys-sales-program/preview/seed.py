"""Seed the local EvoSys Sales preview (SQLite). Sample businesses only.
Outbound email is a recording stub; replies come through the real poller with
Graph-shaped messages; the AI provider is a stand-in (outputs labelled so)."""
import io
import json
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace

import requests

sys.path.insert(0, "/home/claude/wt-api")
from app.deps import SessionLocal  # noqa: E402
import app.models.registry  # noqa: F401,E402
from app.models.models import Organization, Platform, User  # noqa: E402
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity  # noqa: E402
from app.models.scheduling_models import AvailabilityWindow, MeetingType  # noqa: E402
from app.models.inbound_mailbox_models import InboundMailbox  # noqa: E402
from app.services import availability as av  # noqa: E402
from app.services.auth_service import hash_password, create_access_token  # noqa: E402

API = "http://localhost:8765"
db = SessionLocal()

plat = db.query(Platform).filter(Platform.slug == "evosyspro").first()
if plat is None:
    plat = Platform(name="EvoSys Pro", slug="evosyspro"); db.add(plat); db.commit()
if db.get(Organization, "org-god-platform") is None:
    db.add(Organization(id="org-god-platform", name="Platform", slug="platform-prev", plan="enterprise", is_active=True))
print("existing brands:", [(x.name, x.slug) for x in db.query(BrandSalesOrg).all()])
bso = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosys-pro-sales-prev", timezone="America/Chicago")
other = BrandSalesOrg(platform_id=plat.id, name="Other Brand Sales", slug="other-sales")
db.add_all([bso, other]); db.commit()


def user(email, name, role="advisor", org=None):
    u = User(organization_id=org, email=email, password_hash=hash_password("not-used-x9"), full_name=name,
             role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


mike = user("platform@preview.test", "Mike Simmons")
rep = user("rep@preview.test", "Riley Rep")
tenant_org = Organization(name="Sample Service Co", slug="sample-service-co", plan="trial", is_active=True)
db.add(tenant_org); db.commit()
tenant = user("owner@sampleservice.test", "Service Co Owner", role="org_admin", org=tenant_org.id)
for u, r in ((mike, "sales_manager"), (rep, "sales_rep")):
    db.add(Membership(user_id=u.id, scope_type="brand_sales_org", scope_id=bso.id, role=r, is_active=True))
for key, name, mins in (("discovery", "Discovery", 30), ("demo", "Product Demo", 60)):
    db.add(MeetingType(brand_sales_org_id=bso.id, key=key, name=name, duration_minutes=mins, is_active=True))
db.add(InboundMailbox(address="support@evosyspro.live", is_active=True, last_status="ok",
                      last_polled_at=datetime.utcnow()))
db.commit()
p = av.get_or_create_profile(db, mike, default_timezone="America/Chicago")
p.timezone, p.min_notice_minutes, p.booking_horizon_days = "America/Chicago", 0, 60
for dow in range(5):
    db.add(AvailabilityWindow(profile_id=p.id, day_of_week=dow, start_minute=9 * 60, end_minute=17 * 60))
db.commit()

tok = {u.email: create_access_token(u, db) for u in (mike, rep, tenant)}
json.dump({"mike": tok[mike.email], "rep": tok[rep.email], "tenant": tok[tenant.email],
           "mike_user": {"id": mike.id, "full_name": "Mike Simmons", "name": "Mike Simmons", "role": "advisor",
                         "email": mike.email}}, open("/home/claude/qa/evs_preview/tokens.json", "w"))
H = {"Authorization": "Bearer " + tok[mike.email]}

CSV = """company,source_record_id,full_name,phone,email,city,state,industry,notes
Example Pro Janitorial,FB-S-01,Ben Cole,2145550101,ben@examplepro.test,Dallas,TX,Commercial cleaning,Office and medical cleaning
Sample Bright Spaces,FB-S-02,Fay Lin,2145550102,fay@brightspaces.test,Plano,TX,Commercial cleaning,Janitorial for offices
Test Fresh Office Care,FB-S-03,Dan Ng,2145550103,dan@freshoffice.test,Irving,TX,Commercial cleaning,
Clear View Windows,FB-S-04,Cara Diaz,2145550104,cara@clearview.test,,TX,Window washing,Serves DFW and Houston
Demo Shine Services,FB-S-05,Eve Moss,2145550105,,Frisco,TX,Commercial cleaning,No email on the ad
Placeholder Clean Co,FB-S-06,Gus Park,2145550106,,Garland,TX,Commercial cleaning,No email on the ad
Sample Bright Spaces Team,FB-S-07,Hal Ito,2145550107,ops@brightspaces.test,Plano,TX,Commercial cleaning,
Alamo Sample Janitorial,FB-S-08,Ivy Cruz,2105550108,ivy@alamosample.test,San Antonio,TX,Commercial cleaning,
Nowhere Sample Cleaning,FB-S-09,Jon Bay,2145550109,jon@nowhere.test,,,Commercial cleaning,No location given
"""
b = requests.post(f"{API}/intake/sales/{bso.id}/batches", headers=H, data={"source_system": "facebook_ads"},
                  files={"file": ("sample-fb-ads.csv", io.BytesIO(CSV.encode()), "text/csv")}).json()
requests.post(f"{API}/intake/sales/{bso.id}/batches/{b['id']}/analyze", headers=H).raise_for_status()
rep_ = requests.post(f"{API}/intake/sales/{bso.id}/batches/{b['id']}/commit", headers=H,
                     json={"confirm_target_id": bso.id}).json()
print("import:", {k: rep_.get(k) for k in ("created", "held_for_review", "matched_existing")})

# Team sender + a sample DFW cleaning campaign, approved and on.
requests.put(f"{API}/sales/evosys/outreach", headers=H, json={"mailing_address": "123 Sample St, Dallas, TX 75201 (preview)",
                                                              "offer": "[Sample offer - preview only]"}).raise_for_status()
camp = requests.post(f"{API}/sales/evosys/campaigns", headers=H, json={
    "industry_key": "commercial_cleaning", "market_key": "dfw", "offer": "[Sample offer - preview only]"}).json()
requests.post(f"{API}/sales/evosys/campaigns/{camp['id']}/approve", headers=H).raise_for_status()
requests.post(f"{API}/sales/evosys/campaigns/{camp['id']}/switch", headers=H, json={"on": True}).raise_for_status()
win = requests.post(f"{API}/sales/evosys/campaigns", headers=H, json={
    "industry_key": "window_washing", "market_key": "dfw"}).json()
print("campaigns:", camp["name"], "/", win["name"])

# One send pass with a recording sender (nothing leaves this machine).
from app.services.evosys_sales import outreach as O  # noqa: E402
from app.services.evosys_sales import ai as AI  # noqa: E402
from app.services import inbound_mailbox_service as S  # noqa: E402
from app.models.evosys_sales_models import EvoSysOutreachState, EvoSysOutreachMessage  # noqa: E402
sent = []
mon = datetime.utcnow().replace(hour=16, minute=0, second=0, microsecond=0)
while mon.weekday() >= 5:
    mon -= timedelta(days=1)
print("pass:", O.run_pass(db, mon - timedelta(hours=26), lambda to, m, s, oid: sent.append(to) or {"success": True}))
print("sent to:", sent)

# Two replies arrive at the tagged addresses (real poller, Graph-shaped messages).
def opp(sid):
    return db.query(Opportunity).filter(Opportunity.source_record_id == sid).first()


msgs = []
for sid, body, mins in (("FB-S-01", "Yes, interested. Can you call me Thursday afternoon?", 200),
                        ("FB-S-02", "How much does it cost per month?", 380)):
    o = opp(sid)
    st = db.get(EvoSysOutreachState, o.id)
    msgs.append({"id": "prev-" + sid, "internetMessageId": "<prev-%s@mail.test>" % sid, "subject": "Re: More commercial cleaning contracts",
                 "from": {"emailAddress": {"address": o.email}},
                 "toRecipients": [{"emailAddress": {"address": "info+evs-%s@evosyspro.live" % st.reply_code}}],
                 "receivedDateTime": (datetime.utcnow() - timedelta(minutes=mins)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "body": {"content": body}})
box = db.query(InboundMailbox).first()
print("poll:", S.poll_mailbox(db, box, fetch=lambda since: msgs))


class Stand:   # stand-in AI provider for the preview
    def __init__(self, out):
        self.out = out
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **k: SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.out)))], usage=None)))


from app.services.evosys_sales import ai as _ai  # noqa: E402
from app.models.evosys_sales_models import EvoSysCampaign  # noqa: E402
s = O.get_settings(db, bso.id)
for sid, out in (("FB-S-02", {"category": "pricing", "summary": "Asked the monthly price.",
                              "draft": "Thanks Fay - happy to walk you through pricing on a quick call."}),
                 ("FB-S-01", {"category": "interested", "summary": "Interested; wants a call Thursday afternoon.",
                              "draft": "Hi Ben, great - Thursday works. Here is a link to pick a time: https://evosyspro.live/request-demo/\n\nMike Simmons"})):
    o = opp(sid)
    m = db.query(EvoSysOutreachMessage).filter_by(opportunity_id=o.id, direction="in").first()
    st = db.get(EvoSysOutreachState, o.id)
    _ai._get_client = lambda out=out: Stand(out)
    t = _ai.reply_assist(db, s, db.get(EvoSysCampaign, st.campaign_id), o, m, "", mike.id)
    m.label = (t.output or {}).get("category")
    db.commit()
    print("assist", sid, t.status, (t.output or {}).get("needs_person"))

# A booked intro call (existing scheduler) for the third lead.
o3 = opp("FB-S-03")
sl = requests.get(f"{API}/sales/evosys/leads/{o3.id}/intro-slots", headers=H).json()
print("slots:", len(sl.get("slots") or []), sl.get("problem"))
if sl.get("slots"):
    print("book:", requests.post(f"{API}/sales/evosys/leads/{o3.id}/intro-call", headers=H,
                                 json={"starts_at": sl["slots"][3]["starts_at"]}).json().get("status"))
db.close()
print("done")
