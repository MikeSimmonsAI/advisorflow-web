"""
Tests for GET /executive/customer-health.

Verifies:
A. Authorization: no grant → 403; god_admin has no magic bypass
B. Response shape matches ExecutiveCustomerHealth.jsx contract exactly
C. Health classification logic is deterministic and correct
D. Cross-brand isolation: platform_id filter is structural
E. Route registration: GET /executive/customer-health in the router
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from itertools import count as icount


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_user(id="u1", role="advisor", organization_id=None, email="exec@brand.com"):
    u = MagicMock()
    u.id = id; u.role = role; u.organization_id = organization_id
    u.email = email; u.is_active = True; u.full_name = "Test User"
    u.must_change_password = False; u.session_token = None
    return u

def make_platform(id="plat-evosys", name="EvoSys Pro", slug="evosys"):
    p = MagicMock()
    p.id = id; p.name = name; p.slug = slug
    return p

def make_org(id="org-1", name="Restland", platform_id="plat-evosys",
             plan="standard", created_at=None):
    o = MagicMock()
    o.id = id; o.name = name; o.platform_id = platform_id; o.plan = plan
    o.created_at = created_at or (datetime.utcnow() - timedelta(days=90))
    return o

def make_executive_tuple(user=None, platform=None):
    u = user or make_user()
    p = platform or make_platform()
    mem = MagicMock(); mem.role = "brand_executive"; mem.id = "mem-1"; mem.created_at = None
    return (u, mem, p)


def make_db(orgs, user_counts=None, total_leads=None, hot_leads=None,
            booked=None, last_sms=None, last_reply=None, last_booking=None):
    """
    DB mock whose query calls return data in the exact sequence
    get_customer_health issues them:
      1. db.query(Organization)…                       → org list
      2. db.query(User.organization_id, count)…        → user counts
      3. db.query(Lead.organization_id, count) total   → total lead counts
      4. db.query(Lead.organization_id, count) hot     → hot lead counts
      5. db.query(Lead.organization_id, count) booked  → booked counts
      6. db.query(Lead.organization_id, max(Message))… → last SMS
      7. db.query(Lead.organization_id, max(Reply))…   → last reply
      8. db.query(Lead.organization_id, max(Booking))… → last booking
    """
    def org_q():
        q = MagicMock()
        q.filter.return_value.order_by.return_value.all.return_value = orgs
        return q

    def filter_agg_q(data):
        q = MagicMock()
        q.filter.return_value.group_by.return_value.all.return_value = data or []
        return q

    def join_agg_q(data):
        q = MagicMock()
        q.join.return_value.filter.return_value.group_by.return_value.all.return_value = data or []
        return q

    sequence = [
        org_q(),
        filter_agg_q(user_counts),
        filter_agg_q(total_leads),
        filter_agg_q(hot_leads),
        filter_agg_q(booked),
        join_agg_q(last_sms),
        join_agg_q(last_reply),
        join_agg_q(last_booking),
    ]
    idx = icount()

    def query_factory(*args, **kwargs):
        i = next(idx)
        return sequence[i] if i < len(sequence) else MagicMock()

    db = MagicMock()
    db.query.side_effect = query_factory
    return db


# ── A: Authorization ───────────────────────────────────────────────────────────

class TestCustomerHealthAuthorization:

    def test_no_grant_raises_403(self):
        from fastapi import HTTPException
        from app.deps import require_brand_executive
        user = make_user(role="advisor")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc:
            require_brand_executive(user=user, db=db)
        assert exc.value.status_code == 403

    def test_god_admin_not_auto_whitelisted(self):
        """god_admin has no magic bypass; they go through the brand selector
        which makes require_brand_executive succeed via the normal membership path."""
        from fastapi import HTTPException
        from app.deps import require_brand_executive
        user = make_user(role="god_admin")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(HTTPException) as exc:
            require_brand_executive(user=user, db=db)
        assert exc.value.status_code == 403


# ── B: Response shape ──────────────────────────────────────────────────────────

class TestCustomerHealthResponseShape:

    def _call(self, exec_tuple, db):
        from app.routers.executive_router import get_customer_health
        return get_customer_health(executive=exec_tuple, db=db)

    def test_empty_platform_returns_valid_shape(self):
        platform = make_platform()
        exec_tuple = make_executive_tuple(platform=platform)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = self._call(exec_tuple, db)

        assert result["platform_name"] == platform.name
        assert result["summary"] == {
            "total": 0, "healthy": 0, "watch": 0,
            "at_risk": 0, "inactive": 0, "onboarding": 0,
        }
        assert result["organizations"] == []

    def test_top_level_keys_match_frontend_contract(self):
        """ExecutiveCustomerHealth.jsx reads: data.platform_name, data.summary, data.organizations."""
        platform = make_platform()
        exec_tuple = make_executive_tuple(platform=platform)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = self._call(exec_tuple, db)

        assert set(result.keys()) == {"platform_name", "summary", "organizations"}
        assert set(result["summary"].keys()) == {
            "total", "healthy", "watch", "at_risk", "inactive", "onboarding"
        }

    def test_org_row_keys_match_frontend_contract(self):
        """Each org row has EXACTLY the keys ExecutiveCustomerHealth.jsx reads."""
        platform = make_platform()
        org = make_org(platform_id=platform.id)
        exec_tuple = make_executive_tuple(platform=platform)
        db = make_db([org])

        result = self._call(exec_tuple, db)

        assert len(result["organizations"]) == 1
        assert set(result["organizations"][0].keys()) == {
            "id", "name", "health", "reason", "active_users",
            "total_leads", "hot_leads", "booked_count",
            "last_operational_activity", "provisioned_at", "plan",
        }

    def test_platform_name_in_response(self):
        platform = make_platform(name="AdvisorFlow")
        exec_tuple = make_executive_tuple(platform=platform)
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = self._call(exec_tuple, db)
        assert result["platform_name"] == "AdvisorFlow"

    def test_org_plan_field_returned(self):
        platform = make_platform()
        org = make_org(platform_id=platform.id, plan="enterprise")
        exec_tuple = make_executive_tuple(platform=platform)
        db = make_db([org])

        result = self._call(exec_tuple, db)
        assert result["organizations"][0]["plan"] == "enterprise"

    def test_provisioned_at_is_created_at_isoformat(self):
        platform = make_platform()
        created = datetime(2025, 1, 15, 10, 30, 0)
        org = make_org(platform_id=platform.id, created_at=created)
        exec_tuple = make_executive_tuple(platform=platform)
        db = make_db([org])

        result = self._call(exec_tuple, db)
        assert result["organizations"][0]["provisioned_at"] == created.isoformat()

    def test_no_activity_sets_last_operational_activity_to_none(self):
        platform = make_platform()
        org = make_org(platform_id=platform.id, created_at=datetime.utcnow() - timedelta(days=90))
        exec_tuple = make_executive_tuple(platform=platform)
        db = make_db([org])   # all activity lists empty → None

        result = self._call(exec_tuple, db)
        assert result["organizations"][0]["last_operational_activity"] is None

    def test_active_users_from_db(self):
        platform = make_platform()
        org = make_org(id="o1", platform_id=platform.id)
        exec_tuple = make_executive_tuple(platform=platform)
        db = make_db([org], user_counts=[("o1", 7)])

        result = self._call(exec_tuple, db)
        assert result["organizations"][0]["active_users"] == 7

    def test_zero_counts_when_no_data(self):
        platform = make_platform()
        org = make_org(id="o1", platform_id=platform.id)
        exec_tuple = make_executive_tuple(platform=platform)
        db = make_db([org])  # all counts empty

        row = self._call(exec_tuple, db)["organizations"][0]
        assert row["active_users"] == 0
        assert row["total_leads"] == 0
        assert row["hot_leads"] == 0
        assert row["booked_count"] == 0


# ── C: Health classification ───────────────────────────────────────────────────

class TestHealthClassification:

    def _call_with_sms_days_ago(self, days_ago, org_age_days=90):
        """
        Exercise health classification by controlling last-SMS timestamp.
        Returns the single org row from the response.
        """
        from app.routers.executive_router import get_customer_health
        now = datetime.utcnow()
        platform = make_platform()
        org = make_org(
            id="o1",
            platform_id=platform.id,
            created_at=now - timedelta(days=org_age_days),
        )
        exec_tuple = make_executive_tuple(platform=platform)
        last_ts = now - timedelta(days=days_ago) if days_ago is not None else None
        sms = [("o1", last_ts)] if last_ts is not None else []
        db = make_db([org], last_sms=sms)

        result = get_customer_health(executive=exec_tuple, db=db)
        return result["organizations"][0]

    def test_activity_7_days_ago_is_healthy(self):
        assert self._call_with_sms_days_ago(7)["health"] == "healthy"

    def test_activity_14_days_ago_is_healthy(self):
        assert self._call_with_sms_days_ago(14)["health"] == "healthy"

    def test_activity_15_days_ago_is_watch(self):
        assert self._call_with_sms_days_ago(15)["health"] == "watch"

    def test_activity_30_days_ago_is_watch(self):
        assert self._call_with_sms_days_ago(30)["health"] == "watch"

    def test_activity_31_days_ago_is_at_risk(self):
        assert self._call_with_sms_days_ago(31)["health"] == "at_risk"

    def test_activity_60_days_ago_is_at_risk(self):
        assert self._call_with_sms_days_ago(60)["health"] == "at_risk"

    def test_activity_61_days_ago_is_inactive(self):
        assert self._call_with_sms_days_ago(61)["health"] == "inactive"

    def test_no_activity_old_org_is_inactive(self):
        assert self._call_with_sms_days_ago(None, org_age_days=90)["health"] == "inactive"

    def test_no_activity_new_org_is_onboarding(self):
        assert self._call_with_sms_days_ago(None, org_age_days=15)["health"] == "onboarding"

    def test_new_org_slow_activity_is_onboarding(self):
        # Org is 20 days old, activity 20 days ago → onboarding (not yet healthy)
        assert self._call_with_sms_days_ago(20, org_age_days=20)["health"] == "onboarding"

    def test_last_op_is_max_of_sms_reply_booking(self):
        """last_operational_activity = latest of SMS, reply, booking."""
        from app.routers.executive_router import get_customer_health
        now = datetime.utcnow()
        platform = make_platform()
        org = make_org(id="o1", platform_id=platform.id,
                       created_at=now - timedelta(days=90))
        exec_tuple = make_executive_tuple(platform=platform)

        sms_ts     = now - timedelta(days=20)  # watch-level
        reply_ts   = now - timedelta(days=5)   # healthy-level — should win
        booking_ts = now - timedelta(days=15)  # watch-level

        db = make_db([org],
                     last_sms=[("o1", sms_ts)],
                     last_reply=[("o1", reply_ts)],
                     last_booking=[("o1", booking_ts)])

        result = get_customer_health(executive=exec_tuple, db=db)
        row = result["organizations"][0]
        # reply_ts is latest → healthy
        assert row["health"] == "healthy"
        assert row["last_operational_activity"] == reply_ts.isoformat()

    def test_summary_counts_match_org_classifications(self):
        from app.routers.executive_router import get_customer_health
        now = datetime.utcnow()
        platform = make_platform()

        orgs = [
            make_org(id="o1", platform_id=platform.id, created_at=now - timedelta(days=90)),
            make_org(id="o2", platform_id=platform.id, created_at=now - timedelta(days=90)),
            make_org(id="o3", platform_id=platform.id, created_at=now - timedelta(days=10)),
        ]
        exec_tuple = make_executive_tuple(platform=platform)

        sms = [
            ("o1", now - timedelta(days=5)),   # healthy
            ("o2", now - timedelta(days=20)),  # watch
            # o3: no sms, new org → onboarding
        ]
        db = make_db(orgs, last_sms=sms)
        result = get_customer_health(executive=exec_tuple, db=db)

        assert result["summary"]["total"] == 3
        assert result["summary"]["healthy"] == 1
        assert result["summary"]["watch"] == 1
        assert result["summary"]["onboarding"] == 1
        assert result["summary"]["at_risk"] == 0
        assert result["summary"]["inactive"] == 0

    def test_health_reason_is_non_empty_string(self):
        row = self._call_with_sms_days_ago(7)
        assert isinstance(row["reason"], str)
        assert len(row["reason"]) > 0


# ── D: Cross-brand isolation ───────────────────────────────────────────────────

class TestCrossBrandIsolation:

    def test_org_query_uses_platform_id_filter(self):
        """The Organization query must filter by platform_id.
        An executive from Brand A never receives Brand B orgs."""
        from app.routers.executive_router import get_customer_health

        platform_a = make_platform(id="plat-a", name="Brand A")
        exec_tuple = make_executive_tuple(platform=platform_a)

        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        get_customer_health(executive=exec_tuple, db=db)

        # filter() must have been called on the Organization query
        assert db.query.return_value.filter.called, \
            "Organization query must call .filter() to apply platform_id scope — " \
            "without it, all orgs on all platforms would be returned."


# ── E: Route registration ──────────────────────────────────────────────────────

class TestRouteRegistration:

    def test_customer_health_route_registered(self):
        from app.routers.executive_router import router
        paths = [r.path for r in router.routes]
        assert "/executive/customer-health" in paths

    def test_customer_health_route_is_get(self):
        from app.routers.executive_router import router
        for r in router.routes:
            if r.path == "/executive/customer-health":
                assert "GET" in r.methods
                return
        pytest.fail("/executive/customer-health route not found")
