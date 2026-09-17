"""SS7 — the six dimensions, and the proof that one column cannot hold them.

THE CENTRAL TEST IN THIS FILE is the one about a family who booked an
appointment and then asked not to be contacted. `Lead.status` can say `booked`
or it can say `dnc`. It cannot say both, so whichever writer runs second
destroys the other - and one of those two errors ends with a phone call to
somebody who asked us to stop.

That is not a vocabulary problem. No better set of eleven words fixes it. It
is a shape problem, and everything else here follows from it.

READ-ONLY, like lead_stage: writes nothing, stores nothing, adds no column,
and changes none of the 77 write sites on Lead.status.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (BookingLink, CadenceState, Lead, LeadOutcome,
                               PipelineConversation, Reply, ReplyClassification)
from app.services import lead_lifecycle as ll

NOW = datetime(2026, 9, 17, 12, 0)


def _lead(db, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=kw.pop("first_name", "Life"), last_name="Cycle",
                phone=kw.pop("phone", "12145556001"),
                email=kw.pop("email", "life@example.com"), **kw)
    db.add(lead); db.commit(); return lead


def dims(db, lead):
    return ll.for_lead(db, lead, now=NOW)


# ── the read model writes nothing ───────────────────────────────────────────

def test_the_module_writes_nothing():
    import ast, inspect
    tree = ast.parse(inspect.getsource(ll))
    offenders = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        recv = getattr(n.func.value, "id", None) or getattr(n.func.value, "attr", None)
        if recv in ("db", "session") and n.func.attr in (
                "commit", "flush", "add", "add_all", "delete", "merge", "execute"):
            offenders.append(n.func.attr)
    assert not offenders, "lead_lifecycle writes: %s" % offenders


def test_it_does_not_reimplement_qualification():
    """The one dimension it must not answer for itself."""
    import ast, inspect
    tree = ast.parse(inspect.getsource(ll))
    called = {getattr(n.func, "attr", None)
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "qualify_leads" in called
    for reimplemented in ("is_phone_suppressed", "load_suppressed_phones",
                          "qualify_one", "check_compliance_preflight"):
        assert reimplemented not in called


# ── THE CENTRAL CASE ────────────────────────────────────────────────────────

def test_a_family_can_be_booked_and_opted_out_at_the_same_time(
        db_session, sample_org, sample_advisor):
    """Both are true. The stored column can only say one."""
    lead = _lead(db_session, sample_org, sample_advisor, status="dnc")
    db_session.add(BookingLink(token="ll-1", lead_id=lead.id,
                               user_id=sample_advisor.id, status="booked",
                               booked_time=NOW + timedelta(days=3)))
    db_session.commit()

    row = dims(db_session, lead)
    assert row["system_state"] == ll.SYS_SUPPRESSED
    assert row["appointment_state"] == ll.APPT_BOOKED
    assert row["outcome"] == ll.OUT_OPTED_OUT
    # And the compatibility layer is honest about the cost.
    assert row["legacy_equivalent"] == "dnc"
    assert any(i.startswith("appointment_state=") for i in row["information_lost"])


def test_suppression_wins_the_single_column_because_of_who_pays_for_the_error(
        db_session, sample_org, sample_advisor):
    """Of the two ways to be wrong here, one ends in a phone call to somebody
    who asked us to stop."""
    assert ll.legacy_for_dimensions({
        "system_state": ll.SYS_SUPPRESSED,
        "appointment_state": ll.APPT_BOOKED,
        "sales_stage": ll.SALE_APPOINTMENT}) == "dnc"


# ── each dimension moves independently ──────────────────────────────────────

def test_a_new_lead_is_new_on_every_axis(db_session, sample_org, sample_advisor):
    row = dims(db_session, _lead(db_session, sample_org, sample_advisor,
                                 status="new"))
    assert row["system_state"] == ll.SYS_ACTIVE
    assert row["sales_stage"] == ll.SALE_NEW
    assert row["communication_state"] == ll.COMM_IDLE
    assert row["appointment_state"] == ll.APPT_NONE
    assert row["outcome"] == ll.OUT_OPEN


def test_an_appointment_does_not_stop_the_sequence_dimension_from_speaking(
        db_session, sample_org, sample_advisor):
    """`booked` in the old column erased whatever the cadence was doing."""
    lead = _lead(db_session, sample_org, sample_advisor, status="booked")
    db_session.add(BookingLink(token="ll-2", lead_id=lead.id,
                               user_id=sample_advisor.id, status="booked",
                               booked_time=NOW + timedelta(days=2)))
    db_session.add(CadenceState(lead_id=lead.id, status="active",
                                current_touch_number=3,
                                cadence_started_at=NOW - timedelta(days=5),
                                next_touch_due_at=NOW + timedelta(hours=2)))
    db_session.commit()
    row = dims(db_session, lead)
    assert row["appointment_state"] == ll.APPT_BOOKED
    assert row["communication_state"] == ll.COMM_SCHEDULED
    assert row["sales_stage"] == ll.SALE_APPOINTMENT


def test_a_duplicate_is_a_system_fact_not_a_sales_stage(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, status="hot",
                 is_duplicate=True)
    row = dims(db_session, lead)
    assert row["system_state"] == ll.SYS_DUPLICATE
    # The sales history is still readable. The old model could not say both.
    assert row["sales_stage"] == ll.SALE_ENGAGED


def test_a_test_record_is_not_a_person(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, status="new",
                 is_test=True)
    assert dims(db_session, lead)["system_state"] == ll.SYS_TEST


def test_a_held_lead_is_held_not_dead(db_session, sample_org, sample_advisor):
    from app.services import lead_capacity
    lead = _lead(db_session, sample_org, sample_advisor, status="new",
                 capacity_state=lead_capacity.OVER_CAPACITY)
    row = dims(db_session, lead)
    assert row["system_state"] == ll.SYS_HELD
    assert row["outcome"] == ll.OUT_OPEN, "a plan ceiling is not an outcome"


# ── the North Star, in order ────────────────────────────────────────────────

def test_the_sales_stage_walks_the_north_star(db_session, sample_org,
                                              sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, status="new")
    assert dims(db_session, lead)["sales_stage"] == ll.SALE_NEW

    lead.last_messaged_at = NOW - timedelta(days=1)          # CONTACT
    db_session.commit()
    assert dims(db_session, lead)["sales_stage"] == ll.SALE_CONTACTED

    db_session.add(CadenceState(lead_id=lead.id, status="active",
                                current_touch_number=1,
                                cadence_started_at=NOW - timedelta(days=2),
                                next_touch_due_at=NOW + timedelta(hours=1)))
    db_session.commit()                                      # FOLLOW-UP
    assert dims(db_session, lead)["sales_stage"] == ll.SALE_FOLLOW_UP

    db_session.add(Reply(lead_id=lead.id, body="yes",         # CONVERSATION
                         classification=ReplyClassification.INTERESTED,
                         received_at=NOW - timedelta(hours=2)))
    db_session.commit()
    assert dims(db_session, lead)["sales_stage"] == ll.SALE_ENGAGED

    db_session.add(BookingLink(token="ll-3", lead_id=lead.id,  # APPOINTMENT
                               user_id=sample_advisor.id, status="booked",
                               booked_time=NOW + timedelta(days=1)))
    db_session.commit()
    assert dims(db_session, lead)["sales_stage"] == ll.SALE_APPOINTMENT

    db_session.add(LeadOutcome(lead_id=lead.id,               # OUTCOME
                               recorded_by_id=sample_advisor.id,
                               resulted_in_sale=True))
    db_session.commit()
    row = dims(db_session, lead)
    assert row["sales_stage"] == ll.SALE_CLOSED
    assert row["outcome"] == ll.OUT_SALE
    assert row["appointment_state"] == ll.APPT_RECORDED


def test_a_recorded_non_sale_is_no_sale_and_an_open_lead_is_not(
        db_session, sample_org, sample_advisor):
    """OPEN IS NOT NO_SALE. Collapsing them is how a pipeline report turns
    every lead nobody has finished with into a loss."""
    open_lead = _lead(db_session, sample_org, sample_advisor, status="sent",
                      first_name="Open")
    assert dims(db_session, open_lead)["outcome"] == ll.OUT_OPEN

    lost = _lead(db_session, sample_org, sample_advisor, status="booked",
                 first_name="Lost", phone="12145556002", email="l@example.com")
    db_session.add(LeadOutcome(lead_id=lost.id,
                               recorded_by_id=sample_advisor.id,
                               resulted_in_sale=False))
    db_session.commit()
    assert dims(db_session, lost)["outcome"] == ll.OUT_NO_SALE


def test_an_exhausted_sequence_is_unreachable_not_a_loss(
        db_session, sample_org, sample_advisor):
    """`cold` means nobody ever answered. That is a different fact from a
    family who heard the offer and said no, and merging them loses the only
    number that says whether outreach is working."""
    lead = _lead(db_session, sample_org, sample_advisor, status="cold",
                 last_messaged_at=NOW - timedelta(days=10))
    row = dims(db_session, lead)
    assert row["outcome"] == ll.OUT_UNREACHABLE
    assert row["communication_state"] == ll.COMM_STOPPED


# ── compatibility, both directions ──────────────────────────────────────────

@pytest.mark.parametrize("legacy", sorted(ll.LEGACY_TO_DIMENSIONS))
def test_every_legacy_value_is_mapped(legacy):
    mapped = ll.dimensions_for_legacy(legacy)
    assert isinstance(mapped, dict)
    for key in mapped:
        assert key in ll.DIMENSIONS, "%s maps to an unknown dimension" % legacy


def test_the_mapping_covers_every_value_the_enum_declares():
    from app.models.models import LeadStatus
    declared = {m.value for m in LeadStatus}
    assert declared <= set(ll.LEGACY_TO_DIMENSIONS), (
        "unmapped: %s" % sorted(declared - set(ll.LEGACY_TO_DIMENSIONS)))


def test_a_round_trip_through_the_legacy_column_is_lossy_and_says_so():
    """The migration argument in two lines."""
    rich = {"system_state": ll.SYS_ACTIVE,
            "sales_stage": ll.SALE_APPOINTMENT,
            "communication_state": ll.COMM_SCHEDULED,
            "appointment_state": ll.APPT_CONFIRMED,
            "outcome": ll.OUT_OPEN}
    legacy = ll.legacy_for_dimensions(rich)
    assert legacy == "booked"
    back = ll.dimensions_for_legacy(legacy)
    assert back.get("communication_state") != ll.COMM_SCHEDULED
    assert ll.information_lost(rich)


def test_needs_tier_review_is_none_of_the_six():
    """It is a workflow flag wearing a status. Mapping it to a sales stage
    would be inventing a meaning it never had."""
    assert ll.dimensions_for_legacy("needs_tier_review") == {}


# ── the number the decision should be made from ─────────────────────────────

def test_migration_readiness_counts_real_rows_and_writes_nothing(
        db_session, sample_org, sample_advisor):
    clean = _lead(db_session, sample_org, sample_advisor, status="new",
                  first_name="Clean")
    conflicted = _lead(db_session, sample_org, sample_advisor, status="dnc",
                       first_name="Conflicted", phone="12145556003",
                       email="c@example.com")
    db_session.add(BookingLink(token="ll-4", lead_id=conflicted.id,
                               user_id=sample_advisor.id, status="booked",
                               booked_time=NOW + timedelta(days=4)))
    db_session.commit()

    report = ll.migration_readiness(db_session, [clean, conflicted], now=NOW)
    assert report["read_only"] is True
    assert report["leads"] == 2
    assert report["cannot_be_expressed_in_one_column"] == 1
    assert "appointment_state" in report["lost_by_dimension"]
    assert not db_session.new and not db_session.dirty


def test_the_batch_and_the_single_lead_agree(db_session, sample_org,
                                             sample_advisor):
    leads = [_lead(db_session, sample_org, sample_advisor, status=s,
                   first_name="B%d" % i, phone="121455561%02d" % i,
                   email="b%d@example.com" % i)
             for i, s in enumerate(["new", "sent", "booked", "cold", "dnc"])]
    batch = ll.for_leads(db_session, leads, now=NOW)
    for lead in leads:
        assert batch[lead.id] == dims(db_session, lead)
