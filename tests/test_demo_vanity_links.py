"""
SS9 — branded vanity demo links, without breaking a single token URL.

THE DEFECT. `create` always inserts a new row and mints a new 43-character
secret, so republishing a demo changed its address. A rep who had read a link
down a phone, put it in a proposal or pasted it into a message had to do it
again after every edit - and the project's own README says "the token CHANGES
on every republish".

THE RULE. The token stays the canonical address and is tried first, so every
link already sent resolves byte-for-byte as before. A slug is the same demo
under a name a person can say, and unlike the token it SURVIVES republish.
"""

import pytest

from app.models.demo_site_models import DemoSite
from app.services import demo_sites as ds


@pytest.fixture()
def brand_world(db_session):
    """Two brands, each with an opportunity, so the isolation is real."""
    from app.models.models import Platform
    from app.models.sales_models import BrandSalesOrg, Opportunity

    made = {}
    for slug in ("brand-a", "brand-b"):
        platform = Platform(name=slug, slug=slug)
        db_session.add(platform); db_session.flush()
        brand = BrandSalesOrg(name=slug, slug=slug, platform_id=platform.id)
        db_session.add(brand); db_session.flush()
        opp = Opportunity(company_name=f"{slug} deal", brand_sales_org_id=brand.id)
        db_session.add(opp); db_session.flush()
        opp2 = Opportunity(company_name=f"{slug} deal two", brand_sales_org_id=brand.id)
        db_session.add(opp2); db_session.flush()
        made[slug] = {"platform": platform, "brand": brand,
                      "opp": opp, "opp2": opp2}
    db_session.commit()
    return made


def _publish(db_session, opp, *, slug=None, html="<p>hi</p>", title="Demo"):
    return ds.create(db_session, opp, None, title=title, html=html, slug=slug)


# ── the name ────────────────────────────────────────────────────────────────

def test_a_readable_name_opens_the_demo(db_session, brand_world):
    opp = brand_world["brand-a"]["opp"]
    out = _publish(db_session, opp, slug="Countryside")
    assert out["ok"], out.get("error")
    assert out["demo"].slug == "countryside"
    assert ds.resolve(db_session, "countryside") is not None


def test_the_token_still_opens_it_and_is_tried_first(db_session, brand_world):
    opp = brand_world["brand-a"]["opp"]
    demo = _publish(db_session, opp, slug="countryside")["demo"]
    assert ds.resolve(db_session, demo.token).id == demo.id


def test_the_name_survives_a_republish_and_the_token_does_not(
        db_session, brand_world):
    """The whole point. A rep says the name once and keeps saying it."""
    opp = brand_world["brand-a"]["opp"]
    first = _publish(db_session, opp, slug="atlantis", html="<p>v1</p>")["demo"]
    first_token = first.token

    second = _publish(db_session, opp, slug="atlantis", html="<p>v2</p>")["demo"]
    assert second.token != first_token, "republish still mints a new secret"

    by_name = ds.resolve(db_session, "atlantis")
    assert by_name.id == second.id
    assert "v2" in by_name.html

    # And the historical token still opens its own historical version.
    historical = ds.resolve(db_session, first_token)
    assert historical.id == first.id and "v1" in historical.html


def test_two_brands_may_each_have_a_prospect_called_countryside(
        db_session, brand_world):
    a = brand_world["brand-a"]
    b = brand_world["brand-b"]
    assert _publish(db_session, a["opp"], slug="countryside")["ok"]
    assert _publish(db_session, b["opp"], slug="countryside")["ok"]

    from_a = ds.resolve(db_session, "countryside",
                        brand_sales_org_id=a["brand"].id)
    from_b = ds.resolve(db_session, "countryside",
                        brand_sales_org_id=b["brand"].id)
    assert from_a.brand_sales_org_id == a["brand"].id
    assert from_b.brand_sales_org_id == b["brand"].id
    assert from_a.id != from_b.id


def test_one_brand_cannot_take_a_name_another_deal_holds(db_session, brand_world):
    a = brand_world["brand-a"]
    assert _publish(db_session, a["opp"], slug="evosys-sales")["ok"]
    clash = _publish(db_session, a["opp2"], slug="evosys-sales")
    assert clash["ok"] is False
    assert "already in use" in clash["error"]


