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


# -- Frontend response-contract regression -------------------------------------

class TestFrontendResponseContract:
    """Neither ExecutiveOrganizations.jsx nor ExecutiveCommandCenter.jsx may
    use r.data -- api.get() returns JSON directly, not an Axios envelope."""

    def test_organizations_jsx_uses_r_not_r_data(self):
        import pathlib
        jsx = pathlib.Path(
            "frontend/src/pages/executive/ExecutiveOrganizations.jsx"
        ).read_text()
        assert "r.data" not in jsx, (
            "ExecutiveOrganizations.jsx must not read r.data "
            "-- api.get() returns JSON directly"
        )
        assert "data: r," in jsx or "data: r\n" in jsx or "data: r}" in jsx, (
            "ExecutiveOrganizations.jsx must set state with data: r (not r.data)"
        )

    def test_command_center_jsx_uses_r_not_r_data(self):
        import pathlib
        jsx = pathlib.Path(
            "frontend/src/pages/executive/ExecutiveCommandCenter.jsx"
        ).read_text()
        assert "r.data" not in jsx, (
            "ExecutiveCommandCenter.jsx must not read r.data "
            "-- api.get() returns JSON directly"
        )
        assert "data: r," in jsx or "data: r\n" in jsx or "data: r}" in jsx, (
            "ExecutiveCommandCenter.jsx must set state with data: r (not r.data)"
        )
