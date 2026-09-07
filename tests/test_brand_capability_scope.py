"""BRAND-SCOPED CAPABILITIES — four authorities that must never leak into each other.

    CAN VIEW COMPENSATION        `sales_comp_view` over one brand
    CAN SETTLE COMPENSATION      `sales_comp_manage` over one brand
    SALES MANAGEMENT AUTHORITY   a manager membership in one brand
    GOD / PLATFORM AUTHORITY     role = god_admin

A finance administrator must be able to hold the first two WITHOUT becoming a
sales manager, without becoming a god_admin, and without being able to walk into
a single customer workspace. That was impossible before this change:
`UserCapabilityGrant.organization_id` was NOT NULL and a brand-sales user has
`organization_id = NULL`, so there was no row that could carry the grant.

THE SCOPE IS NAMED, NOT INFERRED. Authority is the pair (scope_type, scope_id).
There is deliberately no NULL-means-everywhere anywhere in this file's
assertions, because a NULL that means "global" is one forgotten filter away from
handing a clerk every brand's payroll.

The lesson is the one `AuditLogEntry` already taught this codebase: when a table
cannot represent the actor, admit the other scope rather than inventing a
parallel permission system.
"""

import itertools
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models.compensation_models import (BASIS_FIXED, COMP_PAYABLE,
                                            PAYEE_OVERRIDE, PAYEE_SELLER,
                                            CompensationEntry,
                                            CompensationPlan, CompensationRule)
from app.models.models import (Organization, Platform, User,
                               UserCapabilityGrant)
from app.models.sales_models import (ROLE_SALES_MANAGER, ROLE_SALES_REP,
                                     SCOPE_BRAND_SALES_ORG, SCOPE_CUSTOMER_ORG,
                                     BrandPackage, BrandSalesOrg, Membership,
                                     Opportunity)
from app.services import capabilities as caps
from app.services import compensation as comp
from app.services import compensation_ledger as ledger
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _user(db, name, role="advisor", org_id=None):
    u = User(organization_id=org_id, email="u%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("x"), full_name=name, role=role,
             must_change_password=False)
    db.add(u); db.commit()
    return u


def _brand(db, label):
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
                            effective_from=datetime(2026, 1, 1).date(),
                            holdback_days=14, max_override_levels=1,
                            is_active=True)
    db.add(plan); db.commit()
    db.add(CompensationRule(plan_id=plan.id, package_id=pkg.id,
                            payee_kind=PAYEE_SELLER, basis=BASIS_FIXED,
                            amount=Decimal("500.00"), sort_order=1))
    db.commit()
    return dict(platform=plat, org=org, pkg=pkg, manager=manager, rep=rep,
                plan=plan)


@pytest.fixture()
def a(db_session):
    return _brand(db_session, "Alpha")


@pytest.fixture()
def b(db_session):
    return _brand(db_session, "Beta")


@pytest.fixture()
def god(db_session):
    return _user(db_session, "Owner", role="god_admin")


def _h(db, u):
    return {"Authorization": "Bearer " + create_access_token(u, db)}


def _grant(db, user, brand, keys, actor):
    caps.set_brand_grants(db, user, brand["org"].id, brand["org"].name,
                          actor, keys)
    db.commit()


def _earned(db, brand, *, days_ago=20):
    opp = Opportunity(brand_sales_org_id=brand["org"].id,
                      owner_user_id=brand["rep"].id,
                      company_name="Deal %d" % next(_SEQ),
                      selected_package_id=brand["pkg"].id,
                      stage="closing", status="won",
                      billing_option="term_agreement", contract_term_months=13)
    db.add(opp); db.commit()
    comp.earn(db, opp, collection_reference="inv-%d" % next(_SEQ),
              collected_amount=Decimal("1497.00"),
              collected_at=datetime.utcnow() - timedelta(days=days_ago))
    return opp


# ═════════════════════════════════════════════════════════════════════════════
# The grant itself
# ═════════════════════════════════════════════════════════════════════════════

