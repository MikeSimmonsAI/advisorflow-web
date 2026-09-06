"""THE COMPENSATION COMMAND CENTER, AND THE LINES IT MUST NOT CROSS.

FOUR THINGS THIS FILE DEFENDS

  1. WON IS NOT EARNED, AND PROJECTED IS NOT OWED. A pipeline number and a
     ledger row are different kinds of thing, and the moment they blur, a
     forecast becomes a liability nobody agreed to.
  2. NOBODY IS PAID TWICE. Not by a retried request, not by two operators, not
     by re-recording the same customer payment.
  3. TOTALS RECONCILE. A headline that cannot be opened and counted is worse
     than no headline, so every summary figure is asserted equal to the sum of
     the rows the ledger returns under the same filter.
  4. BRAND ISOLATION. Brand A's finance sees Brand A. Every read starts from
     the caller's brands, and a caller with none gets nothing rather than
     everything.

The worked example is the configured EvoSys one: $1,497 Starter, $500 seller,
$100 level-1 override where an eligible manager exists, $800 cap, 14-day
holdback — all of it plan CONFIGURATION, none of it constants in the engine.
"""

import itertools
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, COMP_EARNED, COMP_PAID,
                                            COMP_PAYABLE, PAYEE_OVERRIDE,
                                            PAYEE_SELLER, CompensationEntry,
                                            CompensationPackageCap,
                                            CompensationPlan, CompensationRule)
from app.models.models import Platform, User
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, BrandPackage,
                                     BrandSalesOrg, Membership, Opportunity)
from app.services import compensation as comp
from app.services import compensation_ledger as ledger
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


# ═════════════════════════════════════════════════════════════════════════════
# Two brands, each with its own package, plan, manager and rep.
# ═════════════════════════════════════════════════════════════════════════════

def _user(db, name, role="advisor"):
    u = User(organization_id=None, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _brand(db, label, *, seller=Decimal("500.00"),
           override=Decimal("100.00"), cap=Decimal("800.00"), holdback=14):
    plat = Platform(name=label, slug="%s-%d" % (label.lower(), next(_SEQ)))
    db.add(plat); db.commit()
    org = BrandSalesOrg(platform_id=plat.id, name=label + " Sales",
                        slug="%s-s-%d" % (label.lower(), next(_SEQ)))
    db.add(org); db.commit()
    pkg = BrandPackage(platform_id=plat.id, name="Starter",
                       key="starter-%d" % next(_SEQ),
                       price=Decimal("1497.00"), setup_fee=Decimal("1497.00"),
                       monthly_price=Decimal("597.00"),
                       contract_monthly_price=Decimal("500.00"),
                       contract_term_months=13, currency="USD")
    db.add(pkg); db.commit()

    manager = _user(db, label + " Manager")
    db.add(Membership(user_id=manager.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=org.id, role=ROLE_SALES_MANAGER, is_active=True))
    db.commit()
    rep = _user(db, label + " Rep")
    db.add(Membership(user_id=rep.id, scope_type=SCOPE_BRAND_SALES_ORG,
                      scope_id=org.id, role=ROLE_SALES_REP, is_active=True,
                      reports_to_user_id=manager.id))
    db.commit()

    plan = CompensationPlan(brand_sales_org_id=org.id, name=label + " Plan",
                            effective_from=date(2026, 1, 1),
                            holdback_days=holdback, max_override_levels=1,
                            is_active=True)
    db.add(plan); db.commit()
    if seller is not None:
        db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                                payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                                amount=seller, sort_order=1))
    if override is not None:
        db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                                payee_kind=PAYEE_OVERRIDE, override_level=1,
                                basis=BASIS_FIXED, amount=override, sort_order=2))
    if cap is not None:
        db.add(CompensationPackageCap(plan_id=plan.id, package_id=pkg.id,
                                      max_total_payout=cap))
    db.commit()
    return dict(platform=plat, org=org, pkg=pkg, manager=manager, rep=rep,
                plan=plan)


@pytest.fixture()
def a(db_session):
    return _brand(db_session, "Alpha")


@pytest.fixture()
def b(db_session):
    return _brand(db_session, "Beta", seller=Decimal("250.00"),
                  override=None, cap=None, holdback=30)


@pytest.fixture()
def god(db_session):
    return _user(db_session, "Owner", role="god_admin")


