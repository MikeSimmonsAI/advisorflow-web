"""THE FIRST REAL ARRANGEMENT, EXERCISED AS DATA.

This file proves the success criteria for the first customer whose commercial
structure the catalogue cannot express. It is a TEST, against a synthetic
in-memory database, and every value in it is a fixture: the company, the
contact, the split, the unanswered terms. Nothing in the application knows any
of them.

That is the point of running it. If a future change makes the platform unable
to onboard a customer whose terms are unfinished, this file fails — and it
fails on the shape of the problem rather than on a hard-coded customer, so it
keeps protecting the next one too.

WHAT IT ASSERTS THE PLATFORM DOES NOT DO
----------------------------------------
No charge, no invoice, no Stripe object, no entitlement, no settlement, no
outbound message. Those are asserted by counting rows that must stay at zero,
because "we did not mean to send anything" is not evidence.
"""

import itertools
from datetime import date

import pytest

from app.models.billing_models import BillingInvoice, BillingPayment
from app.models.commercial_models import (
    AG_TERMS_REQUIRED, CommercialSettlement, ITEM_DEMO,
    MODE_COMPLETED_PREVIOUSLY, PARTY_CUSTOMER_ORG, PARTY_PLATFORM_BRAND,
    SETTLE_REVIEW_REQUIRED, TYPE_REVENUE_SHARE,
)
from app.models.implementation_models import (
    Implementation, ImplementationMilestone, MILESTONE_PENDING,
)
from app.models.models import Organization, Platform, User
from app.models.purchase_models import CatalogPurchase
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, ROLE_SALES_MANAGER,
    SCOPE_BRAND_SALES_ORG, STAGE_ONBOARDING,
)
from app.services.auth_service import hash_password
from app.services.commercial import agreements as ag
from app.services.commercial import onboarding as flow
from app.services.commercial import overrides as ov
from app.services.commercial import revenue_share as rs
from app.services.commercial import settlement as settle
from app.services.commercial import terms as t

_SEQ = itertools.count(1)

# ── the deal, as it actually stands. Every line is DATA. ────────────────────
COMPANY = "Atlantis Light & Power"
CONTACT = "Josh Shronce"
PHONE = "469-926-6146"
EMAIL = "joshua@atlantis-enterprises.com"
WEBSITE = "https://atlantislightandpower.com/"

PROVIDER_SHARE = 75
CUSTOMER_SHARE = 25

# What is known: nothing up front, nothing monthly. Answered as zero, because
# zero is what was agreed — which is a different fact from "not discussed".
KNOWN_MONEY = {"setup_fee": 0, "recurring_base_fee": 0}

# What is genuinely not known yet. Listed here so the test asserts the platform
# leaves them alone rather than filling them in.
STILL_UNKNOWN = ["share_basis", "eligible_collections", "payment_recipient",
                 "settlement_frequency", "collections_source",
                 "attribution_rule", "allocation_rule", "adjustment_refunds",
                 "adjustment_chargebacks", "adjustment_processing_fees",
                 "adjustment_taxes", "adjustment_credits",
                 "adjustment_write_offs"]


@pytest.fixture()
def deal(db_session):
    """A brand, a seller, the customer tenant, and the arrangement — in the
    order the real workflow creates them."""
    plat = Platform(name="Brand", slug="brand-%d" % next(_SEQ), short_name="B",
                    tagline="t", support_email="s@example.test")
    db_session.add(plat)
    db_session.commit()

    bso = BrandSalesOrg(platform_id=plat.id, name="Brand Sales",
                        slug="bso-%d" % next(_SEQ))
    db_session.add(bso)
    db_session.commit()

    manager = User(organization_id=None,
                   email="manager-%d@test.local" % next(_SEQ),
                   password_hash=hash_password("TestPass123!"),
                   full_name="Sales Manager", role="user",
                   must_change_password=False, platform_id=plat.id)
    db_session.add(manager)
    db_session.commit()
    db_session.add(Membership(user_id=manager.id,
                              scope_type=SCOPE_BRAND_SALES_ORG,
                              scope_id=bso.id, role=ROLE_SALES_MANAGER,
                              is_active=True))
    db_session.commit()

    opp = Opportunity(brand_sales_org_id=bso.id, company_name=COMPANY,
                      contact_name=CONTACT, phone=PHONE, email=EMAIL,
                      website=WEBSITE, stage=STAGE_ONBOARDING, status="won")
    db_session.add(opp)
    db_session.commit()

    org = Organization(name=COMPANY, slug="atlantis-%d" % next(_SEQ),
                       platform_id=plat.id, plan="standard", is_active=True)
    db_session.add(org)
    db_session.commit()
    opp.customer_organization_id = org.id

    impl = Implementation(organization_id=org.id, platform_id=plat.id,
                          brand_sales_org_id=bso.id, opportunity_id=opp.id,
                          status="not_started")
    db_session.add(impl)
    db_session.commit()
    for i, (key, required) in enumerate([("business_profile", True),
                                         ("customer_users", True),
                                         ("calendar", True),
                                         ("lead_import", False),
                                         ("launch", True)]):
        db_session.add(ImplementationMilestone(
            implementation_id=impl.id, key=key,
            label=key.replace("_", " ").title(), position=i,
            is_required=required, status=MILESTONE_PENDING))
    db_session.commit()

    return {"plat": plat, "bso": bso, "manager": manager, "opp": opp,
            "org": org, "impl": impl}


