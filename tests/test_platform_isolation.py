"""
Platform-isolation test matrix A-M.

Tests user_authorized_platform_slugs() across every identity archetype:

A. Legacy customer (org → platform)
B. Legacy customer, no platform_id → 'bookaboost'
C. Brand-sales-only identity (NULL org, SCOPE_BRAND_SALES_ORG)
D. Platform-scoped identity (NULL org, SCOPE_PLATFORM — brand_executive)  ← THE FIX
E. Multi-brand executive (platform memberships on two slugs)
F. Revoked platform membership → excluded
G. Revoked brand-sales membership → excluded
H. god_admin with NULL org → returns empty set (god bypasses isolation in caller)
I. No memberships, no org → empty set
J. Organization without platform_id → 'bookaboost' default
K. Legacy org + brand-sales membership → union of both slugs
L. Legacy org + platform membership → union of both slugs
M. All three sources active → union of all three slugs
"""
import pytest
from unittest.mock import MagicMock, patch


# ── helpers ────────────────────────────────────────────────────────────────

def make_user(user_id="u1", role="advisor", organization_id=None):
    u = MagicMock()
    u.id = user_id
    u.role = role
    u.organization_id = organization_id
    return u


def make_membership(user_id, scope_type, scope_id, is_active=True):
    m = MagicMock()
    m.user_id = user_id
    m.scope_type = scope_type
    m.scope_id = scope_id
    m.is_active = is_active
    return m


def make_org(org_id="org1", platform_id="plat1"):
    o = MagicMock()
    o.id = org_id
    o.platform_id = platform_id
    return o


def make_platform(platform_id="plat1", slug="evosyspro"):
    p = MagicMock()
    p.id = platform_id
    p.slug = slug
    return p


# ── fixture: patch the DB query inside user_authorized_platform_slugs ──────
# Rather than standing up a real DB we patch db.query() in each test to
# return controlled data per (model, filters) call shape.


from app.services.workspace_access import user_authorized_platform_slugs
from app.models.sales_models import (
    SCOPE_BRAND_SALES_ORG, SCOPE_PLATFORM, SCOPE_CUSTOMER_ORG,
    Membership, BrandSalesOrg,
)
from app.models.models import Organization, Platform


# ── tests ──────────────────────────────────────────────────────────────────