def _won(db, brand, *, owner=None, status="won"):
    o = Opportunity(brand_sales_org_id=brand["org"].id,
                    owner_user_id=(owner or brand["rep"]).id,
                    company_name="Deal %d" % next(_SEQ),
                    selected_package_id=brand["pkg"].id,
                    stage="closing", status=status,
                    billing_option="term_agreement", contract_term_months=13)
    db.add(o); db.commit()
    return o


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


# ═════════════════════════════════════════════════════════════════════════════
# 12-14. Projected is not earned; Won alone is not earned; collection is
# ═════════════════════════════════════════════════════════════════════════════

def test_an_open_deal_projects_but_earns_nothing(db_session, a):
    opp = _won(db_session, a, status="open")
    assert comp.compute(db_session, opp)["total"] == Decimal("600.00")
    assert db_session.query(CompensationEntry).count() == 0


def test_won_alone_creates_no_ledger_row(db_session, a):
    _won(db_session, a)
    assert db_session.query(CompensationEntry).count() == 0


def test_a_collected_payment_creates_earned_compensation(db_session, a):
    opp = _won(db_session, a)
    made = comp.earn(db_session, opp, collection_reference="inv-1",
                     collected_amount=Decimal("1497.00"))
    assert len(made) == 2                      # seller + eligible override
    assert all(e.state == COMP_EARNED for e in made)
    assert sum(e.amount for e in made) == Decimal("600.00")


def test_earning_refuses_a_deal_that_is_not_won(db_session, a):
    opp = _won(db_session, a, status="open")
    with pytest.raises(comp.NotEarnable):
        comp.earn(db_session, opp, collection_reference="inv-1",
                  collected_amount=Decimal("1497.00"))


def test_earning_refuses_without_a_collection_reference(db_session, a):
    opp = _won(db_session, a)
    with pytest.raises(comp.NotEarnable):
        comp.earn(db_session, opp, collection_reference="  ",
                  collected_amount=Decimal("1497.00"))


# ═════════════════════════════════════════════════════════════════════════════
# 15-16. The holdback is plan configuration, and it gates payability
# ═════════════════════════════════════════════════════════════════════════════

def test_the_holdback_comes_from_the_plan_not_a_constant(db_session, a, b):
    """Alpha holds 14 days, Beta holds 30. Same engine, different plans."""
    ea = comp.earn(db_session, _won(db_session, a), collection_reference="a-1",
                   collected_amount=Decimal("1497.00"),
                   collected_at=datetime(2026, 9, 1))[0]
    eb = comp.earn(db_session, _won(db_session, b), collection_reference="b-1",
                   collected_amount=Decimal("1497.00"),
                   collected_at=datetime(2026, 9, 1))[0]
    assert ea.payable_at == datetime(2026, 9, 1) + timedelta(days=14)
    assert eb.payable_at == datetime(2026, 9, 1) + timedelta(days=30)


def test_a_fresh_entry_is_on_hold_not_payable(db_session, a):
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"))
    e = db_session.query(CompensationEntry).first()
    assert ledger.derived_view(e) == ledger.VIEW_ON_HOLD


def test_an_elapsed_holdback_reads_as_payable_even_before_promotion(
        db_session, a):
    """The read tells the truth; the bulk promotion catches up.

    Reporting an elapsed row as still held would understate what can be paid
    today, purely because a background job had not run.
    """
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    e = db_session.query(CompensationEntry).first()
    assert e.state == COMP_EARNED
    assert ledger.derived_view(e) == ledger.VIEW_PAYABLE_NOW