def test_a_brand_grant_is_stored_with_a_named_scope_and_no_organization(
        db_session, a, god):
    """The row says WHERE it applies. It does not leave it to be inferred."""
    finance = _user(db_session, "Finance")
    _grant(db_session, finance, a, ["sales_comp_view"], god)

    row = db_session.query(UserCapabilityGrant).filter(
        UserCapabilityGrant.user_id == finance.id).one()
    assert row.scope_type == SCOPE_BRAND_SALES_ORG
    assert row.scope_id == a["org"].id
    # NULL here means "not a customer-org grant" — never "every organization".
    assert row.organization_id is None
    assert row.capability == "sales_comp_view"


def test_a_capability_nothing_enforces_at_brand_scope_is_refused(
        db_session, a, god):
    """A grant that can never take effect is worse than no grant: the screen
    would show somebody as authorised while every request they made failed."""
    from fastapi import HTTPException
    finance = _user(db_session, "Finance")
    with pytest.raises(HTTPException) as e:
        caps.set_brand_grants(db_session, finance, a["org"].id, "Alpha", god,
                              ["twilio_manage"])
    assert e.value.status_code == 400
    assert db_session.query(UserCapabilityGrant).count() == 0


def test_revoking_deactivates_rather_than_deletes(db_session, a, god):
    """"Who could settle commissions in June" must stay answerable."""
    finance = _user(db_session, "Finance")
    _grant(db_session, finance, a, ["sales_comp_view"], god)
    _grant(db_session, finance, a, [], god)

    row = db_session.query(UserCapabilityGrant).filter(
        UserCapabilityGrant.user_id == finance.id).one()
    assert row.is_active is False
    assert caps.brand_grants_for(db_session, finance.id, a["org"].id) == []


# ═════════════════════════════════════════════════════════════════════════════
# 1-2. A grant over one brand is not a grant over another
# ═════════════════════════════════════════════════════════════════════════════

def test_brand_a_view_cannot_read_brand_b_compensation(db_session, a, b, god):
    finance = _user(db_session, "Alpha Finance")
    _grant(db_session, finance, a, ["sales_comp_view"], god)
    _earned(db_session, a)
    _earned(db_session, b)

    assert caps.has_brand_capability(db_session, finance, a["org"].id,
                                     "sales_comp_view") is True
    assert caps.has_brand_capability(db_session, finance, b["org"].id,
                                     "sales_comp_view") is False
    assert ledger.visible_brand_ids(db_session, finance) == [a["org"].id]
    rows = ledger.entries(db_session, finance)
    assert rows
    assert {r["brand_sales_org_id"] for r in rows} == {a["org"].id}


def test_brand_a_view_is_refused_brand_b_over_http(client, db_session, a, b, god):
    finance = _user(db_session, "Alpha Finance")
    _grant(db_session, finance, a, ["sales_comp_view"], god)

    ok = client.get("/sales/compensation/ledger?brand_sales_org_id=" + a["org"].id,
                    headers=_h(db_session, finance))
    assert ok.status_code == 200

    denied = client.get("/sales/compensation/ledger?brand_sales_org_id=" + b["org"].id,
                        headers=_h(db_session, finance))
    assert denied.status_code == 403


def test_brand_a_manage_cannot_settle_brand_b(client, db_session, a, b, god):
    finance = _user(db_session, "Alpha Finance")
    _grant(db_session, finance, a, ["sales_comp_view", "sales_comp_manage"], god)
    _earned(db_session, b)
    comp.promote_due_to_payable(db_session)
    beta_entry = db_session.query(CompensationEntry).filter(
        CompensationEntry.brand_sales_org_id == b["org"].id).first()

    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [beta_entry.id],
                          "payment_reference": "ACH-1"},
                    headers=_h(db_session, finance))
    # 404, not 403: they cannot see Beta at all, and confirming the entry
    # exists would itself be a disclosure.
    assert r.status_code == 404
    db_session.refresh(beta_entry)
    assert beta_entry.state == COMP_PAYABLE


