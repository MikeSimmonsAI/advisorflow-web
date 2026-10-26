# -*- coding: utf-8 -*-
"""Program Access expansion - previews and general clients on the launcher.

Pins:
  * a sales manager of the brand sees one PREVIEW per open, pre-won deal that
    has no workspace and a live demo link (platform demo preferred);
  * revoked / expired / won / lost / workspace-linked deals give no preview;
  * a rep, a customer-only user and another brand's manager see none
    (organization boundary), and previews never enter `contexts`;
  * a general client workspace (e.g. cleaning) is listed with its real name
    and industry, and only for its members;
  * a workspace the login does not belong to is refused on direct access,
    whatever the launcher shows.
"""
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.demo_site_models import DemoSite
from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Membership, Opportunity
from app.services.auth_service import create_access_token, hash_password


def _u(db, email, org=None, role="advisor"):
    u = User(organization_id=org.id if org else None, email=email, full_name=email.split("@")[0], role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


def _h(db, u, ws=None):
    h = {"Authorization": "Bearer %s" % create_access_token(u, db)}
    if ws:
        h["X-Workspace-Id"] = ws
    return h


def _opp(db, bso, owner, name, stage, status="open", contact=None, cust_org=None):
    o = Opportunity(brand_sales_org_id=bso.id, owner_user_id=owner.id, company_name=name,
                    contact_name=contact, stage=stage, status=status, customer_organization_id=cust_org)
    db.add(o)
    db.commit()
    return o


def _demo(db, opp, slot="platform", live=True, expired=False, slug=None):
    d = DemoSite(opportunity_id=opp.id, brand_sales_org_id=opp.brand_sales_org_id, slot=slot,
                 title="demo", html="<p>x</p>", token=uuid.uuid4().hex + uuid.uuid4().hex, slug=slug,
                 is_active=live, revoked_at=None if live else datetime.utcnow(),
                 expires_at=(datetime.utcnow() - timedelta(days=1)) if expired else (datetime.utcnow() + timedelta(days=30)))
    db.add(d)
    db.commit()
    return d


@pytest.fixture()
def w(db_session):
    db = db_session
    plat = Platform(name="EvoSys Pro", slug="evosyspro-%s" % uuid.uuid4().hex[:4])
    other_plat = Platform(name="Other Brand", slug="other-%s" % uuid.uuid4().hex[:4])
    db.add_all([plat, other_plat])
    db.commit()
    a = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="a-%s" % uuid.uuid4().hex[:4])
    b = BrandSalesOrg(platform_id=other_plat.id, name="Other Sales", slug="b-%s" % uuid.uuid4().hex[:4])
    db.add_all([a, b])
    db.commit()
    ccb = Organization(name="Commercial Cleaning Blueprint", slug="ccb-%s" % uuid.uuid4().hex[:4],
                       plan="trial", industry="cleaning", is_active=True)
    db.add(ccb)
    db.commit()

    mgr = _u(db, "mgr-%s@example.test" % uuid.uuid4().hex[:4])
    _m(db, mgr, "brand_sales_org", a.id, "sales_manager")
    rep = _u(db, "rep-%s@example.test" % uuid.uuid4().hex[:4])
    _m(db, rep, "brand_sales_org", a.id, "sales_rep")
    other_mgr = _u(db, "omgr-%s@example.test" % uuid.uuid4().hex[:4])
    _m(db, other_mgr, "brand_sales_org", b.id, "sales_manager")
    cleaner = _u(db, "owner-%s@example.test" % uuid.uuid4().hex[:4], org=ccb, role="org_admin")
    _m(db, cleaner, "customer_org", ccb.id, "org_admin")

    country = _opp(db, a, mgr, "Countryside Land Partners", "demo_proposal", contact="Sam Example")
    _demo(db, country, slot="website")
    country_platform = _demo(db, country, slot="platform", slug="countryside")
    clean = _opp(db, a, mgr, "Commercial Cleaning Blueprint — Dan Example", "demo_build", contact="Dan Example")
    clean_demo = _demo(db, clean)
    revoked = _opp(db, a, mgr, "Revoked Co", "demo_build")
    _demo(db, revoked, live=False)
    expired = _opp(db, a, mgr, "Expired Co", "demo_build")
    _demo(db, expired, expired=True)
    won = _opp(db, a, mgr, "Onboarding Co", "onboarding")
    _demo(db, won)
    lost = _opp(db, a, mgr, "Lost Co", "lost", status="lost")
    _demo(db, lost)
    linked = _opp(db, a, mgr, "Linked Co", "closing", cust_org=ccb.id)
    _demo(db, linked)
    no_demo = _opp(db, a, mgr, "Prospect Only Co", "prospect")
    b_opp = _opp(db, b, other_mgr, "Other Brand Deal", "demo_build")
    _demo(db, b_opp)
    return dict(db=db, a=a, b=b, ccb=ccb, mgr=mgr, rep=rep, other_mgr=other_mgr, cleaner=cleaner,
                country=country, country_platform=country_platform, clean=clean, clean_demo=clean_demo,
                no_demo=no_demo)