def test_promotion_moves_only_elapsed_entries(db_session, a):
    comp.earn(db_session, _won(db_session, a), collection_reference="old",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.earn(db_session, _won(db_session, a), collection_reference="new",
              collected_amount=Decimal("1497.00"))
    moved = comp.promote_due_to_payable(db_session)
    assert moved == 2                       # the old deal's seller + override
    assert db_session.query(CompensationEntry).filter(
        CompensationEntry.state == COMP_PAYABLE).count() == 2
    assert db_session.query(CompensationEntry).filter(
        CompensationEntry.state == COMP_EARNED).count() == 2


# ═════════════════════════════════════════════════════════════════════════════
# 19-20. Nobody is paid twice
# ═════════════════════════════════════════════════════════════════════════════

def test_recording_the_same_collection_twice_creates_no_duplicates(db_session, a):
    opp = _won(db_session, a)
    first = comp.earn(db_session, opp, collection_reference="inv-1",
                      collected_amount=Decimal("1497.00"))
    again = comp.earn(db_session, opp, collection_reference="inv-1",
                      collected_amount=Decimal("1497.00"))
    assert {e.id for e in first} == {e.id for e in again}
    assert db_session.query(CompensationEntry).count() == 2


def test_a_second_collection_earns_again_because_it_is_different_money(
        db_session, a):
    opp = _won(db_session, a)
    comp.earn(db_session, opp, collection_reference="inv-1",
              collected_amount=Decimal("1497.00"))
    comp.earn(db_session, opp, collection_reference="inv-2",
              collected_amount=Decimal("500.00"))
    assert db_session.query(CompensationEntry).count() == 4


def test_an_entry_cannot_be_paid_twice(db_session, a, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)
    e = db_session.query(CompensationEntry).first()
    comp.mark_paid(db_session, e, payment_reference="ACH-1", paid_by=god.id)
    assert e.state == COMP_PAID
    with pytest.raises(comp.NotEarnable):
        comp.mark_paid(db_session, e, payment_reference="ACH-2", paid_by=god.id)
    assert e.payment_reference == "ACH-1"


def test_an_entry_still_on_hold_cannot_be_paid(db_session, a, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"))
    e = db_session.query(CompensationEntry).first()
    with pytest.raises(comp.NotEarnable):
        comp.mark_paid(db_session, e, payment_reference="ACH-1", paid_by=god.id)


# ═════════════════════════════════════════════════════════════════════════════
# 18. Paying captures durable evidence
# ═════════════════════════════════════════════════════════════════════════════

def test_paying_records_who_how_and_under_what_batch(db_session, a, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)
    e = db_session.query(CompensationEntry).first()
    comp.mark_paid(db_session, e, payment_reference="ACH-99", paid_by=god.id,
                   payment_method="ach", payment_note="weekly run",
                   payment_batch_reference="WK-2026-36")
    assert e.paid_by == god.id
    assert e.payment_method == "ach"
    assert e.payment_batch_reference == "WK-2026-36"
    assert e.paid_at is not None


# ═════════════════════════════════════════════════════════════════════════════
# 21-23. Overrides and caps
# ═════════════════════════════════════════════════════════════════════════════

def test_no_override_is_created_without_an_eligible_manager(db_session, a):
    """A rep who reports to nobody generates no override expense."""
    lone = _user(db_session, "Lone")
    db_session.add(Membership(user_id=lone.id, scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=a["org"].id, role=ROLE_SALES_REP,
                              is_active=True))
    db_session.commit()
    made = comp.earn(db_session, _won(db_session, a, owner=lone),
                     collection_reference="inv-1",
                     collected_amount=Decimal("1497.00"))
    assert len(made) == 1
    assert made[0].payee_kind == PAYEE_SELLER


def test_the_override_goes_to_the_actual_manager(db_session, a):
    made = comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
                     collected_amount=Decimal("1497.00"))
    override = [e for e in made if e.payee_kind == PAYEE_OVERRIDE][0]
    assert override.payee_user_id == a["manager"].id
    assert override.override_level == 1
    assert override.amount == Decimal("100.00")


def test_the_package_cap_is_enforced(db_session, a):
    """$500 + $100 is under the $800 cap; raising the rules past it must not."""
    a["plan"].max_override_levels = 1
    rules = db_session.query(CompensationRule).filter(
        CompensationRule.plan_id == a["plan"].id).all()
    for r in rules:
        r.amount = Decimal("600.00")        # 1200 total, over the 800 cap
    db_session.commit()

    made = comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
                     collected_amount=Decimal("1497.00"))
    assert sum(e.amount for e in made) == Decimal("800.00")
    # Reduced proportionally, and each row records what it was cut from.
    assert all(e.capped_from_amount == Decimal("600.00") for e in made)


# ═════════════════════════════════════════════════════════════════════════════
# 24. A plan change does not rewrite history
# ═════════════════════════════════════════════════════════════════════════════

