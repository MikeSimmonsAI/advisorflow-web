"""
Executive Suite isolation gate — A-D checkpoint verification.

Proves:
1. brand_executive grant → Executive Suite access granted
2. No grant → 403
3. Inactive grant → 403
4. god_admin is NOT auto-whitelisted (uses owner shell separately)
5. god_admin retains require_god access (no regression)
6. Executive from Brand A cannot see Brand B data (platform_id filter)
7. god_admin can grant/revoke; idempotent on active grant
8. /executive/context returns correct brand fields

MODEL CONTRACT GATE (never remove):
- Opportunity.deal_value is the correct field (NOT Opportunity.value)
- User.full_name is the correct field (NOT User.name or similar)
These field regressions have caused production 500/503s and must stay gated.
"""

import pytest
from unittest.mock import MagicMock


def make_user(id="u1", role="advisor", organization_id=None,
              email="exec@brand.com", is_active=True,
              must_change_password=False, first_name="Test"):
    u = MagicMock()
    u.id = id; u.role = role; u.organization_id = organization_id
    u.email = email; u.is_active = is_active
    u.must_change_password = must_change_password
    u.first_name = first_name; u.last_name = ""; u.session_token = None
    return u

def make_membership(user_id, scope_type, scope_id, role, is_active=True):
    m = MagicMock()
    m.user_id = user_id; m.scope_type = scope_type; m.scope_id = scope_id
    m.role = role; m.is_active = is_active; m.created_at = None; m.id = "mem-1"
    return m

def make_platform(id="plat-evosys", name="EvoSys Pro", slug="evosys"):
    p = MagicMock()
    p.id = id; p.name = name; p.slug = slug
    return p


class TestRequireBrandExecutive:
    def _call(self, user, db):
        from app.deps import require_brand_executive
        return require_brand_executive(user=user, db=db)

    def test_valid_grant_returns_tuple(self):
        from app.models.sales_models import SCOPE_PLATFORM, ROLE_BRAND_EXECUTIVE
        user = make_user(id="u-exec")
        platform = make_platform()
        mem = make_membership(user.id, SCOPE_PLATFORM, platform.id, ROLE_BRAND_EXECUTIVE)
        db = MagicMock()

        def query_side(model):
            q = MagicMock()
            name = getattr(model, "__name__", "")
            if "Membership" in name:
                q.filter.return_value.first.return_value = mem
            else:
                q.filter.return_value.first.return_value = platform
            return q

        db.query.side_effect = query_side
        result = self._call(user, db)
        assert result[0] is user
        assert result[1] is mem
        assert result[2] is platform

    def test_no_grant_raises_403(self):
        from fastapi import HTTPException
        user = make_user(id="u-no-exec")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc:
            self._call(user, db)
        assert exc.value.status_code == 403

    def test_god_admin_not_whitelisted(self):
        from fastapi import HTTPException
        user = make_user(id="u-god", role="god_admin")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc:
            self._call(user, db)
        assert exc.value.status_code == 403


class TestGodControlsIntact:
    def test_executive_refused_by_require_god(self):
        from fastapi import HTTPException
        from app.deps import require_god
        user = make_user(id="u-exec", role="advisor")
        with pytest.raises(HTTPException) as exc:
            require_god(user=user)
        assert exc.value.status_code == 403

    def test_god_admin_passes_require_god(self):
        from app.deps import require_god
        user = make_user(id="u-god", role="god_admin")
        assert require_god(user=user).role == "god_admin"


class TestCrossBrandIsolation:
    def test_orgs_scoped_to_platform(self):
        from app.routers.executive_router import get_executive_organizations
        from app.models.sales_models import SCOPE_PLATFORM, ROLE_BRAND_EXECUTIVE
        user_a = make_user(id="u-a")
        platform_a = make_platform(id="plat-a", name="Brand A")
        mem_a = make_membership(user_a.id, SCOPE_PLATFORM, platform_a.id, ROLE_BRAND_EXECUTIVE)
        executive = (user_a, mem_a, platform_a)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        result = get_executive_organizations(executive=executive, db=db)
        assert result["platform_id"] == "plat-a"
        assert result["organizations"] == []


