"""PERMANENT BRAND-RESOLUTION GATE (backend half) - reusable across modules.

A page that works but renders the wrong tenant/platform brand is a FAILED
acceptance test. On a host that is not a brand domain (localhost, preview
hosts) the shell must take its brand from the AUTHENTICATED organization's
platform, which `GET /branding/org` states as `platform`. These tests pin that
answer; tests/frontend/brandResolution.test.mjs pins how the shell uses it, and
scripts/review/brand_gate.py checks it in a real browser on every acceptance run.
"""
import pytest

from app.models.models import Platform


def _link(db_session, org, slug, name):
    platform = Platform(name=name, slug=slug)
    db_session.add(platform)
    db_session.flush()
    org.platform_id = platform.id
    db_session.commit()
    return platform


def test_an_evosyspro_workspace_resolves_evosyspro_and_its_wholesale_product(
        client, auth_headers, db_session, sample_org):
    _link(db_session, sample_org, "evosyspro", "EvoSys Pro")
    body = client.get("/branding/org", headers=auth_headers).json()
    assert body["organization_id"] == sample_org.id
    p = body["platform"]
    assert p["slug"] == "evosyspro"
    assert p["theme"] == "evosyspro"
    assert p["display_name"] == "EvoSys Pro"
    assert p["products"]["wholesale"] == "EvoSys Wholesale"


def test_another_brand_never_borrows_evosyspro_s_product_name(
        client, auth_headers, db_session, sample_org):
    _link(db_session, sample_org, "bookaboost", "BookaBoost")
    p = client.get("/branding/org", headers=auth_headers).json()["platform"]
    assert p["slug"] == "bookaboost"
    assert p["products"]["wholesale"] is None


def test_a_workspace_with_no_platform_states_that_rather_than_guessing(
        client, auth_headers, sample_org):
    assert sample_org.platform_id is None
    assert client.get("/branding/org", headers=auth_headers).json()["platform"] is None


def test_product_names_are_brand_owned_and_never_guessed():
    from app.services.brand_config import product_name
    assert product_name("evosyspro", "wholesale") == "EvoSys Wholesale"
    assert product_name("EvoSysPro", "wholesale") == "EvoSys Wholesale"
    assert product_name("bookaboost", "wholesale") is None
    assert product_name(None, "wholesale") is None
    assert product_name("evosyspro", "unknown_module") is None


# ── Offering vs naming (express run 2026-09-28) ─────────────────────────────
# BookaBoost has no commercial name for Wholesale, but the entitlement control
# plane - not the name table - decides whether the brand OFFERS it. Fiber Cartel
# (BookaBoost, entitled) could open every Wholesale screen by URL and find none.

def test_bookaboost_offers_wholesale_by_default_without_borrowing_a_name(
        client, auth_headers, db_session, sample_org):
    _link(db_session, sample_org, "bookaboost", "BookaBoost")
    p = client.get("/branding/org", headers=auth_headers).json()["platform"]
    assert p["products"]["wholesale"] is None
    assert p["offered"]["wholesale"] is True


def test_a_brand_level_disable_withdraws_the_offer_and_an_org_grant_restores_it(
        client, auth_headers, db_session, sample_org):
    from app.services import entitlement_resolver as er
    plat = _link(db_session, sample_org, "bookaboost", "BookaBoost")
    er.set_override(db_session, scope="brand", scope_id=plat.id,
                    feature_key="wholesale_real_estate", state="disabled",
                    actor_user_id=None, reason="test")
    db_session.commit()
    p = client.get("/branding/org", headers=auth_headers).json()["platform"]
    assert p["offered"]["wholesale"] is False
    er.set_override(db_session, scope="org", scope_id=sample_org.id,
                    feature_key="wholesale_real_estate", state="enabled",
                    actor_user_id=None, reason="test")
    db_session.commit()
    body = client.get("/branding/org", headers=auth_headers).json()
    assert body["platform"]["offered"]["wholesale"] is True
