"""Inbound prospects are HELD, never dropped — and held means held.

═══════════════════════════════════════════════════════════════════════════
THE POLICY THIS ENFORCES
═══════════════════════════════════════════════════════════════════════════

The first lead ceiling refused every creation path equally, public webhooks
included. Defensible as billing, indefensible as product: a real family filled
in a form and the platform threw them away over a number on an invoice. The
customer never learns the prospect existed, and no upgrade brings them back.

So arrival is split by WHO INITIATED:

  USER-INITIATED - somebody clicked. They are present and can be told.
  REFUSED, with a structured PLAN_CAPACITY_REACHED naming resource, current
  and limit.

  EXTERNAL ARRIVAL - webhook, public form, integration, automation. Nobody is
  watching and the prospect is real. HELD.

═══════════════════════════════════════════════════════════════════════════
WHAT WOULD MAKE THIS DANGEROUS, AND IS THEREFORE TESTED HARDEST
═══════════════════════════════════════════════════════════════════════════

A held lead that could still be texted, mailed, called, enrolled in a cadence
or handed to the AI would be worse than dropping it: the customer would be
billed for outreach on a prospect their plan does not cover. Every one of
those gates is asserted individually, because this codebase enforces `dnc` by
repeated checks at each site rather than one chokepoint, and a capacity hold
that reached only some of them would be a hole shaped exactly like the one
this whole item exists to close.
"""

import itertools

import pytest

from app.models.billing_models import BrandBillingPlan
from app.models.models import Lead, Organization, Platform, User
from app.services import (cadence_service, compliance_service, lead_capacity,
                          plan_limits, qualification)
from app.services.auth_service import create_access_token, hash_password

_SEQ = itertools.count(20000)


def _platform(db):
    p = Platform(name="Alpha", slug="alpha-cap-%d" % next(_SEQ))
    db.add(p); db.commit()
    return p


def _plan(db, platform, key, *, max_leads=None, max_users=None):
    plan = BrandBillingPlan(
        platform_id=platform.id, key=key, name=key.title(),
        monthly_cents=49700, currency="usd", is_purchasable=True,
        is_active=True, sort_order=10,
        max_leads=max_leads, max_users=max_users)
    db.add(plan); db.commit()
    return plan


def _org(db, platform, plan_key):
    n = next(_SEQ)
    org = Organization(name="Cap %d" % n, slug="cap-%d" % n,
                       platform_id=platform.id, is_active=True,
                       plan=plan_key, billing_plan_key=plan_key,
                       billing_status="active")
    db.add(org); db.commit()
    return org


def _lead(db, org, **kw):
    kw.setdefault("first_name", "Held")
    kw.setdefault("last_name", "Prospect%d" % next(_SEQ))
    kw.setdefault("status", "new")
    lead = Lead(organization_id=org.id, **kw)
    db.add(lead); db.commit()
    return lead


def _user(db, org, role="org_admin"):
    u = User(organization_id=org.id,
             email="cap%d@evosyspro.live" % next(_SEQ),
             password_hash=hash_password("CapPass123!"),
             full_name="Cap User", role=role, must_change_password=False,
             is_active=True)
    db.add(u); db.commit()
    return u


def _headers(db, org):
    return {"Authorization": "Bearer " + create_access_token(_user(db, org), db)}


@pytest.fixture()
def brand(db_session):
    p = _platform(db_session)
    _plan(db_session, p, "tiny", max_leads=2, max_users=50)
    _plan(db_session, p, "big", max_leads=100, max_users=50)
    _plan(db_session, p, "uncapped", max_leads=None, max_users=50)
    return p


@pytest.fixture()
def full_org(db_session, brand):
    """An org sitting exactly on its 2-lead ceiling."""
    org = _org(db_session, brand, "tiny")
    _lead(db_session, org)
    _lead(db_session, org)
    assert plan_limits.usage_for(db_session, org, plan_limits.LIMIT_LEADS) == 2
    return org


# ═══════════════════════════════════════════════════════════════════════════
# THE PROSPECT IS KEPT
# ═══════════════════════════════════════════════════════════════════════════

def test_an_inbound_lead_over_capacity_is_persisted_not_discarded(
        db_session, full_org):
    lead = Lead(organization_id=full_org.id, first_name="Real",
                last_name="Family", phone="+12145550101",
                source="facebook", status="new")
    held = lead_capacity.hold_if_over_capacity(db_session, lead, full_org)
    db_session.add(lead)
    db_session.commit()

    assert held is True
    assert lead.id is not None, "the prospect was not written at all"
    found = db_session.query(Lead).filter(Lead.id == lead.id).first()
    assert found is not None, "THE PROSPECT WAS DROPPED. This is the failure "\
                              "the whole capacity-hold design exists to prevent."


