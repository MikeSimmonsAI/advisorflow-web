# -*- coding: utf-8 -*-
"""Issue #268 - two operator accounts, one owner.

Production shape (names only): wholesale@ (home org EVO, named Wholesale seat)
and Michael (no home org; brand executive + brand sales manager; org_admin on
SCI, EVO/Wholesale, Restland, Roofing, Atlantis and the Max Life demo agency).

Pins:
  * Michael lands on the program launcher ("Choose your business"), with Back
    Office as a separate destination - not dropped into the back office;
  * Michael's Wholesale card is hosted by EVO (his org_admin seat there), and
    EVO is the Wholesale experience for him exactly as for wholesale@;
  * wholesale@ still lands in Wholesale;
  * neither operator reaches GOD routes; god_admin stays one account;
  * an executive with a single workspace keeps the executive home.
"""
import itertools
import json
import uuid

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Membership
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _org(db, name, slug, industry, feats):
    o = Organization(name=name, slug=slug if slug == "evo-integrated-solutions-llc" else "%s-%s" % (slug, uuid.uuid4().hex[:4]),
                     plan="trial", industry=industry, is_active=True,
                     enabled_features=json.dumps(feats) if feats is not None else None)
    db.add(o)
    db.commit()
    return o


def _user(db, org, email, role="org_admin"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role="org_admin", active=True):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=active))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


@pytest.fixture()
def w(db_session):
    db = db_session
    plat = Platform(name="EvoSys Pro", slug="evosyspro-%s" % uuid.uuid4().hex[:4])
    db.add(plat)
    db.commit()
    bso = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="eps-%s" % uuid.uuid4().hex[:4])
    db.add(bso)
    db.commit()
    evo = _org(db, "EVO Integrated Solutions LLC", "evo-integrated-solutions-llc", "generic",
               ["leads", "reports", "users", "wholesale_real_estate"])
    sci = _org(db, "Service Corporation International", "service-corporation-international", "funeral",
               ["leads", "reports", "users", "sms", "email", "cadences"])
    from app.services.programs import setup as PS
    PS.ensure_program(db, sci, name="SCI Family Service Center")
    db.commit()
    rest = _org(db, "Restland Cemetery and Funeral Home", "restland", "funeral", None)
    roof = _org(db, "EvoSys Pro Roofing", "evosys-pro-roofing", "generic", ["leads", "users", "roofing_program"])
    atl = _org(db, "Atlantis Light & Power", "atlantis-light-power", "energy", ["leads", "reports", "users"])
    ins = _org(db, "Max Life Demo Agency (DEMO)", "max-life-demo-agency", "insurance",
               ["leads", "insurance_agency", "users"])
    six = [sci, evo, rest, roof, atl, ins]
    ws = _user(db, evo, "wholesale-%d@example.com" % next(_SEQ))
    for o in six:
        _m(db, ws, "customer_org", o.id)
    _m(db, ws, "product", "wholesale", "member")
    mike = _user(db, None, "michael-%d@example.com" % next(_SEQ), role="advisor")
    _m(db, mike, "platform", plat.id, "brand_executive")
    _m(db, mike, "brand_sales_org", bso.id, "sales_manager")
    for o in six:
        _m(db, mike, "customer_org", o.id)
    _m(db, mike, "product", "wholesale", "member")
    exec_only = _user(db, None, "exec-%d@example.com" % next(_SEQ), role="advisor")
    _m(db, exec_only, "platform", plat.id, "brand_executive")
    _m(db, exec_only, "customer_org", rest.id, "brand_executive")
    return dict(db=db, plat=plat, evo=evo, sci=sci, rest=rest, roof=roof, atl=atl, ins=ins, ws=ws, mike=mike,
                exec_only=exec_only, six=six)


def _ctx(client, db, u):
    r = client.get("/auth/my-contexts", headers=_h(db, u))
    assert r.status_code == 200, r.text
    return r.json()


