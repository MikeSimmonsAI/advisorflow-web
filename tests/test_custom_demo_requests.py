"""Custom demo requests: asked for on the deal, decided by a manager in scope,
worked from one queue. Synthetic people, deals and demos only."""
import itertools
from datetime import date, datetime, timedelta

import pytest

from app.models.demo_site_models import DemoSite
from app.models.models import Platform, User
from app.models.sales_models import (DEMO_PENDING_REVIEW, DEMO_READY, DEMO_REQUESTED,
                                     ROLE_SALES_MANAGER, ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     STAGE_DEMO_BUILD, BrandSalesOrg, DiscoveryRecord, Membership,
                                     Opportunity, OpportunityEvent, demo_is_outstanding)
from app.models.sales_team_models import ONB_APPROVED, SalesOnboarding
from app.services import capabilities as caps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="dr%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


def _member(db, u, brand, role, manager=None):
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id, role=role,
                      is_active=True, reports_to_user_id=manager.id if manager else None))
    db.add(SalesOnboarding(brand_sales_org_id=brand.id, user_id=u.id, status=ONB_APPROVED, email=u.email))
    db.commit()


@pytest.fixture()
def w(db_session):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-dr-%d" % next(_SEQ))
    db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    god = _user(db, "God", role="god_admin")
    owner = _user(db, "Owner"); _member(db, owner, brand, ROLE_SALES_MANAGER)
    caps.set_brand_grants(db, owner, brand.id, brand.name, god, ["sales_program_owner"])
    sevp = _user(db, "Sr EVP"); _member(db, sevp, brand, ROLE_SALES_MANAGER)
    caps.set_brand_grants(db, sevp, brand.id, brand.name, god, ["sales_team_executive"])
    asm = _user(db, "ASM A"); _member(db, asm, brand, ROLE_SALES_MANAGER, sevp)
    asm_b = _user(db, "ASM B"); _member(db, asm_b, brand, ROLE_SALES_MANAGER)
    rep = _user(db, "Rep A"); _member(db, rep, brand, ROLE_SALES_REP, asm)
    rep_b = _user(db, "Rep B"); _member(db, rep_b, brand, ROLE_SALES_REP, asm_b)
    builder = _user(db, "Builder"); _member(db, builder, brand, ROLE_SALES_REP)
    opp = Opportunity(brand_sales_org_id=brand.id, company_name="Synthetic Spa", owner_user_id=rep.id,
                      status="open", stage="discovery")
    opp_b = Opportunity(brand_sales_org_id=brand.id, company_name="Synthetic Gym", owner_user_id=rep_b.id,
                        status="open", stage="discovery")
    db.add_all([opp, opp_b]); db.commit()
    db.add(DiscoveryRecord(opportunity_id=opp.id, demo_requirements="Online booking + reminder texts"))
    site = DemoSite(opportunity_id=opp_b.id, brand_sales_org_id=brand.id, title="Spa booking walkthrough",
                    html="<p>synthetic</p>", token="tok-%d" % next(_SEQ), is_active=True)
    db.add(site); db.commit()
    return dict(db=db, brand=brand, god=god, owner=owner, sevp=sevp, asm=asm, asm_b=asm_b,
                rep=rep, rep_b=rep_b, builder=builder, opp=opp, opp_b=opp_b, site=site)


def H(w, u):
    return {"Authorization": "Bearer " + create_access_token(u, w["db"])}


def _req(client, w, u, opp, needs=None):
    return client.post("/sales/opportunities/%s/demo-request" % opp.id, json={"needs": needs}, headers=H(w, u))


def _decide(client, w, u, opp, **body):
    return client.post("/sales/opportunities/%s/demo-request/decision" % opp.id, json=body, headers=H(w, u))


def _queue(client, w, u):
    r = client.get("/sales/demo-requests", headers=H(w, u))
    assert r.status_code == 200, r.text
    return r.json()


def test_rep_requests_with_discovery_and_it_is_not_build_work(client, w):
    r = _req(client, w, w["rep"], w["opp"])
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == DEMO_PENDING_REVIEW and j["needs"] == "Online booking + reminder texts"
    assert j["can_decide"] is False                               # the rep does not decide
    w["db"].refresh(w["opp"])
    assert demo_is_outstanding(w["opp"]) is False
    assert _req(client, w, w["rep"], w["opp"]).status_code == 409   # no duplicate
    # The rep tracks it.
    mine = _queue(client, w, w["rep"])["my_requests"]
    assert [m["company_name"] for m in mine] == ["Synthetic Spa"]
    # The general edit route can't approve it.
    r = client.patch("/sales/opportunities/%s" % w["opp"].id, json={"demo_status": "requested"},
                     headers=H(w, w["rep"]))
    assert r.status_code == 403


def test_request_needs_something_to_show(client, w):
    assert _req(client, w, w["rep_b"], w["opp_b"]).status_code == 422
    assert _req(client, w, w["rep_b"], w["opp_b"], "Show the member app").status_code == 200


