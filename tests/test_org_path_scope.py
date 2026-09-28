"""Routes that take an organization id in their PATH must scope it.

THE DEFECT FIXED HERE
=====================
`launch_experience_router._actor_platform_ids` and
`commercial_router._actor_platform_ids` counted `users.platform_id` as a
brand-staff grant for ANY role. That column is a platform-admin grant only for
a super_admin (see its model comment and `get_platform_org_ids`), and customer
identities carry it too: `customer_activation.create_customer_admin` stamps
`org.platform_id` on every org_admin / advisor / viewer it creates. So a
customer's own org_admin was "staff" of their whole brand and could:

  * GET  /launch-experience/preview/{other customer}    - read their launch
  * PUT  /launch-experience/config/brand/{brand}         - rewrite every
                                                           customer's shell
  * PUT  /launch-experience/config/organization/{other}  - rewrite another
                                                           customer's shell
  * GET  /commercial/onboarding/{other customer}         - read their
                                                           onboarding + overrides

The existing suites never saw it because their customer fixtures have no
platform_id. These fixtures give the customer one, exactly as production does.

Also pinned: the admin_router org-path routes stay brand-scoped for super_admin
(load_org_in_scope) and the platform-reassignment route stays god-only.

Out-of-scope answers are 404, never 403, matching load_org_in_scope.
"""
import itertools
import uuid

import pytest

from app.models.implementation_models import (
    Implementation, ImplementationMilestone, MILESTONE_PENDING,
)
from app.models.launch_experience_models import LaunchExperienceConfig
from app.models.models import Organization, Platform, TierDefinition, User
from app.models.sales_models import BrandSalesOrg
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


# ── builders ────────────────────────────────────────────────────────────────

def _platform(db, name):
    p = Platform(name=name, slug="brand-%s" % uuid.uuid4().hex[:8], short_name=name[:2],
                 tagline="t", support_email="support@%s.test" % uuid.uuid4().hex[:6])
    db.add(p)
    db.commit()
    return p


def _bso(db, plat):
    b = BrandSalesOrg(platform_id=plat.id, name="%s Sales" % plat.name,
                      slug="bso-%d" % next(_SEQ))
    db.add(b)
    db.commit()
    return b


def _org(db, name, platform, industry="energy"):
    o = Organization(name=name, slug="o-%s" % uuid.uuid4().hex[:8], plan="standard",
                     industry=industry, is_active=True, platform_id=platform.id)
    db.add(o)
    db.commit()
    return o


