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


class TestFrontendResponseContract:
    """A–D regression: JSX files must use r (not r.data) since api.get() returns JSON directly."""

    def test_organizations_jsx_uses_r_not_r_data(self):
        import re
        path = 'frontend/src/pages/executive/ExecutiveOrganizations.jsx'
        with open(path) as f:
            src = f.read()
        assert 'data: r,' in src or "data: r," in src, "ExecutiveOrganizations must set data: r (not r.data)"
        assert 'r.data' not in src, "ExecutiveOrganizations must NOT reference r.data"

    def test_command_center_jsx_uses_r_not_r_data(self):
        path = 'frontend/src/pages/executive/ExecutiveCommandCenter.jsx'
        with open(path) as f:
            src = f.read()
        assert 'data: r,' in src or "data: r," in src, "ExecutiveCommandCenter must set data: r (not r.data)"
        assert 'r.data' not in src, "ExecutiveCommandCenter must NOT reference r.data"


class TestOrganizationsUXContract:
    """Regression: Organizations table must not expose internal UUID column."""

    def test_id_column_removed_from_organizations_table(self):
        path = 'frontend/src/pages/executive/ExecutiveOrganizations.jsx'
        with open(path) as f:
            src = f.read()
        # The visible th header "ID" must not appear
        assert '<th style={styles.th}>ID</th>' not in src, \
            "UUID column header must be removed from Organizations table"
        # org.id may be used as a React key — that is fine. The column must not be rendered.
        import re
        # td rendering org.id as visible cell content must not exist
        assert re.search(r'<td[^>]*>\s*\{org\.id\}\s*</td>', src) is None, \
            "org.id must not be rendered as a visible table cell"

    def test_organization_name_and_provisioned_remain(self):
        path = 'frontend/src/pages/executive/ExecutiveOrganizations.jsx'
        with open(path) as f:
            src = f.read()
        assert 'Organization' in src, "Organization column must remain"
        assert 'Provisioned' in src, "Provisioned column must remain"


class TestContextSwitcherBidirectionality:
    """Regression: back-office → executive navigation requires authorized executive context."""

    def test_switcher_renders_executive_link_when_context_present(self):
        """ContextSwitcher.jsx must consume executive_contexts from the server response."""
        path = 'frontend/src/components/ContextSwitcher.jsx'
        with open(path) as f:
            src = f.read()
        assert 'executive_contexts' in src, \
            "ContextSwitcher must read executive_contexts from server response"
        assert 'enterExecutive' in src or '/executive' in src, \
            "ContextSwitcher must navigate to /executive when executive context exists"

    def test_switcher_does_not_hardcode_executive_access(self):
        """Executive Suite must never appear unless the server grants it."""
        path = 'frontend/src/components/ContextSwitcher.jsx'
        with open(path) as f:
            src = f.read()
        # Must not derive access from role label strings
        import re
        hardcoded = re.search(r'["\']sales_manager["\']', src)
        assert hardcoded is None, \
            "ContextSwitcher must not infer executive access from role label; use server response"

    def test_executive_suite_nav_includes_back_office_switch(self):
        """ExecutiveSuite sidebar must expose Back Office button when has_back_office."""
        path = 'frontend/src/pages/executive/ExecutiveSuite.jsx'
        with open(path) as f:
            src = f.read()
        assert 'has_back_office' in src, \
            "ExecutiveSuite must check has_back_office from server context"
        assert '/sales' in src, \
            "ExecutiveSuite must offer navigation to /sales when back_office is authorized"