def _ctx(client, db, u):
    r = client.get("/auth/my-contexts", headers=_h(db, u))
    assert r.status_code == 200, r.text
    return r.json()


def test_manager_sees_one_preview_per_live_open_deal(client, w):
    c = _ctx(client, w["db"], w["mgr"])
    names = [p["company_name"] for p in c["preview_contexts"]]
    assert names == ["Commercial Cleaning Blueprint — Dan Example", "Countryside Land Partners"]
    cs = next(p for p in c["preview_contexts"] if p["opportunity_id"] == w["country"].id)
    # platform walkthrough preferred over the website concept; vanity name used
    assert cs["demo_slot"] == "platform"
    assert cs["url"].endswith("/demo/countryside?staff=1")
    assert cs["proposal_sent"] is False and cs["stage_label"] == "Demo / Proposal"
    cl = next(p for p in c["preview_contexts"] if p["opportunity_id"] == w["clean"].id)
    assert cl["url"].endswith("/demo/" + w["clean_demo"].token + "?staff=1")


def test_previews_are_never_contexts_or_workspaces(client, w):
    c = _ctx(client, w["db"], w["mgr"])
    assert all(x.get("type") != "preview" for x in c["contexts"])
    assert c["workspace_contexts"] == []
    # login landing unchanged by previews: a sales manager still lands in the back office
    assert c["default_context"]["path"] == "/sales"


@pytest.mark.parametrize("who", ["rep", "other_mgr", "cleaner"])
def test_no_previews_for_rep_customer_or_other_brand(client, w, who):
    c = _ctx(client, w["db"], w[who])
    ids = {p["opportunity_id"] for p in c["preview_contexts"]}
    assert w["country"].id not in ids and w["clean"].id not in ids
    if who != "other_mgr":
        assert c["preview_contexts"] == []
    else:
        assert [p["company_name"] for p in c["preview_contexts"]] == ["Other Brand Deal"]


def test_general_client_workspace_keeps_its_identity_for_members_only(client, w):
    c = _ctx(client, w["db"], w["cleaner"])
    ws = c["workspace_contexts"]
    assert [x["organization_name"] for x in ws] == ["Commercial Cleaning Blueprint"]
    assert ws[0]["industry"] == "cleaning"
    # the sales manager has the deal and the preview, but NOT the workspace
    assert all(x["organization_id"] != w["ccb"].id for x in _ctx(client, w["db"], w["mgr"])["workspace_contexts"])


def test_direct_workspace_access_is_refused_without_membership(client, w):
    db = w["db"]
    assert client.get("/leads/", headers=_h(db, w["cleaner"], ws=w["ccb"].id)).status_code == 200
    for who in ("mgr", "rep", "other_mgr"):
        r = client.get("/leads/", headers=_h(db, w[who], ws=w["ccb"].id))
        assert r.status_code in (403, 404), (who, r.status_code)


def _views(db, demo):
    db.expire_all()
    return db.query(DemoSite).filter(DemoSite.id == demo.id).first().view_count


def test_staff_preview_open_is_not_counted_as_a_prospect_view(client, w):
    db, d = w["db"], w["clean_demo"]
    url = "/public/demo/" + d.token
    base = _views(db, d)
    # the prospect (no login) is counted, with or without the flag
    assert client.get(url).status_code == 200
    assert client.get(url + "?staff=1").status_code == 200
    assert _views(db, d) == base + 2
    # the brand's own manager and rep opening it from Program Access are not
    assert client.get(url + "?staff=1", headers=_h(db, w["mgr"])).status_code == 200
    assert client.get(url + "?staff=1", headers=_h(db, w["rep"])).status_code == 200
    assert _views(db, d) == base + 2
    # without the flag a signed-in manager is still counted (unchanged behaviour)
    assert client.get(url, headers=_h(db, w["mgr"])).status_code == 200
    assert _views(db, d) == base + 3
    # another brand's manager, a customer user, or a bad token cannot hide a view
    assert client.get(url + "?staff=1", headers=_h(db, w["other_mgr"])).status_code == 200
    assert client.get(url + "?staff=1", headers=_h(db, w["cleaner"])).status_code == 200
    assert client.get(url + "?staff=1", headers={"Authorization": "Bearer nonsense"}).status_code == 200
    assert _views(db, d) == base + 6
