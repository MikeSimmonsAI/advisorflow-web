"""SS3 + SS4 + SS5 — the read model, verified against the questions it exists
to answer.

THE THREE PIECES ARE ONE CLAIM: that a person can ask "what happened with this
family", "what did we send today" and "how much of our outbound actually
landed", and get an answer that is true.

Three properties carry most of the weight, and each one replaces a specific
way the old code lied:

  IT PAGES TO THE BEGINNING. The old timeline took the 200 newest rows per
  channel and had no offset, cursor or page parameter on its signature. Past
  200 - which one nine-touch cadence plus a couple of bulk sends reaches - the
  rest could not be retrieved by any request the API could express. The test
  below builds a MIXED stream of over 300 events across five kinds and walks
  it to the first one, checking for gaps, duplicates and order on the way.

  ATTEMPTED IS NOT SENT. A blocked touch, a suppressed number and a provider
  rejection are all things that happened and none of them is a message the
  family received. Every report is asked about a lead that has one of each,
  and none of them may count any of it as sent.

  NOTHING CROSSES A TENANT. Every read endpoint added during this workstream
  is listed in one place here and asked for another organization's data.
"""

from datetime import datetime, timedelta

import pytest

from app.models.models import (
    CadenceState, CadenceTouchLog, EmailMessage, Lead, Message, Organization,
    Reply, User,
)
from app.services import activity_reporting as ar
from app.services import cadence_service as cs
from app.services import communication_history as ch
from app.services import send_source as src
from app.services.auth_service import create_access_token, hash_password


def _lead(db_session, org, advisor, **kw):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id,
                first_name=kw.pop("first_name", "Read"), last_name="Model",
                phone=kw.pop("phone", "12145554001"),
                email=kw.pop("email", "read@example.com"),
                status=kw.pop("status", "replied"))
    lead.phone_raw = lead.phone
    db_session.add(lead); db_session.commit(); return lead


def _second_tenant(db_session, slug="rm-other"):
    org = Organization(name="Other Tenant", slug=slug, plan="standard",
                       industry="funeral")
    db_session.add(org); db_session.commit()
    user = User(organization_id=org.id, email="%s@other.test" % slug,
                password_hash=hash_password("TestPass123!"), full_name="Other",
                role="advisor", must_change_password=False)
    db_session.add(user); db_session.commit()
    return org, user


def _headers(db_session, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db_session)}


# ═══════════════════════════════════════════════════════════════════════════
# IT PAGES TO THE BEGINNING - and the stream is genuinely mixed
# ═══════════════════════════════════════════════════════════════════════════

def _build_mixed_history(db_session, lead, advisor, *, n=320):
    """n events across five kinds, one per minute, oldest first.

    Deliberately interleaved rather than blocked by kind: a merge that only
    worked when one source dominated a window would pass a single-channel test
    and drop events here.
    """
    base = datetime.utcnow() - timedelta(days=40)
    state = CadenceState(lead_id=lead.id, status="active",
                         current_touch_number=0,
                         cadence_started_at=base,
                         next_touch_due_at=base + timedelta(days=1))
    db_session.add(state); db_session.commit()

    made = 0
    for i in range(n):
        when = base + timedelta(minutes=i)
        slot = i % 5
        if slot == 0:
            db_session.add(Message(lead_id=lead.id, sender_id=advisor.id,
                                   body="sms %d" % i, twilio_status="sent",
                                   sent_at=when, send_source=src.CADENCE))
        elif slot == 1:
            db_session.add(EmailMessage(lead_id=lead.id, sender_id=advisor.id,
                                        subject="email %d" % i,
                                        body_html="<p>%d</p>" % i,
                                        status="sent", sent_at=when,
                                        send_source=src.MANUAL,
                                        sent_by_user_id=advisor.id))
        elif slot == 2:
            db_session.add(Reply(lead_id=lead.id, body="reply %d" % i,
                                 received_at=when))
        elif slot == 3:
            db_session.add(CadenceTouchLog(
                cadence_state_id=state.id, lead_id=lead.id,
                organization_id=lead.organization_id,
                touch_number=(i // 5) + 1, attempt_seq=1, channel="sms",
                outcome=cs.OUTCOME_BLOCKED, reason="suppressed",
                scheduled_for=when, attempted_at=when))
        else:
            db_session.add(Message(lead_id=lead.id, sender_id=advisor.id,
                                   body="failed %d" % i,
                                   twilio_status="failed", sent_at=when,
                                   send_source=src.BULK,
                                   sent_by_user_id=advisor.id))
        made += 1
        if made % 50 == 0:
            db_session.commit()
    db_session.commit()
    return made


def test_a_mixed_history_of_more_than_three_hundred_events_pages_to_the_start(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor)
    made = _build_mixed_history(db_session, lead, sample_advisor, n=320)

    seen, keys, before, pages = [], [], None, 0
    while pages < 20:
        page = ch.fetch(db_session, lead.id, limit=50, before=before)
        pages += 1
        seen.extend(page["events"])
        keys.extend((e["kind"], e["id"]) for e in page["events"])
        if not page["has_more"]:
            break
        before = page["next_before"]

    # NO DUPLICATES. A cursor that re-serves its boundary row inflates history.
    assert len(keys) == len(set(keys)), "the cursor served a row twice"
    # NO GAPS. Everything written is reachable.
    assert len(keys) >= made, "only %d of %d events were reachable" % (len(keys), made)
    # ORDER HOLDS ACROSS PAGE BOUNDARIES, not just within a page.
    stamps = [e["timestamp"] for e in seen if e["timestamp"] is not None]
    assert stamps == sorted(stamps, reverse=True), "order broke across pages"


def test_every_kind_survives_the_paging(db_session, sample_org, sample_advisor):
    """A merge that starved one source out of every page would still satisfy
    the counts above."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554002",
                 email="kinds@example.com")
    _build_mixed_history(db_session, lead, sample_advisor, n=320)

    kinds, before = set(), None
    for _ in range(20):
        page = ch.fetch(db_session, lead.id, limit=50, before=before)
        kinds.update(e["kind"] for e in page["events"])
        if not page["has_more"]:
            break
        before = page["next_before"]

    assert {ch.OUTBOUND, ch.INBOUND, ch.SYSTEM} <= kinds
    channels = set()
    before = None
    for _ in range(20):
        page = ch.fetch(db_session, lead.id, limit=50, before=before)
        channels.update(e["channel"] for e in page["events"])
        if not page["has_more"]:
            break
        before = page["next_before"]
    assert "sms" in channels and "email" in channels


def test_the_page_size_ceiling_cannot_be_argued_past(db_session, sample_org,
                                                     sample_advisor):
    """A caller asking for 100,000 gets MAX_LIMIT, not a table scan."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554003",
                 email="cap@example.com")
    _build_mixed_history(db_session, lead, sample_advisor, n=60)
    page = ch.fetch(db_session, lead.id, limit=100_000)
    assert page["limit"] == ch.MAX_LIMIT


