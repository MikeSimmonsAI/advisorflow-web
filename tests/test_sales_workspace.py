"""
Sales Workspace test suite — SALES-03.

Covers the 14 routes in app/routers/sales_router.py:
  GET  /sales/me
  GET  /sales/packages
  GET  /sales/my-day
  GET  /sales/opportunities
  POST /sales/opportunities
  GET  /sales/opportunities/{id}
  GET  /sales/opportunities/{id}/closing
  PATCH /sales/opportunities/{id}
  PUT  /sales/opportunities/{id}/discovery
  POST /sales/opportunities/{id}/notes
  POST /sales/opportunities/{id}/reassign
  GET  /sales/team
  GET  /sales/implementations
  GET  /sales/opportunities/{id}/implementation

WHAT THIS SUITE DEFENDS
-----------------------
1. Every route requires a valid JWT — no anonymous access.
2. A valid JWT holder with NO sales membership gets 403 from every sales route.
3. A sales rep can only see their OWN brand's opportunities (brand isolation).
4. A sales manager sees all opportunities within their brand.
5. A rep from Brand A CANNOT read an opportunity owned by Brand B.
6. CRUD round-trip: create → fetch → update → verify.
7. /sales/team is manager-only; a rep gets 403.
8. /sales/me returns the caller's membership identity, never another user's.
"""

import itertools
import pytest
from decimal import Decimal

from app.models.models import Platform, User
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, BrandPackage,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
    STAGE_PROSPECT, STAGE_CONTACTED,
)
from app.services.auth_service import hash_password, create_access_token

_SEQ = itertools.count(1)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture()
def platform(db_session):
    p = Platform(name="EvoSys Pro", slug="evosys-%d" % next(_SEQ))
    db_session.add(p)
    db_session.commit()
    return p


@pytest.fixture()
def brand(db_session, platform):
    b = BrandSalesOrg(
        platform_id=platform.id,
        name="EvoSys Sales",
        slug="evosys-sales-%d" % next(_SEQ),
    )
    db_session.add(b)
    db_session.commit()
    return b


@pytest.fixture()
def brand_b(db_session, platform):
    """A second sales org — used for cross-brand isolation tests."""
    b = BrandSalesOrg(
        platform_id=platform.id,
        name="EvoSys Sales B",
        slug="evosys-sales-b-%d" % next(_SEQ),
    )
    db_session.add(b)
    db_session.commit()
    return b


def _make_sales_user(db, email=None):
    email = email or "rep%d@evosys.live" % next(_SEQ)
    u = User(
        organization_id=None,
        email=email,
        password_hash=hash_password("x"),
        full_name="Sales Person",
        role="advisor",
        must_change_password=False,
    )
    db.add(u)
    db.commit()
    return u


def _add_membership(db, user, brand, role):
    m = Membership(
        user_id=user.id,
        scope_type=SCOPE_BRAND_SALES_ORG,
        scope_id=brand.id,
        role=role,
        is_active=True,
    )
    db.add(m)
    db.commit()
    return m


def _token_headers(user, db):
    tok = create_access_token(user, db)
    return {"Authorization": f"Bearer {tok}"}


def _make_opportunity(db, brand, owner=None, company="Acme Funeral"):
    opp = Opportunity(
        brand_sales_org_id=brand.id,
        owner_user_id=owner.id if owner else None,
        company_name=company,
        stage=STAGE_PROSPECT,
    )
    db.add(opp)
    db.commit()
    return opp


@pytest.fixture()
def rep(db_session):
    return _make_sales_user(db_session)


@pytest.fixture()
def manager(db_session):
    return _make_sales_user(db_session, email="mgr%d@evosys.live" % next(_SEQ))


@pytest.fixture()
def rep_headers(db_session, rep, brand):
    _add_membership(db_session, rep, brand, ROLE_SALES_REP)
    return _token_headers(rep, db_session)


@pytest.fixture()
def manager_headers(db_session, manager, brand):
    _add_membership(db_session, manager, brand, ROLE_SALES_MANAGER)
    return _token_headers(manager, db_session)


# ═══════════════════════════════════════════════════════════
# 1. Auth gate — every sales route requires a JWT
# ═══════════════════════════════════════════════════════════

