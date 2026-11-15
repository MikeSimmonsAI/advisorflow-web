"""Program Access launcher: per-account pins and the Needs Attention row.
Synthetic people and deals only."""
import itertools
from datetime import datetime, timedelta

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (DEMO_PENDING_REVIEW, DEMO_REQUESTED, ROLE_SALES_MANAGER,
                                     ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG, BrandSalesOrg,
                                     Membership, Opportunity)
from app.models.sales_team_models import ONB_APPROVED, ONB_SUBMITTED, SalesOnboarding
from app.services import capabilities as caps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="la%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _member(db, u, brand, role, manager=None, approved=True):
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id,
                      role=role, is_active=True, reports_to_user_id=manager.id if manager else None))
    if approved:
        db.add(SalesOnboarding(brand_sales_org_id=brand.id, user_id=u.id, status=ONB_APPROVED,
                               email=u.email, legal_name=u.full_name))
    db.commit()


@pytest.fixture()
def world(db_session):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-la-%d" % next(_SEQ))
    db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    god = _user(db, "God", role="god_admin")
    owner = _user(db, "Owner")
    _member(db, owner, brand, ROLE_SALES_MANAGER)
    caps.set_brand_grants(db, owner, brand.id, brand.name, god, ["sales_program_owner"])
    sevp = _user(db, "Sr EVP")
    _member(db, sevp, brand, ROLE_SALES_MANAGER)
    caps.set_brand_grants(db, sevp, brand.id, brand.name, god, ["sales_team_executive"])
    asm = _user(db, "ASM A")
    _member(db, asm, brand, ROLE_SALES_MANAGER, sevp)
    other_asm = _user(db, "ASM B")             # not under the Sr EVP
    _member(db, other_asm, brand, ROLE_SALES_MANAGER)
    rep = _user(db, "Rep A")
    _member(db, rep, brand, ROLE_SALES_REP, asm)
    rep_b = _user(db, "Rep B")
    _member(db, rep_b, brand, ROLE_SALES_REP, other_asm)
    return dict(db=db, brand=brand, god=god, owner=owner, sevp=sevp, asm=asm,
                other_asm=other_asm, rep=rep, rep_b=rep_b)


def H(w, u):
    return {"Authorization": "Bearer " + create_access_token(u, w["db"])}


def _opp(w, owner, **kw):
    o = Opportunity(brand_sales_org_id=w["brand"].id, company_name="Synthetic %d" % next(_SEQ),
                    owner_user_id=owner.id, status="open", **kw)
    w["db"].add(o); w["db"].commit()
    return o


# ── pins ─────────────────────────────────────────────────────────────────────

def test_pins_are_per_account(client, world):
    w = world
    assert client.get("/launcher/pins", headers=H(w, w["rep"])).json() == {"keys": []}
    r = client.put("/launcher/pins", json={"keys": ["deal_builder::sales", "ws:abc", "ws:abc", " "]},
                   headers=H(w, w["rep"]))
    assert r.status_code == 200 and r.json()["keys"] == ["deal_builder::sales", "ws:abc"]
    client.put("/launcher/pins", json={"keys": ["ws:xyz"]}, headers=H(w, w["asm"]))
    assert client.get("/launcher/pins", headers=H(w, w["rep"])).json()["keys"] == ["deal_builder::sales", "ws:abc"]
    assert client.get("/launcher/pins", headers=H(w, w["asm"])).json()["keys"] == ["ws:xyz"]
    client.put("/launcher/pins", json={"keys": []}, headers=H(w, w["rep"]))
    assert client.get("/launcher/pins", headers=H(w, w["rep"])).json()["keys"] == []


def test_pins_signed_out_and_limits(client, world):
    assert client.get("/launcher/pins").status_code == 401
    r = client.put("/launcher/pins", json={"keys": ["k%d" % i for i in range(30)]},
                   headers=H(world, world["rep"]))
    assert r.status_code == 400


# ── needs attention ──────────────────────────────────────────────────────────

def _items(client, w, u):
    r = client.get("/launcher/attention", headers=H(w, u))
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_empty_when_nothing_needs_attention(client, world):
    for who in ("god", "owner", "sevp", "asm", "rep"):
        assert _items(client, world, world[who]) == [], who
    outsider = _user(world["db"], "Outsider")
    assert _items(client, world, outsider) == []