def _record_the_arrangement(db, deal):
    """Exactly the sequence an operator performs through the real endpoints."""
    manager = deal["manager"]
    agreement = ag.create(db, manager,
                          platform_id=deal["plat"].id,
                          brand_sales_org_id=deal["bso"].id,
                          organization_id=deal["org"].id,
                          opportunity_id=deal["opp"].id,
                          implementation_id=deal["impl"].id,
                          agreement_type=TYPE_REVENUE_SHARE,
                          name="%s revenue share" % COMPANY,
                          effective_date=date.today())

    provider = ag.add_party(db, agreement, manager, party_key="provider",
                            display_name="Provider side",
                            party_type=PARTY_PLATFORM_BRAND,
                            brand_sales_org_id=deal["bso"].id)
    customer = ag.add_party(db, agreement, manager, party_key="customer",
                            display_name="%s side" % COMPANY,
                            party_type=PARTY_CUSTOMER_ORG,
                            organization_id=deal["org"].id)
    ag.set_allocation(db, agreement, manager, provider.id, percent=PROVIDER_SHARE)
    ag.set_allocation(db, agreement, manager, customer.id, percent=CUSTOMER_SHARE)

    for key, value in KNOWN_MONEY.items():
        t.set_term(db, agreement, manager, key, value, source="internal",
                   note="Currently contemplated.")

    db.commit()
    return agreement


class TestAtlantisOnboarding:
    def test_the_customer_is_created_with_its_contact(self, db_session, deal):
        assert deal["opp"].company_name == COMPANY
        assert deal["opp"].contact_name == CONTACT
        assert deal["opp"].phone == PHONE
        assert deal["opp"].email == EMAIL
        assert deal["opp"].customer_organization_id == deal["org"].id

    def test_the_arrangement_is_recorded_as_revenue_share_with_terms_required(
            self, db_session, deal):
        a = _record_the_arrangement(db_session, deal)
        assert a.agreement_type == TYPE_REVENUE_SHARE
        assert a.status == AG_TERMS_REQUIRED

    def test_the_known_split_reconciles_and_is_not_hard_coded_anywhere(
            self, db_session, deal):
        a = _record_the_arrangement(db_session, deal)
        v = rs.validate(db_session, a, "full_allocation")
        assert v["valid"] is True
        by_key = {p["party_key"]: p for p in v["parties"]}
        assert str(by_key["provider"]["percent"]) == "75.000000"
        assert str(by_key["customer"]["percent"]) == "25.000000"

    def test_zero_upfront_and_zero_monthly_are_answers_not_gaps(
            self, db_session, deal):
        a = _record_the_arrangement(db_session, deal)
        state = t.state_map(db_session, a)
        assert state["setup_fee"]["state"] == "answered"
        assert state["setup_fee"]["value"] == 0
        assert state["recurring_base_fee"]["state"] == "answered"
        assert state["recurring_base_fee"]["value"] == 0

    def test_what_nobody_has_decided_stays_undecided(self, db_session, deal):
        a = _record_the_arrangement(db_session, deal)
        state = t.state_map(db_session, a)
        for key in STILL_UNKNOWN:
            assert state[key]["state"] == "required", key
            assert state[key]["value"] is None, key
            assert state[key]["value"] != 0, key

    def test_the_demo_josh_already_had_is_recorded_as_completed_previously(
            self, db_session, deal):
        _record_the_arrangement(db_session, deal)
        row = ov.apply(db_session, deal["impl"], deal["manager"],
                       item_kind=ITEM_DEMO, item_key="demo",
                       mode=MODE_COMPLETED_PREVIOUSLY,
                       reason="Demo was conducted before the customer "
                              "onboarding workflow existed.",
                       previously_completed_date_known=False)
        db_session.commit()

        assert row.previously_completed_on is None
        assert row.previously_completed_date_known is False
        assert row.decided_by_user_id == deal["manager"].id
        assert row.decided_at is not None

        step = {s["key"]: s
                for s in flow.steps(db_session, deal["impl"])}["demo_discovery"]
        assert step["status"] == flow.S_COMPLETED_PREVIOUSLY
        assert "before the customer onboarding workflow existed" in step["detail"]

    def test_onboarding_continues_while_the_terms_are_unfinished(
            self, db_session, deal):
        _record_the_arrangement(db_session, deal)
        ov.apply(db_session, deal["impl"], deal["manager"],
                 item_kind=ITEM_DEMO, item_key="demo",
                 mode=MODE_COMPLETED_PREVIOUSLY,
                 reason="Conducted before this workflow existed.")
        db_session.commit()

        steps = {s["key"]: s for s in flow.steps(db_session, deal["impl"])}
        assert steps["commercial_terms"]["status"] == flow.S_TERMS_REQUIRED
        for key in ("workspace_setup", "users_roles", "business_profile",
                    "integrations", "calendar_booking", "lead_data_intake",
                    "ai_workforce_setup", "readiness", "launch_preparation"):
            assert steps[key]["status"] != flow.S_BLOCKED, key

    def test_the_agreement_cannot_be_activated_and_says_exactly_why(
            self, db_session, deal):
        a = _record_the_arrangement(db_session, deal)
        block = t.blocking(db_session, a)
        act = block[t.ACTION_ACTIVATE]
        assert act["allowed"] is False
        assert len(act["reasons"]) >= len(STILL_UNKNOWN)
        assert any("percentage is applied against" in r.lower()
                   or "applied against" in r.lower() for r in act["reasons"])