def test_releasing_holdbacks_does_not_cross_a_brand_boundary(
        client, db_session, a, b, god):
    """A bulk operation with no id in it must still be scoped."""
    finance = _user(db_session, "Alpha Finance")
    _grant(db_session, finance, a, ["sales_comp_view", "sales_comp_manage"], god)
    _earned(db_session, a)
    _earned(db_session, b)

    r = client.post("/sales/compensation/promote-due", json={},
                    headers=_h(db_session, finance))
    assert r.status_code == 200
    assert r.json()["promoted"] == 1          # Alpha's seller entry only

    beta = db_session.query(CompensationEntry).filter(
        CompensationEntry.brand_sales_org_id == b["org"].id).all()
    assert all(e.state != COMP_PAYABLE for e in beta)


# ═════════════════════════════════════════════════════════════════════════════
# 3-4. The four authorities do not imply one another
# ═════════════════════════════════════════════════════════════════════════════

def test_view_does_not_imply_manage(client, db_session, a, god):
    viewer = _user(db_session, "Viewer")
    _grant(db_session, viewer, a, ["sales_comp_view"], god)
    _earned(db_session, a)
    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).first()

    assert client.get("/sales/compensation/overview",
                      headers=_h(db_session, viewer)).status_code == 200

    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [entry.id], "payment_reference": "ACH-1"},
                    headers=_h(db_session, viewer))
    assert r.status_code == 403
    db_session.refresh(entry)
    assert entry.state == COMP_PAYABLE


def test_manage_does_not_imply_sales_manager_authority(db_session, a, god):
    """Settling commissions must not hand somebody a sales team."""
    from app.services.sales_access import is_sales_manager, is_sales_member
    finance = _user(db_session, "Finance")
    _grant(db_session, finance, a, ["sales_comp_view", "sales_comp_manage"], god)

    assert is_sales_manager(finance, db_session, a["org"].id) is False
    assert is_sales_member(finance, db_session, a["org"].id) is False


def test_a_sales_manager_cannot_settle_without_the_capability(
        client, db_session, a):
    """Running a team and moving money are different jobs."""
    _earned(db_session, a)
    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).first()

    # They CAN see it — running the team includes knowing what it earns.
    assert client.get("/sales/compensation/overview",
                      headers=_h(db_session, a["manager"])).status_code == 200

    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [entry.id], "payment_reference": "ACH-1"},
                    headers=_h(db_session, a["manager"]))
    assert r.status_code == 403
    db_session.refresh(entry)
    assert entry.state == COMP_PAYABLE


def test_a_manager_granted_the_capability_may_then_settle(
        client, db_session, a, god):
    """The escalation is one audited grant, not a role change."""
    _earned(db_session, a)
    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).first()
    _grant(db_session, a["manager"], a, ["sales_comp_manage"], god)

    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [entry.id], "payment_reference": "ACH-1"},
                    headers=_h(db_session, a["manager"]))
    assert r.status_code == 200
    db_session.refresh(entry)
    assert entry.state == "paid"


# ═════════════════════════════════════════════════════════════════════════════
# 5-6. Compensation authority and customer workspaces are separate worlds
# ═════════════════════════════════════════════════════════════════════════════

def test_compensation_capabilities_grant_no_customer_workspace_access(
        db_session, a, god):
    from app.services.workspace_access import has_workspace, workspace_org_ids
    finance = _user(db_session, "Finance")
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    _grant(db_session, finance, a, ["sales_comp_view", "sales_comp_manage"], god)

    # No customer-org grant was created as a side effect.
    assert caps.grants_for(db_session, finance.id, org.id) == []
    # And no workspace opened up.
    assert workspace_org_ids(finance, db_session) == []
    assert has_workspace(finance, db_session, org.id) is False