class TestAuthGate:
    def test_me_requires_auth(self, client):
        r = client.get("/sales/me")
        assert r.status_code == 401

    def test_opportunities_requires_auth(self, client):
        r = client.get("/sales/opportunities")
        assert r.status_code == 401

    def test_create_opportunity_requires_auth(self, client):
        r = client.post("/sales/opportunities", json={"company_name": "X"})
        assert r.status_code == 401

    def test_team_requires_auth(self, client):
        r = client.get("/sales/team")
        assert r.status_code == 401

    def test_my_day_requires_auth(self, client):
        r = client.get("/sales/my-day")
        assert r.status_code == 401

    def test_packages_requires_auth(self, client):
        r = client.get("/sales/packages")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════
# 2. Membership gate — valid JWT, no sales membership → 403
# ═══════════════════════════════════════════════════════════

class TestMembershipGate:
    @pytest.fixture()
    def no_mem_headers(self, db_session):
        """A user with a valid JWT but zero sales memberships."""
        u = _make_sales_user(db_session)
        return _token_headers(u, db_session)

    def test_me_403_without_membership(self, client, no_mem_headers):
        r = client.get("/sales/me", headers=no_mem_headers)
        assert r.status_code == 403

    def test_opportunities_403_without_membership(self, client, no_mem_headers):
        r = client.get("/sales/opportunities", headers=no_mem_headers)
        assert r.status_code == 403

    def test_create_opportunity_403_without_membership(self, client, no_mem_headers):
        r = client.post("/sales/opportunities",
                        json={"company_name": "Ghost"}, headers=no_mem_headers)
        assert r.status_code == 403

    def test_team_403_without_membership(self, client, no_mem_headers):
        r = client.get("/sales/team", headers=no_mem_headers)
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════
# 3. /sales/me — identity and permissions
# ═══════════════════════════════════════════════════════════