def test_retiring_a_demo_frees_its_name_and_kills_the_link(
        db_session, brand_world):
    opp = brand_world["brand-a"]["opp"]
    demo = _publish(db_session, opp, slug="countryside")["demo"]
    ds.revoke(db_session, demo)
    db_session.commit()

    assert ds.resolve(db_session, "countryside") is None
    assert ds.resolve(db_session, demo.token) is None
    # And the name is reusable rather than held hostage by a dead deal.
    assert _publish(db_session, brand_world["brand-a"]["opp2"],
                    slug="countryside")["ok"]


def test_an_expired_demo_is_not_reachable_by_its_name(db_session, brand_world):
    from datetime import datetime, timedelta
    opp = brand_world["brand-a"]["opp"]
    demo = _publish(db_session, opp, slug="countryside")["demo"]
    demo.expires_at = datetime.utcnow() - timedelta(days=1)
    db_session.commit()
    assert ds.resolve(db_session, "countryside") is None


# ── the vocabulary ──────────────────────────────────────────────────────────

def test_a_name_is_normalised_not_mangled(db_session):
    assert ds.normalize_slug("Countryside Land Partners") == "countryside-land-partners"
    assert ds.normalize_slug("EvoSys__Sales") == "evosys-sales"
    assert ds.normalize_slug(None) is None
    assert ds.normalize_slug("   ") is None


def test_a_name_that_would_shadow_a_real_route_is_refused(db_session):
    for reserved in ("reset", "seed", "preview", "admin"):
        with pytest.raises(ValueError, match="reserved"):
            ds.normalize_slug(reserved)


def test_an_unusable_name_is_an_error_not_a_silent_substitution(db_session):
    """slugify-ing whatever arrives is how a demo ends up at an address nobody
    expected and the rep reads the wrong URL down the phone."""
    for bad in ("a", "-leading", "trailing-", "Wxy Z!!", "x" * 60):
        with pytest.raises(ValueError):
            ds.normalize_slug(bad)


def test_publishing_with_a_bad_name_publishes_nothing(db_session, brand_world):
    opp = brand_world["brand-a"]["opp"]
    out = _publish(db_session, opp, slug="reset")
    assert out["ok"] is False
    assert db_session.query(DemoSite).count() == 0


def test_a_demo_with_no_name_is_unaffected(db_session, brand_world):
    opp = brand_world["brand-a"]["opp"]
    demo = _publish(db_session, opp)["demo"]
    assert demo.slug is None
    assert ds.resolve(db_session, demo.token).id == demo.id


def test_many_unnamed_demos_do_not_collide(db_session, brand_world):
    """NULL slugs must not trip the unique index."""
    opp = brand_world["brand-a"]["opp"]
    for _ in range(5):
        assert _publish(db_session, opp)["ok"]
    db_session.commit()
    assert db_session.query(DemoSite).count() == 5


def test_the_serialised_shape_carries_both_addresses(db_session, brand_world):
    opp = brand_world["brand-a"]["opp"]
    demo = _publish(db_session, opp, slug="countryside")["demo"]
    out = ds.out(demo, base_url="https://app.example.com")
    assert out["slug"] == "countryside"
    assert out["url"] == "https://app.example.com/demo/countryside"
    assert out["token_url"].endswith(demo.token)


# ═══════════════════════════════════════════════════════════════════════════
# SS9 VERIFICATION PASS — scoping, permissions, and no customer in the code
# ═══════════════════════════════════════════════════════════════════════════

def test_a_brands_host_never_resolves_another_brands_name(db_session, brand_world):
    """The same readable name is safe for two brands only if the lookup is
    scoped to the brand the request arrived at."""
    a = brand_world["brand-a"]; b = brand_world["brand-b"]
    _publish(db_session, a["opp"], slug="countryside")
    db_session.commit()

    # Brand A's own host finds it.
    assert ds.resolve(db_session, "countryside",
                      brand_sales_org_id=a["brand"].id) is not None
    # Brand B's host does not, even though the name exists.
    assert ds.resolve(db_session, "countryside",
                      brand_sales_org_id=b["brand"].id) is None


