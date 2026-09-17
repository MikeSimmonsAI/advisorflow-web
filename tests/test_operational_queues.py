"""SS8 - the four queues, and the fact that they are views.

THE CLAIM UNDER TEST is that SMS, EMAIL, VOICE and FOLLOW-UP need no new
tables, because the state that answers them already exists. So the tests are
mostly about what the queues DO NOT do:

  - they write nothing and enqueue nothing;
  - they never widen scope - another advisor's lead and another tenant's lead
    cannot appear in either, whatever the stage says;
  - they do not decide eligibility for themselves, they ask `qualification`,
    which is the same module the send gate asks;
  - a lead with automated work already scheduled is NOT also handed to a rep,
    which is how a lead gets two messages in an hour from two places;
  - an unknown queue name is a 400, not an empty list. "Nothing to do today"
    is the most expensive wrong answer this endpoint could give.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (CadenceState, Lead, LeadStatus, Organization,
                               Reply, ReplyClassification, User, VoiceCall)
from app.services import lead_stage, operational_queues as oq, qualification
from app.services.auth_service import hash_password

NOW = datetime(2026, 9, 17, 12, 0)


def mklead(db, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=kw.pop("first_name", "Queue"), last_name="Lead",
                phone=kw.pop("phone", "12145550000"),
                email=kw.pop("email", "q@example.com"),
                status=kw.pop("status", LeadStatus.NEW), **kw)
    db.add(lead)
    db.commit()
    return lead


def ids(result, key="items"):
    return {r["lead_id"] for r in result[key]}


# ── they are views ──────────────────────────────────────────────────────────

def test_no_queue_writes_anything():
    import ast, inspect
    tree = ast.parse(inspect.getsource(oq))
    offenders = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute):
            continue
        recv = getattr(n.func.value, "id", None) or getattr(n.func.value, "attr", None)
        if recv in ("db", "session") and n.func.attr in (
                "commit", "flush", "add", "add_all", "delete", "merge", "execute"):
            offenders.append(n.func.attr)
    assert not offenders, "operational_queues writes: %s" % offenders


def test_no_queue_reaches_a_sender():
    import ast, inspect
    tree = ast.parse(inspect.getsource(oq))
    names = {getattr(n.func, "id", None) or getattr(n.func, "attr", None)
             for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for forbidden in ("send_sms", "send_email", "send_email_to_lead",
                      "send_email_via_provider", "enqueue"):
        assert forbidden not in names


def test_building_a_queue_leaves_the_session_clean(db_session, sample_org, sample_advisor):
    for i in range(5):
        mklead(db_session, sample_org, sample_advisor, first_name="L%d" % i,
               phone="1214555000%d" % i, email="l%d@example.com" % i)
    # expire, not expunge: expunge would detach sample_advisor and the queue
    # could not read its own caller.
    db_session.expire_all()
    oq.sms_queue(db_session, sample_advisor, now=NOW)
    assert not db_session.new and not db_session.dirty and not db_session.deleted


# ── scope is never widened ──────────────────────────────────────────────────

def test_another_advisors_lead_is_not_in_the_queue(db_session, sample_org,
                                                   sample_advisor, second_advisor):
    mine = mklead(db_session, sample_org, sample_advisor, first_name="Mine")
    theirs = mklead(db_session, sample_org, second_advisor, first_name="Theirs",
                    phone="12145557777", email="theirs@example.com")
    result = oq.sms_queue(db_session, sample_advisor, now=NOW,
                          include_excluded=True)
    seen = (ids(result) | ids(result, "review_items")
            | ids(result, "excluded_items"))
    assert theirs.id not in seen
    # And the advisor's own lead IS there, in one bucket or another - otherwise
    # this test would pass just as well against a queue that returned nothing.
    assert mine.id in seen


def test_another_tenants_lead_is_not_in_the_queue(db_session, sample_org, sample_advisor):
    other = Organization(name="Somebody Else", slug="else", plan="standard",
                         industry="funeral")
    db_session.add(other)
    db_session.commit()
    other_advisor = User(organization_id=other.id, email="them@else.com",
                         password_hash=hash_password("TestPass123!"),
                         full_name="Them", role="advisor", must_change_password=False)
    db_session.add(other_advisor)
    db_session.commit()
    foreign = mklead(db_session, other, other_advisor, first_name="Foreign",
                     phone="12145558888", email="foreign@else.com")
    result = oq.sms_queue(db_session, sample_advisor, now=NOW)
    everything = ids(result) | ids(result, "review_items")
    assert foreign.id not in everything


def test_a_duplicate_never_enters_a_queue(db_session, sample_org, sample_advisor):
    dup = mklead(db_session, sample_org, sample_advisor, first_name="Dup",
                 is_duplicate=True)
    result = oq.sms_queue(db_session, sample_advisor, now=NOW,
                          include_excluded=True)
    everything = (ids(result) | ids(result, "review_items")
                  | ids(result, "excluded_items"))
    assert dup.id not in everything


# ── the queues ask qualification, they do not re-answer it ──────────────────

def test_eligibility_is_not_reimplemented():
    """Every DNC / suppression / consent / test-record rule already lives in
    qualification, and the send gate uses the same module. A queue with its own
    copy would be the sixth disagreeing answer."""
    import ast, inspect
    src = inspect.getsource(oq)
    tree = ast.parse(src)
    called = {getattr(n.func, "attr", None)
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "qualify_leads" in called
    for reimplemented in ("is_phone_suppressed", "load_suppressed_phones",
                          "check_compliance_preflight", "_compliance_check"):
        assert reimplemented not in called, "re-answers eligibility: %s" % reimplemented


def test_a_dnc_lead_is_never_offered_as_sendable(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Dnc",
                  status="dnc")
    result = oq.sms_queue(db_session, sample_advisor, now=NOW, include_excluded=True)
    # DNC is UNWORKABLE at the stage level, so it never reaches qualification -
    # and it is not in the actionable list either. Both are the right answer;
    # what matters is that it is not offered as sendable.
    assert lead.id not in ids(result)
    assert lead.id not in ids(result, "review_items")


# ── a lead with scheduled work is not also handed to a rep ──────────────────

def test_a_lead_in_an_active_cadence_is_not_in_the_sms_queue(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Running",
                  status="sent", last_messaged_at=NOW - timedelta(days=1))
    db_session.add(CadenceState(lead_id=lead.id, status="active",
                                current_touch_number=2,
                                next_touch_due_at=NOW + timedelta(hours=3)))
    db_session.commit()
    result = oq.sms_queue(db_session, sample_advisor, now=NOW, include_excluded=True)
    everything = (ids(result) | ids(result, "review_items")
                  | ids(result, "excluded_items"))
    assert lead.id not in everything


def test_an_overdue_cadence_touch_does_show_up_in_follow_up(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Overdue",
                  status="sent", last_messaged_at=NOW - timedelta(days=4))
    db_session.add(CadenceState(lead_id=lead.id, status="active",
                                current_touch_number=2,
                                next_touch_due_at=NOW - timedelta(hours=2)))
    db_session.commit()
    result = oq.follow_up_queue(db_session, sample_advisor, now=NOW)
    assert lead.id in ids(result)


# ── follow-up ───────────────────────────────────────────────────────────────

def test_follow_up_carries_the_same_reason_the_lead_itself_gives(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Waiting",
                  status="replied")
    db_session.add(Reply(lead_id=lead.id, body="yes",
                         classification=ReplyClassification.INTERESTED,
                         received_at=NOW - timedelta(hours=1)))
    db_session.commit()
    row = [r for r in oq.follow_up_queue(db_session, sample_advisor, now=NOW)["items"]
           if r["lead_id"] == lead.id][0]
    derived = lead_stage.for_lead(db_session, lead, now=NOW)
    assert row["reason"] == derived["reason"]
    assert row["stage"] == lead_stage.NEEDS_REPLY


def test_a_manager_sees_the_teams_owed_work_not_only_their_own(
        db_session, sample_org, sample_advisor, second_advisor):
    """`/workqueue/today` filters on assigned_to_id == me, so a manager saw
    nothing their team owed. These go through lead_scope instead."""
    sample_advisor.role = "org_admin"
    db_session.commit()
    theirs = mklead(db_session, sample_org, second_advisor, first_name="Theirs",
                    phone="12145556666", email="t@example.com", status="booked")
    result = oq.follow_up_queue(db_session, sample_advisor, now=NOW)
    assert theirs.id in ids(result)


# ── voice ───────────────────────────────────────────────────────────────────

def test_a_promised_callback_outranks_everything_in_the_voice_queue(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Callback",
                  status="sent", last_messaged_at=NOW - timedelta(days=1))
    db_session.add(VoiceCall(lead_id=lead.id, advisor_id=sample_advisor.id,
                             organization_id=sample_org.id, to_phone=lead.phone,
                             call_number=1, status="completed", outcome="no_answer",
                             callback_at=NOW - timedelta(hours=1)))
    db_session.commit()
    result = oq.voice_queue(db_session, sample_advisor, now=NOW)
    row = [r for r in result["callbacks"] if r["lead_id"] == lead.id][0]
    assert row["overdue"] is True
    assert result["counts"]["callbacks_due"] == 1


def test_a_lead_at_the_three_attempt_ceiling_is_not_offered_again(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Thrice")
    db_session.add(VoiceCall(lead_id=lead.id, advisor_id=sample_advisor.id,
                             organization_id=sample_org.id, to_phone=lead.phone,
                             call_number=3, status="completed", outcome="no_answer"))
    db_session.commit()
    result = oq.voice_queue(db_session, sample_advisor, now=NOW)
    assert lead.id not in ids(result)


def test_a_booked_call_does_not_leave_a_callback_hanging(
        db_session, sample_org, sample_advisor):
    lead = mklead(db_session, sample_org, sample_advisor, first_name="Booked")
    db_session.add(VoiceCall(lead_id=lead.id, advisor_id=sample_advisor.id,
                             organization_id=sample_org.id, to_phone=lead.phone,
                             call_number=1, status="completed", outcome="booked",
                             callback_at=NOW - timedelta(hours=1)))
    db_session.commit()
    result = oq.voice_queue(db_session, sample_advisor, now=NOW)
    assert result["counts"]["callbacks_due"] == 0


# ── counts and shape ────────────────────────────────────────────────────────

def test_counts_are_the_whole_set_not_the_first_page(db_session, sample_org, sample_advisor):
    for i in range(7):
        mklead(db_session, sample_org, sample_advisor, first_name="P%d" % i,
               phone="121455510%02d" % i, email="p%d@example.com" % i)
    result = oq.sms_queue(db_session, sample_advisor, now=NOW, limit=2)
    total = (result["counts"]["ready"] + result["counts"]["review"]
             + result["counts"]["excluded"])
    assert total == 7
    assert len(result["items"]) <= 2


def test_an_unknown_queue_name_raises_rather_than_returning_nothing(
        db_session, sample_advisor):
    with pytest.raises(ValueError, match="Unknown queue"):
        oq.get("smss", db_session, sample_advisor)


def test_the_summary_names_all_four(db_session, sample_org, sample_advisor):
    out = oq.summary(db_session, sample_advisor, now=NOW)
    assert [q["queue"] for q in out["queues"]] == list(oq.QUEUES)


# ── the endpoints ───────────────────────────────────────────────────────────

def test_the_queue_endpoints_are_reachable(client, auth_headers, db_session,
                                           sample_org, sample_advisor):
    mklead(db_session, sample_org, sample_advisor, first_name="Api")
    r = client.get("/workqueue/queues", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["queues"]) == 4
    for name in oq.QUEUES:
        r = client.get("/workqueue/queues/%s" % name, headers=auth_headers)
        assert r.status_code == 200, (name, r.text)
        assert r.json()["derived"] is True


def test_a_typo_in_the_queue_name_is_a_400(client, auth_headers):
    r = client.get("/workqueue/queues/txt", headers=auth_headers)
    assert r.status_code == 400
    assert "Unknown queue" in r.text


def test_the_old_workqueue_today_still_answers(client, auth_headers):
    """Nothing above replaces it yet, and the page that calls it still works."""
    r = client.get("/workqueue/today", headers=auth_headers)
    assert r.status_code == 200
    assert set(r.json()) == {"needs_text", "needs_reply", "cadence_due",
                             "outcomes_needed"}