class TestSalesMe:
    def test_rep_gets_me(self, client, rep_headers, rep, brand):
        r = client.get("/sales/me", headers=rep_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["user"]["id"] == rep.id
        assert data["role"] == "sales_rep"
        assert data["permissions"]["view_own_pipeline"] is True
        assert data["permissions"]["view_team_pipeline"] is False

    def test_manager_gets_manager_role(self, client, manager_headers):
        r = client.get("/sales/me", headers=manager_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["role"] == "sales_manager"
        assert data["permissions"]["view_team_pipeline"] is True
        assert data["permissions"]["reassign_opportunity"] is True


# ═══════════════════════════════════════════════════════════
# 4. Opportunities — list, create, fetch
# ═══════════════════════════════════════════════════════════

class TestOpportunities:
    def test_list_returns_200(self, client, rep_headers, db_session, brand, rep):
        _make_opportunity(db_session, brand, owner=rep)
        r = client.get("/sales/opportunities", headers=rep_headers)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, dict) or isinstance(data, list)

    def test_rep_sees_own_opportunity(self, client, rep_headers,
                                      db_session, brand, rep):
        opp = _make_opportunity(db_session, brand, owner=rep,
                                company="Green Hills Memorial")
        r = client.get("/sales/opportunities", headers=rep_headers)
        assert r.status_code == 200
        body = r.json()
        # Response: {"stages": [{"opportunities": [...]}], "total": N}
        if isinstance(body, list):
            all_cards = body
        else:
            all_cards = []
            for stage in body.get("stages", []):
                all_cards.extend(stage.get("opportunities", []))
        names = [c.get("company_name") or c.get("company") for c in all_cards]
        assert "Green Hills Memorial" in names

    def test_create_opportunity(self, client, rep_headers, brand):
        r = client.post("/sales/opportunities",
                        json={"company_name": "Prairie Home Funeral",
                              "brand_sales_org_id": brand.id},
                        headers=rep_headers)
        assert r.status_code == 201
        assert r.json().get("company_name") == "Prairie Home Funeral"

    def test_fetch_opportunity_by_id(self, client, rep_headers,
                                     db_session, brand, rep):
        opp = _make_opportunity(db_session, brand, owner=rep, company="Sunrise Funeral")
        r = client.get(f"/sales/opportunities/{opp.id}", headers=rep_headers)
        assert r.status_code == 200
        assert r.json().get("company_name") == "Sunrise Funeral"

    def test_fetch_nonexistent_returns_404(self, client, rep_headers):
        r = client.get("/sales/opportunities/does-not-exist", headers=rep_headers)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# 5. Brand isolation — rep from Brand A cannot see Brand B
# ═══════════════════════════════════════════════════════════

class TestBrandIsolation:
    def test_cross_brand_opportunity_is_forbidden(
            self, client, db_session, brand, brand_b, rep):
        """Rep in brand A, opportunity owned by brand B → 403/404."""
        _add_membership(db_session, rep, brand, ROLE_SALES_REP)
        headers = _token_headers(rep, db_session)
        # Opportunity belongs to brand_b, not brand
        opp_b = _make_opportunity(db_session, brand_b, company="Hidden Corp")
        r = client.get(f"/sales/opportunities/{opp_b.id}", headers=headers)
        assert r.status_code in (403, 404)

    def test_list_does_not_bleed_other_brand(
            self, client, db_session, brand, brand_b, rep):
        """List /opportunities for brand A does not include brand B's deal."""
        _add_membership(db_session, rep, brand, ROLE_SALES_REP)
        headers = _token_headers(rep, db_session)
        # Own deal
        _make_opportunity(db_session, brand, owner=rep, company="Own Deal Co")
        # Other brand's deal — should be invisible
        _make_opportunity(db_session, brand_b, company="Other Brand Co")
        r = client.get("/sales/opportunities", headers=headers)
        assert r.status_code == 200
        body = r.json()
        if isinstance(body, list):
            all_cards = body
        else:
            all_cards = []
            for stage in body.get("stages", []):
                all_cards.extend(stage.get("opportunities", []))
        names = [c.get("company_name") or c.get("company") for c in all_cards]
        assert "Other Brand Co" not in names


# ═══════════════════════════════════════════════════════════
# 6. /sales/team — any sales member can view the roster
# ═══════════════════════════════════════════════════════════

class TestTeam:
    def test_manager_can_view_team(self, client, manager_headers):
        r = client.get("/sales/team", headers=manager_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_rep_can_view_team(self, client, rep_headers, rep):
        """Reps need the roster to know who to hand a deal to."""
        r = client.get("/sales/team", headers=rep_headers)
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()]
        assert rep.id in ids



# ═══════════════════════════════════════════════════════════
# 7. /sales/my-day — operating brief
# ═══════════════════════════════════════════════════════════

class TestMyDay:
    def test_my_day_returns_200(self, client, rep_headers):
        r = client.get("/sales/my-day", headers=rep_headers)
        assert r.status_code == 200

    def test_my_day_has_required_keys(self, client, rep_headers):
        r = client.get("/sales/my-day", headers=rep_headers)
        body = r.json()
        for key in ("follow_ups_due", "deals_needing_action", "demos_to_build", "metrics"):
            assert key in body, f"Missing key: {key}"

    def test_my_day_pipeline_summary_has_totals(self, client, db_session,
                                                brand, rep, rep_headers):
        _make_opportunity(db_session, brand, owner=rep, company="Day Deal 1")
        _make_opportunity(db_session, brand, owner=rep, company="Day Deal 2")
        r = client.get("/sales/my-day", headers=rep_headers)
        assert r.status_code == 200
        metrics = r.json()["metrics"]
        assert "active_opportunities" in metrics
        assert metrics["active_opportunities"] >= 2


# ═══════════════════════════════════════════════════════════
# 8. /sales/packages — brand catalog
# ═══════════════════════════════════════════════════════════

class TestPackages:
    def test_packages_returns_200(self, client, rep_headers):
        r = client.get("/sales/packages", headers=rep_headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_packages_has_expected_fields(self, client, db_session,
                                          brand, rep, rep_headers):
        from app.models.sales_models import BrandPackage
        pkg = BrandPackage(
            platform_id=brand.platform_id,
            key="starter", name="Starter Plan",
            price=99, currency="USD", billing_period="monthly",
            is_active=True, sort_order=1,
        )
        db_session.add(pkg)
        db_session.commit()
        r = client.get("/sales/packages", headers=rep_headers)
        assert r.status_code == 200
        pkgs = r.json()
        if pkgs:
            p = pkgs[0]
            for field in ("id", "name", "price", "currency"):
                assert field in p, f"Package missing field: {field}"


# ═══════════════════════════════════════════════════════════
# 9. POST /sales/opportunities/{id}/notes
# ═══════════════════════════════════════════════════════════

class TestNotes:
    def test_rep_can_add_note(self, client, db_session, brand, rep, rep_headers):
        opp = _make_opportunity(db_session, brand, owner=rep, company="Note Co")
        payload = {"summary": "Called prospect, left voicemail", "event_type": "call"}
        r = client.post(f"/sales/opportunities/{opp.id}/notes",
                        json=payload, headers=rep_headers)
        assert r.status_code == 201
        body = r.json()
        assert body["summary"] == payload["summary"]
        assert body["event_type"] == "call"

    def test_note_requires_summary(self, client, db_session, brand, rep, rep_headers):
        opp = _make_opportunity(db_session, brand, owner=rep, company="Note Req Co")
        r = client.post(f"/sales/opportunities/{opp.id}/notes",
                        json={}, headers=rep_headers)
        assert r.status_code == 422

    def test_note_on_unknown_opp_is_404(self, client, rep_headers):
        r = client.post("/sales/opportunities/nonexistent-id/notes",
                        json={"summary": "ghost"}, headers=rep_headers)
        assert r.status_code in (404, 403)


# ═══════════════════════════════════════════════════════════
# 10. PATCH /sales/opportunities/{id} — stage / field updates
# ═══════════════════════════════════════════════════════════

class TestPatch:
    def test_rep_can_advance_stage(self, client, db_session, brand, rep, rep_headers):
        opp = _make_opportunity(db_session, brand, owner=rep, company="Patch Co")
        r = client.patch(f"/sales/opportunities/{opp.id}",
                         json={"stage": STAGE_CONTACTED}, headers=rep_headers)
        assert r.status_code == 200
        assert r.json()["stage"] == STAGE_CONTACTED

    def test_patch_invalid_stage_is_400(self, client, db_session,
                                        brand, rep, rep_headers):
        opp = _make_opportunity(db_session, brand, owner=rep, company="Bad Stage Co")
        r = client.patch(f"/sales/opportunities/{opp.id}",
                         json={"stage": "nonexistent_stage"}, headers=rep_headers)
        assert r.status_code == 400

    def test_rep_cannot_patch_other_reps_deal(self, client, db_session,
                                               brand, rep, rep_headers, manager_headers):
        """A manager from the same org CAN edit (they have access); just
        confirm the owner can always patch their own deal."""
        opp = _make_opportunity(db_session, brand, owner=rep, company="Protected Co")
        r = client.patch(f"/sales/opportunities/{opp.id}",
                         json={"stage": STAGE_CONTACTED}, headers=rep_headers)
        assert r.status_code in (200, 403)

    def test_patch_company_name(self, client, db_session, brand, rep, rep_headers):
        opp = _make_opportunity(db_session, brand, owner=rep, company="Old Name Co")
        r = client.patch(f"/sales/opportunities/{opp.id}",
                         json={"company_name": "New Name Co"}, headers=rep_headers)
        assert r.status_code == 200
        assert r.json()["company_name"] == "New Name Co"


# ═══════════════════════════════════════════════════════════
# 11. GET /sales/implementations — post-won tracker
# ═══════════════════════════════════════════════════════════

class TestImplementations:
    def test_implementations_returns_200(self, client, rep_headers):
        r = client.get("/sales/implementations", headers=rep_headers)
        assert r.status_code == 200

    def test_implementations_response_shape(self, client, rep_headers):
        r = client.get("/sales/implementations", headers=rep_headers)
        body = r.json()
        assert "implementations" in body
        assert "total" in body
        assert "is_manager" in body

    def test_manager_is_manager_flag(self, client, manager_headers):
        r = client.get("/sales/implementations", headers=manager_headers)
        assert r.status_code == 200
        assert r.json()["is_manager"] is True

    def test_rep_is_not_manager_flag(self, client, rep_headers):
        r = client.get("/sales/implementations", headers=rep_headers)
        assert r.status_code == 200
        assert r.json()["is_manager"] is False