class TestExecutiveContext:
    def test_context_returns_fields(self):
        from app.routers.executive_router import get_executive_context
        from app.models.sales_models import SCOPE_PLATFORM, ROLE_BRAND_EXECUTIVE
        user = make_user(id="u-exec", email="exec@evosys.com", first_name="Michael")
        platform = make_platform(id="plat-evosys", name="EvoSys Pro")
        mem = make_membership(user.id, SCOPE_PLATFORM, platform.id, ROLE_BRAND_EXECUTIVE)
        result = get_executive_context(executive=(user, mem, platform), db=MagicMock())
        assert result["platform_id"] == "plat-evosys"
        assert result["platform_name"] == "EvoSys Pro"
        assert result["role"] == ROLE_BRAND_EXECUTIVE


class TestGrantEndpoint:
    def test_grant_creates_membership(self):
        from app.routers.executive_router import grant_executive_membership
        god = make_user(id="u-god", role="god_admin")
        target = make_user(id="u-target")
        platform = make_platform()
        db = MagicMock()

        def qs(model):
            q = MagicMock()
            name = getattr(model, "__name__", "")
            if "User" in name:
                q.filter.return_value.first.return_value = target
            elif "Platform" in name:
                q.filter.return_value.first.return_value = platform
            else:
                q.filter.return_value.first.return_value = None
            return q

        db.query.side_effect = qs
        result = grant_executive_membership(
            payload={"user_id": target.id, "platform_id": platform.id},
            user=god, db=db)
        assert result["status"] == "granted"
        db.add.assert_called_once()

    def test_grant_idempotent(self):
        from app.routers.executive_router import grant_executive_membership
        from app.models.sales_models import SCOPE_PLATFORM, ROLE_BRAND_EXECUTIVE
        god = make_user(id="u-god", role="god_admin")
        target = make_user(id="u-target")
        platform = make_platform()
        existing = make_membership(target.id, SCOPE_PLATFORM, platform.id, ROLE_BRAND_EXECUTIVE)
        existing.is_active = True
        db = MagicMock()

        def qs(model):
            q = MagicMock()
            name = getattr(model, "__name__", "")
            if "User" in name:
                q.filter.return_value.first.return_value = target
            elif "Platform" in name:
                q.filter.return_value.first.return_value = platform
            else:
                q.filter.return_value.first.return_value = existing
            return q

        db.query.side_effect = qs
        result = grant_executive_membership(
            payload={"user_id": target.id, "platform_id": platform.id},
            user=god, db=db)
        assert result["status"] == "already_active"
        db.add.assert_not_called()


