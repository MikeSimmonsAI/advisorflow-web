"""DEFECT 2 — THE PLAN CARD SAYS WHAT THE CONFIGURATION SAYS.

WHAT WAS WRONG
--------------
The customer Change Plan screen rendered `features_json` — a list of
sentences somebody typed into `evosys_billing_seed`:

    Starter       "Up to 2 users"                 settled at 1
    Growth        "AI email + SMS 1,000/mo"       settled at 1,500 SMS
    Growth        "AI voice 300 min/mo"           AI Voice is an ADD-ON now
    Professional  "AI voice 750 min/mo"           likewise
    Professional  "Priority support + 24-month
                   price lock"                    no term was ever configured
    Professional  max_leads 7,500                 settled at 10,000

Every one was wrong on a live screen, and none could be corrected without a
deploy. The card was a marketing artefact pretending to be configuration.

WHAT THESE TESTS DEFEND
-----------------------
  1. Capacity comes from the plan's own COLUMNS, so a God Mode edit changes
     the customer's card with no deploy.
  2. An unconfigured dimension is ABSENT — never zero, never invented.
  3. The commitment label carries the plan's own term, and reads the neutral
     "Term agreement" when nobody has configured one. No "24-month" anywhere.
  4. No voice allowance is a plan dimension at all, so the stale line cannot
     grow back.
  5. Support entitlement on the card is the SAME fact the support engine
     enforces.
  6. The settled EvoSys capacity is what the seed actually writes.
"""

import itertools
import json

import pytest

from app.models.billing_models import BillingCommitment, BrandBillingPlan
from app.models.models import Organization, Platform, User
from app.services import billing_catalog, evosys_billing_seed
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(1)


def _platform(db, name="EvoSys Pro", slug="evosyspro"):
    p = Platform(name=name, slug="%s-%d" % (slug, next(_SEQ)),
                 domain="app.evosyspro.live", is_active=True)
    db.add(p)
    db.commit()
    return p


def _org(db, platform, plan_key="starter"):
    n = next(_SEQ)
    org = Organization(name="Customer %d" % n, slug="customer-%d" % n,
                       plan=plan_key, billing_plan_key=plan_key,
                       platform_id=platform.id, is_active=True)
    db.add(org)
    db.commit()
    return org


def _admin_headers(db, org):
    admin = User(organization_id=org.id, email="admin%d@example.com" % next(_SEQ),
                 password_hash=hash_password("AdminPass123!"),
                 full_name="Org Admin", role="org_admin",
                 must_change_password=False)
    db.add(admin)
    db.commit()
    return {"Authorization": "Bearer " + create_access_token(admin, db)}


def _plan(db, platform, key, **kw):
    kw.setdefault("name", key.title())
    kw.setdefault("currency", "usd")
    kw.setdefault("is_purchasable", True)
    kw.setdefault("is_active", True)
    kw.setdefault("sort_order", 10)
    plan = BrandBillingPlan(platform_id=platform.id, key=key, **kw)
    db.add(plan)
    db.commit()
    return plan


# ═══════════════════════════════════════════════════════════════════════════
# 1. CAPACITY IS DATA
# ═══════════════════════════════════════════════════════════════════════════

def test_capacity_comes_from_the_columns(db_session):
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "growth", monthly_cents=100000,
                 max_users=3, max_leads=5000,
                 email_monthly_allowance=5000, sms_monthly_allowance=1500)

    got = {c["key"]: c for c in billing_catalog.capacity_for(plan)}
    assert got["max_users"]["value"] == 3
    assert got["max_leads"]["value"] == 5000
    assert got["email_monthly_allowance"]["value"] == 5000
    assert got["email_monthly_allowance"]["unit"] == "per month"
    assert got["sms_monthly_allowance"]["value"] == 1500
    # Nobody configured locations on this tier, so nothing is claimed.
    assert "max_locations" not in got


def test_an_unconfigured_allowance_is_absent_not_zero(db_session):
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "starter", monthly_cents=50000,
                 max_users=1, max_leads=2500,
                 email_monthly_allowance=None, sms_monthly_allowance=None)
    keys = [c["key"] for c in billing_catalog.capacity_for(plan)]
    assert "email_monthly_allowance" not in keys
    assert "sms_monthly_allowance" not in keys
    # And the enforced dimensions are still stated.
    assert "max_users" in keys and "max_leads" in keys


def test_a_null_on_an_enforced_dimension_reads_as_unlimited(db_session):
    """Matching `plan_limits.limit_for`, which refuses nothing at NULL."""
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "custom", monthly_cents=None,
                 is_purchasable=False, max_users=None, max_leads=None)
    got = {c["key"]: c for c in billing_catalog.capacity_for(plan)}
    assert got["max_users"]["unlimited"] is True
    assert got["max_users"]["value"] is None


