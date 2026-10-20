"""PIPELINE HEALTH (Phase Nine): one read-only report that says what failed,
where, and what to do - stage funnel counts plus a problems list - with no
family PII, one tenant only, managers (and view-only spectators) only."""
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.models import BookingLink, Organization, Reply, User
from app.models.program_models import (
    LocationProfile, ProgramAlert, ProgramConversation, ProgramConversationTurn, ProgramResponse,
    ProgramUnmatchedReply,
)
from app.services.programs import pipeline_health as PH
from app.services.programs import setup
from app.services.workspace_access import grant_workspace_membership
from tests.test_alert_dedupe import _reply, wired  # noqa: F401 (fixture)
from tests.test_outreach_program import _h, _lead_for, hash_password, program  # noqa: F401 (fixture)

# Tuesday 11:00 Central / noon Eastern: inside contact hours for a 214 number.
NOW = datetime(2030, 1, 8, 17, 0)
NIGHT = datetime(2030, 1, 8, 7, 0)          # 1:00 Central - contact hours closed

FAMILY_STRINGS = ("Ollie", "OLLIE", "Testcase", "TESTCASE", "2145550101", "ollie@example.com",
                  "Thursday afternoon", "+18435550000", "owner@example.com", "+14105550100")


def _loc(db, org):
    return (db.query(LocationProfile)
            .filter_by(organization_id=org.id, is_review_bucket=False).first().location_id)


def _resp(db, org, lead, *, at=NOW - timedelta(hours=1), cls="active", status="new", loc="__auto__", **kw):
    r = ProgramResponse(organization_id=org.id, lead_id=lead.id, channel="sms", response_class=cls,
                        body_excerpt="Ollie here, call me at 2145550101 Thursday afternoon",
                        summary="Ollie Testcase wrote", received_at=at, handling_status=status,
                        location_id=_loc(db, org) if loc == "__auto__" else loc, **kw)
    db.add(r)
    db.commit()
    return r


def _conv(db, org, lead, state="ai_active", at=NOW - timedelta(hours=1), **kw):
    c = ProgramConversation(organization_id=org.id, lead_id=lead.id, state=state, updated_at=at,
                            last_family_at=at, **kw)
    db.add(c)
    db.commit()
    return c


def _turn(db, conv, *, role="ai", delivery=None, at=NOW - timedelta(hours=1), reason=None, action="answer"):
    t = ProgramConversationTurn(conversation_id=conv.id, organization_id=conv.organization_id,
                                lead_id=conv.lead_id, role=role, delivery=delivery, created_at=at,
                                reason=reason, action=action, text="Hi Ollie, see you Thursday afternoon")
    db.add(t)
    db.commit()
    return t


def _alert(db, org, lead, *, state, at=NOW - timedelta(hours=1), channel="sms", attempts=1, **kw):
    a = ProgramAlert(organization_id=org.id, lead_id=lead.id, kind="active", audience="primary",
                     channel=channel, recipient="+18435550000", send_state=state, attempts=attempts,
                     created_at=at, last_attempt_at=at, message="SCI Reply | Ollie Testcase",
                     dedupe_key="k:%s" % uuid.uuid4().hex, **kw)
    db.add(a)
    db.commit()
    return a


def _good_config(db, prog, advisor, monkeypatch):
    prog.booking_user_id = advisor.id
    prog.staff_sms_alerts_enabled = True
    prog.alert_phone, prog.alert_email = "+18435550000", "owner@example.com"
    db.commit()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")


def _by_key(rep):
    return {p["key"]: p for p in rep["problems"]}


# ── funnel ──────────────────────────────────────────────────────────────────

