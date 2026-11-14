"""EARNED COMMISSION for EvoSys Pro Sales — the same versioned rules as the
Deal Builder, applied to collected money. Synthetic people and deals only."""
import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.compensation_models import CompensationEntry
from app.models.models import Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandSalesOrg, Membership,
                                     Opportunity)
from app.models.sales_team_models import (SalesCollectionSplit, SalesDealCredit,
                                          SalesDeparture, SalesUnclaimedShare)
from app.services import capabilities as caps
from app.services import compensation as comp
from app.services import sales_program_comp as spc
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="ec%d@example.test" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _member(db, u, brand, role, manager=None):
    db.add(Membership(user_id=u.id, scope_type=SCOPE_BRAND_SALES_ORG, scope_id=brand.id,
                      role=role, is_active=True,
                      reports_to_user_id=manager.id if manager else None))
    db.commit()


@pytest.fixture()
def team(db_session, monkeypatch):
    db = db_session
    plat = Platform(name="EvoSys", slug="evosys-ec-%d" % next(_SEQ))
    db.add(plat); db.commit()
    brand = BrandSalesOrg(platform_id=plat.id, name="EvoSys Pro Sales", slug="evosyspro-sales")
    db.add(brand); db.commit()
    god = _user(db, "God", role="god_admin")
    owner = _user(db, "Owner")
    caps.set_brand_grants(db, owner, brand.id, brand.name, god, ["sales_program_owner"])
    sevp = _user(db, "Sr EVP")
    _member(db, sevp, brand, ROLE_SALES_MANAGER)
    caps.set_brand_grants(db, sevp, brand.id, brand.name, god, ["sales_team_executive"])
    asm = _user(db, "Area Manager")
    _member(db, asm, brand, ROLE_SALES_MANAGER, sevp)
    rep = _user(db, "Rep")
    _member(db, rep, brand, ROLE_SALES_REP, asm)
    db.commit()

    econ = {"key": "starter", "fee": Decimal("1800"), "mrr": Decimal("597"), "kind": "standard"}

    def fake_econ(_db, _opp):
        return {"package": SimpleNamespace(key=econ["key"]), "deal_kind": econ["kind"],
                "implementation_fee": econ["fee"], "mrr": econ["mrr"],
                "term_months": 12, "tcv": None, "currency": "USD"}
    monkeypatch.setattr(comp, "deal_economics", fake_econ)
    return SimpleNamespace(db=db, brand=brand, god=god, owner=owner, sevp=sevp, asm=asm,
                           rep=rep, econ=econ)


def _deal(t, owner=None):
    o = Opportunity(brand_sales_org_id=t.brand.id, company_name="Synthetic Co %d" % next(_SEQ),
                    owner_user_id=(owner or t.rep).id, status="won")
    t.db.add(o); t.db.commit()
    return o


def _pay(t, opp, ref, amount, when=None):
    return comp.earn(t.db, opp, collection_reference=ref, collected_amount=amount,
                     collected_at=when or datetime(2026, 12, 1, 12, 0))


def _by_role(entries):
    return {e.payee_kind: float(e.amount) for e in entries}


def test_partial_setup_basic_plus_addon_is_prorated(team):
    """Basic $1,600 + $200 add-on, $900 collected -> $265 / $110 / $165."""
    opp = _deal(team)
    got = _by_role(_pay(team, opp, "pay-1", "900"))
    assert got == {"rep": 265.0, "asm": 110.0, "sevp": 165.0}


def test_rest_of_setup_then_monthly(team):
    opp = _deal(team)
    _pay(team, opp, "pay-1", "900")
    assert _by_role(_pay(team, opp, "pay-2", "900")) == {"rep": 265.0, "asm": 110.0, "sevp": 165.0}
    # Setup is done; the next $597 is monthly service at 15 / 10 / 15.
    assert _by_role(_pay(team, opp, "pay-3", "597")) == {"rep": 89.55, "asm": 59.7, "sevp": 89.55}


def test_one_payment_spanning_setup_and_monthly(team):
    opp = _deal(team)
    # 1,800 setup + 597 first month in one payment.
    got = _by_role(_pay(team, opp, "pay-1", "2397"))
    assert got == {"rep": 530 + 89.55, "asm": 220 + 59.7, "sevp": 330 + 89.55}
    split = team.db.query(SalesCollectionSplit).filter_by(opportunity_id=opp.id).one()
    assert float(split.setup_part) == 1800.0 and float(split.monthly_part) == 597.0


def test_retry_is_idempotent(team):
    opp = _deal(team)
    _pay(team, opp, "pay-1", "900")
    _pay(team, opp, "pay-1", "900")
    _pay(team, opp, "pay-1", "900")
    rows = team.db.query(CompensationEntry).filter_by(opportunity_id=opp.id).all()
    assert len(rows) == 3
    assert team.db.query(SalesCollectionSplit).filter_by(opportunity_id=opp.id).count() == 1


def test_fourteen_day_holdback(team):
    opp = _deal(team)
    when = datetime(2026, 12, 1, 12, 0)
    for e in _pay(team, opp, "pay-1", "900", when):
        assert e.payable_at == when + timedelta(days=14)
        assert e.basis.startswith("evosys_launch_rules:")


def test_saas_setup_and_monthly_20_10_15(team):
    team.econ.update(key="multi_tenant", fee=Decimal("10000"), mrr=Decimal("2000"))
    opp = _deal(team)
    assert _by_role(_pay(team, opp, "s-1", "10000")) == {"rep": 2000.0, "asm": 1000.0, "sevp": 1500.0}
    assert _by_role(_pay(team, opp, "s-2", "2000")) == {"rep": 400.0, "asm": 200.0, "sevp": 300.0}