def test_route_to_existing_demo(client, w):
    _req(client, w, w["rep"], w["opp"])
    q = _queue(client, w, w["asm"])
    assert q["summary"]["needs_review"] == 1 and q["needs_review"][0]["company_name"] == "Synthetic Spa"
    info = client.get("/sales/opportunities/%s/demo-request" % w["opp"].id, headers=H(w, w["asm"])).json()
    assert info["can_decide"] and any(d["id"] == w["site"].id for d in info["existing_demos"])
    r = _decide(client, w, w["asm"], w["opp"], action="route_existing", demo_site_id=w["site"].id,
                note="Use the spa walkthrough")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == DEMO_READY and j["demo_url"]
    assert [h["type"] for h in j["history"]][:2] == ["demo_routed_existing", "demo_requested_custom"]
    q = _queue(client, w, w["asm"])
    assert q["summary"] == {"needs_review": 0, "assigned": 0, "overdue": 0}
    assert q["recently_ready"][0]["company_name"] == "Synthetic Spa"


def test_approve_custom_build_assigns_builder_and_date(client, w):
    _req(client, w, w["rep"], w["opp"], "Custom intake form")
    target = date.today() + timedelta(days=5)
    r = _decide(client, w, w["sevp"], w["opp"], action="approve_build",
                builder_user_id=w["builder"].id, target_date=target.isoformat())
    assert r.status_code == 200, r.text
    w["db"].refresh(w["opp"])
    assert w["opp"].demo_status == DEMO_REQUESTED and w["opp"].demo_owner_user_id == w["builder"].id
    assert w["opp"].stage == STAGE_DEMO_BUILD and demo_is_outstanding(w["opp"]) is True
    # The builder sees it assigned; counts move from review to assigned.
    qb = _queue(client, w, w["builder"])
    assert [i["company_name"] for i in qb["assigned"]] == ["Synthetic Spa"]
    qs = _queue(client, w, w["sevp"])
    assert qs["summary"]["needs_review"] == 0 and qs["summary"]["assigned"] == 1
    # Overdue once the target passes.
    w["opp"].demo_due_at = datetime.utcnow() - timedelta(days=1)
    w["db"].commit()
    assert _queue(client, w, w["sevp"])["summary"]["overdue"] == 1
    # Ready leaves the outstanding count.
    prog = "/sales/opportunities/%s/demo-request/progress" % w["opp"].id
    assert client.post(prog, json={"status": "ready"}, headers=H(w, w["rep_b"])).status_code == 403
    assert client.post(prog, json={"status": "ready"}, headers=H(w, w["builder"])).status_code == 422  # no link
    r = client.post(prog, json={"status": "ready", "demo_url": "https://demo.example.test/spa"},
                    headers=H(w, w["builder"]))
    assert r.status_code == 200, r.text
    assert _queue(client, w, w["sevp"])["summary"] == {"needs_review": 0, "assigned": 0, "overdue": 0}


def test_decisions_outside_scope_are_refused(client, w):
    _req(client, w, w["rep"], w["opp"], "x")
    # ASM B does not lead Rep A; Rep A himself cannot decide; the builder cannot.
    for who in ("asm_b", "rep", "builder", "rep_b"):
        r = _decide(client, w, w[who], w["opp"], action="approve_build",
                    builder_user_id=w["builder"].id, target_date=(date.today() + timedelta(days=3)).isoformat())
        assert r.status_code in (403, 404), who
    assert _queue(client, w, w["asm_b"])["summary"]["needs_review"] == 0
    # Owner and God Mode can decide anyone's.
    assert client.get("/sales/opportunities/%s/demo-request" % w["opp"].id,
                      headers=H(w, w["owner"])).json()["can_decide"] is True
    assert client.get("/sales/opportunities/%s/demo-request" % w["opp"].id,
                      headers=H(w, w["god"])).json()["can_decide"] is True


def test_leader_does_not_decide_his_own_request(client, w):
    own = Opportunity(brand_sales_org_id=w["brand"].id, company_name="ASM's own deal",
                      owner_user_id=w["asm"].id, status="open")
    w["db"].add(own); w["db"].commit()
    _req(client, w, w["asm"], own, "y")
    assert _decide(client, w, w["asm"], own, action="send_back", note="n").status_code == 403
    assert _decide(client, w, w["sevp"], own, action="send_back", note="Need the budget").status_code == 200


def test_bad_decisions(client, w):
    _req(client, w, w["rep"], w["opp"], "z")
    assert _decide(client, w, w["asm"], w["opp"], action="approve_build",
                   builder_user_id=w["builder"].id).status_code == 422             # no date
    assert _decide(client, w, w["asm"], w["opp"], action="approve_build", builder_user_id="nobody",
                   target_date=(date.today() + timedelta(days=2)).isoformat()).status_code == 422
    assert _decide(client, w, w["asm"], w["opp"], action="route_existing",
                   demo_site_id="nope").status_code == 422
    assert _decide(client, w, w["asm"], w["opp"], action="send_back").status_code == 422  # no note
    assert _decide(client, w, w["asm"], w["opp"], action="bogus").status_code == 422
    # Not pending any more -> nothing to decide.
    _decide(client, w, w["asm"], w["opp"], action="send_back", note="Add the budget")
    assert _decide(client, w, w["asm"], w["opp"], action="send_back", note="again").status_code == 409


def test_signed_out(client, w):
    assert client.get("/sales/demo-requests").status_code == 401
    assert client.post("/sales/opportunities/%s/demo-request" % w["opp"].id, json={}).status_code == 401