def test_a_held_lead_retains_everything_it_arrived_with(db_session, full_org):
    """Source, timestamp, org, payload, dedupe keys, attribution."""
    lead = Lead(organization_id=full_org.id, first_name="Real",
                last_name="Family", phone="+12145550102",
                email="family@example.com", source="facebook",
                source_file="meta_leadgen", source_category="organic",
                import_list_name="Sept Campaign",
                custom_fields='{"campaign_id": "abc123"}',
                status="new")
    lead_capacity.hold_if_over_capacity(db_session, lead, full_org)
    db_session.add(lead); db_session.commit()

    got = db_session.query(Lead).filter(Lead.id == lead.id).first()
    assert got.source == "facebook"
    assert got.source_file == "meta_leadgen"
    assert got.source_category == "organic"
    assert got.import_list_name == "Sept Campaign"
    assert got.custom_fields == '{"campaign_id": "abc123"}'
    assert got.organization_id == full_org.id
    assert got.phone == "+12145550102"          # dedupe identifier
    assert got.email == "family@example.com"    # dedupe identifier
    assert got.created_at is not None           # received timestamp
    assert got.capacity_held_at is not None
    assert got.capacity_hold_reason == lead_capacity.REASON_MAX_LEADS


def test_the_hold_does_not_overwrite_the_leads_status(db_session, full_org):
    """`status` says what the lead IS. The hold says what the plan can do."""
    lead = Lead(organization_id=full_org.id, first_name="Booked",
                last_name="Caller", phone="+12145550103", status="booked")
    lead_capacity.hold_if_over_capacity(db_session, lead, full_org)
    db_session.add(lead); db_session.commit()

    assert lead.status == "booked", (
        "the hold overwrote status - every `status == ...` filter in the "
        "product would now be wrong about this lead")
    assert lead_capacity.is_held(lead)


def test_under_capacity_nothing_is_held(db_session, brand):
    org = _org(db_session, brand, "big")
    lead = Lead(organization_id=org.id, first_name="Fine", last_name="Lead",
                status="new")
    assert lead_capacity.hold_if_over_capacity(db_session, lead, org) is False
    assert lead.capacity_state is None


def test_an_unlimited_plan_never_holds(db_session, brand):
    org = _org(db_session, brand, "uncapped")
    for _ in range(5):
        _lead(db_session, org)
    lead = Lead(organization_id=org.id, first_name="Fine", last_name="Lead",
                status="new")
    assert lead_capacity.hold_if_over_capacity(db_session, lead, org) is False


# ═══════════════════════════════════════════════════════════════════════════
# HELD LEADS DO NOT CONSUME THE PLAN
# ═══════════════════════════════════════════════════════════════════════════

def test_a_held_lead_does_not_count_toward_usage(db_session, full_org):
    before = plan_limits.usage_for(db_session, full_org, plan_limits.LIMIT_LEADS)
    _lead(db_session, full_org, capacity_state=lead_capacity.OVER_CAPACITY)
    after = plan_limits.usage_for(db_session, full_org, plan_limits.LIMIT_LEADS)
    assert after == before, (
        "a held lead was counted as used. The customer would see "
        "'3 of 2 used' - a number they can neither act on nor reduce.")


def test_pre_existing_leads_with_null_capacity_state_still_count(
        db_session, full_org):
    """Every row that predates this feature has capacity_state NULL."""
    assert plan_limits.usage_for(db_session, full_org, plan_limits.LIMIT_LEADS) == 2


