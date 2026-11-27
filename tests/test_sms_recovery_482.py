"""#482 SMS Delivery Recovery: a carrier failure puts a family's TEXTS on a
reviewable hold - never DNC, never proof of a landline - and nothing is resent
until a person records evidence and releases it. Covers the Twilio callback
(30003/30005/30006/30007, duplicates, out-of-order, failed-then-delivered),
two workers, the cadence runner, the send gate, STOP/DNC priority, email
fallback, the operator workflow, versioned policy, the SCI backfill, the
self-check, and tenant isolation of the queue API. Synthetic data; the
provider is mocked; nothing is sent."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.models.models import CadenceState, EmailMessage, Lead, Message, Organization, Reply
from app.models.sms_recovery_models import (SMS_REVIEW_REQUIRED, SMS_VERIFIED_UNREACHABLE, SmsRecoveryEvent,
                                            SmsRecoveryItem, SmsRecoveryPolicy)
from app.services import cadence_service as cs
from app.services import sms_recovery as R
from app.services import sms_recovery_policy as P
from tests.test_cadence_engine import AT, _due, _lead, _logs, _sending_on, _twilio
from tests.test_outreach_program import _admin, _h, program  # noqa: F401


def _msg(db, lead, adv, *, status="undelivered", code="30006", sid="SMtest0001", hours_ago=2):
    m = Message(lead_id=lead.id, sender_id=adv.id, body="Hello", twilio_sid=sid, send_source="cadence",
                sent_at=datetime.utcnow() - timedelta(hours=hours_ago), delivery_status=status, error_code=code)
    db.add(m)
    db.commit()
    return m


def _callback(client, sid, status, code=None):
    data = {"MessageSid": sid, "MessageStatus": status}
    if code:
        data["ErrorCode"] = code
    with patch("app.routers.sms_router.guard_status_callback", new=AsyncMock(return_value=None)):
        return client.post("/sms/webhook/status-callback", data=data)


def _items(db, lead):
    return db.query(SmsRecoveryItem).filter(SmsRecoveryItem.lead_id == lead.id).all()


# ── the callback ────────────────────────────────────────────────────────────

def test_a_30006_holds_the_texts_and_is_not_dnc_or_a_landline(client, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554001")
    m = _msg(db_session, lead, sample_advisor, status="sent", code=None, sid="SM30006a")
    r = _callback(client, "SM30006a", "undelivered", "30006")
    assert r.status_code == 200
    db_session.refresh(lead)
    [item] = _items(db_session, lead)
    assert item.state == SMS_REVIEW_REQUIRED and item.open_lead_id == lead.id
    assert item.error_code == "30006" and item.trigger_message_id == m.id and item.provider_sid == "SM30006a"
    assert item.policy_version == P.DEFAULT_VERSION and item.policy_action == P.HOLD_FOR_REVIEW
    assert lead.status == "new"                         # not DNC
    assert lead.phone_line_type is None                 # not written as a landline
    assert getattr(lead, "allow_sms", None) is not False
    assert R.send_refusal(db_session, lead).startswith(R.HOLD_TAG)


def test_the_same_receipt_twice_changes_nothing(client, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554002")
    _msg(db_session, lead, sample_advisor, status="sent", code=None, sid="SMdup")
    for _ in range(3):
        assert _callback(client, "SMdup", "undelivered", "30006").status_code == 200
    [item] = _items(db_session, lead)
    assert item.failures == 1
    assert db_session.query(SmsRecoveryEvent).filter_by(lead_id=lead.id, kind="failure").count() == 1
    assert db_session.query(SmsRecoveryEvent).filter_by(lead_id=lead.id, kind="opened").count() == 1


@pytest.mark.parametrize("code", ["30003", "30005", "30007"])
def test_other_codes_keep_their_own_rules_and_hold_nobody(client, db_session, sample_org, sample_advisor, code):
    lead = _lead(db_session, sample_org, sample_advisor, phone="1214555410%s" % code[-1])
    _msg(db_session, lead, sample_advisor, status="sent", code=None, sid="SM" + code)
    _callback(client, "SM" + code, "undelivered", code)
    assert _items(db_session, lead) == []
    ev = db_session.query(SmsRecoveryEvent).filter_by(lead_id=lead.id, kind="failure").one()
    assert ev.error_code == code                        # recorded with the provider's own code
    assert R.send_refusal(db_session, lead) is None
    assert P.rule_for(P.DEFAULT_RULES, code)["action"] == ({"30003": P.CONTINUE_SCHEDULE,
                                                        "30005": P.HOLD_FOR_REVIEW,
                                                        "30007": P.INVESTIGATE}[code])


def test_a_late_sent_receipt_never_overwrites_the_final_one(client, db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554003")
    m = _msg(db_session, lead, sample_advisor, status="queued", code=None, sid="SMorder")
    _callback(client, "SMorder", "delivered")
    _callback(client, "SMorder", "sent")                # arrives late
    db_session.refresh(m)
    assert m.delivery_status == "delivered"


def test_failed_then_delivered_closes_the_hold_as_the_providers_correction(client, db_session, sample_org,
                                                                            sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554004")
    m = _msg(db_session, lead, sample_advisor, status="sent", code=None, sid="SMflip")
    _callback(client, "SMflip", "undelivered", "30006")
    _callback(client, "SMflip", "delivered")
    db_session.refresh(m)
    [item] = _items(db_session, lead)
    assert m.delivery_status == "delivered" and m.error_code == "30006"     # history kept
    assert item.open_lead_id is None and item.disposition == "delivered_after_failure"
    assert R.send_refusal(db_session, lead) is None


def test_two_workers_seeing_one_failure_open_one_hold(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554005")
    m1 = _msg(db_session, lead, sample_advisor, sid="SMw1", hours_ago=3)
    m2 = _msg(db_session, lead, sample_advisor, sid="SMw2", hours_ago=1)
    R.record_failure(db_session, m1, source="callback")
    R.record_failure(db_session, m1, source="cadence")          # same failure, second worker
    # a worker that lost the race to open: the unique open slot refuses it
    version, rules = P.effective(db_session, sample_org.id)
    R._open_hold(db_session, lead, m2, "30006", rules["30006"], version, "cadence")
    db_session.commit()
    items = _items(db_session, lead)
    assert len(items) == 1 and items[0].failures == 2


# ── the schedule and the send gate ──────────────────────────────────────────

def test_a_held_family_is_skipped_counted_once_and_others_still_send(db_session, sample_org, sample_advisor,
                                                                    monkeypatch):
    _sending_on(monkeypatch)
    held = _lead(db_session, sample_org, sample_advisor, phone="12145554006")
    st = _due(db_session, held, touch=1)
    _msg(db_session, held, sample_advisor, sid="SMheld", hours_ago=30)
    ok = _lead(db_session, sample_org, sample_advisor, phone="12145554007")
    ok_state = _due(db_session, ok)
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        r = cs.run_due_cadences(db_session, now=AT)
        st.next_touch_due_at = AT - timedelta(minutes=1)      # due again: the same touch
        db_session.commit()
        cs.run_due_cadences(db_session, now=AT)
    db_session.refresh(st)
    db_session.refresh(ok_state)
    assert tw.messages.create.call_count == 1                   # only the family who can be texted
    assert ok_state.current_touch_number == 1
    assert st.status == "active" and st.current_touch_number == 1     # not ended, nothing spent
    assert st.next_touch_due_at >= AT + R.HOLD_RECHECK - timedelta(seconds=1)
    [item] = _items(db_session, held)
    assert item.prevented == 1                                  # one held-back touch, counted once
    assert any(l.reason and l.reason.startswith(R.HOLD_TAG) for l in _logs(db_session, held))


def test_send_sms_refuses_a_held_family_on_any_path_but_answers_their_own_text(db_session, sample_org,
                                                                              sample_advisor):
    from app.services.sms_service import send_sms
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554008")
    m = _msg(db_session, lead, sample_advisor, sid="SMgate")
    R.record_failure(db_session, m, source="callback")
    db_session.commit()
    with pytest.raises(ValueError, match="SMS HOLD"):
        send_sms(db=db_session, advisor=sample_advisor, lead=lead, template="Hi", include_booking_link=False,
                 send_source="manual")
    db_session.add(Reply(lead_id=lead.id, body="Who is this?", source="sms", received_at=datetime.utcnow()))
    db_session.commit()
    assert R.send_refusal(db_session, lead) is None


def test_stop_dnc_wins_and_a_hold_never_reopens_texts(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554009")
    m = _msg(db_session, lead, sample_advisor, sid="SMstop")
    R.record_failure(db_session, m, source="callback")
    db_session.commit()
    lead.status = "dnc"
    db_session.commit()
    assert R.active_hold(db_session, lead) is None              # moot: DNC stops texts itself
    [item] = _items(db_session, lead)
    assert item.disposition == "opted_out"
    lead2 = _lead(db_session, sample_org, sample_advisor, phone="12145554010", status="dnc")
    m2 = _msg(db_session, lead2, sample_advisor, sid="SMstop2")
    assert R.record_failure(db_session, m2, source="callback") is None
    with pytest.raises(R.ActionRefused):
        R.release(db_session, item, "u1")


def test_the_runner_opens_the_hold_when_the_callback_was_missed(db_session, sample_org, sample_advisor,
                                                                monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554011")
    st = _due(db_session, lead, touch=1)
    _msg(db_session, lead, sample_advisor, sid="SMmissed", hours_ago=30)
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        cs.run_due_cadences(db_session, now=AT)
    assert tw.messages.create.call_count == 0
    [item] = _items(db_session, lead)
    assert item.opened_by == "cadence" and st.status == "active"


# ── operator workflow ───────────────────────────────────────────────────────

def test_release_needs_evidence_and_never_sends_at_once(db_session, sample_org, sample_advisor, monkeypatch):
    _sending_on(monkeypatch)
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554012")
    st = _due(db_session, lead, touch=1)
    m = _msg(db_session, lead, sample_advisor, sid="SMrel")
    item = R.record_failure(db_session, m, source="callback")
    db_session.commit()
    with pytest.raises(R.ActionRefused):
        R.release(db_session, item, "u1")                       # not reviewed yet
    with pytest.raises(R.ActionRefused):
        R.mark_capable(db_session, item, "u1", "  ")            # evidence required
    R.mark_capable(db_session, item, "u1", "Family confirmed by phone it is their cell")
    out = R.release(db_session, item, "u1", "verified")
    db_session.commit()
    db_session.refresh(st)
    assert item.open_lead_id is None and item.disposition == "released"
    assert st.next_touch_due_at >= datetime.utcnow() + R.RELEASE_SETTLE - timedelta(minutes=1)
    assert out["next_text_not_before"]
    tw = _twilio()
    with patch("app.services.sms_service._resolve_twilio_creds", return_value=(tw, "+19998887777", None)):
        cs.run_due_cadences(db_session, now=datetime.utcnow())
    assert tw.messages.create.call_count == 0                   # no immediate send


def test_verified_not_capable_keeps_texts_stopped_without_dnc(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554013")
    m = _msg(db_session, lead, sample_advisor, sid="SMnc")
    item = R.record_failure(db_session, m, source="callback")
    R.mark_not_capable(db_session, item, "u1", "Line check: landline")
    db_session.commit()
    db_session.refresh(lead)
    assert item.state == SMS_VERIFIED_UNREACHABLE and lead.status == "new"
    assert "cannot take texts" in R.send_refusal(db_session, lead)


# ── email fallback ──────────────────────────────────────────────────────────

def test_email_fallback_respects_provider_holds_bounces_and_never_sends(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554014")
    m = _msg(db_session, lead, sample_advisor, sid="SMfb")
    item = R.record_failure(db_session, m, source="callback")
    db_session.commit()
    assert R.email_fallback(db_session, lead)["reason"] == "no email address"
    with pytest.raises(R.ActionRefused, match="no email address"):
        R.route_email(db_session, item, "u1")
    assert item.open_lead_id == lead.id                          # stays in review
    lead.email = "fam@gmail.com"
    db_session.commit()
    assert R.email_fallback(db_session, lead)["eligible"] is True
    R.route_email(db_session, item, "u1")
    assert item.email_fallback == "routed"
    db_session.add(EmailMessage(lead_id=lead.id, sender_id=sample_advisor.id, subject="x", body_html="x",
                                status="bounced"))
    db_session.commit()
    assert "bounced" in R.email_fallback(db_session, lead)["reason"]
    assert db_session.query(EmailMessage).filter(EmailMessage.lead_id == lead.id,
                                                 EmailMessage.status != "bounced").count() == 0


def test_email_fallback_respects_the_programs_outlook_yahoo_hold(program, sample_advisor):
    from tests.test_outreach_program import _lead_for
    db, org, prog = program["db"], program["org"], program["prog"]
    prog.email_hold_providers = '["outlook","yahoo"]'
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    lead.email = "fam@yahoo.com"
    db.commit()
    fb = R.email_fallback(db, lead)
    assert fb["eligible"] is False and "on hold" in fb["reason"]


# ── policy, backfill, self-check ────────────────────────────────────────────

def test_policy_changes_are_new_versions_and_never_release_holds(client, db_session, sample_org, sample_advisor):
    admin = _admin(db_session, sample_org, email="recov-admin@example.com")
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554015")
    item = R.record_failure(db_session, _msg(db_session, lead, sample_advisor, sid="SMpol"), source="callback")
    db_session.commit()
    bad = client.put("/sms-recovery/policy", headers=_h(db_session, admin),
                     json={"rules": {"30006": {"action": "resend_now"}}, "note": "x"})
    assert bad.status_code == 422
    r = client.put("/sms-recovery/policy", headers=_h(db_session, admin),
                   json={"rules": {"30006": {"action": "temporary_hold", "hours": 48}}, "note": "Twilio answer"})
    assert r.status_code == 200
    v1 = r.json()["version"]
    r2 = client.put("/sms-recovery/policy", headers=_h(db_session, admin),
                    json={"rules": {"30005": {"action": "hold_for_review"}}, "note": "second"})
    assert r2.json()["version"] != v1
    assert db_session.query(SmsRecoveryPolicy).filter_by(organization_id=sample_org.id).count() == 2
    version, rules = P.effective(db_session, sample_org.id)
    assert version == r2.json()["version"] and rules["30005"]["action"] == P.HOLD_FOR_REVIEW
    assert rules["30006"]["action"] == P.HOLD_FOR_REVIEW        # the default, the old version is inactive
    db_session.refresh(item)
    assert item.open_lead_id == lead.id and item.policy_version == P.DEFAULT_VERSION


def test_backfill_turns_the_old_terminal_stop_into_a_reviewable_hold_once(db_session, sample_org, sample_advisor):
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554016")
    st = _due(db_session, lead, touch=1)
    st.status = "stopped_bad_number"
    db_session.commit()
    _msg(db_session, lead, sample_advisor, sid="SMold", hours_ago=60)
    first = R.backfill(db_session, sample_org.id)
    again = R.backfill(db_session, sample_org.id)
    db_session.refresh(st)
    assert first["opened"] == 1 and first["converted"] == 1
    assert again["opened"] == 0 and again["converted"] == 0
    assert st.status == "active" and st.next_touch_due_at > datetime.utcnow()
    assert len(_items(db_session, lead)) == 1
    # a family whose later text WAS delivered is not held
    ok = _lead(db_session, sample_org, sample_advisor, phone="12145554017")
    _msg(db_session, ok, sample_advisor, sid="SMold2", hours_ago=60)
    _msg(db_session, ok, sample_advisor, sid="SMok2", status="delivered", code=None, hours_ago=10)
    R.backfill(db_session, sample_org.id)
    assert _items(db_session, ok) == []


def test_held_families_are_not_reported_late_by_the_self_check(db_session, sample_org, sample_advisor):
    from app.services.programs import self_check
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554018")
    st = _due(db_session, lead, touch=1, minutes_ago=180)
    R.record_failure(db_session, _msg(db_session, lead, sample_advisor, sid="SMsc"), source="callback")
    db_session.commit()
    res = self_check.texts_overdue(db_session, sample_org.id, datetime.utcnow())
    assert "Cad Ence" not in " ".join(res.get("people") or [])


# ── the queue API ───────────────────────────────────────────────────────────

def test_queue_is_tenant_scoped_masked_and_actions_are_audited(client, db_session, sample_org, sample_advisor):
    from app.models.models import AuditLogEntry
    admin = _admin(db_session, sample_org, email="q-admin@example.com")
    lead = _lead(db_session, sample_org, sample_advisor, phone="12145554019")
    item = R.record_failure(db_session, _msg(db_session, lead, sample_advisor, sid="SMqueue123456"),
                            source="callback")
    w = _lead(db_session, sample_org, sample_advisor, phone="12145554020")
    _msg(db_session, w, sample_advisor, sid="SMwatch", code="30003")
    db_session.commit()
    r = client.get("/sms-recovery/queue", headers=_h(db_session, admin))
    assert r.status_code == 200
    body = r.json()
    rows = {x["lead_id"]: x for x in body["rows"]}
    assert rows[lead.id]["kind"] == "held" and rows[w.id]["kind"] == "watch"
    assert rows[lead.id]["phone"] == "•••-4019" and "12145554019" not in r.text
    assert "SMqueue123456" not in r.text                         # only the SID tail on screen
    m = body["metrics"]
    assert m["families_affected"] == 2 and m["failed_by_code"] == {"30006": 1, "30003": 1}
    assert m["holds_open"] == 1
    assert client.get("/sms-recovery/queue?code=30003", headers=_h(db_session, admin)).json()["rows"][0]["lead_id"] == w.id

    other = Organization(name="Other Tenant", slug="other-tenant-482")
    db_session.add(other)
    db_session.commit()
    stranger = _admin(db_session, other, email="stranger@example.com")
    assert client.get("/sms-recovery/queue", headers=_h(db_session, stranger)).json()["rows"] == []
    assert client.post("/sms-recovery/items/%s/action" % item.id, headers=_h(db_session, stranger),
                       json={"action": "request_review"}).status_code == 404
    assert client.get("/sms-recovery/families/%s/history" % lead.id,
                      headers=_h(db_session, stranger)).status_code == 404

    r = client.post("/sms-recovery/items/%s/action" % item.id, headers=_h(db_session, admin),
                    json={"action": "release"})
    assert r.status_code == 409                                  # evidence first
    assert client.post("/sms-recovery/items/%s/action" % item.id, headers=_h(db_session, admin),
                       json={"action": "mark_capable", "note": "Family confirmed"}).status_code == 200
    assert client.post("/sms-recovery/items/%s/action" % item.id, headers=_h(db_session, admin),
                       json={"action": "release"}).status_code == 200
    assert db_session.query(AuditLogEntry).filter(AuditLogEntry.action.like("sms_recovery.%")).count() >= 2
    csv = client.get("/sms-recovery/evidence.csv", headers=_h(db_session, admin))
    assert csv.status_code == 200 and "SMqueue123456" in csv.text and "12145554019" not in csv.text