def test_owner_sees_pending_approvals_and_demo_decisions(client, world):
    w = world
    newbie = _user(w["db"], "Newbie")
    _member(w["db"], newbie, w["brand"], ROLE_SALES_REP, w["asm"], approved=False)
    w["db"].add(SalesOnboarding(brand_sales_org_id=w["brand"].id, user_id=newbie.id,
                                status=ONB_SUBMITTED, email=newbie.email))
    w["db"].commit()
    _opp(w, w["rep_b"], demo_status=DEMO_PENDING_REVIEW)
    for who in ("owner", "god"):
        kinds = {i["kind"]: i for i in _items(client, w, w[who])}
        assert kinds["approvals"]["href"] == "/sales/my-team" and kinds["approvals"]["count"] == 1
        assert kinds["demo_decisions"]["count"] == 1
    # The Sr EVP does not approve onboarding.
    assert "approvals" not in {i["kind"] for i in _items(client, w, w["sevp"])}


def test_leaders_see_only_their_scope(client, world):
    w = world
    _opp(w, w["rep"], demo_status=DEMO_PENDING_REVIEW)                      # in Sr EVP / ASM A scope
    _opp(w, w["rep_b"], demo_status=DEMO_PENDING_REVIEW)                    # ASM B only
    _opp(w, w["rep_b"], demo_status=DEMO_REQUESTED, demo_due_at=datetime.utcnow() - timedelta(days=2))
    def count(u, kind):
        return next((i["count"] for i in _items(client, w, u) if i["kind"] == kind), 0)
    assert count(w["sevp"], "demo_decisions") == 1
    assert count(w["asm"], "demo_decisions") == 1
    assert count(w["other_asm"], "demo_decisions") == 1
    assert count(w["sevp"], "overdue_builds") == 0
    assert count(w["other_asm"], "overdue_builds") == 1
    assert count(w["owner"], "demo_decisions") == 2
    # A rep never sees decisions.
    assert count(w["rep"], "demo_decisions") == 0


def test_rep_sees_own_follow_ups_and_builder_sees_own_overdue(client, world):
    w = world
    _opp(w, w["rep"], next_action="Call back", next_action_due_at=datetime.utcnow() - timedelta(hours=3))
    _opp(w, w["rep_b"], next_action="Call back", next_action_due_at=datetime.utcnow() - timedelta(hours=3))
    items = _items(client, w, w["rep"])
    fu = [i for i in items if i["kind"] == "follow_ups"]
    assert fu and fu[0]["count"] == 1 and fu[0]["href"] == "/sales/follow-ups"
    _opp(w, w["rep_b"], demo_status=DEMO_REQUESTED, demo_owner_user_id=w["rep"].id,
         demo_due_at=datetime.utcnow() - timedelta(days=1))
    assert any(i["kind"] == "overdue_builds" for i in _items(client, w, w["rep"]))


def test_at_most_three_items(client, world):
    w = world
    for _ in range(2):
        nb = _user(w["db"], "NB")
        _member(w["db"], nb, w["brand"], ROLE_SALES_REP, w["asm"], approved=False)
        w["db"].add(SalesOnboarding(brand_sales_org_id=w["brand"].id, user_id=nb.id,
                                    status=ONB_SUBMITTED, email=nb.email))
    w["db"].commit()
    _opp(w, w["owner"], demo_status=DEMO_PENDING_REVIEW)
    _opp(w, w["owner"], demo_status=DEMO_REQUESTED, demo_due_at=datetime.utcnow() - timedelta(days=1))
    _opp(w, w["owner"], next_action="x", next_action_due_at=datetime.utcnow() - timedelta(hours=1))
    assert len(_items(client, w, w["owner"])) == 3


def test_a_failing_source_does_not_break_the_row(client, world, monkeypatch):
    from app.services import sales_team
    w = world
    _opp(w, w["rep"], next_action="x", next_action_due_at=datetime.utcnow() - timedelta(hours=1))
    monkeypatch.setattr(sales_team, "opp_scope", lambda *a, **k: 1 / 0)
    items = _items(client, w, w["rep"])
    assert [i["kind"] for i in items] == ["follow_ups"]


def test_pending_review_is_not_build_work(world):
    from app.models.sales_models import STAGE_DEMO_BUILD, demo_is_outstanding
    o = _opp(world, world["rep"], demo_status=DEMO_PENDING_REVIEW, stage=STAGE_DEMO_BUILD)
    assert demo_is_outstanding(o) is False
    o.demo_status = DEMO_REQUESTED
    assert demo_is_outstanding(o) is True