# ═══════════════════════════════════════════════════════════════════════════
# ATTEMPTED IS NOT SENT
# ═══════════════════════════════════════════════════════════════════════════

def _one_of_each_outcome(db_session, lead, advisor, when):
    """A family with one real send and three things that are not sends."""
    state = CadenceState(lead_id=lead.id, status="active",
                         current_touch_number=0, cadence_started_at=when,
                         next_touch_due_at=when)
    db_session.add(state); db_session.commit()
    db_session.add_all([
        # The only real one. It has a provider SID, which is the evidence a
        # request was actually made.
        Message(lead_id=lead.id, sender_id=advisor.id, body="real",
                twilio_sid="SM_real", twilio_status="sent", send_state="sent",
                delivery_status="pending", sent_at=when,
                send_source=src.MANUAL, sent_by_user_id=advisor.id),
        # A provider rejection. It happened; nobody received it.
        Message(lead_id=lead.id, sender_id=advisor.id, body="rejected",
                twilio_sid="SM_bad", twilio_status="failed", send_state="failed",
                delivery_status="pending", error_code="30007",
                sent_at=when, send_source=src.MANUAL,
                sent_by_user_id=advisor.id),
        # Refused before submission: no SID at all.
        Message(lead_id=lead.id, sender_id=advisor.id, body="never submitted",
                twilio_status="sent", sent_at=when, send_source=src.MANUAL,
                sent_by_user_id=advisor.id),
        EmailMessage(lead_id=lead.id, sender_id=advisor.id, subject="bounced",
                     body_html="<p>x</p>", status="failed", sent_at=when,
                     send_source=src.BULK_AI, sent_by_user_id=advisor.id),
        # Refused before any provider was reached.
        CadenceTouchLog(cadence_state_id=state.id, lead_id=lead.id,
                        organization_id=lead.organization_id,
                        touch_number=1, attempt_seq=1, channel="sms",
                        outcome=cs.OUTCOME_BLOCKED, reason="on the DNC list",
                        scheduled_for=when, attempted_at=when),
        CadenceTouchLog(cadence_state_id=state.id, lead_id=lead.id,
                        organization_id=lead.organization_id,
                        touch_number=2, attempt_seq=1, channel="sms",
                        outcome=cs.OUTCOME_SUPPRESSED, reason="demo tenant",
                        scheduled_for=when, attempted_at=when),
    ])
    db_session.commit()
    return state


