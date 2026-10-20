"""SCI Live advisor workflow fixes (Oct 20 audit): answering / closing a
family's newest reply settles their older ones; a home-visit request survives a
change of owner; needs-attention counts only unanswered replies; STOP closes
opened replies; the legacy hand-over goes through set_owner; scheduling marks
replies answered and books the conversation; tasks follow the location filter;
assign / schedule / close are audited."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.models import AuditLogEntry
from app.models.program_models import ProgramConversation, ProgramResponse
from app.services.programs import ai_setter as S
from app.services.programs import appointments as A
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import setter  # noqa: F401 (fixture)
from tests.test_sci_home_visit_queue import _home_request

ROOT = Path(__file__).resolve().parents[1]
SLOT = datetime(2030, 1, 8, 10, 0)


def _in(db, lead, text, minutes_ago, channel="sms"):
    with patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        return R.on_inbound(db, lead, text, channel, now=datetime.utcnow() - timedelta(minutes=minutes_ago),
                            run_setter=False)


def _audits(db, action):
    return db.query(AuditLogEntry).filter(AuditLogEntry.action == action).all()


# ── fix 2 + fix 8 (close audit) ─────────────────────────────────────────────

def test_closing_the_newest_reply_closes_the_older_open_ones(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    old1 = _in(db, lead, "Can I schedule a visit this week?", 30)
    old2 = _in(db, lead, "Hello? Any times open?", 20)
    R.mark(db, old2, "opened", program["admin"])
    newest = _in(db, lead, "Thursday works for me", 10)
    h = _h(db, program["admin"])
    r = client.post("/program/responses/%s/mark" % newest.id, headers=h, json={"state": "closed"})
    assert r.status_code == 200 and r.json()["status"] == "closed"
    for x in (old1, old2, newest):
        db.refresh(x)
        assert x.handling_status == "closed" and x.closed_at is not None
        assert x.opened_by == program["admin"].id or x.id == old2.id
    # never past SLA, never back in the inbox
    assert R.sla_sweep(db, now=datetime.utcnow() + timedelta(hours=2)) == []
    assert client.get("/program/command", headers=h).json()["inbox"] == []
    entry = _audits(db, "sci.response_closed")
    assert len(entry) == 1 and entry[0].target_id == newest.id
    assert program["admin"].id in (entry[0].details or "")


def test_marking_responded_never_moves_older_backwards_or_touches_later_ones(program, sample_advisor):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    older = _in(db, lead, "Can I schedule a visit?", 30)
    R.mark(db, older, "active", admin)
    mid = _in(db, lead, "Is Friday open?", 20)
    later = _in(db, lead, "Also, what about Saturday?", 5)
    R.mark(db, mid, "responded", admin)
    for x in (older, mid, later):
        db.refresh(x)
    assert older.handling_status == "active"           # never backwards
    assert mid.handling_status == "responded"
    assert later.handling_status == "new"              # a reply after it is still waiting


def test_sms_reply_from_sci_live_answers_every_earlier_reply(program, sample_advisor, client):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    old = _in(db, lead, "Can I schedule a visit?", 30)
    newest = _in(db, lead, "Hello?", 10)
    sent = MagicMock(id="m1", twilio_sid="SM1", twilio_status="queued", sent_at=None)
    with patch("app.services.sms_service.send_sms", return_value=sent):
        r = client.post("/program/command/reply", headers=_h(db, program["admin"]),
                        json={"lead_id": lead.id, "channel": "sms", "text": "Thursday at 10 works.",
                              "idempotency_key": "k1", "response_id": newest.id})
    assert r.status_code == 200, r.text
    assert r.json()["response_status"] == "responded"
    for x in (old, newest):
        db.refresh(x)
        assert x.handling_status == "responded" and x.responded_at is not None


# ── fix 3 ────────────────────────────────────────────────────────────────────

def _home_tasks(home):
    return [t for t in home["tasks"] if t["kind"] == "home_visit"]


def test_taking_or_returning_the_conversation_keeps_the_home_visit_task(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    lead = _home_request(db, org, sample_advisor)
    conv = S.conversation_for(db, lead, create=False)
    assert S.memory_of(conv)["home_visit"]["requested_at"]
    h = _h(db, admin)
    for owner in ("human", "ai"):
        assert client.post("/program/command/handling", headers=h,
                           json={"lead_id": lead.id, "owner": owner}).status_code == 200
        db.refresh(conv)
        assert conv.handoff_reason != S.HOME_VISIT_REASON
        home = client.get("/program/command", headers=h).json()
        assert [t["lead_id"] for t in _home_tasks(home)] == [lead.id], owner
        fam = client.get("/program/command/family/%s" % lead.id, headers=h).json()
        assert fam["home_visit"]["waiting"] is True
    # Arranged: gone. Opted out: never waiting.
    assert client.post("/program/command/home-visit", headers=h, json={"lead_id": lead.id}).status_code == 200
    assert not _home_tasks(client.get("/program/command", headers=h).json())


def test_home_visit_never_waits_once_stopped_or_on_an_older_conversation(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]
    lead = _home_request(db, org, sample_advisor)
    conv = S.conversation_for(db, lead, create=False)
    h = _h(db, admin)
    conv.state = "stopped"
    db.commit()
    assert not _home_tasks(client.get("/program/command", headers=h).json())
    conv.state = "human"
    db.commit()
    newer = ProgramConversation(organization_id=org.id, lead_id=lead.id, state="ai_active",
                                created_at=datetime.utcnow() + timedelta(minutes=5))
    db.add(newer)
    db.commit()
    assert not _home_tasks(client.get("/program/command", headers=h).json())


def test_old_home_visit_handoff_without_requested_at_still_waits(client, program, sample_advisor):
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    _in(db, lead, "Could someone come to the house?", 5)
    db.add(ProgramConversation(organization_id=org.id, lead_id=lead.id, state="human",
                               handoff_reason=S.HOME_VISIT_REASON, memory=json.dumps({"wants": ["home visit"]})))
    db.commit()
    home = client.get("/program/command", headers=_h(db, program["admin"])).json()
    assert [t["lead_id"] for t in _home_tasks(home)] == [lead.id]
    assert home["inbox"][0]["home_visit"] is True


# ── fix 4 ────────────────────────────────────────────────────────────────────

def test_needs_attention_skips_replies_already_answered(program, sample_advisor, client):
    db, org, admin = program["db"], program["org"], program["admin"]
    a = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    b = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat", email="pat@example.com")
    _in(db, a, "Can I schedule a visit this week?", 10)
    rb = _in(db, b, "Can I schedule a visit this week?", 10)
    R.mark(db, rb, "responded", admin)
    r = client.get("/program/command", headers=_h(db, admin)).json()
    assert r["counts"]["needs_attention"] == 1 == r["filter_counts"]["needs_reply"]


# ── fix 5 / 7 / 13 (frontend source) ────────────────────────────────────────

def test_frontend_snooze_is_mark_opened_and_responses_reply_resolves():
    cmd = (ROOT / "frontend/src/pages/program/command/Command.jsx").read_text()
    assert "Snoozed" not in cmd and "'Mark opened'" in cmd and "Marked as opened." in cmd
    rsp = (ROOT / "frontend/src/pages/program/sci/Responses.jsx").read_text()
    assert "'/sms/send'" not in rsp and "'/program/command/reply'" in rsp
    assert "status: state }" not in rsp and "d?.status" in rsp and "response_status" in rsp


# ── fix 6 ────────────────────────────────────────────────────────────────────

def test_stop_after_an_opened_reply_closes_it(program, sample_advisor, client):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    hot = _in(db, lead, "Can I schedule a visit this week?", 30)
    R.mark(db, hot, "opened", admin)
    _in(db, lead, "STOP", 1)
    db.refresh(hot)
    assert hot.handling_status == "closed" and hot.closed_at is not None
    assert client.get("/program/command", headers=_h(db, admin)).json()["inbox"] == []


# ── fix 9 ────────────────────────────────────────────────────────────────────

def test_legacy_hand_goes_through_set_owner_and_is_audited(program, sample_advisor, client):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    conv = S.conversation_for(db, lead)
    conv.next_followup_at = datetime.utcnow() + timedelta(hours=3)
    db.commit()
    h = _h(db, admin)
    r = client.post("/program/conversations/%s/hand" % lead.id, headers=h, json={"state": "human"})
    assert r.status_code == 200, r.text
    db.refresh(conv)
    assert conv.state == "human" and conv.next_followup_at is None and conv.owner_changed_by == admin.id
    assert len(_audits(db, "sci.conversation_owner")) == 1
    conv.state = "stopped"
    db.commit()
    r = client.post("/program/conversations/%s/hand" % lead.id, headers=h, json={"state": "ai_active"})
    assert r.status_code == 409
    db.refresh(conv)
    assert conv.state == "stopped"
    conv.state = "human"
    lead.status = "dnc"
    db.commit()
    assert client.post("/program/conversations/%s/hand" % lead.id, headers=h,
                       json={"state": "ai_active"}).status_code == 409


# ── fix 10 + fix 8 (schedule / assign audit) ────────────────────────────────

def test_schedule_answers_replies_books_the_conversation_and_is_audited(program, sample_advisor, client):
    db, org, prog, admin = program["db"], program["org"], program["prog"], program["admin"]
    prog.booking_user_id = sample_advisor.id
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    old = _in(db, lead, "Can I schedule a visit?", 30)
    newest = _in(db, lead, "Any time next week", 10)
    assert S.conversation_for(db, lead, create=False) is None
    with patch.object(A, "book", return_value={"ok": True, "status": "confirmed", "booking_id": "b1"}), \
         patch.object(A, "notify_team_booked"):
        r = client.post("/program/command/schedule", headers=_h(db, admin),
                        json={"lead_id": lead.id, "slot": SLOT.isoformat(), "kind": "visit"})
    assert r.status_code == 200 and r.json()["ok"] is True
    conv = S.conversation_for(db, lead, create=False)
    assert conv is not None and conv.state == "booked" and conv.booked_time == SLOT and conv.booking_link_id == "b1"
    for x in (old, newest):
        db.refresh(x)
        assert x.handling_status == "responded"
    e = _audits(db, "sci.schedule")
    assert len(e) == 1 and "b1" in e[0].details and SLOT.isoformat() in e[0].details


def test_failed_schedule_changes_nothing(program, sample_advisor, client):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    resp = _in(db, lead, "Can I schedule a visit?", 10)
    with patch.object(A, "book", return_value={"ok": False, "error": "taken"}):
        client.post("/program/command/schedule", headers=_h(db, admin),
                    json={"lead_id": lead.id, "slot": SLOT.isoformat(), "kind": "visit"})
    db.refresh(resp)
    assert resp.handling_status == "new" and not _audits(db, "sci.schedule")


def test_assign_is_audited_with_before_and_after(program, sample_advisor, client):
    db, org, admin = program["db"], program["org"], program["admin"]
    lead = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    h = _h(db, admin)
    assert client.post("/program/command/assign", headers=h, json={"lead_id": lead.id, "user_id": None}).status_code == 200
    e = _audits(db, "sci.assign")
    assert len(e) == 1 and e[0].target_id == lead.id
    assert sample_advisor.id in (e[0].before_state or "") and sample_advisor.id not in (e[0].after_state or "")


# ── fix 11 ───────────────────────────────────────────────────────────────────

def test_tasks_follow_the_location_filter(program, sample_advisor, client):
    db, org, admin = program["db"], program["org"], program["admin"]
    a = _lead_for(db, org, sample_advisor, "L001", first="Lester")
    b = _lead_for(db, org, sample_advisor, "L003", phone="2145550103", first="Pat", email="pat@example.com")
    ra = _in(db, a, "Please mail me the guide", 10)
    rb = _in(db, b, "Please mail me the guide", 10)
    assert ra.location_id and rb.location_id and ra.location_id != rb.location_id
    h = _h(db, admin)
    for lead in (a, b):
        assert client.post("/program/command/mail-task", headers=h, json={"lead_id": lead.id}).status_code == 200
    every = client.get("/program/command", headers=h).json()["tasks"]
    assert {t["lead_id"] for t in every} == {a.id, b.id}
    only_a = client.get("/program/command?location_id=%s" % ra.location_id, headers=h).json()["tasks"]
    assert [t["lead_id"] for t in only_a] == [a.id]


# --- independent-review follow-ups (Oct 20) --------------------------------

def test_dismissing_an_auto_reply_never_closes_an_earlier_hot_question(program, sample_advisor):
    from app.models.program_models import ProgramResponse
    from app.services.programs import responses as R
    from datetime import datetime, timedelta
    db, org = program["db"], program["org"]
    lead = _lead_for(db, org, sample_advisor, "L001")
    t0 = datetime.utcnow() - timedelta(minutes=30)
    hot = R.on_inbound(db, lead, "Can I schedule a visit this week?", "sms", reply_id="RV1", now=t0)
    auto = R.on_inbound(db, lead, "I am out of the office with limited access to email", "sms", reply_id="RV2",
                        now=t0 + timedelta(minutes=1))
    assert hot.response_class == "hot"
    R.mark(db, auto, "closed", sample_advisor)
    db.refresh(hot)
    assert hot.handling_status != "closed"


def test_a_booked_family_has_no_home_visit_waiting(program, sample_advisor):
    from types import SimpleNamespace
    import json as _json
    from app.routers.sci_command_router import _home_visit_waiting
    conv = SimpleNamespace(state="booked", handoff_reason=None, lead_id=None,
                           memory=_json.dumps({"home_visit": {"done": False, "requested_at": "2026-10-20T01:00:00Z"}}))
    assert _home_visit_waiting(conv) is False
    conv.state = "closed"
    assert _home_visit_waiting(conv) is False
