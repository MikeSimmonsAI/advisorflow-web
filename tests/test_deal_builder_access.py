"""DEAL BUILDER — the salesperson sees their own commission; nobody else's.

What must hold:
  * the salesperson commission engine produces the agreed figures;
  * the salesperson's API answer carries NO other role's figures or labels;
  * the executive page is served only to the owner or an explicit
    `deal_builder_executive` grantee — never to a rep, never to a sales
    manager, never to a signed-out visitor;
  * the executive page and the team split are not in the public static site.
"""
import itertools
import json
import os
import re

import pytest

from app.models.models import Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandSalesOrg,
                                     Membership)
from app.services import capabilities as caps
from app.services import deal_builder_commission as dbc
from app.services.auth_service import create_access_token, hash_password

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SEQ = itertools.count(1)
UPSTREAM = re.compile(r"area sales|sr executive|executive vice|m&m|evosys keeps|"
                      r"retained|allocation|\b55\b|\b10%|\b15%.*split", re.I)


# ── engine ────────────────────────────────────────────────────────────────

def _c(**kw):
    base = dict(plan="custom_saas", saas_setup=0, addon_setup=0, monthly=0,
                basis="quote", collected_setup=0, collected_monthly=0)
    base.update(kw)
    return dbc.seller_commission(base)


def test_saas_50k_setup_default_monthly():
    r = _c(saas_setup=50000, monthly=7500)
    assert r["one_time"]["total"] == 10000.0
    assert r["recurring"]["monthly"] == 1500.0
    assert r["setup_total"] == 50000.0


def test_saas_monthly_override_4000():
    r = _c(saas_setup=50000, monthly=4000)
    assert r["recurring"]["monthly"] == 800.0
    assert r["one_time"]["total"] == 10000.0


@pytest.mark.parametrize("plan,setup,payout", [
    ("basic", 1600, 500), ("growth", 3200, 1000), ("professional", 5900, 2000)])
def test_standard_package_fixed_payouts(plan, setup, payout):
    r = _c(plan=plan, monthly={"basic": 700, "growth": 1300, "professional": 2400}[plan])
    assert r["setup_total"] == float(setup)
    assert r["one_time"]["package"] == float(payout)
    assert r["one_time"]["addon_status"] == "none"
    # not silently replaced by 20%
    assert r["one_time"]["package"] != round(setup * 0.2, 2)


def test_standard_setup_price_comes_from_server_not_client():
    r = _c(plan="basic", saas_setup=999999, monthly=700)
    assert r["setup_total"] == 1600.0 and r["one_time"]["total"] == 500.0


def test_addon_setup_is_proposed_and_separate():
    r = _c(plan="growth", addon_setup=1500, monthly=1300 + 299)
    assert r["one_time"]["package"] == 1000.0
    assert r["one_time"]["addon"] == 225.0
    assert r["one_time"]["addon_status"] == "proposed"
    assert "not final" in r["one_time"]["addon_rule"]
    assert r["recurring"]["monthly"] == round((1300 + 299) * 0.2, 2)


def test_collected_payment_is_proportionate():
    r = _c(saas_setup=50000, monthly=4000, basis="paid",
           collected_setup=25000, collected_monthly=2000)
    assert r["one_time"]["total"] == 5000.0
    assert r["recurring"]["monthly"] == 400.0
    # cannot collect more than was quoted
    r = _c(saas_setup=50000, monthly=4000, basis="paid",
           collected_setup=90000, collected_monthly=9000)
    assert r["one_time"]["total"] == 10000.0 and r["recurring"]["monthly"] == 800.0


def test_nothing_collected_nothing_earned():
    r = _c(saas_setup=50000, monthly=7500, basis="paid")
    assert r["one_time"]["total"] == 0 and r["recurring"]["monthly"] == 0


@pytest.mark.parametrize("bad", [{"plan": "enterprise"}, {"monthly": -1},
                                 {"addon_setup": "abc"}, {"saas_setup": 1e12}])
def test_bad_input_refused(bad):
    with pytest.raises(dbc.DealInputError):
        _c(**bad)


def test_engine_output_names_no_other_role():
    r = _c(plan="professional", addon_setup=999, monthly=5000)
    assert not UPSTREAM.search(json.dumps(r))


# ── access ────────────────────────────────────────────────────────────────

def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="db%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def team(db_session):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-%d" % next(_SEQ))
    db.add(plat); db.commit()
    org = BrandSalesOrg(platform_id=plat.id, name="EvoSys Sales",
                        slug="evosys-s-%d" % next(_SEQ))
    db.add(org); db.commit()
    god = _user(db, "Owner", role="god_admin")
    mgr = _user(db, "Area Manager")
    rep = _user(db, "Rep")
    exe = _user(db, "Sr EVP")
    outsider = _user(db, "Not Sales")
    db.add(Membership(user_id=mgr.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=org.id,
                      role=ROLE_SALES_MANAGER, is_active=True))
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=org.id,
                      role=ROLE_SALES_REP, is_active=True, reports_to_user_id=mgr.id))
    db.add(Membership(user_id=exe.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=org.id,
                      role=ROLE_SALES_MANAGER, is_active=True))
    db.commit()
    caps.set_brand_grants(db, exe, org.id, org.name, god, ["deal_builder_executive"])
    db.commit()
    return dict(db=db, org=org, god=god, mgr=mgr, rep=rep, exe=exe, outsider=outsider)