def test_a_refused_touch_never_counts_as_a_send_in_any_report(
        db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554010",
                 email="mixed@example.com")
    now = datetime.utcnow()
    _one_of_each_outcome(db_session, lead, sample_advisor, now)

    # SS3 - today. Four rows exist; the delivery state distinguishes them.
    today = ar.sent_today(db_session, sample_org.id, on=now)
    # `preview` is the body, truncated - the row does not carry the raw one.
    by_body = {r.get("preview"): (r.get("delivery") or {}).get("state")
               for r in today["items"] if r["channel"] == "sms"}
    # The provider rejected it. It is reported as failed, not as a send.
    assert by_body["rejected"] == "failed"
    # NO PROVIDER SID MEANS NO REQUEST WAS EVER MADE, whatever the status
    # column happens to say. This one never reaches the carrier and must never
    # render as sent.
    assert by_body["never submitted"] == "blocked"
    # And the real one is neither.
    assert by_body["real"] not in ("failed", "blocked")

    # SS4 - email performance. The one email failed; nothing may claim a send.
    perf = ar.email_performance(db_session, sample_org.id,
                                since=now - timedelta(days=1))
    blob = perf["totals"] if "totals" in perf else perf
    assert blob.get("sent", 0) == 0, "a bounced email counted as sent"

    # The cadence half, which is the only place refusals are countable at all.
    outcomes = ar.cadence_outcomes(db_session, sample_org.id,
                                   since=now - timedelta(days=1))
    counts = outcomes["by_outcome"]
    assert counts.get(cs.OUTCOME_BLOCKED, 0) == 1
    assert counts.get(cs.OUTCOME_SUPPRESSED, 0) == 1
    assert counts.get(cs.OUTCOME_SENT, 0) == 0


def test_the_history_shows_the_refusals_rather_than_a_gap(
        db_session, sample_org, sample_advisor):
    """'Why did this family never hear from us' is answerable only if the
    things that did not happen are visible."""
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554011",
                 email="why@example.com")
    now = datetime.utcnow()
    _one_of_each_outcome(db_session, lead, sample_advisor, now)

    events = ch.fetch(db_session, lead.id, limit=100)["events"]
    reasons = " ".join(str(e.get("status") or "") + " " +
                       str((e.get("meta") or {}).get("reason") or "")
                       for e in events).lower()
    assert "dnc" in reasons or "blocked" in reasons
    assert any(e["kind"] == ch.SYSTEM for e in events)


# ═══════════════════════════════════════════════════════════════════════════
# NOTHING CROSSES A TENANT
# ═══════════════════════════════════════════════════════════════════════════

# Every read endpoint this workstream added or changed. One list, so a new one
# is a one-line addition rather than a forgotten test.
TENANT_READ_ENDPOINTS = [
    "/activity/sent-today",
    "/reports/email-performance",
    "/workqueue/queues",
    "/workqueue/queues/sms",
    "/workqueue/queues/email",
    "/workqueue/queues/voice",
    "/workqueue/queues/follow_up",
]


@pytest.mark.parametrize("path", TENANT_READ_ENDPOINTS)
def test_a_read_endpoint_never_returns_another_tenants_lead(
        path, client, db_session, sample_org, sample_advisor):
    other_org, other_user = _second_tenant(db_session, slug="rm-cross")
    foreign = _lead(db_session, other_org, other_user, phone="12145557777",
                    email="foreign@other.test", first_name="Foreign")
    db_session.add(Message(lead_id=foreign.id, sender_id=other_user.id,
                           body="theirs", twilio_status="sent",
                           sent_at=datetime.utcnow(), send_source=src.MANUAL,
                           sent_by_user_id=other_user.id))
    db_session.commit()

    r = client.get(path, headers=_headers(db_session, sample_advisor))
    # 200 or a refusal are both fine. What is not fine is another tenant's data.
    if r.status_code == 200:
        assert foreign.id not in r.text, "%s leaked a foreign lead" % path
        assert "Foreign" not in r.text


def test_the_lead_history_endpoint_refuses_another_tenants_lead(
        client, db_session, sample_org, sample_advisor):
    other_org, other_user = _second_tenant(db_session, slug="rm-hist")
    foreign = _lead(db_session, other_org, other_user, phone="12145557778",
                    email="hist@other.test")
    r = client.get("/leads/%s/history" % foreign.id,
                   headers=_headers(db_session, sample_advisor))
    assert r.status_code in (403, 404), r.status_code


def test_the_reporting_services_scope_by_organization_not_by_caller(
        db_session, sample_org, sample_advisor):
    """The services are asked directly, with the OTHER organization's id, and
    must answer about that organization only."""
    other_org, other_user = _second_tenant(db_session, slug="rm-svc")
    mine = _lead(db_session, sample_org, sample_advisor, phone="12145554020",
                 email="mine@example.com")
    theirs = _lead(db_session, other_org, other_user, phone="12145554021",
                   email="theirs@other.test")
    now = datetime.utcnow()
    db_session.add_all([
        Message(lead_id=mine.id, sender_id=sample_advisor.id, body="mine",
                twilio_status="sent", sent_at=now, send_source=src.MANUAL),
        Message(lead_id=theirs.id, sender_id=other_user.id, body="theirs",
                twilio_status="sent", sent_at=now, send_source=src.MANUAL),
    ])
    db_session.commit()

    mine_rows = ar.sent_today(db_session, sample_org.id, on=now)["items"]
    assert {r["lead_id"] for r in mine_rows} == {mine.id}
    theirs_rows = ar.sent_today(db_session, other_org.id, on=now)["items"]
    assert {r["lead_id"] for r in theirs_rows} == {theirs.id}
