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


def test_addons_follow_the_deal():
    # Package deal: add-on setup and monthly at the package rates (15/10/15).
    r = _c(plan="growth", addon_setup=1500, monthly=1300 + 299)
    assert r["one_time"]["package"] == 1000.0
    assert r["one_time"]["addon"] == 225.0            # 15% of 1,500
    assert r["recurring"]["monthly"] == round((1300 + 299) * 0.15, 2)
    assert "not final" not in json.dumps(r).lower()


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
    org = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales",
                        slug="evosyspro-sales")
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
    rep = flags("rep")
    assert rep["sales"] is True and rep["executive"] is False and rep["view"] == "rep"
    assert rep["team_view"] is False
    mgr = flags("mgr")
    assert mgr["executive"] is False and mgr["view"] == "asm" and mgr["team_view"] is True
    assert flags("exe")["executive"] is True and flags("exe")["view"] == "full"
    assert flags("god")["executive"] is True
    out = flags("outsider")
    assert out["sales"] is False and out["executive"] is False and out["view"] is None


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


# ── production shape: admins who also hold the EvoSys Wholesale seat ─────────
#
# platform@evosyspro.live and Michael Schlueter are org admins of the EvoSys
# Wholesale host org ("evo-integrated-solutions-llc") AND hold the named
# Wholesale product seat. With that workspace active, program_experience locks
# the login to Wholesale and refused EVERY /deal-builder call - including the
# /access check Program Access uses - with "This workspace runs Wholesale".
# The Deal Builder routes authorize themselves, so the lock must let them
# through (the same treatment the sales Back Office already gets).

@pytest.fixture()
def wholesale_admins(db_session, team):
    from app.models.models import Organization
    db = db_session
    host = Organization(name="EVO Integrated Solutions LLC", slug="evo-integrated-solutions-llc",
                        plan="enterprise", is_active=True)
    db.add(host); db.commit()
    plat_user = User(organization_id=host.id, email="platform-like-%d@evosyspro.live" % next(_SEQ),
                     password_hash=hash_password("x"), full_name="EvoSys Platform User",
                     role="org_admin", must_change_password=False)
    adv = _user(db, "Michael Schlueter")              # advisor, no home org
    db.add(plat_user); db.commit()
    for u in (plat_user, adv):
        db.add(Membership(user_id=u.id, scope_type="customer_org", scope_id=host.id,
                          role="org_admin", is_active=True))
        db.add(Membership(user_id=u.id, scope_type="product", scope_id="wholesale",
                          role="member", is_active=True))
        db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=team["org"].id,
                          role=ROLE_SALES_MANAGER, is_active=True))
    db.commit()
    return dict(db=db, host=host, plat=plat_user, adv=adv)


def _hw(db, u, host_id=None):
    h = {"Authorization": "Bearer " + create_access_token(u, db)}
    if host_id:
        h["X-Workspace-Id"] = host_id
    return h