def test_changing_the_plan_does_not_move_a_paid_entry(db_session, a, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)
    e = (db_session.query(CompensationEntry)
         .filter(CompensationEntry.payee_kind == PAYEE_SELLER).first())
    comp.mark_paid(db_session, e, payment_reference="ACH-1", paid_by=god.id)
    before = (e.amount, e.rate_amount, e.payable_at, e.paid_at, e.state)

    for r in db_session.query(CompensationRule).filter(
            CompensationRule.plan_id == a["plan"].id).all():
        r.amount = Decimal("9999.00")
    a["plan"].holdback_days = 90
    db_session.commit()
    db_session.refresh(e)

    assert (e.amount, e.rate_amount, e.payable_at, e.paid_at, e.state) == before
    assert e.amount == Decimal("500.00")


def test_the_entry_explains_itself_without_the_plan(db_session, a):
    """The snapshot is what answers "why this amount" years later."""
    made = comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
                     collected_amount=Decimal("1497.00"))
    e = [x for x in made if x.payee_kind == PAYEE_SELLER][0]
    assert e.basis == BASIS_FIXED
    assert e.rate_amount == Decimal("500.00")
    assert e.basis_implementation_fee == Decimal("1497.00")
    assert e.basis_tcv == Decimal("7997.00")
    assert e.collection_reference == "inv-1"


# ═════════════════════════════════════════════════════════════════════════════
# 26. Unconfigured packages invent nothing
# ═════════════════════════════════════════════════════════════════════════════

def test_an_unconfigured_package_earns_nothing_and_is_not_zero(db_session, a):
    growth = BrandPackage(platform_id=a["platform"].id, name="Growth",
                          key="growth-%d" % next(_SEQ),
                          price=Decimal("2495.00"), setup_fee=Decimal("2495.00"),
                          currency="USD")
    db_session.add(growth); db_session.commit()
    opp = _won(db_session, a)
    opp.selected_package_id = growth.id
    db_session.commit()

    made = comp.earn(db_session, opp, collection_reference="inv-1",
                     collected_amount=Decimal("2495.00"))
    assert made == []
    assert db_session.query(CompensationEntry).count() == 0


# ═════════════════════════════════════════════════════════════════════════════
# 31. Totals reconcile EXACTLY to ledger rows
# ═════════════════════════════════════════════════════════════════════════════