def test_a_customer_org_grant_confers_no_compensation_authority(
        db_session, a, god):
    """The mirror image, and the reason `grants_for` filters on scope_type."""
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    admin = _user(db_session, "Customer Admin", role="org_admin", org_id=org.id)

    # Written directly, the way the customer-org path writes it.
    db_session.add(UserCapabilityGrant(
        user_id=admin.id, organization_id=org.id,
        scope_type=SCOPE_CUSTOMER_ORG, scope_id=org.id,
        capability="sales_comp_view", is_active=True))
    db_session.commit()

    assert caps.brand_grants_for(db_session, admin.id, a["org"].id) == []
    assert caps.has_brand_capability(db_session, admin, a["org"].id,
                                     "sales_comp_view") is False
    assert ledger.visible_brand_ids(db_session, admin) == []


def test_a_brand_grant_never_satisfies_a_customer_org_question(
        db_session, a, god):
    """A brand row has organization_id NULL, and `grants_for` also names the
    scope — so it cannot answer a customer-org check even by accident."""
    finance = _user(db_session, "Finance")
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    _grant(db_session, finance, a, ["sales_comp_view"], god)

    assert caps.grants_for(db_session, finance.id, org.id) == []
    assert caps.user_has_grant(db_session, finance.id, org.id,
                               "sales_comp_view") is False


# ═════════════════════════════════════════════════════════════════════════════
# 7. Dual-role users hold both, independently
# ═════════════════════════════════════════════════════════════════════════════

def test_a_dual_role_user_holds_both_authorities_without_merging_them(
        client, db_session, a, god):
    """Mike legitimately runs a team AND signs the cheques. Both authorities
    apply; neither was inferred from the other."""
    from app.services.sales_access import is_sales_manager
    _earned(db_session, a)
    comp.promote_due_to_payable(db_session)
    entry = db_session.query(CompensationEntry).first()
    _grant(db_session, a["manager"], a, ["sales_comp_view", "sales_comp_manage"], god)

    assert is_sales_manager(a["manager"], db_session, a["org"].id) is True
    assert caps.has_brand_capability(db_session, a["manager"], a["org"].id,
                                     "sales_comp_manage") is True
    r = client.post("/sales/compensation/pay",
                    json={"entry_ids": [entry.id], "payment_reference": "ACH-1"},
                    headers=_h(db_session, a["manager"]))
    assert r.status_code == 200


def test_a_finance_user_with_no_membership_can_still_work(
        client, db_session, a, god):
    """THE WHOLE POINT. No sales membership, no god role, no customer org —
    and a working compensation surface."""
    finance = _user(db_session, "Finance")
    _grant(db_session, finance, a, ["sales_comp_view", "sales_comp_manage"], god)
    _earned(db_session, a)
    h = _h(db_session, finance)

    assert client.get("/sales/compensation/overview", headers=h).status_code == 200
    assert client.post("/sales/compensation/promote-due", json={},
                       headers=h).json()["promoted"] == 1

    pay = client.get("/sales/compensation/payables", headers=h).json()
    assert pay["can_process_payments"] is True
    ids = [i for l in pay["lines"] for i in l["entry_ids"]]
    assert client.post("/sales/compensation/pay",
                       json={"entry_ids": ids, "payment_reference": "ACH-1"},
                       headers=h).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# 8-10. God still works; nothing existing regressed
# ═════════════════════════════════════════════════════════════════════════════

def test_god_still_passes_every_gate(client, db_session, a, b, god):
    _earned(db_session, a)
    _earned(db_session, b)
    h = _h(db_session, god)
    assert client.get("/sales/compensation/overview", headers=h).status_code == 200
    assert caps.has_brand_capability(db_session, god, a["org"].id,
                                     "sales_comp_manage") is True
    assert client.post("/sales/compensation/promote-due", json={},
                       headers=h).json()["promoted"] == 2


