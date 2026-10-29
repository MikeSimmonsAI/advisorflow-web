# -*- coding: utf-8 -*-
"""/auth/my-contexts `brand` - which brand's look Program Access wears.

Pins: decided by the server from the login's brand links (never per person);
a brand domain decides only for a login that belongs to it; the legacy "no
platform means BookaBoost" default is never used, so an EvoSys operator never
turns BookaBoost; god / super admin and mixed-brand logins get None; the
answer grants nothing (cards are unchanged). Synthetic data only.
"""
import uuid

import pytest

from app.models.models import Organization, Platform, User
from app.models.sales_models import BrandSalesOrg, Membership
from app.services.auth_service import create_access_token, hash_password


def _s():
    return uuid.uuid4().hex[:6]


def _u(db, email, org=None, role="advisor"):
    u = User(organization_id=org.id if org else None, email=email, full_name="Example Person", role=role,
             password_hash=hash_password("Pass12345!"), is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


def _m(db, u, st, sid, role):
    db.add(Membership(user_id=u.id, scope_type=st, scope_id=sid, role=role, is_active=True))
    db.commit()


@pytest.fixture()
def w(db_session):
    db = db_session
    # real slugs: the frozen brand config keys the look by them
    evo = db.query(Platform).filter(Platform.slug == "evosyspro").first() or Platform(name="EvoSys Pro", slug="evosyspro")
    bab = db.query(Platform).filter(Platform.slug == "bookaboost").first() or Platform(name="BookaBoost", slug="bookaboost")
    db.add_all([evo, bab])
    db.commit()
    evo_sales = BrandSalesOrg(platform_id=evo.id, name="EvoSys Pro Sales", slug="es-" + _s())
    bab_sales = BrandSalesOrg(platform_id=bab.id, name="BookaBoost Sales", slug="bs-" + _s())
    db.add_all([evo_sales, bab_sales])
    db.commit()
    bab_org = Organization(name="BookaBoost Wholesale", slug="bw-" + _s(), plan="trial", is_active=True, platform_id=bab.id)
    legacy = Organization(name="Legacy Org", slug="lg-" + _s(), plan="trial", is_active=True, platform_id=None)
    db.add_all([bab_org, legacy])
    db.commit()

    bb = _u(db, "bb-%s@example.com" % _s(), org=bab_org, role="org_admin")
    _m(db, bb, "customer_org", bab_org.id, "org_admin")
    _m(db, bb, "brand_sales_org", bab_sales.id, "sales_manager")
    evo_mgr = _u(db, "evo-%s@example.com" % _s())
    _m(db, evo_mgr, "brand_sales_org", evo_sales.id, "sales_manager")
    # an EvoSys operator whose customer orgs carry no platform (legacy)
    legacy_op = _u(db, "op-%s@example.com" % _s())
    _m(db, legacy_op, "customer_org", legacy.id, "org_admin")
    _m(db, legacy_op, "brand_sales_org", evo_sales.id, "sales_manager")
    legacy_only = _u(db, "lo-%s@example.com" % _s(), org=legacy, role="org_admin")
    _m(db, legacy_only, "customer_org", legacy.id, "org_admin")
    mixed = _u(db, "mx-%s@example.com" % _s())
    _m(db, mixed, "brand_sales_org", evo_sales.id, "sales_rep")
    _m(db, mixed, "brand_sales_org", bab_sales.id, "sales_rep")
    god = _u(db, "god-%s@example.com" % _s(), role="god_admin")
    return dict(db=db, bb=bb, evo_mgr=evo_mgr, legacy_op=legacy_op, legacy_only=legacy_only, mixed=mixed, god=god)


def _brand(client, w, who, origin=None):
    h = {"Authorization": "Bearer %s" % create_access_token(w[who], w["db"])}
    if origin:
        h["Origin"] = origin
    r = client.get("/auth/my-contexts", headers=h)
    assert r.status_code == 200, r.text
    return r.json()["brand"]


def test_bookaboost_admin_gets_bookaboost(client, w):
    b = _brand(client, w, "bb")
    assert b["slug"] == "bookaboost" and b["theme"] == "bookaboost" and b["name"] == "BookaBoost"
    assert _brand(client, w, "bb", "https://app.bookaboost.live")["theme"] == "bookaboost"


def test_evosys_logins_never_turn_bookaboost(client, w):
    assert _brand(client, w, "evo_mgr")["theme"] == "evosyspro"
    # legacy org without a platform is NOT read as BookaBoost here
    assert _brand(client, w, "legacy_op")["theme"] == "evosyspro"
    assert _brand(client, w, "legacy_only") is None


def test_brand_domain_decides_only_for_its_own_logins(client, w):
    assert _brand(client, w, "evo_mgr", "https://app.bookaboost.live") is None
    assert _brand(client, w, "bb", "https://app.evosyspro.live") is None
    assert _brand(client, w, "evo_mgr", "http://127.0.0.1:4173")["theme"] == "evosyspro"


def test_god_and_mixed_brand_logins_get_none(client, w):
    assert _brand(client, w, "god") is None
    assert _brand(client, w, "god", "https://app.bookaboost.live") is None
    assert _brand(client, w, "mixed") is None
    assert _brand(client, w, "mixed", "https://app.bookaboost.live")["theme"] == "bookaboost"


def test_brand_changes_no_destination(client, w):
    h = {"Authorization": "Bearer %s" % create_access_token(w["bb"], w["db"])}
    a = client.get("/auth/my-contexts", headers=h).json()
    b = client.get("/auth/my-contexts", headers=dict(h, Origin="https://app.evosyspro.live")).json()
    a.pop("brand"), b.pop("brand")
    assert a == b