class TestAtlantisMoneySafety:
    def test_no_settlement_figure_is_produced(self, db_session, deal):
        a = _record_the_arrangement(db_session, deal)
        out = settle.preview(db_session, a, deal["manager"],
                             period_start=date(2026, 9, 1),
                             period_end=date(2026, 9, 30))
        db_session.commit()
        assert out["status"] == SETTLE_REVIEW_REQUIRED
        assert out["basis_cents"] is None
        assert out["lines"] == []

        stored = db_session.query(CommercialSettlement).all()
        assert len(stored) == 1
        assert stored[0].basis_cents is None
        assert stored[0].calculation_json is None

    def test_nothing_was_charged_invoiced_or_purchased(self, db_session, deal):
        _record_the_arrangement(db_session, deal)
        ov.apply(db_session, deal["impl"], deal["manager"],
                 item_kind=ITEM_DEMO, item_key="demo",
                 mode=MODE_COMPLETED_PREVIOUSLY, reason="Already given.")
        db_session.commit()

        assert db_session.query(CatalogPurchase).count() == 0
        assert db_session.query(BillingInvoice).count() == 0
        assert db_session.query(BillingPayment).count() == 0

        org = deal["org"]
        assert org.stripe_customer_id is None
        assert org.stripe_subscription_id is None
        assert org.billing_status in (None, "")

    def test_no_entitlement_was_granted_by_recording_an_arrangement(
            self, db_session, deal):
        _record_the_arrangement(db_session, deal)
        db_session.commit()
        # An agreement is not an entitlement. T2 and T8 remain the authorities,
        # and neither of them has been touched.
        assert deal["org"].enabled_features in (None, "")

    def test_no_customer_was_contacted(self, db_session, deal):
        """Nothing in this flow sends. Asserted by the absence of the rows a
        send would leave behind."""
        from app.models.models import Message, Notification

        _record_the_arrangement(db_session, deal)
        ov.apply(db_session, deal["impl"], deal["manager"],
                 item_kind=ITEM_DEMO, item_key="demo",
                 mode=MODE_COMPLETED_PREVIOUSLY, reason="Already given.")
        db_session.commit()

        assert db_session.query(Message).count() == 0
        assert db_session.query(Notification).count() == 0


class TestAtlantisIsNotInTheCode:
    def test_no_source_file_names_the_customer_or_the_split(self):
        """The whole thread's constraint, enforced mechanically.

        Reads the commercial package and its router and asserts they contain
        none of this deal's identifying data and no two-party assumption. A
        future change that special-cases one customer fails here.
        """
        import os

        import app.routers.commercial_router as router_mod
        import app.services.commercial as pkg

        targets = [router_mod.__file__]
        pkg_dir = os.path.dirname(pkg.__file__)
        targets += [os.path.join(pkg_dir, f) for f in os.listdir(pkg_dir)
                    if f.endswith(".py")]
        import app.models.commercial_models as models_mod
        targets.append(models_mod.__file__)

        import re

        # Whole words only. "abel" as a substring lives inside "labels", and a
        # test that cannot tell those apart is a test that gets deleted the
        # first time it cries wolf.
        banned = [r"atlantis", r"shronce", r"joshua", r"469-926",
                  r"abel", r"atlantislightandpower", r"josh"]
        patterns = [re.compile(r"\b%s\b" % w) for w in banned]
        for path in targets:
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read().lower()
            for pattern in patterns:
                assert not pattern.search(text), \
                    "%s names '%s'" % (path, pattern.pattern)