def test_a_revoked_demos_name_cannot_be_resolved_by_any_brand(
        db_session, brand_world):
    a = brand_world["brand-a"]
    demo = _publish(db_session, a["opp"], slug="retire-me")["demo"]
    db_session.commit()
    ds.revoke(db_session, demo)
    db_session.commit()
    for brand in (a["brand"].id, brand_world["brand-b"]["brand"].id, None):
        assert ds.resolve(db_session, "retire-me", brand_sales_org_id=brand) is None


def test_a_wrong_token_and_a_wrong_name_fail_identically(db_session, brand_world):
    """Distinguishing 'expired' from 'never existed' turns this into an oracle
    for guessing live links - which matters more now that an address can be a
    short readable word rather than a 43-character secret."""
    a = brand_world["brand-a"]
    demo = _publish(db_session, a["opp"], slug="guessable")["demo"]
    db_session.commit()
    ds.revoke(db_session, demo)
    db_session.commit()
    assert ds.resolve(db_session, "guessable") is None
    assert ds.resolve(db_session, "never-existed-at-all") is None
    assert ds.resolve(db_session, "not-a-real-token-value") is None


def test_taking_a_name_never_moves_another_brands_row(db_session, brand_world):
    """Brand B publishing a name brand A already uses must leave A's row
    untouched - not silently repoint it."""
    a = brand_world["brand-a"]; b = brand_world["brand-b"]
    mine = _publish(db_session, a["opp"], slug="shared-word")["demo"]
    db_session.commit()
    mine_id, mine_token = mine.id, mine.token

    theirs = _publish(db_session, b["opp"], slug="shared-word")["demo"]
    db_session.commit()

    still = db_session.query(DemoSite).filter(DemoSite.id == mine_id).one()
    assert still.slug == "shared-word"
    assert still.token == mine_token
    assert theirs.id != mine_id
    assert ds.resolve(db_session, mine_token) is not None


def test_the_owner_lookup_is_scoped_to_one_brand(db_session, brand_world):
    a = brand_world["brand-a"]; b = brand_world["brand-b"]
    _publish(db_session, a["opp"], slug="owned")
    db_session.commit()
    assert ds.slug_owner(db_session, a["brand"].id, "owned") is not None
    assert ds.slug_owner(db_session, b["brand"].id, "owned") is None


def test_a_republish_keeps_the_name_on_exactly_one_row(db_session, brand_world):
    """Two live rows holding the same slug would make resolution arbitrary."""
    a = brand_world["brand-a"]
    first = _publish(db_session, a["opp"], slug="once")["demo"]
    db_session.commit()
    second = _publish(db_session, a["opp"], slug="once")["demo"]
    db_session.commit()
    holders = (db_session.query(DemoSite)
               .filter(DemoSite.brand_sales_org_id == a["brand"].id,
                       DemoSite.slug == "once").all())
    assert len(holders) == 1
    assert holders[0].id == second.id
    # And the first demo's token still opens it - nothing already sent broke.
    assert ds.resolve(db_session, first.token) is not None


def test_no_customer_or_brand_is_named_in_the_demo_link_code():
    """SS9 is platform machinery. A literal here is a customer's name in the
    engine, and the next one gets added because the first one was allowed."""
    import io, re
    from pathlib import Path
    banned = (r"\bevosys\b", r"\bbookaboost\b", r"\brestland\b",
              r"\batlantis\b", r"\bcountryside land\b")
    root = Path(__file__).resolve().parents[1] / "app"
    offences = []
    for rel in ("services/demo_sites.py", "models/demo_site_models.py"):
        text = io.open(root / rel, encoding="utf-8").read().lower()
        for pattern in banned:
            if re.search(pattern, text):
                offences.append("%s: %s" % (rel, pattern))
    assert offences == [], offences


def test_the_public_route_takes_a_token_or_a_name_and_nothing_else(client):
    """A path that resolved arbitrary input would be a different surface."""
    r = client.get("/public/demo/definitely-not-a-real-demo")
    assert r.status_code in (404, 410), r.status_code


def test_publishing_requires_an_authenticated_caller(client):
    r = client.post("/sales/opportunities/does-not-exist/demo-site",
                    json={"title": "x", "html": "<p>x</p>"})
    assert r.status_code in (401, 403), r.status_code


def test_revoking_requires_an_authenticated_caller(client):
    r = client.post("/sales/demo-sites/does-not-exist/revoke")
    assert r.status_code in (401, 403), r.status_code