def test_held_leads_are_reported_separately_so_the_customer_can_see_them(
        db_session, full_org):
    _lead(db_session, full_org, capacity_state=lead_capacity.OVER_CAPACITY)
    _lead(db_session, full_org, capacity_state=lead_capacity.OVER_CAPACITY)

    report = plan_limits.report(db_session, full_org)
    assert report["capacity_hold"]["held"] == 2, (
        "held prospects are invisible. A held lead nobody is told about is "
        "barely better than a dropped one.")
    assert report["limits"][plan_limits.LIMIT_LEADS]["used"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# HELD MEANS HELD — EVERY PAID PATH, INDIVIDUALLY
# ═══════════════════════════════════════════════════════════════════════════

def test_qualification_excludes_a_held_lead(db_session, full_org):
    lead = _lead(db_session, full_org, phone="+12145550111",
                 email="held@example.com",
                 capacity_state=lead_capacity.OVER_CAPACITY)
    ctx = qualification.QualificationContext(
        db=db_session, leads=[lead], organization_id=full_org.id,
        rules=qualification.org_rules(db_session, full_org.id))
    decision = qualification.qualify_one(lead, qualification.CHANNEL_EMAIL, ctx)

    assert decision["bucket"] == qualification.EXCLUDED, (
        "a held lead reached %s. REVIEW_REQUIRED would be wrong here - no "
        "amount of human review creates plan capacity."
        % decision["bucket"])
    assert any(r.get("code") == "over_plan_capacity"
               for r in decision["reasons"]), decision["reasons"]


def test_the_compliance_preflight_blocks_a_held_lead_on_every_channel(
        db_session, full_org):
    lead = _lead(db_session, full_org, phone="+12145550112",
                 email="held@example.com", allow_email=True,
                 capacity_state=lead_capacity.OVER_CAPACITY)

    for channel in (compliance_service.CHANNEL_SMS,
                    compliance_service.CHANNEL_EMAIL):
        with pytest.raises(ValueError) as exc:
            compliance_service.check_compliance_preflight(
                db_session, lead, channel=channel)
        assert "capacity" in str(exc.value).lower(), str(exc.value)


def test_send_sms_refuses_a_held_lead(db_session, full_org):
    from app.services import sms_service
    advisor = _user(db_session, full_org, role="advisor")
    lead = _lead(db_session, full_org, phone="+12145550113",
                 capacity_state=lead_capacity.OVER_CAPACITY)

    with pytest.raises(ValueError) as exc:
        sms_service.send_sms(db_session, advisor, lead, "hello")
    assert "capacity" in str(exc.value).lower()


def test_send_batch_skips_held_leads(db_session, full_org):
    from app.services import sms_service
    advisor = _user(db_session, full_org, role="advisor")
    held = _lead(db_session, full_org, phone="+12145550114",
                 capacity_state=lead_capacity.OVER_CAPACITY)

    result = sms_service.send_batch(db_session, advisor, [held], "hello")
    assert result["sent_count"] == 0
    assert held.id in result["skipped_ids"]


def test_a_held_lead_is_not_enrolled_in_a_cadence(db_session, full_org):
    """A cadence is a standing commitment to spend nine more times."""
    lead = _lead(db_session, full_org, phone="+12145550115",
                 contact_channel="sms",
                 capacity_state=lead_capacity.OVER_CAPACITY)
    assert cadence_service.start_cadence(db_session, lead) is None


def test_a_held_lead_does_not_start_an_ai_conversation(db_session, full_org):
    from app.services import ai_conversation_service
    advisor = _user(db_session, full_org, role="advisor")
    lead = _lead(db_session, full_org, email="held@example.com",
                 capacity_state=lead_capacity.OVER_CAPACITY)

    result = ai_conversation_service.start_ai_conversation(
        db_session, lead, advisor)
    assert result["success"] is False
    assert "capacity" in result["error"].lower()


def test_a_held_lead_is_not_callable(db_session, full_org):
    from app.services import voice_orchestrator
    lead = _lead(db_session, full_org, phone="+12145550116",
                 capacity_state=lead_capacity.OVER_CAPACITY)

    elig = voice_orchestrator.check_call_eligibility(
        db_session, lead, full_org.id)
    assert elig.ok is False
    assert elig.code == "lead_over_capacity", elig.code


def test_an_unheld_lead_is_not_blocked_by_any_of_those_gates(
        db_session, brand):
    """The gates must not fire on ordinary leads."""
    org = _org(db_session, brand, "big")
    lead = _lead(db_session, org, phone="+12145550117",
                 email="fine@example.com", allow_email=True,
                 contact_channel="sms")

    assert lead_capacity.is_held(lead) is False
    compliance_service.check_compliance_preflight(
        db_session, lead, channel=compliance_service.CHANNEL_SMS)
    assert cadence_service.start_cadence(db_session, lead) is not None


# ═══════════════════════════════════════════════════════════════════════════
# USER-INITIATED IS REFUSED, WITH STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════

def test_user_initiated_creation_is_refused_with_a_structured_payload(
        db_session, full_org):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        lead_capacity.require_capacity_user_initiated(db_session, full_org)

    assert exc.value.status_code == 402
    detail = exc.value.detail
    assert detail["error"] == lead_capacity.PLAN_CAPACITY_REACHED
    assert detail["resource"] == "leads"
    assert detail["current"] == 2
    assert detail["limit"] == 2


def test_the_manual_create_endpoint_returns_that_payload(
        client, db_session, full_org):
    headers = _headers(db_session, full_org)
    response = client.post("/leads/create", json={
        "first_name": "Manual", "last_name": "Entry", "phone": "5125550199",
    }, headers=headers)

    assert response.status_code == 402, response.text
    body = response.json()["detail"]
    assert body["error"] == "PLAN_CAPACITY_REACHED"
    assert body["resource"] == "leads"
    assert body["limit"] == 2


def test_a_public_inbound_door_does_not_refuse(client, db_session, full_org):
    """The same org, the same full plan, the other kind of arrival."""
    before = db_session.query(Lead).filter(
        Lead.organization_id == full_org.id).count()

    response = client.post("/leads/sms-optin", json={
        "org_id": full_org.id,
        "first_name": "Inbound",
        "last_name": "Prospect",
        "phone": "5125550200",
        "source": "optin_page",
    })

    # Whatever this endpoint answers about routing, it must not have thrown
    # the prospect away for capacity.
    assert response.status_code != 402, (
        "a public inbound door refused a prospect over plan capacity")


# ═══════════════════════════════════════════════════════════════════════════
# RELEASE
# ═══════════════════════════════════════════════════════════════════════════

def test_release_frees_held_leads_oldest_first_up_to_the_headroom(
        db_session, brand):
    from datetime import datetime, timedelta
    org = _org(db_session, brand, "tiny")          # max_leads = 2
    base = datetime(2026, 9, 1, 12, 0, 0)

    order = []
    for i in range(4):
        lead = _lead(db_session, org,
                     capacity_state=lead_capacity.OVER_CAPACITY,
                     capacity_held_at=base + timedelta(hours=i))
        order.append(lead.id)
    db_session.commit()

    # Nothing counts yet, so all 2 slots are headroom.
    result = lead_capacity.release_available(db_session, org)
    db_session.commit()

    assert result["released"] == 2, result
    assert result["still_held"] == 2

    released = {l.id for l in db_session.query(Lead).filter(
        Lead.organization_id == org.id, Lead.capacity_state.is_(None)).all()}
    assert released == set(order[:2]), (
        "release did not take the oldest first - the family who enquired in "
        "March must not stay held while April's are let through")


def test_release_does_not_start_any_outreach(db_session, brand):
    """Release is release. It is not a send trigger."""
    from app.models.models import CadenceState
    org = _org(db_session, brand, "big")
    lead = _lead(db_session, org, phone="+12145550120",
                 contact_channel="sms",
                 capacity_state=lead_capacity.OVER_CAPACITY)

    lead_capacity.release_available(db_session, org)
    db_session.commit()

    assert lead.capacity_state is None
    assert lead.capacity_released_at is not None
    assert db_session.query(CadenceState).filter(
        CadenceState.lead_id == lead.id).first() is None, (
        "release enrolled a lead in a cadence. Releasing five thousand held "
        "leads straight into outbound would spend the customer's money on a "
        "decision they never made.")


def test_release_with_no_headroom_releases_nothing(db_session, full_org):
    _lead(db_session, full_org, capacity_state=lead_capacity.OVER_CAPACITY)
    result = lead_capacity.release_available(db_session, full_org)
    assert result["released"] == 0
    assert result["still_held"] == 1


def test_an_upgrade_to_unlimited_releases_everything(db_session, brand):
    org = _org(db_session, brand, "tiny")
    for _ in range(5):
        _lead(db_session, org, capacity_state=lead_capacity.OVER_CAPACITY)

    org.billing_plan_key = "uncapped"
    org.plan = "uncapped"
    db_session.commit()

    result = lead_capacity.release_available(db_session, org)
    db_session.commit()
    assert result["released"] == 5
    assert result["still_held"] == 0


def test_release_never_deletes_a_lead_to_make_room(db_session, full_org):
    before = db_session.query(Lead).filter(
        Lead.organization_id == full_org.id).count()
    _lead(db_session, full_org, capacity_state=lead_capacity.OVER_CAPACITY)
    lead_capacity.release_available(db_session, full_org)
    db_session.commit()
    after = db_session.query(Lead).filter(
        Lead.organization_id == full_org.id).count()
    assert after == before + 1, "release removed a lead"