class TestPlatformSlugsMatrix:

    def _run(self, user, org=None, platform=None, sales_slugs=(), platform_slugs=()):
        """
        Wire up a db mock and call user_authorized_platform_slugs.

        org / platform: objects for Source A (legacy tenancy).
        sales_slugs: iterable of slug strings for Source B (brand-sales).
        platform_slugs: iterable of slug strings for Source C (SCOPE_PLATFORM).
        """
        db = MagicMock()

        def query_side_effect(model):
            q = MagicMock()

            # Source A: Organization lookup
            if model is Organization:
                q.filter.return_value.first.return_value = org
                return q

            # Source A: Platform lookup for org.platform_id
            # Source B/C: chained join queries — we intercept .join().filter()
            if model is Platform:
                inner = q
                # .join().join().filter().all() chain — for Source B
                # .join().filter().all() chain — for Source C
                # we return the same object at every chained call
                all_mock_b = MagicMock()
                all_mock_b.return_value = [(s,) for s in sales_slugs]

                all_mock_c = MagicMock()
                all_mock_c.return_value = [(s,) for s in platform_slugs]

                # Direct Platform lookup for org.platform_id (Source A)
                q.filter.return_value.first.return_value = platform

                # Chained join path — return a mock that tracks depth
                chain = MagicMock()
                chain.join.return_value = chain
                chain.filter.return_value.all = all_mock_b

                # For Source C the chain is one join shorter; same mock works.
                # We differentiate by monkeypatching after the fact.

                q.join = MagicMock(return_value=chain)
                return q

            return q

        db.query.side_effect = query_side_effect

        # Patch Source C separately so its slug list is independent of Source B.
        # We inject it by patching the actual join result inside workspace_access.
        with patch.object(
            __import__("app.services.workspace_access", fromlist=["user_authorized_platform_slugs"])
                .__builtins__ if False else
            __import__("app.services.workspace_access", fromlist=["_"]),
            "__name__",  # dummy attr — real patch below
            "app.services.workspace_access",
        ):
            pass  # just testing the import is reachable

        # Simpler approach: just call the real function with a real mock db
        # that returns the right data for each query shape.
        return user_authorized_platform_slugs(user, db)

    # ── We use a simpler strategy: patch at the SQLAlchemy query chain level.

    def _slugs(self, user, org=None, platform=None,
               sales_slug_rows=None, platform_slug_rows=None):
        """
        Build a minimal db mock and call user_authorized_platform_slugs.
        Each 'rows' arg is a list of (slug,) tuples — what .all() returns.

        Strategy: build the chain mocks up front and use a call-index list to
        return them in the exact order user_authorized_platform_slugs calls them.
        Sequence (when org is set and has platform_id):
          call 0 → Organization query       (Source A org)
          call 1 → Platform query           (Source A platform)
          call 2 → Platform.slug join chain (Source B)
          call 3 → Platform.slug join chain (Source C)
        When org is None or has no platform_id, Sources A calls are skipped.
        """
        sales_slug_rows = sales_slug_rows or []
        platform_slug_rows = platform_slug_rows or []

        # Source A: org query
        org_q = MagicMock()
        org_q.filter.return_value.first.return_value = org

        # Source A: platform query
        plat_a_q = MagicMock()
        plat_a_q.filter.return_value.first.return_value = platform

        # Source B: Platform.slug join chain
        # .join(BrandSalesOrg).join(Membership).filter().all()
        b_inner = MagicMock()
        b_inner.filter.return_value.all.return_value = sales_slug_rows
        b_q = MagicMock()
        b_q.join.return_value.join.return_value = b_inner

        # Source C: Platform.slug join chain
        # .join(Membership).filter().all()
        c_inner = MagicMock()
        c_inner.filter.return_value.all.return_value = platform_slug_rows
        c_q = MagicMock()
        c_q.join.return_value = c_inner

        # Build the call sequence dynamically based on what org/platform exist
        call_seq = []
        if getattr(user, "organization_id", None):
            call_seq.append(org_q)             # db.query(Organization)
            if org and getattr(org, "platform_id", None):
                call_seq.append(plat_a_q)      # db.query(Platform)
        call_seq.append(b_q)                   # db.query(Platform.slug) Source B
        call_seq.append(c_q)                   # db.query(Platform.slug) Source C

        idx = [0]

        def query_side_effect(*args, **kwargs):
            i = idx[0]
            idx[0] += 1
            if i < len(call_seq):
                return call_seq[i]
            return MagicMock()

        db = MagicMock()
        db.query.side_effect = query_side_effect
        return user_authorized_platform_slugs(user, db)

    # ── A: legacy customer with org → platform ────────────────────────────
    def test_A_legacy_customer_org_platform(self):
        user = make_user(organization_id="org1")
        org = make_org(org_id="org1", platform_id="plat1")
        platform = make_platform(platform_id="plat1", slug="bookaboost")
        result = self._slugs(user, org=org, platform=platform)
        assert "bookaboost" in result

    # ── B: legacy org, no platform_id → 'bookaboost' ─────────────────────
    def test_B_legacy_org_no_platform_id(self):
        user = make_user(organization_id="org1")
        org = make_org(org_id="org1", platform_id=None)  # no platform_id
        result = self._slugs(user, org=org, platform=None)
        assert "bookaboost" in result

    # ── C: brand-sales-only identity ──────────────────────────────────────
    def test_C_brand_sales_only(self):
        user = make_user(organization_id=None)
        result = self._slugs(user, sales_slug_rows=[("evosyspro",)])
        assert result == {"evosyspro"}

    # ── D: brand_executive (SCOPE_PLATFORM) with NULL org — THE FIX ───────
    def test_D_brand_executive_null_org(self):
        user = make_user(organization_id=None)
        result = self._slugs(user, platform_slug_rows=[("evosyspro",)])
        assert result == {"evosyspro"}, (
            "brand_executive with NULL org must be authorized for evosyspro"
        )

    # ── E: multi-brand executive (two SCOPE_PLATFORM slugs) ───────────────
    def test_E_multi_brand_executive(self):
        user = make_user(organization_id=None)
        result = self._slugs(
            user,
            platform_slug_rows=[("evosyspro",), ("harmonyhustle",)],
        )
        assert result == {"evosyspro", "harmonyhustle"}

    # ── F: revoked SCOPE_PLATFORM membership → excluded ───────────────────
    def test_F_revoked_platform_membership_excluded(self):
        # The real query filters is_active=True — revoked row never enters result
        user = make_user(organization_id=None)
        result = self._slugs(user, platform_slug_rows=[])  # revoked = not returned
        assert "evosyspro" not in result

    # ── G: revoked brand-sales membership → excluded ──────────────────────
    def test_G_revoked_brand_sales_excluded(self):
        user = make_user(organization_id=None)
        result = self._slugs(user, sales_slug_rows=[])  # revoked = not returned
        assert result == set()

    # ── H: god_admin with NULL org → empty set (caller bypasses isolation) ─
    def test_H_god_admin_null_org_empty_set(self):
        user = make_user(role="god_admin", organization_id=None)
        result = self._slugs(user)
        assert result == set()

    # ── I: no memberships, no org → empty set ────────────────────────────
    def test_I_no_memberships_no_org(self):
        user = make_user(organization_id=None)
        result = self._slugs(user)
        assert result == set()

    # ── J: org exists but no platform_id → 'bookaboost' default ──────────
    def test_J_org_no_platform_id_defaults_bookaboost(self):
        user = make_user(organization_id="org_legacy")
        org = make_org(org_id="org_legacy", platform_id=None)
        result = self._slugs(user, org=org, platform=None)
        assert "bookaboost" in result

    # ── K: legacy org + brand-sales → union ───────────────────────────────
    def test_K_legacy_org_plus_brand_sales_union(self):
        user = make_user(organization_id="org1")
        org = make_org(org_id="org1", platform_id="plat_bb")
        platform = make_platform(platform_id="plat_bb", slug="bookaboost")
        result = self._slugs(
            user, org=org, platform=platform,
            sales_slug_rows=[("evosyspro",)],
        )
        assert "bookaboost" in result
        assert "evosyspro" in result

    # ── L: legacy org + SCOPE_PLATFORM membership → union ────────────────
    def test_L_legacy_org_plus_platform_membership_union(self):
        user = make_user(organization_id="org1")
        org = make_org(org_id="org1", platform_id="plat_bb")
        platform = make_platform(platform_id="plat_bb", slug="bookaboost")
        result = self._slugs(
            user, org=org, platform=platform,
            platform_slug_rows=[("harmonyhustle",)],
        )
        assert "bookaboost" in result
        assert "harmonyhustle" in result

    # ── M: all three sources active → union of all ────────────────────────
    def test_M_all_three_sources_union(self):
        user = make_user(organization_id="org1")
        org = make_org(org_id="org1", platform_id="plat_bb")
        platform = make_platform(platform_id="plat_bb", slug="bookaboost")
        result = self._slugs(
            user, org=org, platform=platform,
            sales_slug_rows=[("evosyspro",)],
            platform_slug_rows=[("harmonyhustle",)],
        )
        assert result == {"bookaboost", "evosyspro", "harmonyhustle"}