def test_there_is_no_voice_dimension_at_all(db_session):
    """THE STALE LINE CANNOT GROW BACK. AI Voice is a catalogue add-on, so
    there is no plan column for it and nothing to advertise by accident."""
    keys = [d["key"] for d in billing_catalog.CAPACITY_DIMENSIONS]
    assert not any("voice" in k for k in keys)
    assert not hasattr(BrandBillingPlan, "voice_minutes_monthly_allowance")


def test_a_god_edit_changes_the_customer_card_with_no_deploy(
        client, db_session):
    """THE WHOLE POINT OF DEFECT 2, END TO END."""
    brand = _platform(db_session)
    plan = _plan(db_session, brand, "starter", monthly_cents=50000,
                 month_to_month_cents=59700, max_users=1, max_leads=2500,
                 email_monthly_allowance=2500, sms_monthly_allowance=500)
    org = _org(db_session, brand, "starter")
    headers = _admin_headers(db_session, org)

    first = client.get("/billing/plans", headers=headers).json()
    starter = [p for p in first["plans"] if p["key"] == "starter"][0]
    sms = [c for c in starter["capacity"] if c["key"] == "sms_monthly_allowance"][0]
    assert sms["value"] == 500

    # God Mode changes the configuration. No code, no deploy.
    plan.sms_monthly_allowance = 900
    db_session.commit()

    second = client.get("/billing/plans", headers=headers).json()
    starter = [p for p in second["plans"] if p["key"] == "starter"][0]
    sms = [c for c in starter["capacity"] if c["key"] == "sms_monthly_allowance"][0]
    assert sms["value"] == 900


# ═══════════════════════════════════════════════════════════════════════════
# 2. NO STALE COMMERCIAL TEXT
# ═══════════════════════════════════════════════════════════════════════════

def test_an_unconfigured_term_reads_as_a_term_agreement_not_24_months():
    assert billing_catalog.commitment_label(BillingCommitment.TERM) \
        == "Term agreement"
    assert billing_catalog.commitment_label(BillingCommitment.TERM, None) \
        == "Term agreement"


def test_a_configured_term_reads_the_configured_number():
    assert billing_catalog.commitment_label(BillingCommitment.TERM, 12) \
        == "12-month agreement"


def test_the_payload_carries_no_stale_commercial_text(client, db_session):
    """A SWEEP OF THE WHOLE CUSTOMER-FACING PAYLOAD.

    Every string the Change Plan screen can render is searched for the
    phrases that were live and wrong.
    """
    brand = _platform(db_session)
    _plan(db_session, brand, "professional", monthly_cents=200000,
          month_to_month_cents=259700, max_users=5, max_leads=10000,
          email_monthly_allowance=10000, sms_monthly_allowance=3000,
          sort_order=30)
    org = _org(db_session, brand, "professional")
    headers = _admin_headers(db_session, org)

    blob = json.dumps(client.get("/billing/plans", headers=headers).json()).lower()
    for stale in ("24-month", "24 month", "price lock", "ai voice",
                  "voice 300", "voice 750", "month 13"):
        assert stale not in blob, "stale commercial text still served: %r" % stale

    blob2 = json.dumps(
        client.get("/billing/subscription", headers=headers).json()).lower()
    for stale in ("24-month", "24 month", "price lock", "ai voice"):
        assert stale not in blob2


def test_the_seed_no_longer_ships_marketing_sentences():
    """`features` is empty on every tier. Capacity is columns now."""
    for spec in evosys_billing_seed.EVOSYS_PLANS:
        assert spec["features"] == [], \
            "%s still ships prose capacity" % spec["key"]


def test_the_seed_carries_the_settled_capacity():
    """THE SETTLED NUMBERS, ASSERTED AS NUMBERS."""
    by_key = {s["key"]: s for s in evosys_billing_seed.EVOSYS_PLANS}

    assert by_key["starter"]["max_users"] == 1
    assert by_key["starter"]["max_leads"] == 2500
    assert by_key["starter"]["email_monthly_allowance"] == 2500
    assert by_key["starter"]["sms_monthly_allowance"] == 500

    assert by_key["growth"]["max_users"] == 3
    assert by_key["growth"]["max_leads"] == 5000
    assert by_key["growth"]["email_monthly_allowance"] == 5000
    assert by_key["growth"]["sms_monthly_allowance"] == 1500

    assert by_key["professional"]["max_users"] == 5
    assert by_key["professional"]["max_leads"] == 10000
    assert by_key["professional"]["email_monthly_allowance"] == 10000
    assert by_key["professional"]["sms_monthly_allowance"] == 3000

    # Custom is quoted. Nothing is stated for it.
    custom = by_key["enterprise"]
    assert custom["name"] == "Custom"
    assert custom["is_purchasable"] is False
    assert custom["monthly_cents"] is None
    assert custom["max_leads"] is None