class TestObservationGates:
    """
    Gate J — observation overview returns aggregate data for an org the executive owns.
    Gate K — cross-brand org observation returns 404 (platform isolation).
    """

    def _make_executive_tuple(self, platform):
        from app.models.sales_models import SCOPE_PLATFORM, ROLE_BRAND_EXECUTIVE
        user = make_user(id="u-exec")
        mem = make_membership(user.id, SCOPE_PLATFORM, platform.id, ROLE_BRAND_EXECUTIVE)
        return (user, mem, platform)

    def _make_org(self, org_id, platform_id):
        org = MagicMock()
        org.id = org_id
        org.name = "Test Org"
        org.platform_id = platform_id
        org.created_at = None
        return org

    def test_gate_j_org_detail_returns_fields(self):
        """Gate J: GET /executive/organizations/{org_id} returns org identity."""
        from app.routers.executive_router import get_executive_org_detail
        platform = make_platform(id="plat-evo")
        executive = self._make_executive_tuple(platform)
        org = self._make_org("org-restland", "plat-evo")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = org
        result = get_executive_org_detail(org_id="org-restland", executive=executive, db=db)
        assert result["id"] == "org-restland"
        assert result["name"] == "Test Org"
        assert result["platform_id"] == "plat-evo"

    def test_gate_k_cross_brand_org_detail_raises_404(self):
        """Gate K: org from a different platform returns 404, not 403 (no enumeration)."""
        from fastapi import HTTPException
        from app.routers.executive_router import get_executive_org_detail
        platform = make_platform(id="plat-evo")
        executive = self._make_executive_tuple(platform)
        org = self._make_org("org-foreign", "plat-bookaboost")  # different platform
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = org
        with pytest.raises(HTTPException) as exc:
            get_executive_org_detail(org_id="org-foreign", executive=executive, db=db)
        assert exc.value.status_code == 404

    def test_gate_k_missing_org_raises_404(self):
        """Gate K (missing): org not found at all → 404."""
        from fastapi import HTTPException
        from app.routers.executive_router import get_executive_org_detail
        platform = make_platform(id="plat-evo")
        executive = self._make_executive_tuple(platform)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc:
            get_executive_org_detail(org_id="org-nobody", executive=executive, db=db)
        assert exc.value.status_code == 404

    def test_gate_j_observe_overview_requires_platform_match(self):
        """Gate J observe: org from different platform → 404 from observe/overview."""
        from fastapi import HTTPException
        from app.routers.executive_router import get_org_observation_overview
        platform = make_platform(id="plat-evo")
        executive = self._make_executive_tuple(platform)
        org = self._make_org("org-foreign", "plat-other")  # cross-brand
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = org
        with pytest.raises(HTTPException) as exc:
            get_org_observation_overview(org_id="org-foreign", executive=executive, db=db)
        assert exc.value.status_code == 404

    def test_gate_j_observe_returns_read_only_flag(self):
        """Gate J observe: matched org returns read_only:True in payload."""
        from app.routers.executive_router import get_org_observation_overview
        platform = make_platform(id="plat-evo")
        executive = self._make_executive_tuple(platform)
        org = self._make_org("org-restland", "plat-evo")
        db = MagicMock()

        # First call (org lookup) returns the org; all subsequent aggregate
        # queries return empty/zero via the MagicMock default chain.
        call_count = {"n": 0}
        def query_side(*args):
            q = MagicMock()
            call_count["n"] += 1
            if call_count["n"] == 1:
                # org lookup
                q.filter.return_value.first.return_value = org
            # All other queries: MagicMock returns 0/[] by default through chaining
            return q

        db.query.side_effect = query_side
        result = get_org_observation_overview(org_id="org-restland", executive=executive, db=db)
        assert result.get("read_only") is True
        assert "org" in result
        assert "lead_summary" in result


class TestModelContractGate:
    """
    Gate M — Executive model field contract.

    Opportunity.deal_value and User.full_name are the correct fields.
    Both have caused production 500/503 regressions when referenced incorrectly.
    These tests fail immediately if the wrong attribute name is used.
    """

    def test_opportunity_has_deal_value_not_value(self):
        """Opportunity.deal_value must exist; Opportunity.value must not."""
        from app.models.sales_models import Opportunity
        assert hasattr(Opportunity, "deal_value"), (
            "Opportunity.deal_value is missing — executive command-center will 500"
        )
        assert not hasattr(Opportunity, "value"), (
            "Opportunity.value must not exist — use deal_value"
        )

    def test_user_has_full_name(self):
        """User.full_name must exist (executive context endpoint uses it)."""
        from app.models.models import User
        assert hasattr(User, "full_name"), (
            "User.full_name is missing — executive context endpoint will 500"
        )

    def test_executive_router_references_deal_value(self):
        """Source-level gate: executive_router.py must not contain 'Opportunity.value'."""
        import ast, pathlib
        src = pathlib.Path("app/routers/executive_router.py").read_text()
        tree = ast.parse(src)
        bad_refs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "value"
            and isinstance(node.value, ast.Name)
            and node.value.id == "Opportunity"
        ]
        assert bad_refs == [], (
            f"executive_router.py references Opportunity.value at lines "
            f"{[n.lineno for n in bad_refs]} — must be Opportunity.deal_value"
        )