def _h(t, who):
    return {"Authorization": "Bearer " + create_access_token(t[who], t["db"])}


DEAL = {"plan": "custom_saas", "saas_setup": 50000, "addon_setup": 0,
        "monthly": 4000, "basis": "quote"}


def test_signed_out_gets_nothing(client, team):
    assert client.get("/deal-builder/executive-page").status_code == 401
    assert client.post("/deal-builder/my-commission", json=DEAL).status_code == 401
    assert client.get("/deal-builder/access").status_code == 401


def test_rep_gets_own_commission_only(client, team):
    r = client.post("/deal-builder/my-commission", json=DEAL, headers=_h(team, "rep"))
    assert r.status_code == 200
    body = r.json()
    assert body["one_time"]["total"] == 10000.0
    assert body["recurring"]["monthly"] == 800.0
    text = r.text
    assert not UPSTREAM.search(text)
    for upstream_amount in ("400", "600", "2200", "2,200"):
        assert upstream_amount not in text.replace("4000", "")
    assert "no-store" in r.headers.get("cache-control", "")


@pytest.mark.parametrize("who", ["rep", "mgr", "outsider"])
def test_executive_page_refused(client, team, who):
    r = client.get("/deal-builder/executive-page", headers=_h(team, who))
    assert r.status_code == 403
    assert "Area Sales" not in r.text and "PACKAGE_PAY" not in r.text


@pytest.mark.parametrize("who", ["god", "exe"])
def test_executive_page_for_owner_and_grantee(client, team, who):
    r = client.get("/deal-builder/executive-page", headers=_h(team, who))
    assert r.status_code == 200
    assert "Area Sales Manager" in r.text and "deal-core.js" in r.text
    assert "no-store" in r.headers.get("cache-control", "")


def test_access_flags(client, team):
    def flags(who):
        return client.get("/deal-builder/access", headers=_h(team, who)).json()
    assert flags("rep") == {"sales": True, "executive": False, "name": "Rep"}
    assert flags("mgr")["executive"] is False
    assert flags("exe")["executive"] is True
    assert flags("god")["executive"] is True
    assert flags("outsider") == {"sales": False, "executive": False, "name": "Not Sales"}


def test_non_sales_user_cannot_get_commission(client, team):
    r = client.post("/deal-builder/my-commission", json=DEAL, headers=_h(team, "outsider"))
    assert r.status_code == 403


def test_revoked_grant_loses_access(client, team):
    db = team["db"]
    caps.set_brand_grants(db, team["exe"], team["org"].id, team["org"].name, team["god"], [])
    db.commit()
    assert client.get("/deal-builder/executive-page", headers=_h(team, "exe")).status_code == 403


# ── what the public static site and bundle contain ─────────────────────────

PUBLIC_DIRS = ["frontend/public", "frontend/src"]
UPSTREAM_SOURCE = re.compile(r"Area Sales Manager|Sr Executive Vice|PACKAGE_PAY|setupRates|"
                             r"55% M&M|M&M / EvoSys", re.I)


def test_executive_page_not_in_public_static_site():
    # The old public path is now a forwarding stub. It must EXIST (the static
    # host keeps serving a deleted file from an earlier deploy) and must carry
    # nothing but the redirect.
    stub = open(os.path.join(ROOT, "frontend/public/team-prices/deal-builder.html"), encoding="utf-8").read()
    assert "/team-prices/executive-deal-builder" in stub
    assert len(stub) < 1500
    assert not re.search(r"\$\d|commission|<style|deal-core|Area Sales|Executive Vice|setup", stub, re.I)
    assert os.path.exists(os.path.join(ROOT, "app/private_pages/deal_builder_executive.html"))


def test_no_team_split_in_public_frontend_source():
    hits = []
    for d in PUBLIC_DIRS:
        for base, _dirs, files in os.walk(os.path.join(ROOT, d)):
            if "node_modules" in base:
                continue
            for f in files:
                if not f.endswith((".html", ".js", ".jsx", ".mjs", ".json", ".css")):
                    continue
                path = os.path.join(base, f)
                try:
                    txt = open(path, encoding="utf-8").read()
                except (UnicodeDecodeError, OSError):
                    continue
                if UPSTREAM_SOURCE.search(txt):
                    hits.append(os.path.relpath(path, ROOT))
    assert hits == []


def test_private_pages_not_mounted():
    main = open(os.path.join(ROOT, "app/main.py"), encoding="utf-8").read()
    assert "private_pages" not in main