def test_the_seed_does_not_invent_a_term_or_a_voice_allowance():
    """NOT DECIDED MEANS NOT WRITTEN."""
    for spec in evosys_billing_seed.EVOSYS_PLANS:
        assert "term_months" not in spec
        assert not any("voice" in k for k in spec)


def test_the_seed_applies_the_settled_capacity_to_the_database(db_session):
    brand = _platform(db_session)
    # A row carrying the OLD, stale configuration.
    _plan(db_session, brand, "professional", monthly_cents=200000,
          max_users=5, max_leads=7500,
          features_json=json.dumps(["AI voice 750 min/mo",
                                    "Priority support + 24-month price lock"]))

    evosys_billing_seed.seed(db_session, brand.id, apply=True)

    row = (db_session.query(BrandBillingPlan)
           .filter(BrandBillingPlan.platform_id == brand.id,
                   BrandBillingPlan.key == "professional").one())
    assert row.max_leads == 10000
    assert row.sms_monthly_allowance == 3000
    assert row.email_monthly_allowance == 10000
    assert json.loads(row.features_json) == []
    assert row.term_months is None


# ═══════════════════════════════════════════════════════════════════════════
# 3. SUPPORT ENTITLEMENT IS THE SUPPORT PRODUCT'S OWN FACT
# ═══════════════════════════════════════════════════════════════════════════

def test_the_card_states_the_support_the_engine_actually_gives(
        client, db_session):
    brand = _platform(db_session)
    _plan(db_session, brand, "growth", monthly_cents=100000,
          month_to_month_cents=129700, max_users=3, max_leads=5000)
    org = _org(db_session, brand, "growth")
    headers = _admin_headers(db_session, org)

    payload = client.get("/billing/plans", headers=headers).json()
    growth = [p for p in payload["plans"] if p["key"] == "growth"][0]
    support = growth["support"]
    assert support is not None

    from app.services import support_entitlements
    rule = support_entitlements._frozen_rule("growth")
    from app.models.support_models import Queue
    assert support["queue_label"] == Queue.LABELS[rule["queue"]]
    assert support["included_assistance_minutes"] == \
        rule["included_assistance_minutes"]
    # A brand that has configured nothing is marked as running on the default
    # rather than presenting an assumption as a promise.
    assert support["configured"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 4. THE CURRENT PLAN AND A SCHEDULED CHANGE ARE BOTH VISIBLE
# ═══════════════════════════════════════════════════════════════════════════

def test_current_plan_commitment_and_scheduled_change_are_all_reported(
        client, db_session):
    brand = _platform(db_session)
    _plan(db_session, brand, "growth", monthly_cents=100000,
          month_to_month_cents=129700, max_users=3, max_leads=5000,
          sort_order=20)
    _plan(db_session, brand, "starter", monthly_cents=50000,
          month_to_month_cents=59700, max_users=1, max_leads=2500,
          sort_order=10)
    org = _org(db_session, brand, "growth")
    org.stripe_subscription_id = "sub_live"
    org.billing_status = "active"
    org.billing_commitment = BillingCommitment.MONTH_TO_MONTH
    org.billing_pending_plan_key = "starter"
    db_session.commit()

    sub = client.get("/billing/subscription",
                     headers=_admin_headers(db_session, org)).json()

    assert sub["plan"] == "growth"
    assert sub["billing_commitment"] == BillingCommitment.MONTH_TO_MONTH
    assert sub["commitment_label"] == "Month-to-month"
    # THE RATE THEY ACTUALLY PAY, not the discounted term rate.
    assert sub["recurring_cents"] == 129700
    assert sub["pending_plan"] == "starter"
    assert sub["plan_details"]["key"] == "growth"
    assert sub["plan_details"]["capacity"]


def test_both_rates_are_offered_and_neither_substitutes_the_other(
        client, db_session):
    brand = _platform(db_session)
    # A tier sold ONLY on a term: no month-to-month price at all.
    _plan(db_session, brand, "termonly", monthly_cents=80000,
          month_to_month_cents=None, max_users=2, max_leads=3000)
    org = _org(db_session, brand, "termonly")

    payload = client.get("/billing/plans",
                         headers=_admin_headers(db_session, org)).json()
    plan = [p for p in payload["plans"] if p["key"] == "termonly"][0]
    keys = {c["key"] for c in plan["commitments"]}
    assert keys == {BillingCommitment.TERM}
    assert plan["commitments"][0]["label"] == "Term agreement"
