"""Dependency-free contract for vanity demo link resolution. Loads the production
demo_sites module with stubbed ORM imports; synthetic data only, no DB/network."""
import importlib.util
import os
import sys
import types
import unittest
from datetime import datetime
from types import SimpleNamespace

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _load():
    stubs = {
        "sqlalchemy": types.ModuleType("sqlalchemy"),
        "sqlalchemy.orm": types.SimpleNamespace(Session=object),
        "app.models.demo_site_models": types.SimpleNamespace(
            DemoSite=object, mint_token=lambda: "t", DEFAULT_TTL_DAYS=30),
        "app.models.sales_models": types.SimpleNamespace(Opportunity=object),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "demo_sites_under_test",
            os.path.join(_ROOT, "app", "services", "demo_sites.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


DS = _load()
NOW = datetime(2026, 10, 7, 12, 0, 0)


def _row(brand="b1", live=True):
    r = SimpleNamespace(brand=brand)
    r.is_live = lambda now=None, _l=live: _l
    return r


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Slugs(unittest.TestCase):
    def test_canonical(self):
        self.assertEqual(DS.normalize_slug(" Country_Side  "), "country-side")
        self.assertEqual(DS.normalize_slug("a--b-c"), "a-b-c")

    def test_reserved_and_internal_rejected(self):
        for bad in ("admin", "api", "../x", "a/b", "http://x.co", "ab"):
            with self.assertRaises(ValueError):
                DS.normalize_slug(bad)

    def test_public_url_uses_demo_route(self):
        self.assertEqual(DS.public_url("https://x.test/", "countryside"),
                         "https://x.test/demo/countryside")


class PickSlugRow(unittest.TestCase):
    def test_single_live(self):
        r = _row()
        self.assertIs(DS.pick_slug_row([r], NOW), r)

    def test_dead_rows_never_resolve(self):
        self.assertIsNone(DS.pick_slug_row([_row(live=False)], NOW))
        self.assertIsNone(DS.pick_slug_row([], NOW))

    def test_dead_row_does_not_shadow_live_one(self):
        live = _row()
        self.assertIs(DS.pick_slug_row([_row(live=False), live], NOW), live)

    def test_cross_brand_collision_is_ambiguous_not_first(self):
        self.assertIsNone(DS.pick_slug_row([_row("b1"), _row("b2")], NOW))


class Wiring(unittest.TestCase):
    def test_resolve_uses_pick_not_first(self):
        s = _src("app/services/demo_sites.py")
        body = s[s.index("def resolve("):s.index("def out(")]
        self.assertIn("pick_slug_row(q.all(), now)", body)
        self.assertNotIn("q.first()", body)

    def test_expired_holder_does_not_block_name(self):
        self.assertIn("holder.is_live(now)", _src("app/services/demo_sites.py"))

    def test_revoke_clears_slug_and_deactivates(self):
        d = SimpleNamespace(slug="x", is_active=True, revoked_at=None)
        DS.revoke(None, d, NOW)
        self.assertIsNone(d.slug)
        self.assertFalse(d.is_active)
        self.assertEqual(d.revoked_at, NOW)

    def test_mutation_routes_are_authorized_and_brand_scoped(self):
        s = _src("app/routers/sales_proposal_router.py")
        pub = s[s.index("def publish_demo_site"):s.index("def list_demo_sites")]
        self.assertIn("require_sales_member", pub)
        self.assertIn("sales_org_ids(user, db)", pub)
        rev = s[s.index("def revoke_demo_site"):s.index("def _brand_for_host")]
        self.assertIn("require_sales_member", rev)
        self.assertIn("demo.brand_sales_org_id not in sales_org_ids", rev)

    def test_publish_returns_copyable_vanity_url(self):
        s = _src("app/routers/sales_proposal_router.py")
        self.assertIn("demo.slug or demo.token", s)
        self.assertIn("share_url", s)
        ui = _src("frontend/src/pages/sales/DemoSitesPanel.jsx")
        self.assertIn("copy(r.url)", ui)


if __name__ == "__main__":
    unittest.main()
