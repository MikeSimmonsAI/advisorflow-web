"""SCI temporary human handoff + automatic AI resume (owner, Nov 4 2026).

A handoff is TEMPORARY: once a person's reply is accepted by the provider the
handoff resolves and the AI owns the family's NEXT message - unless the person
chose "Keep Human Control". A failed send keeps the handoff open; a family
message that lands while the reply is sending stays with the person; nothing
restarts the cadence; STOP / DNC close the conversation. A person's answer to
a handed-off question becomes a redacted learning CANDIDATE; only an approved,
ACTIVE answer - in its own organization and location - ever reaches the AI.

Synthetic families; the AI is a scripted fake; Twilio and Graph are faked.
Nothing is sent."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.handoff_learning_models import ProgramHandoffLearning, ProgramLearningUse
from app.models.models import CadenceState, Message, Organization, User
from app.models.program_models import ProgramConversation, ProgramResponse
from app.routers import sci_command_router as C
from app.services.programs import ai_setter
from app.services.programs import handoff_learning as HL
from app.services.programs import responses as R
from tests.test_outreach_program import _admin, _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, setter  # noqa: F401 (fixture)
from tests.test_sci_live_email import _email_in, _estes, _graph_ok

QUESTION = "Can I talk to somebody about moving my husband's marker to the new section?"


@pytest.fixture
def world(setter, sample_advisor, monkeypatch):
    """Auto mode, contact hours open, a fake SMS provider that records every
    send (and can be told to fail, or to run something mid-send)."""
    db, prog = setter["db"], setter["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    sent, hooks = [], {"fail": None, "during": None}

    def fake_send(db_, adv, lead_, text, *a, **k):
        if hooks["during"]:
            hooks["during"]()
        if hooks["fail"]:
            raise RuntimeError(hooks["fail"])
        m = Message(lead_id=lead_.id, sender_id=adv.id, body=text, send_source=k.get("send_source"),
                    twilio_sid="SM%04d" % len(sent), twilio_status="queued", sent_at=datetime.utcnow())
        db_.add(m)
        db_.flush()
        sent.append({"text": text, "source": k.get("send_source")})
        return m
    monkeypatch.setattr("app.services.sms_service.send_sms", fake_send)
    lead = _lead_for(db, setter["org"], sample_advisor, "L001", first="Marion", last="Testfam")
    lead.is_test = False
    db.commit()
    return {**setter, "lead": lead, "sent": sent, "hooks": hooks, "h": _h(db, setter["admin"])}


def _inbound(w, text, fake=None, channel="sms", **kw):
    with patch.object(ai_setter, "_get_client", return_value=fake or _Fake([])), \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)):
        return R.on_inbound(w["db"], w["lead"], text, channel, **kw)


def _escalate(w, text=QUESTION):
    """The AI is unsure and asks for a person (HUMAN_REQUIRED)."""
    fake = _Fake([_d("handoff", "I'll reach out to you personally.", reason="marker move - needs a person")])
    resp = _inbound(w, text, fake)
    return resp, _conv(w)


def _conv(w):
    c = w["db"].query(ProgramConversation).filter_by(lead_id=w["lead"].id).one()
    w["db"].refresh(c)
    return c


def _reply(w, resp, text="Yes - Kerry can meet you Thursday at 10 to look at the new section.", key="k1",
           then=None, channel="sms"):
    body = {"lead_id": w["lead"].id, "channel": channel, "text": text, "idempotency_key": key,
            "response_id": resp.id if resp is not None else None}
    if then:
        body["then"] = then
    return w["client"].post("/program/command/reply", headers=w["h"], json=body)


def _family(w):
    return w["client"].get("/program/command/family/%s" % w["lead"].id, headers=w["h"]).json()


def _counts(w):
    return w["client"].get("/program/command", headers=w["h"]).json()["filter_counts"]


@pytest.fixture
def client_w(world, client):
    world["client"] = client
    return world


# 1 ── AI escalation ──────────────────────────────────────────────────────────

def test_1_ai_escalates_and_the_family_is_human_response_needed(client_w):
    w = client_w
    resp, conv = _escalate(w)
    assert conv.state == "human" and "marker" in (conv.handoff_reason or "")
    assert C.ownership_state(conv) == "HUMAN_REQUIRED"
    fam = _family(w)
    assert fam["handling"]["ownership"] == "HUMAN_REQUIRED" and fam["handling"]["owner"] == "human"
    assert fam["handoff_message"]["text"] and fam["handoff_message"]["channel"] == "sms"
    assert _counts(w)["handoff"] == 1


# 2 + 4 ── human SMS reply resumes the AI after the provider accepts it ────────

def test_2_4_human_sms_reply_resolves_the_handoff_and_resumes_the_ai(client_w):
    w = client_w
    resp, _ = _escalate(w)
    before = len(w["sent"])
    r = _reply(w, resp)
    assert r.status_code == 200, r.text
    j = r.json()
    assert len(w["sent"]) == before + 1 and j["message"]["sid"]                 # exactly one, provider id kept
    assert j["handoff"] == {"was_handoff": True, "ai_resumed": True, "kept_human": False, "race": False,
                            "note": C.RESUMED_NOTE}
    conv = _conv(w)
    assert conv.state == "ai_active" and ai_setter.owner_of(conv) == "ai"
    assert C.ownership_state(conv) == "AI_RESUMED"
    w["db"].refresh(resp)
    assert resp.handling_status == "responded"
    assert _counts(w)["handoff"] == 0


# 3 ── human EMAIL reply resumes the AI too ───────────────────────────────────

def test_3_human_email_reply_resolves_the_handoff_and_resumes_the_ai(client_w, sample_advisor):
    w = client_w
    db = w["db"]
    w["prog"].ai_setter_mode = "off"
    db.commit()
    lead = _estes(db, w["org"], sample_advisor)
    w["lead"] = lead
    resp = _email_in(db, lead, body="Who do I talk to about my mother's contract?")
    ai_setter.set_owner(db, lead, "human", actor=None, note="AI asked for help: contract question")
    assert C.ownership_state(_conv(w)) == "HUMAN_REQUIRED"
    with patch("app.services.inbound_mailbox_service._access_token_send", return_value="tok"), \
         patch("app.services.programs.email_reply.httpx.post", return_value=_graph_ok()) as post:
        r1 = _reply(w, resp, text="Thank you for writing. Kerry will call you tomorrow morning.", channel="email")
        r2 = _reply(w, resp, text="Thank you for writing. Kerry will call you tomorrow morning.", channel="email")
    assert r1.status_code == 200, r1.text
    assert post.call_count == 1 and r2.json().get("duplicate") is True             # 21: retried request is a no-op
    assert r1.json()["handoff"]["ai_resumed"] is True
    assert C.ownership_state(_conv(w)) == "AI_RESUMED"
    assert db.query(ProgramHandoffLearning).filter_by(response_id=resp.id).count() == 1


# 5 + 6 ── the next family message goes to the AI, once ──────────────────────

def test_5_6_next_message_is_answered_by_the_ai_once_and_the_old_one_never(client_w):
    w = client_w
    resp, _ = _escalate(w)
    _reply(w, resp, text="Kerry can meet you Thursday at 10 by the new section.")
    after_human = len(w["sent"])
    assert ai_setter.sweep(w["db"])["sent"] == 0                                  # nothing re-answers the old one
    assert len(w["sent"]) == after_human
    fake = _Fake([_d("answer", "Wonderful - we'll see you Thursday.")])
    nxt = _inbound(w, "Thursday works, thank you", fake)
    assert len(fake.calls) == 1 and len(w["sent"]) == after_human + 1           # one inbound -> one AI reply
    assert "Kerry can meet you Thursday" in fake.calls[0]["messages"][1]["content"]   # it sees the human answer
    assert nxt.id != resp.id
    assert C.ownership_state(_conv(w)) == "AI_ACTIVE"
    # 21: Twilio retries the same inbound webhook - one response, one AI reply
    n = len(w["sent"])
    fake = _Fake([_d("answer", "See you then!")])
    _inbound(w, "See you then", fake, reply_id="SM-dup-1")
    _inbound(w, "See you then", fake, reply_id="SM-dup-1")
    assert w["db"].query(ProgramResponse).filter_by(lead_id=w["lead"].id, reply_id="SM-dup-1").count() == 1
    assert len(fake.calls) == 1 and len(w["sent"]) == n + 1


# 7 ── a failed send keeps the handoff open ──────────────────────────────────

def test_7_failed_send_keeps_the_handoff_and_claims_nothing(client_w):
    w = client_w
    resp, _ = _escalate(w)
    w["hooks"]["fail"] = "Twilio 30003 unreachable"
    r = _reply(w, resp)
    assert r.status_code == 400 and "Not sent" in r.json()["detail"]
    conv = _conv(w)
    assert conv.state == "human" and C.ownership_state(conv) == "HUMAN_REQUIRED"
    w["db"].refresh(resp)
    assert resp.handling_status in ("new", "opened")                             # still waiting
    assert _counts(w)["handoff"] == 1
    assert w["db"].query(ProgramHandoffLearning).count() == 0
    # corrected and retried: goes once, resolves
    w["hooks"]["fail"] = None
    r = _reply(w, resp, key="k2")
    assert r.status_code == 200 and r.json()["handoff"]["ai_resumed"] is True


# 8 ── while the provider outcome is pending the person still owns it ─────────

def test_8_ownership_does_not_move_until_the_provider_accepts(client_w):
    w = client_w
    resp, _ = _escalate(w)
    seen = {}

    def during():
        c = w["db"].query(ProgramConversation).filter_by(lead_id=w["lead"].id).one()
        seen["state"] = c.state
    w["hooks"]["during"] = during
    assert _reply(w, resp).status_code == 200
    assert seen["state"] == "human"                                               # HUMAN_REPLY_PENDING
    assert _conv(w).state == "ai_active"


# 9 ── double-click send ─────────────────────────────────────────────────────

def test_9_double_click_sends_once(client_w):
    w = client_w
    resp, _ = _escalate(w)
    before = len(w["sent"])
    r1 = _reply(w, resp)
    r2 = _reply(w, resp)
    assert r1.status_code == 200 and r2.status_code == 409
    assert len(w["sent"]) == before + 1
    assert w["db"].query(ProgramHandoffLearning).count() == 1


# 10 ── a family message that lands while the reply is sending ───────────────

def test_10_concurrent_family_message_stays_with_the_person(client_w):
    w = client_w
    resp, _ = _escalate(w)
    fake = _Fake([_d("answer", "should never be sent")])

    def during():
        _inbound(w, "Also, can my son come too?", fake, now=datetime.utcnow() + timedelta(seconds=1))
    w["hooks"]["during"] = during
    before = len(w["sent"])
    r = _reply(w, resp)
    assert r.status_code == 200, r.text
    h = r.json()["handoff"]
    assert h["race"] is True and h["ai_resumed"] is False
    assert fake.calls == [] and len(w["sent"]) == before + 1                     # the AI did not answer it
    conv = _conv(w)
    assert conv.state == "human" and C.ownership_state(conv) == "HUMAN_REQUIRED"
    newer = (w["db"].query(ProgramResponse).filter_by(lead_id=w["lead"].id)
             .order_by(ProgramResponse.received_at.desc()).first())
    assert newer.id != resp.id and newer.handling_status in ("new", "opened")
    assert _counts(w)["handoff"] == 1


# 11 ── Keep Human Control ───────────────────────────────────────────────────

def test_11_keep_human_control(client_w):
    w = client_w
    resp, _ = _escalate(w)
    r = _reply(w, resp, then="human")
    assert r.json()["handoff"]["kept_human"] is True and r.json()["handoff"]["ai_resumed"] is False
    conv = _conv(w)
    assert conv.state == "human" and C.ownership_state(conv) == "HUMAN_RETAINED"
    fake = _Fake([_d("answer", "never")])
    _inbound(w, "Thanks!", fake)
    assert fake.calls == []                                                       # the AI stays out
    assert w["db"].query(ProgramHandoffLearning).one().outcome == "human_retained"


# 12 ── the AI can escalate again later ──────────────────────────────────────

def test_12_later_genuine_escalation_is_a_new_work_item(client_w):
    w = client_w
    resp, _ = _escalate(w)
    _reply(w, resp)
    assert _counts(w)["handoff"] == 0
    resp2 = _inbound(w, "I want to cancel my wife's contract", _Fake([_d("handoff", "I'll reach out personally.",
                                                                           reason="cancel contract")]))
    conv = _conv(w)
    assert conv.state == "human" and C.ownership_state(conv) == "HUMAN_REQUIRED"
    assert "cancel" in conv.handoff_reason and resp2.id != resp.id
    assert _counts(w)["handoff"] == 1


# 13 + 14 ── STOP / DNC close it ─────────────────────────────────────────────

def test_13_stop_after_a_handoff_closes_and_the_ai_can_never_resume(client_w):
    w = client_w
    resp, _ = _escalate(w)
    _inbound(w, "STOP")
    conv = _conv(w)
    assert conv.state == "stopped" and _family(w)["handling"]["ownership"] == "CLOSED"
    assert _counts(w)["handoff"] == 0
    with pytest.raises(ValueError):
        ai_setter.set_owner(w["db"], w["lead"], "ai", actor=None)


def test_14_an_existing_opt_out_is_closed(client_w):
    w = client_w
    w["lead"].status = "dnc"
    w["db"].commit()
    assert _family(w)["handling"]["ownership"] == "CLOSED"
    assert C.ownership_state(None, opted_out=True) == "CLOSED"


# 15 ── location-scoped guidance ─────────────────────────────────────────────

def _learning(db, org_id, loc, q, a, scope="location"):
    row = ProgramHandoffLearning(organization_id=org_id, location_id=loc, question=q, human_answer=a,
                                 status="candidate", channel="sms")
    db.add(row)
    db.flush()
    HL.approve(db, org_id, row.id, None, question=q, answer=a, scope=scope)
    db.commit()
    return row


def test_15_location_specific_answers_stay_at_their_location(setter):
    db, org = setter["db"], setter["org"]
    _learning(db, org.id, "loc-A", "Is the chapel open on Sunday afternoons?", "Leeds chapel: 1-5 PM Sundays.")
    _learning(db, org.id, None, "Do you offer payment plans for plots?", "Yes, monthly plans are offered.",
              scope="program")
    msg = "Is your chapel open Sunday afternoon?"
    assert [x["answer"] for x in HL.retrieve(db, org.id, "loc-A", msg)] == ["Leeds chapel: 1-5 PM Sundays."]
    assert HL.retrieve(db, org.id, "loc-B", msg) == []                                # never another location's
    assert len(HL.retrieve(db, org.id, "loc-B", "Do you have payment plans for a plot?")) == 1   # program-wide
    assert HL.retrieve(db, "another-org", "loc-A", msg) == []
    row = ProgramHandoffLearning(organization_id=org.id, location_id=None, question="q", status="candidate")
    db.add(row)
    db.flush()
    with pytest.raises(HL.Refused):
        HL.approve(db, org.id, row.id, None, question="Hours?", answer="9-5", scope="location")


# 16 ── privacy ──────────────────────────────────────────────────────────────

def test_16_candidates_are_redacted_and_approval_refuses_personal_data(client_w):
    w = client_w
    resp, _ = _escalate(w, "This is Marion Testfam, call me at 205-555-0199 or marion@example.com, 12 Oak Street")
    r = _reply(w, resp, text="Marion, Kerry will call 205-555-0199 today; we'll mail to 12 Oak Street.")
    cid = r.json()["learning_candidate_id"]
    row = w["db"].query(ProgramHandoffLearning).filter_by(id=cid).one()
    for t in (row.question, row.human_answer, row.context):
        assert "Marion" not in t and "Testfam" not in t and "555-0199" not in t
        assert "marion@example.com" not in t and "12 Oak Street" not in t
    assert "[name]" in row.human_answer and "[phone]" in row.human_answer
    bad = w["client"].post("/program/command/learning/%s/approve" % cid, headers=w["h"],
                           json={"question": "Can you call me?", "answer": "Call 205-555-0199"})
    assert bad.status_code == 422 and row.status == "candidate"


# 17 ── unapproved / rejected / retired answers never reach the AI ────────────

def test_17_only_active_answers_reach_the_ai_and_their_use_is_counted(client_w):
    w = client_w
    db = w["db"]
    resp, _ = _escalate(w, "Do you have cremation niches in the garden section?")
    cid = _reply(w, resp, text="Yes, we have garden niches starting at the north wall.").json()["learning_candidate_id"]
    ask = "Are there cremation niches in your garden section?"
    fake = _Fake([_d("offer_times", "I'd be glad to show you. Would {TIMES} work?")])
    _inbound(w, ask, fake)
    assert "approved_staff_answers" not in fake.calls[0]["messages"][1]["content"]     # candidate: never
    # reject a second candidate; approve the first for this location
    loc = db.query(ProgramHandoffLearning).filter_by(id=cid).one().location_id
    r = w["client"].post("/program/command/learning/%s/approve" % cid, headers=w["h"],
                         json={"question": "Do you have cremation niches in the garden section?",
                               "answer": "Yes - garden niches are along the north wall.",
                               "scope": "location" if loc else "program"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    fake = _Fake([_d("offer_times", "Yes - along the north wall. Would {TIMES} work to see them?")])
    resp3 = _inbound(w, "One more - any cremation niches in the garden?", fake)
    assert "garden niches are along the north wall" in fake.calls[0]["messages"][1]["content"]
    use = db.query(ProgramLearningUse).filter_by(learning_id=cid).one()
    assert use.action == "offer_times" and use.response_id == resp3.id
    counts = w["client"].get("/program/command/learning?status=active", headers=w["h"]).json()["counts"]
    assert counts["active"] == 1 and counts["uses"] == 1 and counts["uses_without_handoff"] == 1
    # retired -> gone again
    assert w["client"].post("/program/command/learning/%s/status" % cid, headers=w["h"],
                            json={"status": "retired"}).json()["status"] == "retired"
    assert HL.retrieve(db, w["org"].id, loc, ask) == []
    rej = ProgramHandoffLearning(organization_id=w["org"].id, question=ask, human_answer="x", status="candidate")
    db.add(rej)
    db.commit()
    assert w["client"].post("/program/command/learning/%s/status" % rej.id, headers=w["h"],
                            json={"status": "rejected"}).json()["status"] == "rejected"
    assert HL.retrieve(db, w["org"].id, loc, ask) == []


# 18 ── no cadence restart ───────────────────────────────────────────────────

def test_18_resuming_the_ai_does_not_restart_the_cadence(client_w):
    w = client_w
    db = w["db"]
    resp, _ = _escalate(w)
    cs = db.query(CadenceState).filter_by(lead_id=w["lead"].id).first()
    if cs is None:
        cs = CadenceState(lead_id=w["lead"].id, status="paused", current_touch_number=3)
        db.add(cs)
        db.commit()
    snap = (cs.status, cs.current_touch_number, cs.next_touch_due_at)
    lead_status = w["lead"].status
    db.refresh(resp)
    paused = resp.cadence_paused
    _reply(w, resp)
    db.refresh(cs)
    db.refresh(w["lead"])
    db.refresh(resp)
    assert (cs.status, cs.current_touch_number, cs.next_touch_due_at) == snap
    assert w["lead"].status == lead_status and resp.cadence_paused == paused
    assert _conv(w).next_followup_at is None


# 19 ── one handoff = one work item ──────────────────────────────────────────

def test_19_one_handoff_is_one_work_item_however_many_alerts(client_w):
    w = client_w
    resp, _ = _escalate(w)
    c = _counts(w)
    assert c["handoff"] == 1
    items = w["client"].get("/program/command?filter=handoff", headers=w["h"]).json()["inbox"]
    assert [i["lead_id"] for i in items] == [w["lead"].id]
    _reply(w, resp)
    c2 = _counts(w)
    assert c2["handoff"] == 0 and c2["all"] <= c["all"]
    again = _counts(w)                                                             # refresh: does not come back
    assert again["handoff"] == 0


# 20 ── tenant and role isolation ────────────────────────────────────────────

def test_20_other_workspaces_and_non_managers_cannot_touch_learning(client_w):
    w = client_w
    db = w["db"]
    resp, _ = _escalate(w)
    cid = _reply(w, resp).json()["learning_candidate_id"]
    other = Organization(name="Elsewhere HO", slug="elsewhere-ho")
    db.add(other)
    db.commit()
    stranger = _admin(db, other, email="stranger-ho@example.com")
    r = w["client"].get("/program/command/learning", headers=_h(db, stranger))
    assert r.status_code in (403, 404) or cid not in [i["id"] for i in r.json()["items"]]
    r = w["client"].post("/program/command/learning/%s/approve" % cid, headers=_h(db, stranger),
                         json={"question": "Hours?", "answer": "9 to 5", "scope": "program"})
    assert r.status_code in (403, 404)
    adv = User(organization_id=w["org"].id, email="adv-ho@sci.example", password_hash="x", full_name="Adv",
               role="advisor", must_change_password=False)
    db.add(adv)
    db.commit()
    r = w["client"].post("/program/command/learning/%s/approve" % cid, headers=_h(db, adv),
                         json={"question": "Hours?", "answer": "9 to 5", "scope": "program"})
    assert r.status_code == 403
    assert db.query(ProgramHandoffLearning).filter_by(id=cid).one().status == "candidate"


# 22 ── the screen ───────────────────────────────────────────────────────────

def test_22_the_screen_resumes_by_default_and_keeps_failed_drafts():
    src = open("frontend/src/pages/program/command/Command.jsx", encoding="utf-8").read()
    assert "HUMAN RESPONSE NEEDED" in src
    assert "Human replied - AI resumed" in src and "Human reply not sent" in src
    assert "send('ai')" in src and "send('human')" in src and "keep human control" in src.lower()
    assert "Review for AI learning" in src and "/program/command/learning" in src
    inbox = src[src.index("function InboxTab"):src.index("function Unknown")]
    assert "visibilitychange" in inbox and "Refresh" in inbox                    # #516 refresh untouched
    assert "setR({ inbox: [] })" not in inbox