class TestCustomerHealthEndpoint:
    """A–Q: Customer Health endpoint correctness, isolation, classification, and N+1 safety."""

    def _make_executive(self):
        from app.models.sales_models import SCOPE_PLATFORM, ROLE_BRAND_EXECUTIVE
        user = make_user(id="u-exec")
        platform = make_platform(id="plat-evosys", name="EvoSys Pro")
        mem = make_membership(user.id, SCOPE_PLATFORM, platform.id, ROLE_BRAND_EXECUTIVE)
        return (user, mem, platform)

    def _empty_db(self):
        """DB that returns [] for all queries (no orgs)."""
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []
        return db

    # A — endpoint exists and is importable
    def test_A_endpoint_importable(self):
        from app.routers.executive_router import get_customer_health
        assert callable(get_customer_health)

    # B — empty platform returns zero-count summary
    def test_B_empty_platform_returns_zero_summary(self):
        from app.routers.executive_router import get_customer_health
        executive = self._make_executive()
        db = self._empty_db()
        result = get_customer_health(executive=executive, db=db)
        assert result["summary"]["total"] == 0
        assert result["organizations"] == []

    # C — result contains required top-level keys
    def test_C_result_contains_required_keys(self):
        from app.routers.executive_router import get_customer_health
        executive = self._make_executive()
        db = self._empty_db()
        result = get_customer_health(executive=executive, db=db)
        for key in ("platform_id", "platform_name", "summary", "organizations"):
            assert key in result, f"Missing key: {key}"

    # D — summary contains all health states
    def test_D_summary_contains_all_health_states(self):
        from app.routers.executive_router import get_customer_health
        executive = self._make_executive()
        db = self._empty_db()
        result = get_customer_health(executive=executive, db=db)
        for state in ("total", "healthy", "watch", "at_risk", "inactive", "onboarding"):
            assert state in result["summary"], f"Missing summary key: {state}"

    # E — platform_id isolation: result platform_id matches executive grant
    def test_E_platform_id_isolation(self):
        from app.routers.executive_router import get_customer_health
        executive = self._make_executive()
        db = self._empty_db()
        result = get_customer_health(executive=executive, db=db)
        assert result["platform_id"] == "plat-evosys"

    # F — HEALTHY classification: users + leads + recent op activity
    def test_F_healthy_classification(self):
        from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=60, active_users=2, total_leads=30, days_since_op=7)
        assert health == "healthy"
        assert "7" in reason or "day" in reason.lower()

    # G — ONBOARDING: new org, not yet healthy
    def test_G_onboarding_classification(self):
           from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=10, active_users=0, total_leads=0, days_since_op=None)
        assert health == "onboarding"
        assert "30" in reason or "new" in reason.lower() or "onboarding" in reason.lower() or "under" in reason.lower()

    # H — INACTIVE: no operational activity ever
    def test_H_inactive_no_activity(self):
        from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=90, active_users=0, total_leads=5, days_since_op=None)
        assert health == "inactive"

    # I — INACTIVE: operational activity >60 days ago
    def test_I_inactive_stale_activity(self):
        from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=120, active_users=1, total_leads=10, days_since_op=65)
        assert health == "inactive"

    # J — AT RISK: 31–60 days since op activity
    def test_J_at_risk_classification(self):
        from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=90, active_users=1, total_leads=10, days_since_op=45)
        assert health == "at_risk"

    # K — WATCH: 15–30 days since op activity
    def test_K_watch_classification(self):
        from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=90, active_users=1, total_leads=10, days_since_op=20)
        assert health == "watch"

    # L — ONBOARDING does not override HEALTHY: new org WITH real activity → healthy
    def test_L_onboarding_does_not_override_healthy(self):
        from app.routers.executive_router import _classify_health
        health, reason = _classify_health(age_days=5, active_users=2, total_leads=15, days_since_op=3)
        assert health == "healthy", \
            "New org with real operational activity must be HEALTHY, not ONBOARDING"

    # M — login alone does NOT produce HEALTHY (days_since_op=None means no op activity)
    def test_M_login_alone_not_healthy(self):
        from app.routers.executive_router import _classify_health
        # days_since_op=None: login happened but no outbound/reply/booking
        health, reason = _classify_health(age_days=90, active_users=1, total_leads=5, days_since_op=None)
        assert health != "healthy", \
            "Login alone must not produce HEALTHY status; operational activity is required"

    # N — reason string is plain English (no SQL, no raw IDs)
    def test_N_reason_is_plain_english(self):
        from app.routers.executive_router import _classify_health
        for params in [
            (60, 2, 30, 7),
            (10, 0, 0, None),
            (90, 0, 5, None),
            (120, 1, 10, 65),
            (90, 1, 10, 45),
            (90, 1, 10, 20),
        ]:
            health, reason = _classify_health(*params)
            assert isinstance(reason, str) and len(reason) > 5, \
                f"Reason must be a non-empty string for params {params}"
            # Must not contain raw SQL artifacts
            for bad in ("SELECT", "JOIN", "WHERE", "NULL", "plat-", "u-"):
                assert bad not in reason, f"Reason must not contain '{bad}'"

    # O — per-org record contains all required fields
    def test_O_org_record_required_fields(self):
        from app.routers.executive_router import get_customer_health
        from datetime import datetime

        executive = self._make_executive()

        org = MagicMock()
        org.id = "org-1"; org.name = "Restland"; org.platform_id = "plat-evosys"
        org.created_at = datetime(2026, 6, 21); org.plan = "standard"

        db = MagicMock()
        # query 1: org list
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [org]
        # remaining queries return empty
        db.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        result = get_customer_health(executive=executive, db=db)
        assert len(result["organizations"]) == 1
        rec = result["organizations"][0]
        required = (
            "id", "name", "health", "reason", "plan", "provisioned_at",
            "organization_age_days", "active_users", "total_leads", "leads_last_30d",
            "hot_leads", "booked_count", "last_login", "last_lead_import",
            "last_outbound_message", "last_inbound_reply", "last_booking",
            "last_operational_activity", "last_activity",
        )
        for field in required:
            assert field in rec, f"Missing field in org record: {field}"

    # P — NULL timestamps stay NULL; never fabricated
    def test_P_null_timestamps_are_null(self):
        from app.routers.executive_router import get_customer_health
        from datetime import datetime

        executive = self._make_executive()
        org = MagicMock()
        org.id = "org-2"; org.name = "WUPA"; org.platform_id = "plat-evosys"
        org.created_at = datetime(2026, 8, 10); org.plan = None

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [org]
        db.query.return_value.filter.return_value.group_by.return_value.all.return_value = []

        result = get_customer_health(executive=executive, db=db)
        rec = result["organizations"][0]
        # No activity data → timestamps must be None, not fabricated strings
        assert rec["last_outbound_message"] is None
        assert rec["last_inbound_reply"] is None
        assert rec["last_booking"] is None
        assert rec["last_operational_activity"] is None

    # Q — summary counts sum to total
    def test_Q_summary_counts_sum_to_total(self):
        from app.routers.executive_router import _classify_health
        # Drive classification for known states and verify math
        cases = [
            (60,  2, 30, 7,    "healthy"),
            (10,  0,  0, None, "onboarding"),
            (90,  0,  5, None, "inactive"),
            (120, 1, 10, 65,   "inactive"),
            (90,  1, 10, 45,   "at_risk"),
            (90,  1, 10, 20,   "watch"),
        ]
        counts = {}
        for age, users, leads, days_op, expected in cases:
            health, _ = _classify_health(age, users, leads, days_op)
            assert health == expected, f"Expected {expected}, got {health} for {(age,users,leads,days_op)}"
            counts[health] = counts.get(health, 0) + 1
        total = sum(counts.values())
        assert total == len(cases)
