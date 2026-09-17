"""SS7 - the derived lead stage, and the one definition of "needs a person".

TWO THINGS ARE UNDER TEST HERE, AND THE FIRST IS THE IMPORTANT ONE.

`lead_stage` is a READ MODEL. The test that matters most is not any single
classification - it is that this module writes nothing, ever. A derived view
that quietly repairs what it reads is a migration wearing a disguise, and it
would be running against production rows on every page load. So there is an
AST assertion that the module contains no commit, no flush and no add, and a
behavioural one that a full pass over a book of leads leaves the session with
nothing to write.

The second is that "this reply needs a person" now has ONE definition. It had
four. Two of them ignored reviewed_at, so a rep marked a reply handled and it
came straight back - in the inbox, and in the number on the Overview page.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (BookingLink, CadenceState, Lead, LeadOutcome,
                               LeadStatus, PipelineConversation, Reply,
                               ReplyClassification)
from app.services import lead_stage
from app.services import reply_classification_service as rcs

NOW = datetime(2026, 9, 17, 12, 0)


def mklead(db, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=kw.pop("first_name", "Test"), last_name="Lead",
                phone=kw.pop("phone", "12145550000"),
                email=kw.pop("email", "t@example.com"), **kw)
    db.add(lead)
    db.commit()
    return lead


def stage_of(db, lead):
    return lead_stage.for_lead(db, lead, now=NOW)["stage"]


# ── the read model writes nothing ───────────────────────────────────────────

def test_the_module_contains_no_write_call_at_all():
    import ast, inspect
    tree = ast.parse(inspect.getsource(lead_stage))
    # Only calls ON THE SESSION count. `set.add` is not `db.add`, and a check
    # that cannot tell them apart would either fail on a set or, worse, be
    # loosened until it stopped catching the real thing.
    forbidden = {"commit", "flush", "add", "add_all", "delete", "merge",
                 "bulk_save_objects", "execute"}
    offenders = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        recv = n.func.value
        name = getattr(recv, "id", None) or getattr(recv, "attr", None)
        if name in ("db", "session", "db_session") and n.func.attr in forbidden:
            offenders.append("%s.%s" % (name, n.func.attr))
    assert not offenders, "lead_stage writes: %s" % offenders


def test_classifying_a_whole_book_leaves_the_session_clean(db_session, sample_org, sample_advisor):
    leads = [mklead(db_session, sample_org, sample_advisor,
                    status=s, first_name="L%d" % i)
             for i, s in enumerate(["new", "sent", "booked", "cold", "dnc"])]
    db_session.expunge_all()
    leads = db_session.query(Lead).all()
    lead_stage.for_leads(db_session, leads, now=NOW)
    assert not db_session.new and not db_session.dirty and not db_session.deleted


# ── precedence ──────────────────────────────────────────────────────────────

def test_a_never_contacted_lead_is_new(db_session, sample_org, sample_advisor):
    assert stage_of(db_session, mklead(db_session, sample_org, sample_advisor,
                                       status=LeadStatus.NEW)) == lead_stage.NEW


def test_a_duplicate_is_unworkable_whatever_its_status_says(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor,
                  status="hot", is_duplicate=True)
    assert stage_of(db_session, lead) == lead_stage.UNWORKABLE


def test_dnc_is_unworkable(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="dnc")
    assert stage_of(db_session, lead) == lead_stage.UNWORKABLE


def test_not_interested_is_closed_and_is_not_dnc(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="not_interested")
    row = lead_stage.for_lead(db_session, lead, now=NOW)
    assert row["stage"] == lead_stage.CLOSED
    # The distinction tool_impls insists on, carried through to the read model.
    assert "opt-out" in row["reason"].lower()


def test_an_unreviewed_reply_outranks_a_booked_appointment(db_session, sample_org, sample_advisor):
    """A person is waiting. The appointment is not going anywhere."""
    lead = mklead(db_session, sample_org, sample_advisor, status="booked")
    db_session.add(BookingLink(token="t1", lead_id=lead.id, user_id=sample_advisor.id,
                               status="booked", booked_time=NOW + timedelta(days=3)))
    db_session.add(Reply(lead_id=lead.id, body="yes please",
                         classification=ReplyClassification.INTERESTED,
                         received_at=NOW - timedelta(hours=2)))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.NEEDS_REPLY


def test_a_reviewed_reply_does_not_hold_the_lead_in_needs_reply(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="booked")
    db_session.add(BookingLink(token="t2", lead_id=lead.id, user_id=sample_advisor.id,
                               status="booked", booked_time=NOW + timedelta(days=3)))
    db_session.add(Reply(lead_id=lead.id, body="yes please",
                         classification=ReplyClassification.INTERESTED,
                         received_at=NOW - timedelta(hours=2),
                         reviewed_at=NOW - timedelta(hours=1)))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.BOOKED


def test_a_neutral_reply_is_not_work(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=1))
    db_session.add(Reply(lead_id=lead.id, body="ok",
                         classification=ReplyClassification.NEUTRAL,
                         received_at=NOW - timedelta(hours=2)))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.AWAITING_REPLY


def test_a_passed_appointment_with_no_outcome_needs_one(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="booked")
    db_session.add(BookingLink(token="t3", lead_id=lead.id, user_id=sample_advisor.id,
                               status="booked", booked_time=NOW - timedelta(days=2)))
    db_session.commit()
    row = lead_stage.for_lead(db_session, lead, now=NOW)
    assert row["stage"] == lead_stage.NEEDS_OUTCOME
    assert row["actionable"] is True


def test_booked_status_with_no_booking_row_still_asks_for_the_outcome(db_session, sample_org, sample_advisor):
    """Bookings made outside the booking-link flow are real and land here."""
    lead = mklead(db_session, sample_org, sample_advisor, status="booked")
    assert stage_of(db_session, lead) == lead_stage.NEEDS_OUTCOME


def test_a_recorded_outcome_closes_the_lead(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="booked")
    db_session.add(BookingLink(token="t4", lead_id=lead.id, user_id=sample_advisor.id,
                               status="booked", booked_time=NOW - timedelta(days=2)))
    db_session.add(LeadOutcome(lead_id=lead.id, recorded_by_id=sample_advisor.id,
                               resulted_in_sale=True))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.CLOSED


def test_an_active_cadence_is_in_sequence_and_carries_its_due_time(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=1))
    due = NOW + timedelta(hours=4)
    db_session.add(CadenceState(lead_id=lead.id, status="active",
                                current_touch_number=2, next_touch_due_at=due))
    db_session.commit()
    row = lead_stage.for_lead(db_session, lead, now=NOW)
    assert row["stage"] == lead_stage.IN_SEQUENCE
    assert row["due_at"] == due


def test_a_cancelled_cadence_is_not_live(db_session, sample_org, sample_advisor):
    """`cancelled` is written by cadence_router and is in neither the enum nor
    the column comment. It plainly means stopped, and is treated as stopped."""
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=1))
    db_session.add(CadenceState(lead_id=lead.id, status="cancelled",
                                current_touch_number=2,
                                next_touch_due_at=NOW + timedelta(hours=4)))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.AWAITING_REPLY


def test_a_live_ai_conversation_is_in_sequence(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=1))
    db_session.add(PipelineConversation(organization_id=sample_org.id, lead_id=lead.id,
                                        advisor_id=sample_advisor.id, stage="engaged",
                                        paused=False, next_send_at=NOW + timedelta(hours=6)))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.IN_SEQUENCE


def test_a_completed_conversation_is_not_in_sequence(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=1))
    db_session.add(PipelineConversation(organization_id=sample_org.id, lead_id=lead.id,
                                        advisor_id=sample_advisor.id, stage="completed",
                                        paused=False, next_send_at=None))
    db_session.commit()
    assert stage_of(db_session, lead) == lead_stage.AWAITING_REPLY


def test_the_cold_status_is_a_stalled_lead_not_an_invisible_one(db_session, sample_org, sample_advisor):
    """`cold` is what an exhausted AI sequence writes. It was in no vocabulary,
    no badge map and no filter, so those leads fell out of the product."""
    lead = mklead(db_session, sample_org, sample_advisor, status="cold",
                  last_messaged_at=NOW - timedelta(days=2))
    row = lead_stage.for_lead(db_session, lead, now=NOW)
    assert row["stage"] == lead_stage.STALLED
    assert row["actionable"] is True


def test_a_long_quiet_lead_with_nothing_scheduled_is_stalled(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=lead_stage.STALE_AFTER_DAYS + 1))
    assert stage_of(db_session, lead) == lead_stage.STALLED


def test_a_recently_contacted_lead_is_only_awaiting(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="sent",
                  last_messaged_at=NOW - timedelta(days=2))
    assert stage_of(db_session, lead) == lead_stage.AWAITING_REPLY


# ── the batch is the same answer as the single ──────────────────────────────

def test_the_batch_and_the_single_lead_path_agree(db_session, sample_org, sample_advisor):
    leads = []
    for i, s in enumerate(["new", "sent", "booked", "cold", "dnc", "not_interested"]):
        leads.append(mklead(db_session, sample_org, sample_advisor, status=s,
                            first_name="B%d" % i,
                            last_messaged_at=None if s == "new" else NOW - timedelta(days=1)))
    batch = lead_stage.for_leads(db_session, leads, now=NOW)
    for lead in leads:
        assert batch[lead.id]["stage"] == stage_of(db_session, lead)


def test_counts_name_every_stage_so_a_caller_never_guesses(db_session, sample_org, sample_advisor):
    tally = lead_stage.counts(db_session, [], now=NOW)
    assert set(tally) == set(lead_stage.STAGES)
    assert sum(tally.values()) == 0


def test_disagreements_reports_drift_without_touching_anything(db_session, sample_org, sample_advisor):
    # Status says booked; there is no booking, no outcome - and there never was
    # any contact either. The derived answer is NEEDS_OUTCOME, which is in the
    # allowed set for "booked", so this one does NOT count as drift.
    ok = mklead(db_session, sample_org, sample_advisor, status="booked", first_name="A")
    # Status says dnc, and dnc can only ever derive to unworkable.
    fine = mklead(db_session, sample_org, sample_advisor, status="dnc", first_name="B")
    # Status says new, but a cadence is running on it. That is real drift.
    drifted = mklead(db_session, sample_org, sample_advisor, status="new", first_name="C")
    db_session.add(CadenceState(lead_id=drifted.id, status="active",
                                current_touch_number=1,
                                next_touch_due_at=NOW + timedelta(hours=1)))
    db_session.commit()
    rows = lead_stage.disagreements(db_session, [ok, fine, drifted], now=NOW)
    assert [r["lead_id"] for r in rows] == [drifted.id]
    assert not db_session.new and not db_session.dirty


# ── one definition of "needs a person" ──────────────────────────────────────

def test_an_answered_reply_leaves_the_inbox(db_session, sample_org, sample_advisor):
    """The defect, stated as a test: two of the four definitions ignored
    reviewed_at, so a rep marked a reply handled and it came back."""
    lead = mklead(db_session, sample_org, sample_advisor, status="replied")
    worked = Reply(lead_id=lead.id, body="call me",
                   classification=ReplyClassification.CALLBACK,
                   received_at=NOW - timedelta(hours=5), reviewed_at=NOW)
    open_one = Reply(lead_id=lead.id, body="interested",
                     classification=ReplyClassification.INTERESTED,
                     received_at=NOW - timedelta(hours=1))
    db_session.add_all([worked, open_one])
    db_session.commit()
    rows = db_session.query(Reply).filter(*rcs.attention_filters()).all()
    assert [r.id for r in rows] == [open_one.id]


def test_include_reviewed_is_the_deliberate_exception(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="replied")
    db_session.add(Reply(lead_id=lead.id, body="call me",
                         classification=ReplyClassification.CALLBACK,
                         received_at=NOW, reviewed_at=NOW))
    db_session.commit()
    assert db_session.query(Reply).filter(*rcs.attention_filters()).count() == 0
    assert db_session.query(Reply).filter(
        *rcs.attention_filters(include_reviewed=True)).count() == 1


def test_only_interested_and_callback_count(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="replied")
    for c in (ReplyClassification.NEUTRAL, ReplyClassification.DNC,
              ReplyClassification.QUESTION, ReplyClassification.WRONG_NUMBER,
              ReplyClassification.NOT_INTERESTED):
        db_session.add(Reply(lead_id=lead.id, body="x", classification=c,
                             received_at=NOW))
    db_session.commit()
    assert db_session.query(Reply).filter(*rcs.attention_filters()).count() == 0


def test_the_row_level_predicate_agrees_with_the_query(db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, status="replied")
    rows = [
        Reply(lead_id=lead.id, body="a", classification=ReplyClassification.INTERESTED,
              received_at=NOW),
        Reply(lead_id=lead.id, body="b", classification=ReplyClassification.INTERESTED,
              received_at=NOW, reviewed_at=NOW),
        Reply(lead_id=lead.id, body="c", classification=ReplyClassification.NEUTRAL,
              received_at=NOW),
    ]
    db_session.add_all(rows)
    db_session.commit()
    from_query = {r.id for r in db_session.query(Reply).filter(*rcs.attention_filters())}
    from_rows = {r.id for r in rows if rcs.reply_needs_attention(r)}
    assert from_query == from_rows


# ── the two values that were written but never declared ─────────────────────

def test_cold_and_not_interested_are_declared_now():
    values = {m.value for m in LeadStatus}
    assert "cold" in values
    assert "not_interested" in values


def test_every_declared_status_is_offered_as_a_filter():
    """The filter list held 7 of the 11 values a lead can hold, so four kinds
    of lead could not be found on the leads page by any filter at all."""
    import io, re
    src = io.open("frontend/src/pages/Leads.jsx", encoding="utf-8").read()
    block = src[src.index("STATUS_FILTER_OPTIONS"):]
    block = block[:block.index("]")]
    offered = set(re.findall(r"value:\s*'([a-z_]*)'", block))
    missing = {m.value for m in LeadStatus} - offered
    assert not missing, "not filterable: %s" % sorted(missing)


def test_every_declared_status_has_a_badge():
    import io, re
    src = io.open("frontend/src/components/StatusBadge.jsx", encoding="utf-8").read()
    block = src[src.index("const STATUS_CONFIG"):]
    block = block[:block.index("\n}")]
    known = set(re.findall(r"^\s*([a-z_]+):\s*\{", block, re.M))
    missing = {m.value for m in LeadStatus} - known
    assert not missing, "renders a raw value: %s" % sorted(missing)