def test_michael_lands_on_launcher_with_back_office_card(client, w):
    c = _ctx(client, w["db"], w["mike"])
    assert c["default_context"]["type"] == "workspace_selector" and c["default_context"]["path"] == "/workspaces"
    assert c["has_back_office"] is True and c["back_office_path"] == "/sales"
    assert {x["organization_id"] for x in c["workspace_contexts"]} == {o.id for o in w["six"]}
    assert all(x["role"] == "org_admin" for x in c["workspace_contexts"])
    exp = {x["organization_id"]: x["experience"] for x in c["workspace_contexts"]}
    assert exp == {w["sci"].id: "sci", w["evo"].id: "wholesale", w["rest"].id: "platform",
                   w["roof"].id: "roofing", w["atl"].id: "energy", w["ins"].id: "insurance"}
    # Wholesale is hosted by EVO, his org_admin seat there
    assert [(p["key"], p["organization_id"]) for p in c["product_contexts"]] == [("wholesale", w["evo"].id)]


def test_wholesale_at_still_lands_in_wholesale(client, w):
    d = _ctx(client, w["db"], w["ws"])["default_context"]
    assert d["type"] == "product" and d["organization_id"] == w["evo"].id


def test_both_operators_work_every_program_but_never_god(client, w):
    db = w["db"]
    for u in (w["ws"], w["mike"]):
        assert client.get("/wholesale/settings", headers=_h(db, u, ws=w["evo"].id)).status_code == 200
        assert client.get("/program/status", headers=_h(db, u, ws=w["sci"].id)).json().get("active") is True
        rr = client.get("/roofing-ops/status", headers=_h(db, u, ws=w["roof"].id))
        assert rr.status_code == 200, (u.email, rr.text)
        assert client.get("/leads/", headers=_h(db, u, ws=w["atl"].id)).status_code == 200
        assert client.get("/leads/", headers=_h(db, u, ws=w["rest"].id)).status_code == 200
        assert client.get("/agency/summary", headers=_h(db, u, ws=w["ins"].id)).status_code == 200
        for path in ("/god/orgs", "/god/access/users/%s" % u.id):
            assert client.get(path, headers=_h(db, u)).status_code in (401, 403, 404), path
    assert db.query(User).filter(User.role == "god_admin").count() == 0   # none created here


def test_wholesale_isolation_holds_for_michael_in_evo(client, w):
    db = w["db"]
    # in the Wholesale workspace the general platform screens are refused for him too
    r = client.get("/leads/status-funnel", headers=_h(db, w["mike"], ws=w["evo"].id))
    assert r.status_code == 403 and "Wholesale" in r.json()["detail"]
    # ...but nowhere else
    assert client.get("/leads/status-funnel", headers=_h(db, w["mike"], ws=w["rest"].id)).status_code == 200


def test_single_workspace_executive_keeps_executive_home(client, w):
    d = _ctx(client, w["db"], w["exec_only"])["default_context"]
    assert d["type"] == "executive"


def test_michael_is_org_admin_inside_each_workspace_and_nowhere_else(client, w):
    db = w["db"]
    for o in (w["rest"], w["atl"], w["sci"]):
        r = client.get("/admin/users", headers=_h(db, w["mike"], ws=o.id))
        assert r.status_code == 200, (o.name, r.status_code, r.text[:200])
    other = _org(db, "Unrelated Customer", "unrelated", "generic", ["leads", "users"])
    # naming a workspace he does not hold is ignored server-side: no tenant, no data
    r = client.get("/admin/users", headers=_h(db, w["mike"], ws=other.id))
    assert r.status_code in (403, 409), r.status_code
    assert client.get("/leads/", headers=_h(db, w["mike"], ws=other.id)).status_code in (403, 409)
    # the role change is per request: his stored row is untouched
    db.expire_all()
    stored = db.query(User).filter(User.id == w["mike"].id).one()
    assert stored.role == "advisor" and stored.organization_id is None
