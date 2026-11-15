"""My Team salesperson profiles: deals, activity and demo work, within the
same team scope as the roster. Synthetic people and deals only."""
import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (DEMO_PENDING_REVIEW, DEMO_REQUESTED, ROLE_SALES_MANAGER,
                                     ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG, BrandSalesOrg, Membership,
                                     Opportunity, OpportunityEvent)
from app.models.sales_team_models import ONB_APPROVED, ONB_SUBMITTED, SalesOnboarding
from app.services import capabilities as caps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="mt%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


def _member(db, u, brand, role, manager=None, status=ONB_APPROVED):
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id, role=role,
                      is_active=True, reports_to_user_id=manager.id if manager else None))
    db.add(SalesOnboarding(brand_sales_org_id=brand.id, user_id=u.id, status=status, email=u.email))
    db.commit()


@pytest.fixture()
def w(db_session):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-mt-%d" % next(_SEQ))
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
    newbie = _user(db, "New Rep"); _member(db, newbie, brand, ROLE_SALES_REP, asm, status=ONB_SUBMITTED)
    now = datetime.utcnow()
    o1 = Opportunity(brand_sales_org_id=brand.id, company_name="Synthetic Cafe", owner_user_id=rep.id,
                     status="open", stage="discovery", next_action="Call", next_action_due_at=now - timedelta(hours=1),
                     demo_status=DEMO_PENDING_REVIEW)
    o2 = Opportunity(brand_sales_org_id=brand.id, company_name="Synthetic Florist", owner_user_id=rep_b.id,
                     status="open", stage="demo_build", demo_status=DEMO_REQUESTED, demo_owner_user_id=rep.id,
                     demo_due_at=now - timedelta(days=1))
    db.add_all([o1, o2]); db.commit()
    db.add(OpportunityEvent(opportunity_id=o1.id, event_type="note", summary="Called the owner",
                            actor_user_id=rep.id))
    db.commit()
    return dict(db=db, god=god, owner=owner, sevp=sevp, asm=asm, asm_b=asm_b, rep=rep, rep_b=rep_b,
                newbie=newbie, o1=o1, o2=o2)


def H(w, u):
    return {"Authorization": "Bearer " + create_access_token(u, w["db"])}


def _profile(client, w, viewer, target):
    return client.get("/sales-team/members/%s/profile" % w[target].id, headers=H(w, w[viewer]))


def test_profile_shows_deals_activity_and_demo_work(client, w):
    r = _profile(client, w, "owner", "rep")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["person"]["name"] == "Rep A" and j["person"]["manager"] == "ASM A"
    assert [d["company_name"] for d in j["deals"]] == ["Synthetic Cafe"]
    assert j["deals"][0]["quote_href"].endswith("/quote") and j["deals"][0]["follow_up_due"] is True
    assert j["summary"] == {"open_deals": 1, "follow_ups_due": 1, "demos_building": 1}
    assert j["activity"][0]["summary"] == "Called the owner"
    assert j["demo_work"]["building"][0]["company_name"] == "Synthetic Florist"
    assert j["demo_work"]["building"][0]["overdue"] is True
    assert j["demo_work"]["requested"][0]["waiting_for_decision"] is True


def test_profile_never_lists_documents(client, w):
    j = _profile(client, w, "owner", "newbie").json()
    assert j["person"]["onboarding_status"] == ONB_SUBMITTED
    text = str(j)
    assert "filename" not in text and "document_id" not in text and "storage" not in text


def test_scope(client, w):
    # Sr EVP: his whole tree (ASM A, Rep A, New Rep) but not ASM B's team.
    assert _profile(client, w, "sevp", "rep").status_code == 200
    assert _profile(client, w, "sevp", "rep_b").status_code == 403
    # ASM A: his own reps only.
    assert _profile(client, w, "asm", "rep").status_code == 200
    assert _profile(client, w, "asm", "rep_b").status_code == 403
    assert _profile(client, w, "asm_b", "rep").status_code == 403
    # Reps have no team view.
    assert _profile(client, w, "rep", "rep").status_code == 403
    assert _profile(client, w, "god", "rep_b").status_code == 200


def test_asm_sees_his_team_read_only(client, w):
    r = client.get("/sales-team/roster", headers=H(w, w["asm"]))
    assert r.status_code == 200, r.text
    j = r.json()
    names = {p["name"] for p in j["people"]}
    assert names == {"ASM A", "Rep A", "New Rep"}
    assert j["rights"]["can_manage"] is False and j["rights"]["can_invite"] is False
    assert j["rights"]["can_open_files"] is False and j["rights"]["can_approve"] is False
    # Read only: no suspend / move / invite.
    assert client.post("/sales-team/members/%s/suspend" % w["rep"].id, headers=H(w, w["asm"])).status_code == 403
    assert client.post("/sales-team/invite", json={"email": "x@example.test", "full_name": "X", "seat": "rep"},
                       headers=H(w, w["asm"])).status_code in (403, 422)
    # Onboarding file names never reach him.
    assert "filename" not in str(j)
    # Program Access gets him a My Team card.
    ctx = client.get("/auth/my-contexts", headers=H(w, w["asm"])).json()
    assert ctx["sales_team"]["my_team"] is True


def test_signed_out(client, w):
    assert client.get("/sales-team/members/%s/profile" % w["rep"].id).status_code == 401