def test_every_headline_equals_the_sum_of_the_rows_behind_it(
        db_session, a, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="old",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.earn(db_session, _won(db_session, a), collection_reference="new",
              collected_amount=Decimal("1497.00"))
    comp.promote_due_to_payable(db_session)
    paid_row = (db_session.query(CompensationEntry)
                .filter(CompensationEntry.state == COMP_PAYABLE).first())
    comp.mark_paid(db_session, paid_row, payment_reference="ACH-1",
                   paid_by=god.id)

    s = ledger.summary(db_session, god)
    for view, bucket in ((ledger.VIEW_ON_HOLD, s["on_hold"]),
                         (ledger.VIEW_PAYABLE_NOW, s["payable_now"]),
                         (ledger.VIEW_PAID, s["paid"])):
        rows = ledger.entries(db_session, god, view=view)
        assert bucket["count"] == len(rows), view
        assert bucket["amount"] == round(sum(r["amount"] for r in rows), 2), view

    all_rows = ledger.entries(db_session, god, view=ledger.VIEW_ALL)
    assert s["earned_total"] == round(sum(r["amount"] for r in all_rows), 2)


def test_payables_by_payee_reconciles_to_the_payable_rows(db_session, a, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="old",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)

    lines = ledger.by_payee(db_session, god, view=ledger.VIEW_PAYABLE_NOW)
    rows = ledger.entries(db_session, god, view=ledger.VIEW_PAYABLE_NOW)
    assert round(sum(l["amount"] for l in lines), 2) == \
        round(sum(r["amount"] for r in rows), 2)
    assert sum(l["count"] for l in lines) == len(rows)
    # Every entry id a payment run would settle is a row the ledger showed.
    assert {i for l in lines for i in l["entry_ids"]} == {r["id"] for r in rows}


def test_upcoming_liability_only_counts_rows_that_already_exist(db_session, a):
    """Nothing from the open pipeline. A deal with no collected payment has no
    payable date, and inventing one would turn a forecast into a liability."""
    _won(db_session, a, status="open")            # projects, earns nothing
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"))
    god = _user(db_session, "Owner2", role="god_admin")

    windows = {w["window_days"]: w for w in
               ledger.upcoming_liability(db_session,
                                         ledger.visible_brand_ids(db_session, god))}
    assert windows[7]["amount"] == 0            # 14-day holdback, nothing in 7
    assert windows[30]["amount"] == 600.0
    assert windows[30]["count"] == 2


# ═════════════════════════════════════════════════════════════════════════════
# 1-8, 30. Brand isolation
# ═════════════════════════════════════════════════════════════════════════════

def test_a_brands_manager_sees_only_their_own_brands_ledger(db_session, a, b):
    comp.earn(db_session, _won(db_session, a), collection_reference="a-1",
              collected_amount=Decimal("1497.00"))
    comp.earn(db_session, _won(db_session, b), collection_reference="b-1",
              collected_amount=Decimal("1497.00"))

    rows_a = ledger.entries(db_session, a["manager"])
    assert rows_a
    assert {r["brand_sales_org_id"] for r in rows_a} == {a["org"].id}

    rows_b = ledger.entries(db_session, b["manager"])
    assert {r["brand_sales_org_id"] for r in rows_b} == {b["org"].id}


def test_a_brands_totals_exclude_another_brands_money(db_session, a, b):
    comp.earn(db_session, _won(db_session, a), collection_reference="a-1",
              collected_amount=Decimal("1497.00"))
    comp.earn(db_session, _won(db_session, b), collection_reference="b-1",
              collected_amount=Decimal("1497.00"))
    # Beta pays $250 with no override; Alpha pays $600 across two people.
    assert ledger.summary(db_session, a["manager"])["earned_total"] == 600.0
    assert ledger.summary(db_session, b["manager"])["earned_total"] == 250.0


def test_a_user_with_no_sales_membership_gets_an_empty_ledger_not_everything(
        db_session, a):
    """A scoping bug must produce nothing, never everything."""
    comp.earn(db_session, _won(db_session, a), collection_reference="a-1",
              collected_amount=Decimal("1497.00"))
    outsider = _user(db_session, "Outsider")
    assert ledger.visible_brand_ids(db_session, outsider) == []
    assert ledger.entries(db_session, outsider) == []
    assert ledger.summary(db_session, outsider)["earned_total"] == 0.0


def test_god_sees_across_brands(db_session, a, b, god):
    comp.earn(db_session, _won(db_session, a), collection_reference="a-1",
              collected_amount=Decimal("1497.00"))
    comp.earn(db_session, _won(db_session, b), collection_reference="b-1",
              collected_amount=Decimal("1497.00"))
    assert ledger.summary(db_session, god)["earned_total"] == 850.0


# ═════════════════════════════════════════════════════════════════════════════
# 28-29. HTTP authorization
# ═════════════════════════════════════════════════════════════════════════════

def test_a_rep_cannot_read_the_team_command_centre(client, db_session, a):
    r = client.get("/sales/compensation/overview", headers=_h(db_session, a["rep"]))
    assert r.status_code == 403


def test_a_rep_cannot_read_the_team_ledger(client, db_session, a):
    r = client.get("/sales/compensation/ledger", headers=_h(db_session, a["rep"]))
    assert r.status_code == 403


def test_a_rep_can_read_their_own_compensation(client, db_session, a):
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"))
    r = client.get("/sales/compensation/me", headers=_h(db_session, a["rep"]))
    assert r.status_code == 200
    d = r.json()
    assert d["payee_user_id"] == a["rep"].id
    # Their own $500, NOT the manager's $100 override on the same deal.
    assert d["summary"]["earned_total"] == 500.0
    assert {e["payee_user_id"] for e in d["entries"]} == {a["rep"].id}


def test_my_compensation_cannot_be_pointed_at_somebody_else(
        client, db_session, a):
    """There is no payee parameter. A filter a client could change is not a
    boundary, so the endpoint takes the id from the token and nothing else."""
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"))
    r = client.get("/sales/compensation/me?payee_user_id=" + a["manager"].id,
                   headers=_h(db_session, a["rep"]))
    assert r.status_code == 200
    assert {e["payee_user_id"] for e in r.json()["entries"]} == {a["rep"].id}


def test_a_manager_of_one_brand_is_refused_another_brands_ledger(
        client, db_session, a, b):
    r = client.get("/sales/compensation/ledger?brand_sales_org_id=" + b["org"].id,
                   headers=_h(db_session, a["manager"]))
    assert r.status_code == 403


def test_a_manager_cannot_settle_payments(client, db_session, a):
    """Seeing what is owed and moving money are different authorities."""
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)
    e = db_session.query(CompensationEntry).first()
    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [e.id], "payment_reference": "ACH-1"},
                    headers=_h(db_session, a["manager"]))
    assert r.status_code == 403
    db_session.refresh(e)
    assert e.state == COMP_PAYABLE