def _user(db, role, org=None, platform=None, label="u"):
    u = User(organization_id=(org.id if org else None),
             platform_id=(platform.id if platform else None),
             email="%s-%d-%s@test.local" % (label, next(_SEQ), uuid.uuid4().hex[:6]),
             password_hash=hash_password("TestPass123!"), full_name=label.title(),
             role=role, is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _impl(db, org, plat, bso):
    im = Implementation(organization_id=org.id, platform_id=plat.id,
                        brand_sales_org_id=bso.id,
                        opportunity_id="opp-%d" % next(_SEQ), status="not_started")
    db.add(im)
    db.commit()
    for i, key in enumerate(["business_profile", "customer_users", "calendar",
                             "lead_import", "launch"]):
        db.add(ImplementationMilestone(implementation_id=im.id, key=key,
                                       label=key.replace("_", " ").title(),
                                       position=i, is_required=(key != "lead_import"),
                                       status=MILESTONE_PENDING))
    db.commit()
    return im


def _h(db, user):
    return {"Authorization": "Bearer " + create_access_token(user, db)}


@pytest.fixture()
def world(db_session):
    """Brand A with two customers, brand B with one.

    `admin_a1` is a customer org_admin carrying brand A's platform_id - the
    shape customer_activation.create_customer_admin produces in production.
    """
    db = db_session
    brand_a = _platform(db, "Brand A")
    brand_b = _platform(db, "Brand B")
    bso_a, bso_b = _bso(db, brand_a), _bso(db, brand_b)
    a1 = _org(db, "Customer A1", brand_a)
    a2 = _org(db, "Customer A2", brand_a)
    b1 = _org(db, "Customer B1", brand_b)
    return {
        "brand_a": brand_a, "brand_b": brand_b,
        "a1": a1, "a2": a2, "b1": b1,
        "impl_a1": _impl(db, a1, brand_a, bso_a),
        "impl_a2": _impl(db, a2, brand_a, bso_a),
        "impl_b1": _impl(db, b1, brand_b, bso_b),
        "admin_a1": _user(db, "org_admin", org=a1, platform=brand_a, label="admina1"),
        "advisor_a1": _user(db, "advisor", org=a1, platform=brand_a, label="adva1"),
        "super_a": _user(db, "super_admin", org=a1, platform=brand_a, label="supera"),
        "super_b": _user(db, "super_admin", org=b1, platform=brand_b, label="superb"),
        "god": _user(db, "god_admin", label="god"),
    }


# ════════════════════════════════════════════════════════════════════════════
# 1. LAUNCH EXPERIENCE - preview / config (FIXED)
# ════════════════════════════════════════════════════════════════════════════

class TestLaunchExperiencePreview:

    def test_customer_admin_cannot_preview_another_customer_on_same_brand(
            self, client, db_session, world):
        r = client.get("/launch-experience/preview/" + world["a2"].id,
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text

    def test_customer_advisor_cannot_preview_another_customer(
            self, client, db_session, world):
        r = client.get("/launch-experience/preview/" + world["a2"].id,
                       headers=_h(db_session, world["advisor_a1"]))
        assert r.status_code == 404, r.text

    def test_refusal_is_indistinguishable_from_nonexistent(
            self, client, db_session, world):
        h = _h(db_session, world["admin_a1"])
        real = client.get("/launch-experience/preview/" + world["a2"].id, headers=h)
        fake = client.get("/launch-experience/preview/does-not-exist", headers=h)
        assert real.status_code == fake.status_code == 404
        assert real.json() == fake.json()

    def test_brand_super_admin_may_preview_own_brand_customer(
            self, client, db_session, world):
        r = client.get("/launch-experience/preview/" + world["a2"].id,
                       headers=_h(db_session, world["super_a"]))
        assert r.status_code == 200, r.text
        assert r.json()["preview_context"]["organization_id"] == world["a2"].id

    def test_other_brand_super_admin_cannot_preview(self, client, db_session, world):
        r = client.get("/launch-experience/preview/" + world["a2"].id,
                       headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text

    def test_god_may_preview_any_customer(self, client, db_session, world):
        r = client.get("/launch-experience/preview/" + world["b1"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text


class TestLaunchExperienceConfig:

    def _rows(self, db):
        db.expire_all()
        return db.query(LaunchExperienceConfig).count()

    def test_customer_admin_cannot_rewrite_the_brand_layer(
            self, client, db_session, world):
        before = self._rows(db_session)
        r = client.put("/launch-experience/config/brand/" + world["brand_a"].id,
                       json={"name": "hijacked", "presentation": {"headline": "x"}},
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text
        assert self._rows(db_session) == before

    def test_customer_admin_cannot_rewrite_another_customers_layer(
            self, client, db_session, world):
        before = self._rows(db_session)
        r = client.put("/launch-experience/config/organization/" + world["a2"].id,
                       json={"name": "hijacked"},
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text
        assert self._rows(db_session) == before

    def test_customer_admin_cannot_resolve_another_customers_config(
            self, client, db_session, world):
        r = client.get("/launch-experience/config/resolved",
                       params={"organization_id": world["a2"].id},
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text

    def test_customer_admin_lists_no_brand_or_customer_layers(
            self, client, db_session, world):
        db_session.add(LaunchExperienceConfig(scope_type="brand",
                                              scope_id=world["brand_a"].id,
                                              name="Brand A layer"))
        db_session.add(LaunchExperienceConfig(scope_type="organization",
                                              scope_id=world["a2"].id,
                                              name="A2 layer"))
        db_session.commit()
        body = client.get("/launch-experience/config",
                          headers=_h(db_session, world["admin_a1"])).json()
        scopes = {c["scope_type"] for c in body["configs"]}
        assert "brand" not in scopes and "organization" not in scopes

    def test_brand_super_admin_may_configure_own_brand(self, client, db_session, world):
        r = client.put("/launch-experience/config/brand/" + world["brand_a"].id,
                       json={"name": "Brand A shell"},
                       headers=_h(db_session, world["super_a"]))
        assert r.status_code == 200, r.text
        assert r.json()["scope_id"] == world["brand_a"].id

    def test_other_brand_super_admin_cannot_configure(self, client, db_session, world):
        r = client.put("/launch-experience/config/brand/" + world["brand_a"].id,
                       json={"name": "nope"},
                       headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text


# ════════════════════════════════════════════════════════════════════════════
# 2. COMMERCIAL ONBOARDING (FIXED)
# ════════════════════════════════════════════════════════════════════════════

class TestCommercialOnboarding:

    def test_customer_admin_cannot_read_another_customers_onboarding(
            self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["a2"].id,
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text

    def test_customer_admin_cannot_list_another_customers_overrides(
            self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s/overrides" % world["a2"].id,
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text

    def test_customer_admin_cannot_read_own_staff_onboarding_either(
            self, client, db_session, world):
        """The staff surface was never the customer's; platform_id changes nothing."""
        r = client.get("/commercial/onboarding/%s" % world["a1"].id,
                       headers=_h(db_session, world["admin_a1"]))
        assert r.status_code == 404, r.text

    def test_brand_super_admin_may_read_own_brand_onboarding(
            self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["a2"].id,
                       headers=_h(db_session, world["super_a"]))
        assert r.status_code == 200, r.text

    def test_other_brand_super_admin_cannot_read(self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["a2"].id,
                       headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text

    def test_god_may_read_any(self, client, db_session, world):
        r = client.get("/commercial/onboarding/%s" % world["b1"].id,
                       headers=_h(db_session, world["god"]))
        assert r.status_code == 200, r.text


# ════════════════════════════════════════════════════════════════════════════
# 3. ADMIN ROUTER - already guarded by load_org_in_scope / require_god (pinned)
# ════════════════════════════════════════════════════════════════════════════

class TestAdminOrgPathRoutes:

    def test_foreign_super_admin_cannot_update_org(self, client, db_session, world):
        r = client.put("/admin/organizations/%s" % world["a2"].id,
                       json={"name": "RENAMED BY SUPER B"},
                       headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text
        db_session.expire_all()
        assert db_session.get(Organization, world["a2"].id).name == "Customer A2"

    def test_own_brand_super_admin_may_update_org(self, client, db_session, world):
        r = client.put("/admin/organizations/%s" % world["a2"].id,
                       json={"name": "Customer A2 Renamed"},
                       headers=_h(db_session, world["super_a"]))
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Customer A2 Renamed"

    def test_customer_admin_cannot_update_org(self, client, db_session, world):
        r = client.put("/admin/organizations/%s" % world["a2"].id,
                       json={"name": "x"}, headers=_h(db_session, world["admin_a1"]))
        assert r.status_code in (403, 404), r.text

    def test_foreign_super_admin_cannot_seed_tiers(self, client, db_session, world):
        r = client.post("/admin/orgs/%s/seed-industry-tiers" % world["a2"].id,
                        headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text
        assert db_session.query(TierDefinition).filter(
            TierDefinition.organization_id == world["a2"].id).count() == 0

    def test_own_brand_super_admin_may_seed_tiers(self, client, db_session, world):
        r = client.post("/admin/orgs/%s/seed-industry-tiers" % world["a2"].id,
                        headers=_h(db_session, world["super_a"]))
        assert r.status_code == 200, r.text

    def test_foreign_super_admin_cannot_seed_demo(self, client, db_session, world):
        r = client.post("/admin/demo/seed/%s" % world["a2"].id,
                        json={"num_leads": 1, "days_span": 1},
                        headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text

    def test_foreign_super_admin_cannot_wipe_demo(self, client, db_session, world):
        r = client.delete("/admin/demo/wipe/%s" % world["a2"].id,
                          headers=_h(db_session, world["super_b"]))
        assert r.status_code == 404, r.text

    def test_super_admin_cannot_move_org_between_platforms(
            self, client, db_session, world):
        r = client.patch("/admin/orgs/%s/platform" % world["a2"].id,
                         json={"platform_id": world["brand_b"].id},
                         headers=_h(db_session, world["super_b"]))
        assert r.status_code == 403, r.text
        db_session.expire_all()
        assert db_session.get(Organization, world["a2"].id).platform_id == world["brand_a"].id