@pytest.mark.parametrize("who", ["plat", "adv"])
def test_wholesale_lock_does_not_block_deal_builder(client, team, wholesale_admins, who):
    w = wholesale_admins
    u = w[who]
    for hdr in (_hw(w["db"], u), _hw(w["db"], u, w["host"].id)):
        r = client.get("/deal-builder/access", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json()["sales"] is True and r.json()["executive"] is False
        r = client.post("/deal-builder/my-commission", json=DEAL, headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json()["recurring"]["monthly"] == 800.0
        r = client.get("/deal-builder/executive-page", headers=hdr)
        assert r.status_code == 403
        assert "Wholesale" not in r.text  # the route's own answer, not the lock's


@pytest.mark.parametrize("who", ["plat", "adv"])
def test_wholesale_admin_with_executive_grant(client, team, wholesale_admins, who):
    w = wholesale_admins
    caps.set_brand_grants(w["db"], w[who], team["org"].id, team["org"].name, team["god"],
                          ["deal_builder_executive"])
    w["db"].commit()
    r = client.get("/deal-builder/executive-page", headers=_hw(w["db"], w[who], w["host"].id))
    assert r.status_code == 200 and "Area Sales Manager" in r.text
    assert client.get("/deal-builder/access", headers=_hw(w["db"], w[who])).json()["executive"] is True


def test_wholesale_lock_still_applies_elsewhere(client, team, wholesale_admins):
    # Opening the Deal Builder does not widen the Wholesale lock for other screens.
    from app.services import program_experience as pe
    assert pe.DEAL_BUILDER_PREFIXES == ("/deal-builder/",)


# ── rules v2 (2026-11-13) and views by seat ──────────────────────────────────

def _full(**kw):
    base = dict(plan="custom_saas", saas_setup=0, addon_setup=0, monthly=0, basis="quote")
    base.update(kw)
    return dbc.split(base)


def test_package_monthly_is_15_10_15_60():
    v = dbc.view(_full(plan="basic", monthly=700), "full")
    by = {l["role"]: l for l in v["layers"]}
    assert [by[r]["monthly"] for r in ("rep", "asm", "sevp")] == [105.0, 70.0, 105.0]
    assert v["evosys"]["monthly"] == 420.0
    assert [by[r]["one_time"] for r in ("rep", "asm", "sevp")] == [500.0, 200.0, 300.0]


@pytest.mark.parametrize("plan,pay", [("basic", [500, 200, 300]), ("growth", [1000, 300, 600]),
                                      ("professional", [2000, 400, 900])])
def test_package_setup_payouts(plan, pay):
    v = dbc.view(_full(plan=plan), "full")
    assert [l["one_time"] for l in v["layers"]] == [float(x) for x in pay]


def test_saas_split_and_override():
    v = dbc.view(_full(saas_setup=50000, monthly=4000), "full")
    by = {l["role"]: l for l in v["layers"]}
    assert [by[r]["one_time"] for r in ("rep", "asm", "sevp")] == [10000.0, 5000.0, 7500.0]
    assert [by[r]["monthly"] for r in ("rep", "asm", "sevp")] == [800.0, 400.0, 600.0]
    assert v["evosys"] == {"one_time": 27500.0, "monthly": 2200.0}


def test_saas_addon_follows_saas_rates():
    v = dbc.view(_full(saas_setup=10000, addon_setup=1000, monthly=1600), "full")
    rep = v["layers"][0]
    assert rep["one_time_package"] == 2000.0 and rep["one_time_addons"] == 200.0
    assert rep["monthly"] == 320.0


def test_basic_addon_100_monthly_pays_rep_15():
    v = dbc.view(_full(plan="basic", monthly=800), "rep")
    assert v["own"]["monthly"] == 120.0      # 15% of 700 + 15% of the $100 add-on


def test_mixed_partial_setup_payment_prorates_each_line():
    # Basic ($1,600) + $200 setup add-on = $1,800; $900 collected = half of each line.
    v = dbc.view(_full(plan="basic", addon_setup=200, basis="paid", collected_setup=900), "full")
    by = {l["role"]: l for l in v["layers"]}
    assert by["rep"]["one_time"] == 265.0
    assert by["asm"]["one_time"] == 110.0
    assert by["sevp"]["one_time"] == 165.0


def test_receipt_allocated_to_one_line():
    # $200 applied to the add-on only: no package payout yet, full add-on share.
    v = dbc.view(_full(plan="basic", addon_setup=200, basis="paid",
                       allocation={"package": 0, "addons": 200}), "full")
    by = {l["role"]: l for l in v["layers"]}
    assert by["rep"]["one_time_package"] == 0.0 and by["rep"]["one_time_addons"] == 30.0
    assert by["asm"]["one_time"] == 20.0 and by["sevp"]["one_time"] == 30.0
    assert v["setup_counted"] == 200.0


def test_empty_seat_goes_to_unclaimed_not_to_anyone_else():
    full = dbc.split(dict(plan="custom_saas", saas_setup=50000, monthly=4000, basis="quote"),
                     filled={"asm": False})
    v = dbc.view(full, "full")
    assert v["unclaimed"]["monthly"] == 400.0 and v["unclaimed"]["one_time"] == 5000.0
    assert v["unclaimed"]["reasons"][0]["reason"] == "seat empty"
    by = {l["role"]: l for l in v["layers"]}
    assert by["rep"]["monthly"] == 800.0 and by["sevp"]["monthly"] == 600.0
    assert v["evosys"]["monthly"] == 2600.0   # includes the unclaimed 400


@pytest.mark.parametrize("view_name,roles", [("rep", ["rep"]), ("asm", ["rep", "asm"]),
                                             ("sevp", ["rep", "asm", "sevp"])])
def test_views_never_carry_a_higher_layer(view_name, roles):
    v = dbc.view(_full(saas_setup=50000, monthly=4000), view_name)
    assert [l["role"] for l in v["layers"]] == roles
    assert "evosys" not in v and "unclaimed" not in v
    text = json.dumps(v)
    assert "2200" not in text and "27500" not in text and "EvoSys" not in text
    if view_name != "sevp":
        assert "600.0" not in text and "Sr Executive" not in text
    if view_name == "rep":
        assert "400.0" not in text and "Area Sales" not in text


def test_commission_endpoint_by_seat(client, team):
    db = team["db"]
    caps.set_brand_grants(db, team["mgr"], team["org"].id, team["org"].name, team["god"],
                          ["sales_team_executive"])
    db.commit()
    deal = {"plan": "custom_saas", "saas_setup": 50000, "monthly": 4000, "basis": "quote"}
    r = client.post("/deal-builder/commission", json=deal, headers=_h(team, "rep")).json()
    assert r["view"] == "rep" and [l["role"] for l in r["layers"]] == ["rep"]
    r = client.post("/deal-builder/commission", json=deal, headers=_h(team, "mgr")).json()
    assert r["view"] == "sevp" and r["own"]["monthly"] == 600.0 and "evosys" not in r
    r = client.post("/deal-builder/commission", json=deal, headers=_h(team, "god")).json()
    assert r["view"] == "full" and r["evosys"]["monthly"] == 2200.0
    r = client.post("/deal-builder/commission", json=deal, headers=_h(team, "outsider"))
    assert r.status_code == 403 and "not on the EvoSys Pro sales team" in r.json()["detail"]
    assert client.post("/deal-builder/commission", json=deal).status_code == 401
    # Sr EVP still cannot open the full executive page.
    assert client.get("/deal-builder/executive-page", headers=_h(team, "mgr")).status_code == 403


def test_owner_grant_gives_full_view(client, team):
    db = team["db"]
    caps.set_brand_grants(db, team["outsider"], team["org"].id, team["org"].name, team["god"],
                          ["sales_program_owner"])
    db.commit()
    a = client.get("/deal-builder/access", headers=_h(team, "outsider")).json()
    assert a["view"] == "full" and a["seat"] == "owner"
    r = client.get("/deal-builder/executive-page", headers=_h(team, "outsider"))
    assert r.status_code == 200


def test_executive_page_gets_rules_injected(client, team):
    html = client.get("/deal-builder/executive-page", headers=_h(team, "god")).text
    assert "/*__DEAL_RULES__*/null" not in html
    assert '"version": "%s"' % dbc.RULES_VERSION in html
    assert "setupRates" not in html and "PACKAGE_PAY" not in html


def test_other_brand_members_do_not_get_the_evosys_builder(client, team, db_session):
    other = BrandSalesOrg(platform_id=team["org"].platform_id, name="Other Sales",
                          slug="other-sales-%d" % next(_SEQ))
    db_session.add(other); db_session.commit()
    u = _user(db_session, "Other Rep")
    db_session.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=other.id,
                              role=ROLE_SALES_REP, is_active=True))
    db_session.commit()
    h = {"Authorization": "Bearer " + create_access_token(u, db_session)}
    assert client.get("/deal-builder/access", headers=h).json()["sales"] is False