def test_funnel_counts_each_stage(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    b = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat", email="p@example.com")
    t = _lead_for(db, org, sample_advisor, "L004", phone="2145550104", first="Synth", email="s@example.com")
    t.is_test = True
    db.commit()
    for lead in (a, b, t):                                       # 3 inbound, the test one not counted
        rp = Reply(lead_id=lead.id, body="hello", received_at=NOW - timedelta(hours=2))
        db.add(rp)
        db.commit()
        _resp(db, org, lead, reply_id=rp.id, at=NOW - timedelta(hours=2))
    db.add(Reply(lead_id=a.id, body="old", received_at=NOW - timedelta(days=3)))     # outside the window
    db.add(ProgramUnmatchedReply(organization_id=org.id, received_at=NOW - timedelta(hours=1), status="handled"))
    db.commit()
    _resp(db, org, b, cls="hot", status="new", at=NOW - timedelta(minutes=5), sla_due_at=NOW + timedelta(minutes=10))
    _resp(db, org, a, status="closed", closed_at=NOW - timedelta(minutes=30))
    ca = _conv(db, org, a, state="human")
    _conv(db, org, b, state="booked")
    _turn(db, ca, delivery="sent")
    _turn(db, ca, delivery="simulated")
    _turn(db, ca, delivery="draft")                              # a person sends a draft: not "answered"
    _turn(db, ca, delivery="held", at=NOW - timedelta(minutes=5))
    _alert(db, org, a, state="sent", delivered=True, channel="email")
    _alert(db, org, a, state="sent", delivered=False, provider_status="queued")
    _alert(db, org, a, state="failed", reason="provider down")
    _alert(db, org, a, state="merged")
    _alert(db, org, a, state="skipped")
    db.add(ProgramAlert(organization_id=org.id, lead_id=a.id, kind="hot", audience="primary", channel="in_app",
                        send_state=None, created_at=NOW - timedelta(hours=1)))          # in-app is not a page
    db.commit()

    rep = PH.report(db, prog, now=NOW)
    f = rep["funnel"]
    assert f["received"] == 3                                    # 2 real replies + 1 unmatched
    assert f["processed"] == 4                                   # 2 replies + hot + closed (test contact left out)
    assert f["ai_answered"] == 2
    assert f["human_action_required"] == 2                       # a (human conv) + b (hot unopened)
    assert f["notification_attempted"] == 3
    assert f["notification_delivered"] == 1
    assert f["notification_failed"] == 1
    assert f["completed"] == 2                                   # one closed reply + one booked conversation
    d = rep["funnel_detail"]
    assert d["received_unmatched"] == 1 and d["notification_merged"] == 1 and d["notification_skipped"] == 1
    assert d["notification_sent_awaiting_confirmation"] == 1
    assert rep["window_hours"] == 24 and rep["since"] == "2030-01-07T17:00:00Z"


def test_window_is_clamped_and_respected(program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    a = _lead_for(db, org, sample_advisor, "L001")
    _resp(db, org, a, at=NOW - timedelta(hours=30))
    assert PH.report(db, prog, now=NOW)["funnel"]["processed"] == 0
    assert PH.report(db, prog, now=NOW, hours=48)["funnel"]["processed"] == 1
    assert PH.report(db, prog, now=NOW, hours=10 ** 6)["window_hours"] == PH.MAX_WINDOW_HOURS


# ── every problem is detected ───────────────────────────────────────────────

def test_alert_stuck_in_sending(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    old = _alert(db, org, a, state="sending", at=NOW - timedelta(minutes=25))
    _alert(db, org, a, state="sending", at=NOW - timedelta(minutes=3))                # still in flight: fine
    p = _by_key(PH.report(db, prog, now=NOW))["alerts_stuck_sending"]
    assert p["count"] == 1 and p["example_ids"] == [old.id] and p["id_type"] == "alert"
    assert p["stage"] == "notification" and p["severity"] == "high" and p["action"]
    assert p["oldest_at"] == "2030-01-08T16:35:00Z"


def test_failed_alerts_at_max_attempts_and_carrier_undelivered(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    final = _alert(db, org, a, state="failed", attempts=3, at=NOW - timedelta(minutes=30),
                   reason="Twilio 21211: invalid To number +12145550101")
    _alert(db, org, a, state="failed", attempts=1, at=NOW - timedelta(minutes=5))      # retry still coming
    und = _alert(db, org, a, state="sent", provider_status="undelivered", provider_error="30007")
    probs = _by_key(PH.report(db, prog, now=NOW))
    assert probs["alerts_failed_final"]["example_ids"] == [final.id]
    assert probs["alerts_failed_final"]["count"] == 1
    assert "2145550101" not in json.dumps(probs["alerts_failed_final"])               # reason masked
    assert probs["alerts_failed_final"]["reasons"] == ["invalid or non-mobile phone number"]
    assert probs["alerts_undelivered"]["reasons"] == ["the carrier filtered the text"]
    assert probs["alerts_undelivered"]["example_ids"] == [und.id]


def test_held_ai_turn_flagged_only_when_hours_are_open(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    c = _conv(db, org, a)
    held = _turn(db, c, delivery="held", at=NOW - timedelta(minutes=45))
    _turn(db, c, delivery="held", at=NOW - timedelta(minutes=10))                     # not 30 min yet
    p = _by_key(PH.report(db, prog, now=NOW))["ai_turns_held_too_long"]
    assert p["count"] == 1 and p["example_ids"] == [held.id] and p["id_type"] == "turn"
    _turn(db, c, delivery="held", at=NIGHT - timedelta(hours=2))
    assert "ai_turns_held_too_long" not in _by_key(PH.report(db, prog, now=NIGHT))    # quiet hours: waiting is right


def test_reasons_are_fixed_labels_never_stored_text():
    assert PH.reason_label("refused for Ollie Testcase at 2145550101") == "a safety rule refused it"
    assert PH.reason_label("Ollie said something odd") == "other - open the record to see the reason"
    assert PH.reason_label("RateLimitError") == "RateLimitError"
    assert PH.reason_label("Twilio error 21610 for +12145550101") == "the family has opted out"
    assert PH.reason_label("code: 12345") == "provider error code 12345"
    assert PH.reason_label(None) is None


def test_blocked_turns_and_ai_provider_failures(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    c = _conv(db, org, a)
    b = _turn(db, c, delivery="blocked", reason="carrier refused send to +12145550101")
    u = _turn(db, c, role="system", action="handoff", reason="AI unavailable: RateLimitError quota")
    probs = _by_key(PH.report(db, prog, now=NOW))
    assert probs["ai_turns_blocked"]["example_ids"] == [b.id]
    assert "2145550101" not in json.dumps(probs["ai_turns_blocked"])
    assert probs["ai_provider_failures"]["example_ids"] == [u.id]


def test_ai_breaker_open_is_reported(program, sample_advisor, monkeypatch):
    db, prog = program["db"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    from app.services import ai_gateway
    monkeypatch.setattr(ai_gateway._BUDGET, "snapshot",
                        lambda: {"breaker_open_for_s": 300, "breaker_reason": "AuthenticationError"})
    p = _by_key(PH.report(db, prog, now=NOW))["ai_provider_paused"]
    assert p["stage"] == "ai_answer" and p["reasons"] == ["AuthenticationError"]


def test_hot_reply_past_sla_still_new(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    late = _resp(db, org, a, cls="hot", at=NOW - timedelta(minutes=40), sla_due_at=NOW - timedelta(minutes=25))
    _resp(db, org, a, cls="hot", at=NOW - timedelta(minutes=5), sla_due_at=NOW + timedelta(minutes=10))
    _resp(db, org, a, cls="hot", status="opened", at=NOW - timedelta(hours=2), sla_due_at=NOW - timedelta(hours=1))
    p = _by_key(PH.report(db, prog, now=NOW))["hot_past_sla"]
    assert p["count"] == 1 and p["example_ids"] == [late.id] and p["id_type"] == "response"
    assert p["location_ids"] == [_loc(db, org)]


def test_handoff_with_no_alert_row(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    b = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat", email="p@example.com")
    c = _lead_for(db, org, sample_advisor, "L004", phone="2145550104", first="Dana", email="d@example.com")
    ra = _resp(db, org, a)
    _conv(db, org, a, state="human", handoff_reason="needs a person")                 # nobody paged
    _conv(db, org, b, state="human", handoff_reason="needs a person")
    _alert(db, org, b, state="sent", at=NOW - timedelta(minutes=55))                  # paged: fine
    _conv(db, org, c, state="human", owner_changed_by=sample_advisor.id)               # a person took it: fine
    p = _by_key(PH.report(db, prog, now=NOW))["handoff_without_alert"]
    assert p["count"] == 1 and p["example_ids"] == [ra.id]


def test_booked_with_no_calendar_event_only_when_calendar_connected(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    bk = BookingLink(lead_id=a.id, user_id=sample_advisor.id, status="booked", calendar_event_id=None,
                     booked_time=NOW + timedelta(days=1), created_at=NOW - timedelta(hours=3))
    db.add(bk)
    db.add(BookingLink(lead_id=a.id, user_id=sample_advisor.id, status="booked", calendar_event_id="evt1",
                       created_at=NOW - timedelta(hours=3)))
    db.commit()
    assert "booked_without_calendar_event" not in _by_key(PH.report(db, prog, now=NOW))   # no calendar provider
    sample_advisor.google_calendar_connected = True
    db.commit()
    rep = PH.report(db, prog, now=NOW)
    assert rep["configuration"]["calendar_connected"] is True
    p = _by_key(rep)["booked_without_calendar_event"]
    assert p["count"] == 1 and p["example_ids"] == [bk.id] and p["id_type"] == "booking"


def test_location_mapping_unprocessed_and_unmatched_replies(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    review = db.query(LocationProfile).filter_by(organization_id=org.id, is_review_bucket=True).one().location_id
    none = _resp(db, org, a, loc=None)
    bucket = _resp(db, org, a, loc=review)
    _resp(db, org, a)                                                                   # mapped: fine
    lost = Reply(lead_id=a.id, body="Ollie here", received_at=NOW - timedelta(hours=1))
    db.add(lost)
    db.add(Reply(lead_id=a.id, body="just now", received_at=NOW - timedelta(minutes=1)))   # still being processed
    un = ProgramUnmatchedReply(organization_id=org.id, status="open", from_address="ollie@example.com",
                               body_excerpt="Ollie Thursday afternoon", created_at=NOW - timedelta(hours=1),
                               received_at=NOW - timedelta(hours=1))
    db.add(un)
    db.commit()
    probs = _by_key(PH.report(db, prog, now=NOW))
    assert set(probs["responses_without_location"]["example_ids"]) == {none.id, bucket.id}
    assert probs["replies_not_processed"]["example_ids"] == [lost.id]
    assert probs["unmatched_replies_open"]["example_ids"] == [un.id]


def test_missing_configuration_is_reported_as_booleans(program, monkeypatch):
    db, prog = program["db"], program["prog"]
    prog.booking_user_id = None
    prog.staff_sms_alerts_enabled = False
    prog.alert_phone = prog.alert_email = None
    prog.ai_setter_mode = "auto"
    db.commit()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rep = PH.report(db, prog, now=NOW)
    assert rep["configuration"] == {"booking_user_set": False, "calendar_connected": False, "staff_alerts_on": False,
                                    "alert_phone_set": False, "alert_email_set": False, "ai_setter_mode": "auto",
                                    "openai_key_present": False}
    keys = set(_by_key(rep))
    assert {"config_no_booking_user", "config_staff_alerts_off", "config_no_alert_recipient",
            "config_no_ai_key"} <= keys
    assert rep["ok"] is False
    assert rep["problems"][0]["severity"] == "high"                                    # worst first


def test_clean_program_is_ok(program, sample_advisor, monkeypatch):
    db, prog = program["db"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    rep = PH.report(db, prog, now=NOW)
    assert rep["ok"] is True and rep["problems"] == []


def test_test_contacts_are_left_out(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    t = _lead_for(db, org, sample_advisor, "L001")
    t.is_test = True
    db.commit()
    _resp(db, org, t, cls="hot", at=NOW - timedelta(hours=1), sla_due_at=NOW - timedelta(minutes=30))
    _alert(db, org, t, state="sending", at=NOW - timedelta(minutes=30))
    rep = PH.report(db, prog, now=NOW)
    assert rep["problems"] == [] and rep["funnel"]["processed"] == 0


# ── no PII, through the real reply path and the API ─────────────────────────

def test_no_family_pii_in_the_json(wired, sample_advisor, client, monkeypatch):
    db, org, prog = wired["db"], wired["org"], wired["prog"]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    lead = _lead_for(db, org, sample_advisor, "L001")
    resp = _reply(db, lead, "Can we meet Thursday afternoon? Call 2145550101 or ollie@example.com", "SM_PII")
    now = datetime.utcnow()
    c = _conv(db, org, lead, state="human", at=now, handoff_reason="Ollie Testcase is upset")
    _turn(db, c, delivery="blocked", at=now - timedelta(minutes=5),
          reason="refused for Ollie Testcase at 2145550101 ollie@example.com")
    for a in db.query(ProgramAlert).filter(ProgramAlert.channel == "sms").limit(1):
        a.send_state, a.attempts, a.reason = "failed", 3, "bad number 2145550101 for ollie@example.com"
        a.created_at = now - timedelta(hours=3)
    resp.response_class, resp.sla_due_at = "hot", now - timedelta(minutes=5)
    db.commit()
    r = client.get("/program/command/pipeline-health", headers=_h(db, wired["admin"]))
    assert r.status_code == 200, r.text
    body = r.text
    data = r.json()
    assert data["funnel"]["received"] >= 1 and data["funnel"]["processed"] >= 1
    assert data["funnel"]["notification_attempted"] >= 1
    assert {"hot_past_sla", "ai_turns_blocked", "alerts_failed_final"} <= {p["key"] for p in data["problems"]}
    for s in FAMILY_STRINGS:
        assert s not in body, s
    assert "upset" not in body and "Thursday" not in body
    for p in data["problems"]:
        assert set(p) <= {"key", "stage", "severity", "what", "count", "oldest_at", "example_ids", "id_type",
                          "action", "reasons", "location_ids"}


# ── tenant isolation & role gating ──────────────────────────────────────────

def _other_tenant(db):
    other = Organization(name="Other Co", slug="other-co-%s" % uuid.uuid4().hex[:6])
    db.add(other)
    db.commit()
    oprog = setup.ensure_program(db, other, name="Other Program")
    adv = User(organization_id=other.id, email="o-%s@example.com" % uuid.uuid4().hex[:6],
               password_hash=hash_password("Pass12345!"), full_name="Other", role="advisor", is_active=True)
    db.add(adv)
    db.commit()
    from app.models.models import Lead
    lead = Lead(organization_id=other.id, assigned_to_id=adv.id, first_name="Zed", last_name="Other",
                phone="12145559999", status="sent")
    db.add(lead)
    db.commit()
    return other, oprog, lead


def test_never_cross_tenant(program, sample_advisor, client, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    other, oprog, olead = _other_tenant(db)
    db.add(ProgramResponse(organization_id=other.id, lead_id=olead.id, channel="sms", response_class="hot",
                           handling_status="new", received_at=NOW - timedelta(hours=1),
                           sla_due_at=NOW - timedelta(minutes=30)))
    db.add(ProgramAlert(organization_id=other.id, lead_id=olead.id, kind="hot", audience="primary", channel="sms",
                        recipient="+15555550000", send_state="sending", attempts=1,
                        created_at=NOW - timedelta(minutes=30)))
    db.add(Reply(lead_id=olead.id, body="hi", received_at=NOW - timedelta(hours=1)))
    db.commit()
    rep = PH.report(db, prog, now=NOW)
    assert rep["problems"] == [] and sum(rep["funnel"].values()) == 0
    assert {p["key"] for p in PH.report(db, oprog, now=NOW)["problems"]} >= {"hot_past_sla", "alerts_stuck_sending"}
    # Naming another workspace without a seat in it is ignored: the caller only
    # ever gets their own workspace's report, never the other tenant's.
    real = datetime.utcnow()
    db.add(ProgramResponse(organization_id=other.id, lead_id=olead.id, channel="sms", response_class="hot",
                           handling_status="new", received_at=real - timedelta(hours=1),
                           sla_due_at=real - timedelta(minutes=30)))
    db.add(ProgramAlert(organization_id=other.id, lead_id=olead.id, kind="hot", audience="primary", channel="sms",
                        recipient="+15555550001", send_state="sending", attempts=1,
                        created_at=real - timedelta(minutes=30)))
    db.commit()
    assert {"hot_past_sla", "alerts_stuck_sending"} <= {p["key"] for p in PH.report(db, oprog)["problems"]}
    h = _h(db, program["admin"])
    assert not {"hot_past_sla", "alerts_stuck_sending"} & {
        p["key"] for p in client.get("/program/command/pipeline-health", headers=h).json()["problems"]}
    r = client.get("/program/command/pipeline-health", headers=dict(h, **{"X-Workspace-Id": other.id}))
    if r.status_code == 200:
        assert not {"hot_past_sla", "alerts_stuck_sending"} & {p["key"] for p in r.json()["problems"]}
        assert r.json()["funnel"]["processed"] == 0
    else:
        assert r.status_code in (403, 404)
    # The other tenant's own advisor (not a manager) is refused outright.
    oadv = db.query(User).filter(User.organization_id == other.id).first()
    assert client.get("/program/command/pipeline-health", headers=_h(db, oadv)).status_code == 403


def test_role_gating(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    assert client.get("/program/command/pipeline-health").status_code in (401, 403)
    assert client.get("/program/command/pipeline-health", headers=_h(db, program["admin"])).status_code == 200
    r = client.get("/program/command/pipeline-health", headers=_h(db, sample_advisor))
    assert r.status_code == 403                                   # an advisor is not a manager
    spect = User(organization_id=None, email="richard.view2@example.com", password_hash=hash_password("Pass12345!"),
                 full_name="Richard", role="advisor", is_active=True, must_change_password=False)
    db.add(spect)
    db.commit()
    grant_workspace_membership(db, spect.id, org.id, role="spectator", check_capacity=False)
    h = dict(_h(db, spect), **{"X-Workspace-Id": org.id})
    assert client.get("/program/command/pipeline-health?hours=6", headers=h).status_code == 200   # read-only seat
    assert client.get("/program/command/pipeline-health?hours=0", headers=_h(db, program["admin"])).status_code == 422


# ── independent-review follow-ups (Oct 20) ──────────────────────────────────

def test_handoff_check_ignores_staff_takeovers_and_counts_only_real_pages(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    b = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat", email="p@example.com")
    _resp(db, org, a)
    rb = _resp(db, org, b)
    _conv(db, org, a, state="human", handoff_reason="taken by staff@example.com")      # staff took it over
    _conv(db, org, b, state="human", handoff_reason="needs a person")
    _alert(db, org, b, state="sent", channel="in_app", at=NOW - timedelta(minutes=55))  # only the in-app notice
    p = _by_key(PH.report(db, prog, now=NOW))["handoff_without_alert"]
    assert p["count"] == 1 and p["example_ids"] == [rb.id]


def test_policy_blocked_turns_are_not_listed_as_answer_them_yourself(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    c = _conv(db, org, a)
    _turn(db, c, delivery="blocked", reason="Lead x is marked DNC (likely a duplicate) - blocked from sending.")
    _turn(db, c, delivery="blocked", reason="CAMPAIGN OFF: this contact's campaign is not switched on")
    assert "ai_turns_blocked" not in _by_key(PH.report(db, prog, now=NOW))


def test_booking_check_only_looks_at_the_connected_calendar(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    sample_advisor.google_calendar_connected = True
    other = User(organization_id=org.id, email="other.adv@example.com", password_hash=hash_password("Pass12345!"),
                 full_name="Other", role="advisor", is_active=True, must_change_password=False)
    db.add(other)
    db.commit()
    a = _lead_for(db, org, sample_advisor, "L001")
    db.add(BookingLink(lead_id=a.id, user_id=other.id, status="booked", calendar_event_id=None,
                       booked_time=NOW + timedelta(days=1), created_at=NOW - timedelta(hours=3)))
    db.commit()
    assert "booked_without_calendar_event" not in _by_key(PH.report(db, prog, now=NOW))


def test_hot_replies_older_than_a_month_are_not_shown_forever(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    _good_config(db, prog, sample_advisor, monkeypatch)
    a = _lead_for(db, org, sample_advisor, "L001")
    _resp(db, org, a, cls="hot", at=NOW - timedelta(days=45), sla_due_at=NOW - timedelta(days=45))
    assert "hot_past_sla" not in _by_key(PH.report(db, prog, now=NOW))