def test_empty_manager_seat_goes_to_unclaimed_and_raises_nobody(team):
    lone = _user(team.db, "Rep with no manager")
    _member(team.db, lone, team.brand, ROLE_SALES_REP, team.sevp)   # no ASM above him
    opp = _deal(team, lone)
    got = _by_role(_pay(team, opp, "pay-1", "900"))
    assert got == {"rep": 265.0, "sevp": 165.0}                      # sevp not raised
    u = team.db.query(SalesUnclaimedShare).filter_by(opportunity_id=opp.id).one()
    assert (u.role, u.reason, float(u.amount)) == ("asm", "seat empty", 110.0)


def test_departure_before_collection_is_unclaimed_after_is_kept(team):
    opp = _deal(team)
    _pay(team, opp, "pay-1", "900", datetime(2026, 12, 1))
    team.db.add(SalesDeparture(brand_sales_org_id=team.brand.id, user_id=team.rep.id,
                               departed_at=datetime(2026, 12, 5)))
    team.db.commit()
    got = _by_role(_pay(team, opp, "pay-2", "900", datetime(2026, 12, 10)))
    assert got == {"asm": 110.0, "sevp": 165.0}
    earned_before = team.db.query(CompensationEntry).filter_by(
        opportunity_id=opp.id, payee_user_id=team.rep.id).count()
    assert earned_before == 1                                          # first payment kept
    u = team.db.query(SalesUnclaimedShare).filter_by(opportunity_id=opp.id).one()
    assert (u.role, u.reason, u.collection_reference) == ("rep", "left the company", "pay-2")
    _pay(team, opp, "pay-2", "900", datetime(2026, 12, 10))           # retry
    assert team.db.query(SalesUnclaimedShare).filter_by(opportunity_id=opp.id).count() == 1


def test_suspension_is_not_departure(team):
    m = team.db.query(Membership).filter_by(user_id=team.rep.id).one()
    m.is_active = False
    team.db.commit()
    opp = _deal(team)
    assert _by_role(_pay(team, opp, "pay-1", "900"))["rep"] == 265.0


def test_moved_rep_keeps_old_manager_on_past_deals(team):
    opp = _deal(team)
    _pay(team, opp, "pay-1", "900")
    new_asm = _user(team.db, "New Area Manager")
    _member(team.db, new_asm, team.brand, ROLE_SALES_MANAGER, team.sevp)
    m = team.db.query(Membership).filter_by(user_id=team.rep.id).one()
    m.reports_to_user_id = new_asm.id
    team.db.commit()
    entries = _pay(team, opp, "pay-2", "900")
    asm_entry = [e for e in entries if e.payee_kind == "asm"][0]
    assert asm_entry.payee_user_id == team.asm.id
    credit = team.db.query(SalesDealCredit).filter_by(opportunity_id=opp.id).one()
    assert credit.rules_version == "2026-11-13"
    # A NEW deal after the move credits the new manager.
    opp2 = _deal(team)
    e2 = [e for e in _pay(team, opp2, "x-1", "900") if e.payee_kind == "asm"][0]
    assert e2.payee_user_id == new_asm.id


def test_not_won_earns_nothing(team):
    opp = _deal(team)
    opp.status = "open"
    team.db.commit()
    with pytest.raises(comp.NotEarnable):
        _pay(team, opp, "pay-1", "900")


def test_unknown_package_is_refused_not_guessed(team):
    team.econ.update(key="mystery")
    opp = _deal(team)
    with pytest.raises(comp.NotEarnable):
        _pay(team, opp, "pay-1", "900")


def test_other_brands_still_use_their_own_plans(team, db_session):
    other = BrandSalesOrg(platform_id=team.brand.platform_id, name="Other", slug="other-%d" % next(_SEQ))
    db_session.add(other); db_session.commit()
    o = Opportunity(brand_sales_org_id=other.id, company_name="Other Co", status="won",
                    owner_user_id=team.rep.id)
    db_session.add(o); db_session.commit()
    assert spc.applies(db_session, o) is False


def test_unclaimed_report_is_owner_only(team, client):
    lone = _user(team.db, "Lone rep")
    _member(team.db, lone, team.brand, ROLE_SALES_REP, team.sevp)
    _pay(team, _deal(team, lone), "pay-1", "900")

    def get(u):
        h = {"Authorization": "Bearer " + create_access_token(u, team.db)} if u else {}
        return client.get("/sales-team/unclaimed", headers=h)
    for who in (team.god, team.owner):
        r = get(who)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 110.0 and body["rows"][0]["reason"] == "seat empty"
    for who in (team.sevp, team.asm, team.rep):
        assert get(who).status_code == 403
    assert get(None).status_code == 401


def test_projection_uses_the_same_rules(team):
    opp = _deal(team)
    r = comp.compute(team.db, opp)
    got = {p["override_level"]: float(p["amount"]) for p in r["payouts"]}
    # Basic + $200 add-on, full quote, plus one month of $597 service.
    assert got == {None: 530 + 89.55, 1: 220 + 59.7, 2: 330 + 89.55}
    assert r["plan_name"].startswith("EvoSys launch rules")
    assert team.db.query(SalesDealCredit).filter_by(opportunity_id=opp.id).count() == 0  # writes nothing