def test_a_manager_cannot_record_a_collection(client, db_session, a):
    opp = _won(db_session, a)
    r = client.post("/sales/compensation/opportunities/%s/record-collection" % opp.id,
                    json={"collection_reference": "inv-1",
                          "collected_amount": 1497.0},
                    headers=_h(db_session, a["manager"]))
    assert r.status_code == 403
    assert db_session.query(CompensationEntry).count() == 0


# ═════════════════════════════════════════════════════════════════════════════
# The owner path, end to end over HTTP
# ═════════════════════════════════════════════════════════════════════════════

def test_the_owner_can_record_a_collection_and_settle_it(client, db_session, a, god):
    h = _h(db_session, god)
    opp = _won(db_session, a)

    r = client.post("/sales/compensation/opportunities/%s/record-collection" % opp.id,
                    json={"collection_reference": "inv-1",
                          "collected_amount": 1497.0,
                          "collected_at": (datetime.utcnow()
                                           - timedelta(days=20)).isoformat()},
                    headers=h)
    assert r.status_code == 200
    assert r.json()["entries_for_this_collection"] == 2

    assert client.post("/sales/compensation/promote-due", json={},
                       headers=h).json()["promoted"] == 2

    pay = client.get("/sales/compensation/payables", headers=h).json()
    assert pay["total"] == 600.0
    ids = [i for l in pay["lines"] for i in l["entry_ids"]]

    done = client.post("/sales/compensation/pay",
                       json={"entry_ids": ids, "payment_reference": "ACH-1",
                             "payment_batch_reference": "WK-36",
                             "payment_method": "ach"}, headers=h)
    assert done.status_code == 200
    assert done.json()["total"] == 600.0

    after = client.get("/sales/compensation/overview", headers=h).json()
    assert after["summary"]["paid"]["amount"] == 600.0
    assert after["summary"]["payable_now"]["amount"] == 0


def test_a_second_settlement_of_the_same_entries_is_refused_and_changes_nothing(
        client, db_session, a, god):
    h = _h(db_session, god)
    comp.earn(db_session, _won(db_session, a), collection_reference="inv-1",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.promote_due_to_payable(db_session)
    ids = [e.id for e in db_session.query(CompensationEntry).all()]

    first = client.post("/sales/compensation/pay",
                        json={"entry_ids": ids, "payment_reference": "ACH-1"},
                        headers=h)
    assert first.status_code == 200

    second = client.post("/sales/compensation/pay",
                         json={"entry_ids": ids, "payment_reference": "ACH-2"},
                         headers=h)
    assert second.status_code == 409
    for e in db_session.query(CompensationEntry).all():
        db_session.refresh(e)
        assert e.payment_reference == "ACH-1"


def test_a_partial_batch_pays_nothing_at_all(client, db_session, a, god):
    """One bad entry aborts the run. A half-finished payment run is the worst
    outcome, because nobody can tell afterwards which cheques went out."""
    h = _h(db_session, god)
    comp.earn(db_session, _won(db_session, a), collection_reference="old",
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=20))
    comp.earn(db_session, _won(db_session, a), collection_reference="new",
              collected_amount=Decimal("1497.00"))
    comp.promote_due_to_payable(db_session)

    payable = [e.id for e in db_session.query(CompensationEntry)
               .filter(CompensationEntry.state == COMP_PAYABLE).all()]
    held = [e.id for e in db_session.query(CompensationEntry)
            .filter(CompensationEntry.state == COMP_EARNED).all()]

    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": payable + held,
                          "payment_reference": "ACH-1"}, headers=h)
    assert r.status_code == 409
    assert db_session.query(CompensationEntry).filter(
        CompensationEntry.state == COMP_PAID).count() == 0
