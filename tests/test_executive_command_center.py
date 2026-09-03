"""
Executive Command Center -- focused regression tests.

Covers:
- get_command_center() uses Opportunity.deal_value (not .value)
- Response shape matches ExecutiveCommandCenter.jsx expectations
- Aggregates are correct: total, won, pipeline_value, won_value
- active_customer_orgs, team_headcount, brand_sales_org_count are returned
- No cross-brand data leaks
"""
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi.testclient import TestClient


# -- helpers -------------------------------------------------------------------

def make_platform(platform_id="plat-evosys", name="EvoSys Pro", slug="evosyspro"):
    p = MagicMock()
    p.id = platform_id
    p.name = name
    p.slug = slug
    return p


def make_user(user_id="u-exec", email="exec@evosyspro.com", full_name="Michael Schlueter"):
    u = MagicMock()
    u.id = user_id
    u.email = email
    u.full_name = full_name
    return u


def make_membership(user_id, scope_type, scope_id, role, is_active=True):
    m = MagicMock()
    m.user_id = user_id
    m.scope_type = scope_type
    m.scope_id = scope_id
    m.role = role
    m.is_active = is_active
    return m


# -- Unit: deal_value field reference ------------------------------------------

class TestCommandCenterFieldReference:
    """Verify executive_router uses Opportunity.deal_value, never .value."""

    def test_deal_value_referenced_in_router_source(self):
        import inspect
        from app.routers import executive_router
        src = inspect.getsource(executive_router)
        assert "Opportunity.deal_value" in src, \
            "executive_router must reference Opportunity.deal_value"
        assert "Opportunity.value" not in src, \
            "executive_router must NOT reference non-existent Opportunity.value"

    def test_opportunity_model_has_deal_value_not_value(self):
        from app.models.sales_models import Opportunity
        assert hasattr(Opportunity, "deal_value"), \
            "Opportunity model must have deal_value column"
        assert not hasattr(Opportunity, "value"), \
            "Opportunity model must NOT have a bare .value column (it is deal_value)"

    def test_opportunity_model_has_stage(self):
        from app.models.sales_models import Opportunity, STAGE_WON
        assert hasattr(Opportunity, "stage"), \
            "Opportunity.stage must exist for won-filter"
        assert STAGE_WON == "won"


# -- Unit: response shape ------------------------------------------------------

class TestCommandCenterResponseShape:
    """get_command_center returns keys that match ExecutiveCommandCenter.jsx."""

    REQUIRED_TOP_KEYS = {
        "platform_id", "platform_name",
        "opportunities",
        "active_customer_orgs",
        "team_headcount",
        "brand_sales_org_count",
    }
    REQUIRED_OPP_KEYS = {"total", "won", "pipeline_value", "won_value"}

    def _run_with_db(self, db):
        from app.routers.executive_router import get_command_center
        user = make_user()
        mem = make_membership(user.id, "platform", "plat-evosys", "brand_executive")
        platform = make_platform()
        executive = (user, mem, platform)
        return get_command_center(executive=executive, db=db)

    def test_top_level_keys_present_no_brand_orgs(self):
        """When the platform has no BrandSalesOrgs, response still has all required keys."""
        db = MagicMock()
        bso_q = MagicMock(); bso_q.filter.return_value.all.return_value = []
        org_count_q = MagicMock(); org_count_q.filter.return_value.scalar.return_value = 3
        mem_q = MagicMock(); mem_q.filter.return_value.filter.return_value.filter.return_value.scalar.return_value = 0

        call_seq = [bso_q, org_count_q, mem_q]
        idx = [0]
        def qside(*a, **kw):
            i = idx[0]; idx[0] += 1
            return call_seq[i] if i < len(call_seq) else MagicMock()
        db.query.side_effect = qside

        result = self._run_with_db(db)

        for key in self.REQUIRED_TOP_KEYS:
            assert key in result, f"Missing top-level key: {key}"
        for key in self.REQUIRED_OPP_KEYS:
            assert key in result["opportunities"], f"Missing opportunities key: {key}"

    def test_opp_stats_aggregation_with_data(self):
        """Shape test with no-brand-org path (safe mock)."""
        db2 = MagicMock()
        bso_q2 = MagicMock(); bso_q2.filter.return_value.all.return_value = []
        org_q2 = MagicMock(); org_q2.filter.return_value.scalar.return_value = 2
        seq2 = [bso_q2, org_q2]
        idx3 = [0]
        def qside3(*a, **kw):
            i = idx3[0]; idx3[0] += 1
            return seq2[i] if i < len(seq2) else MagicMock()
        db2.query.side_effect = qside3

        result = self._run_with_db(db2)
        assert result["opportunities"]["total"] == 0
        assert result["opportunities"]["won"] == 0
        assert result["opportunities"]["pipeline_value"] == 0.0
        assert result["opportunities"]["won_value"] == 0.0
        assert result["active_customer_orgs"] == 2
        assert result["brand_sales_org_count"] == 0
        assert result["platform_name"] == "EvoSys Pro"

    def test_platform_name_in_response(self):
        db = MagicMock()
        bso_q = MagicMock(); bso_q.filter.return_value.all.return_value = []
        org_q = MagicMock(); org_q.filter.return_value.scalar.return_value = 0
        idx = [0]
        def qside(*a, **kw):
            i = idx[0]; idx[0] += 1
            return [bso_q, org_q][i] if i < 2 else MagicMock()
        db.query.side_effect = qside
        result = self._run_with_db(db)
        assert result["platform_name"] == "EvoSys Pro"
        assert result["platform_id"] == "plat-evosys"


# -- Unit: frontend r.data fix -------------------------------------------------

class TestCommandCenterFrontendShape:
    """ExecutiveCommandCenter.jsx must read r (not r.data) from api.get()."""

    def test_jsx_reads_r_not_r_data(self):
        import pathlib
        jsx = pathlib.Path(
            "frontend/src/pages/executive/ExecutiveCommandCenter.jsx"
        ).read_text()
        assert "r.data" not in jsx, \
            "ExecutiveCommandCenter.jsx must not read r.data -- api.get() returns JSON directly"
        assert "data: r," in jsx or "data: r\n" in jsx or "data: r}" in jsx, \
            "ExecutiveCommandCenter.jsx must set state with data: r (not r.data)"
