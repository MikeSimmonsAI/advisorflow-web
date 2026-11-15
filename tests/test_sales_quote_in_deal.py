"""The Salesperson Deal Builder inside a deal: save, reopen, revise, and the
proposal quoting the same numbers. Synthetic people and deals only."""
import itertools

import pytest

from app.models.models import PROP_DRAFT, PROP_SENT, Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP, SCOPE_BRAND_SALES_ORG,
                                     BrandSalesOrg, Membership, Opportunity)
from app.models.sales_quote_models import SalesQuoteSnapshot
from app.models.sales_team_models import ONB_APPROVED, SalesOnboarding
from app.services import proposal_service as ps
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="q%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role, must_change_password=False)
    db.add(u); db.commit()
    return u


@pytest.fixture()
def w(db_session):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-q-%d" % next(_SEQ))
    db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    out = {"db": db, "brand": brand}
    for key, role in (("mgr", ROLE_SALES_MANAGER), ("rep", ROLE_SALES_REP), ("rep2", ROLE_SALES_REP)):
        u = _user(db, key)
        db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id,
                          role=role, is_active=True))
        db.add(SalesOnboarding(brand_sales_org_id=brand.id, user_id=u.id, status=ONB_APPROVED, email=u.email))
        out[key] = u
    db.commit()
    o = Opportunity(brand_sales_org_id=brand.id, company_name="Synthetic Roofing Co",
                    contact_name="Dana Test", email="dana@example.test",
                    owner_user_id=out["rep"].id, status="open")
    db.add(o); db.commit()
    out["opp"] = o
    return out


def H(w, u):
    return {"Authorization": "Bearer " + create_access_token(u, w["db"])}


SAAS = {
    "plan_key": "custom_saas", "setup_total": 12000, "monthly_total": 2000, "addon_setup": 0,
    "monthly_override": True, "plan_line": {"setup": 12000, "monthly": 2000},
    "lines": [], "pending": [],
    "state": {"plan": 4, "saas_setup": 12000, "saas_month": 2000, "saas_override": True,
              "items": {"voice": {"on": True, "s": 0, "m": 0}}, "commission": {"rep": 2400}},
}


def _put(client, w, u, body, **kw):
    return client.put("/sales/opportunities/%s/quote-draft" % w["opp"].id, json={**body, **kw}, headers=H(w, u))


def test_rep_saves_reopens_and_revises(client, w):
    r = client.get("/sales/opportunities/%s/quote-draft" % w["opp"].id, headers=H(w, w["rep"]))
    assert r.status_code == 200
    j = r.json()
    assert j["draft"] is None and j["can_edit"] is True
    assert j["opportunity"]["company_name"] == "Synthetic Roofing Co"
    assert j["credited_salesperson"]["name"] == "rep"

    r = _put(client, w, w["rep"], SAAS)
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["revision"] == 1
    # Reopen: the exact builder state comes back, including the monthly override.
    d = client.get("/sales/opportunities/%s/quote-draft" % w["opp"].id, headers=H(w, w["rep"])).json()["draft"]
    assert d["state"]["saas_override"] is True and d["state"]["saas_month"] == 2000
    assert d["monthly_override"] is True and d["monthly_total"] == 2000.0
    # Commission is never stored with the quote.
    assert "commission" not in d["state"]
    w["db"].refresh(w["opp"])
    assert float(w["opp"].implementation_fee) == 12000.0

    # Restore the 15% default and revise.
    r = _put(client, w, w["rep"], {**SAAS, "monthly_total": 1800, "monthly_override": False,
                                   "plan_line": {"setup": 12000, "monthly": 1800}}, expected_revision=1)
    assert r.status_code == 200 and r.json()["draft"]["revision"] == 2
    assert r.json()["draft"]["monthly_override"] is False
    # A stale editor is told, not silently overwritten.
    assert _put(client, w, w["rep"], SAAS, expected_revision=1).status_code == 409


def test_other_rep_cannot_open_or_save(client, w):
    assert client.get("/sales/opportunities/%s/quote-draft" % w["opp"].id,
                      headers=H(w, w["rep2"])).status_code in (403, 404)
    assert _put(client, w, w["rep2"], SAAS).status_code in (403, 404)
    assert client.get("/sales/opportunities/%s/quote-draft" % w["opp"].id).status_code == 401


def test_bad_input_and_closed_deal(client, w):
    assert _put(client, w, w["rep"], {**SAAS, "plan_key": "nope"}).status_code == 422
    assert _put(client, w, w["rep"], {**SAAS, "setup_total": -5}).status_code == 422
    w["opp"].status = "won"
    w["db"].commit()
    assert _put(client, w, w["rep"], SAAS).status_code == 409


def test_proposal_uses_the_same_numbers_and_shows_no_compensation(client, w):
    db = w["db"]
    prop = ps.create_proposal(db, w["opp"], w["rep"])
    prop.sales_status = PROP_DRAFT
    db.commit()
    basic = {"plan_key": "basic", "setup_total": 2350, "monthly_total": 850, "addon_setup": 750,
             "plan_line": {"setup": 1600, "monthly": 700},
             "lines": [{"name": "AI voice agent", "setup": 750, "monthly": 150}],
             "pending": [], "state": {"plan": 1}}
    r = _put(client, w, w["rep"], basic)
    assert r.status_code == 200, r.text
    assert r.json()["applied_to_proposal"]["proposal_number"] == prop.proposal_number
    md = ps._investment_markdown(db, prop)
    assert "AI voice agent" in md and "$2,350" in md and "$850/month" in md and "$1,600" in md
    for word in ("commission", "Commission", "payout", "Salesperson", "%"):
        assert word not in md, word
    db.refresh(prop)
    assert prop.package_id or float(prop.final_amount) == 2350.0

    # Once sent, the customer's document never moves.
    prop.sales_status = PROP_SENT
    db.commit()
    r = _put(client, w, w["rep"], {**basic, "setup_total": 9999, "addon_setup": 0, "lines": []},
             expected_revision=1)
    assert r.status_code == 200
    assert r.json()["applied_to_proposal"] is None
    assert r.json()["proposal_not_changed"]["status"] == PROP_SENT
    assert "$2,350" in ps._investment_markdown(db, prop)

    # A new version picks up the latest quote.
    nxt = ps.create_version(db, prop, w["rep"])
    db.commit()
    assert "$9,999" in ps._investment_markdown(db, nxt)
    assert db.query(SalesQuoteSnapshot).filter_by(proposal_id=prop.id).one().draft_revision == 1


def test_proposal_without_a_builder_quote_is_unchanged(client, w):
    db = w["db"]
    prop = ps.create_proposal(db, w["opp"], w["rep"])
    db.commit()
    from app.services import sales_quote
    assert sales_quote.snapshot_for(db, prop) is None


def test_manager_can_open_any_deal_quote(client, w):
    r = client.get("/sales/opportunities/%s/quote-draft" % w["opp"].id, headers=H(w, w["mgr"]))
    assert r.status_code == 200