def test_an_existing_customer_org_grant_still_resolves(db_session, a):
    """THE REGRESSION THAT MATTERED. `scope_type` defaults to customer_org and
    the migration backfills `scope_id`, so a grant written before this change
    keeps working."""
    org = Organization(name="A Customer", slug="cust-%d" % next(_SEQ),
                       platform_id=a["platform"].id, is_active=True)
    db_session.add(org); db_session.commit()
    admin = _user(db_session, "Customer Admin", role="org_admin", org_id=org.id)

    # No scope columns passed — exactly how the pre-existing code writes it.
    db_session.add(UserCapabilityGrant(
        user_id=admin.id, organization_id=org.id,
        capability="twilio_credentials", is_active=True))
    db_session.commit()

    row = db_session.query(UserCapabilityGrant).filter(
        UserCapabilityGrant.user_id == admin.id).one()
    assert row.scope_type == SCOPE_CUSTOMER_ORG      # from the column default
    assert caps.grants_for(db_session, admin.id, org.id) == ["twilio_credentials"]
    assert caps.user_has_grant(db_session, admin.id, org.id,
                               "twilio_credentials") is True


def test_a_user_with_no_authority_anywhere_is_refused_not_given_an_empty_page(
        client, db_session, a):
    """403, not 200-with-nothing. An empty ledger reads as "there is no
    compensation", which is a different and much worse answer."""
    nobody = _user(db_session, "Nobody")
    _earned(db_session, a)
    r = client.get("/sales/compensation/overview", headers=_h(db_session, nobody))
    assert r.status_code == 403


# ═════════════════════════════════════════════════════════════════════════════
# The God Mode grant surface
# ═════════════════════════════════════════════════════════════════════════════

def test_only_god_may_grant_brand_capabilities(client, db_session, a, god):
    finance = _user(db_session, "Finance")
    body = {"brand_sales_org_id": a["org"].id, "user_id": finance.id,
            "capabilities": ["sales_comp_view"]}

    for who in (a["manager"], a["rep"], finance):
        r = client.put("/god/pricing/brand-access", json=body,
                       headers=_h(db_session, who))
        assert r.status_code == 403
    assert db_session.query(UserCapabilityGrant).count() == 0

    ok = client.put("/god/pricing/brand-access", json=body,
                    headers=_h(db_session, god))
    assert ok.status_code == 200
    assert ok.json()["capabilities"] == ["sales_comp_view"]


def test_the_grant_screen_lists_members_and_anybody_already_granted(
        client, db_session, a, god):
    finance = _user(db_session, "Finance")
    _grant(db_session, finance, a, ["sales_comp_manage"], god)

    d = client.get("/god/pricing/brand-access?brand_sales_org_id=" + a["org"].id,
                   headers=_h(db_session, god)).json()
    by_id = {p["user_id"]: p for p in d["people"]}
    # The finance user has no membership and must still be listed, or revoking
    # them would mean finding them by memory.
    assert by_id[finance.id]["capabilities"] == ["sales_comp_manage"]
    assert by_id[finance.id]["is_brand_member"] is False
    assert by_id[a["manager"].id]["is_brand_member"] is True
    assert {c["key"] for c in d["available_capabilities"]} == {
        "sales_comp_view", "sales_comp_manage"}


def test_granting_is_audited_with_the_scope_it_applied_to(db_session, a, god):
    from app.models.models import AuditLogEntry
    import json as _json
    finance = _user(db_session, "Finance")
    _grant(db_session, finance, a, ["sales_comp_view"], god)

    row = (db_session.query(AuditLogEntry)
           .filter(AuditLogEntry.action == "brand.capabilities_set").one())
    assert row.actor_user_id == god.id
    assert row.target_id == finance.id
    after = _json.loads(row.after_state) if isinstance(row.after_state, str) \
        else row.after_state
    assert after["scope_type"] == SCOPE_BRAND_SALES_ORG
    assert after["scope_id"] == a["org"].id
    assert after["capabilities"] == ["sales_comp_view"]
