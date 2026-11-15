"""Back Office Pricing & Plans: who may see the rates; God Mode stays owner-only."""
import itertools

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     BrandPackage, BrandSalesOrg, Membership)
from app.services import capabilities as caps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(email="pp%d@example.test" % next(_SEQ), password_hash=hash_password("x"),
             full_name=name, role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def w(db_session):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-pp-%d" % next(_SEQ)); db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand)
    db.add(BrandPackage(platform_id=plat.id, key="starter", name="Starter", setup_fee=1500, monthly_price=597))
    db.commit()
    god = _user(db, "God", "god_admin")
    out = {"db": db, "god": god}
    for k, role, grants in (("owner", ROLE_SALES_MANAGER, ["sales_program_owner"]),
                            ("finance", ROLE_SALES_MANAGER, ["sales_comp_view"]),
                            ("mgr", ROLE_SALES_MANAGER, []), ("rep", ROLE_SALES_REP, [])):
        u = _user(db, k)
        db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id, role=role, is_active=True))
        db.commit()
        if grants:
            caps.set_brand_grants(db, u, brand.id, brand.name, god, grants)
        out[k] = u
    return out


def g(client, w, who):
    return client.get("/sales-team/pricing", headers={"Authorization": "Bearer " + create_access_token(w[who], w["db"])})


def test_owner_god_and_granted_finance_see_pricing(client, w):
    for who in ("owner", "god", "finance"):
        r = g(client, w, who)
        assert r.status_code == 200, (who, r.text)
        j = r.json()
        assert j["rules_version"] and j["packages"][0]["setup_fee"] == 1500.0
        basic = [p for p in j["commission"]["plans"] if p["key"] == "basic"][0]
        assert basic["payout"] == {"rep": 500.0, "asm": 200.0, "sevp": 300.0}
    assert g(client, w, "god").json()["can_edit_in_god_mode"] is True
    assert g(client, w, "owner").json()["can_edit_in_god_mode"] is False


def test_others_are_refused(client, w):
    assert g(client, w, "mgr").status_code == 403
    assert g(client, w, "rep").status_code == 403
    assert client.get("/sales-team/pricing").status_code == 401


def test_god_pricing_api_stays_god_only(client, w):
    h = {"Authorization": "Bearer " + create_access_token(w["owner"], w["db"])}
    assert client.get("/god/pricing/overview", headers=h).status_code in (401, 403)
