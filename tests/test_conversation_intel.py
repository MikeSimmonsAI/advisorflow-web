"""CONVERSATION INTELLIGENCE - regression corpus + red team.

Each scenario is a real-shaped thread (inbound replies, outbound SMS/email,
human vs automated senders) played into the platform's own tables, then read
back through `conversation_intel.build_context`. Nothing here calls a model:
memory must hold without AI.

What is pinned, in the words of the requirement:
  * a prospect should not have to repeat themselves;
  * AI should not ask a question we already know the answer to;
  * AI should not forget what was said earlier;
  * AI should not respond when a human should take over;
  * an inference is never a fact;
  * "call me Monday" means not Tuesday, and "actually Monday" replaces Friday.
"""
import itertools
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.conversation_models import (ConversationMemoryItem, ConversationState,
                                            SUPERSEDED, ACTIVE)
from app.models.models import EmailMessage, Lead, Message, Organization, Reply, User
from app.services import conversation_intel as ci
from app.services.auth_service import create_access_token, hash_password

_N = itertools.count(1)
# Thursday 2026-10-01 15:00 UTC = 10:00 Central
T0 = datetime(2026, 10, 1, 15, 0)


@pytest.fixture()
def world(db_session):
    def make(industry="insurance", sms_consent=True, email="pat@example.test", **lead_kw):
        db = db_session
        n = next(_N)
        org = Organization(name="Org %d" % n, slug="ci-org-%d-%s" % (n, uuid.uuid4().hex[:6]),
                           plan="standard", industry=industry, is_active=True)
        db.add(org)
        db.commit()
        user = User(organization_id=org.id, email="ci%d@test.live" % n, password_hash=hash_password("x"),
                    full_name="Maya Agent", role="org_admin", must_change_password=False, is_active=True,
                    booking_timezone="America/Chicago")
        db.add(user)
        db.commit()
        lead = Lead(organization_id=org.id, assigned_to_id=user.id, first_name="Pat", last_name="Test",
                    email=email, phone="+12145550100", status="replied", sms_consent=sms_consent, **lead_kw)
        db.add(lead)
        db.commit()
        return {"db": db, "org": org, "user": user, "lead": lead}
    return make


def inbound(w, text, at, channel="sms"):
    r = Reply(lead_id=w["lead"].id, body=text, source=channel, received_at=at)
    w["db"].add(r)
    w["db"].commit()
    return r


def outbound(w, text, at, channel="sms", source="ai_conversation"):
    if channel == "sms":
        m = Message(lead_id=w["lead"].id, sender_id=w["user"].id, body=text, sent_at=at, send_source=source)
    else:
        m = EmailMessage(lead_id=w["lead"].id, sender_id=w["user"].id, subject="Re: your question",
                         body_html="<p>%s</p>" % text, sent_at=at, status="sent", send_source=source)
    w["db"].add(m)
    w["db"].commit()
    return m


def ctx(w, now):
    return ci.build_context(w["db"], w["lead"], now=now)


def fact(c, key):
    return next((f["value"] for f in c["known_facts"] if f["key"] == key), None)


# ── memory across days ─────────────────────────────────────────────────────

def test_monday_family_wednesday_cost_remembers_what_that_means(world):
    w = world("insurance")
    inbound(w, "I need coverage for my wife and two kids.", T0)
    outbound(w, "Happy to help, Pat. When is a good time to talk?", T0 + timedelta(minutes=5))
    inbound(w, "What would something like that cost?", T0 + timedelta(days=2))
    c = ctx(w, T0 + timedelta(days=2, minutes=1))
    assert fact(c, "household.spouse") == "wife"
    assert fact(c, "household.children") == "2"
    assert "family_protection" in c["vertical_intents"]
    # the inference is kept apart from facts
    assert any("family protection" in i["value"] for i in c["inferences"])
    assert all(f["certainty"] == "fact" for f in c["known_facts"])
    assert [q["value"] for q in c["open_questions"]] == ["What would something like that cost?"]
    # the next question is NOT "what type of coverage" and NOT "who would it protect"
    assert c["next_best_question"] and "coverage today" in c["next_best_question"]
    # the gate stops the dumb reply ...
    bad = ci.check_reply(c, "Thanks Pat! What type of coverage are you interested in?", "sms")
    codes = {f["code"] for f in bad["failures"]}
    assert {"repeats_known_question", "ignores_open_question"} <= codes
    # ... and the memory-built draft passes and refers to the family
    draft = ci.suggest(w["db"], w["lead"], c, channel="sms")
    assert draft["source"] == "template"
    assert "wife" in draft["suggestion"] and "2 kids" in draft["suggestion"]
    assert draft["quality"]["passed"], draft["quality"]


def test_call_me_friday_then_actually_monday_keeps_history_and_one_truth(world):
    w = world("general")
    inbound(w, "Busy this week. Call me Friday.", T0)
    inbound(w, "Actually Monday is better.", T0 + timedelta(hours=1))
    c = ctx(w, T0 + timedelta(hours=1, minutes=1))
    assert c["follow_up"]["value"] == "Monday"
    assert c["follow_up"]["date"] == "2026-10-05"
    assert [h["value"] for h in c["follow_up"]["history"]] == ["Friday"]
    assert c["follow_up"]["history"][0]["status"] == SUPERSEDED
    db = w["db"]
    active = db.query(ConversationMemoryItem).filter(ConversationMemoryItem.lead_id == w["lead"].id,
                                                     ConversationMemoryItem.key == "follow_up.when",
                                                     ConversationMemoryItem.status == ACTIVE).count()
    assert active == 1
    assert c["next_best_action"]["action"] == "wait"
    gate = ci.check_reply(c, "Sounds good, I'll call you Friday.", "sms")
    assert "contradicts_follow_up" in {f["code"] for f in gate["failures"]}


def test_a_late_arriving_older_message_does_not_override_the_newer_truth(world):
    w = world("general")
    inbound(w, "Call me Monday.", T0 + timedelta(hours=2))
    ctx(w, T0 + timedelta(hours=2, minutes=1))
    inbound(w, "Call me Friday.", T0)                       # delivered late, older timestamp
    c = ctx(w, T0 + timedelta(hours=3))
    assert c["follow_up"]["value"] == "Monday"


def test_corrected_fact_supersedes(world):
    w = world("insurance")
    inbound(w, "We have two kids.", T0)
    inbound(w, "Sorry, actually three kids - the baby counts!", T0 + timedelta(minutes=10))
    c = ctx(w, T0 + timedelta(minutes=11))
    assert fact(c, "household.children") == "3"
    gate = ci.check_reply(c, "Great - coverage for your two children is simple.", "sms")
    assert "invented_fact" in {f["code"] for f in gate["failures"]}


# ── stop / no / not now ─────────────────────────────────────────────────────

def test_stop_ends_everything(world):
    w = world("general")
    outbound(w, "Hi Pat, following up on your request.", T0)
    inbound(w, "Stop texting me.", T0 + timedelta(minutes=3))
    c = ctx(w, T0 + timedelta(minutes=4))
    assert c["state"]["state"] == "stopped" and c["state"]["health"] == "stopped"
    assert c["ai_decision"]["sms"]["allowed"] is False and c["ai_decision"]["sms"]["code"] == "stop"
    assert c["ai_decision"]["email"]["allowed"] is False
    assert c["next_best_action"]["action"] == "do_nothing"


def test_not_interested_is_no_and_january_is_not_now(world):
    w = world("general")
    outbound(w, "Hi Pat!", T0)
    inbound(w, "Not interested.", T0 + timedelta(minutes=1))
    c = ctx(w, T0 + timedelta(minutes=2))
    assert c["state"]["state"] == "closed" and c["next_best_action"]["action"] == "do_nothing"
    assert c["ai_decision"]["sms"]["allowed"] is False

    w2 = world("general")
    outbound(w2, "Hi Pat!", T0)
    inbound(w2, "Call me in January.", T0 + timedelta(minutes=1))
    c2 = ctx(w2, T0 + timedelta(minutes=2))
    assert c2["state"]["state"] == "not_now"
    assert c2["next_best_action"]["action"] == "nurture"
    assert c2["follow_up"]["date"] == "2027-01-01"
    assert c2["current_intent"] != "not_interested"


def test_talk_to_my_wife_is_a_follow_up_not_a_no(world):
    w = world("insurance")
    outbound(w, "Want to set up a time?", T0)
    inbound(w, "Yes. But I need to talk to my wife. Call me tomorrow", T0 + timedelta(seconds=40))
    c = ctx(w, T0 + timedelta(minutes=2))
    assert any(o["key"] == "objection.spouse_decision" for o in c["objections"])
    assert c["follow_up"]["value"] == "tomorrow"
    assert c["state"]["state"] == "follow_up_needed"
    assert c["current_intent"] != "not_interested"


# ── humans ──────────────────────────────────────────────────────────────────

def test_asking_for_a_person_hands_off(world):
    w = world("general")
    outbound(w, "Hi Pat!", T0)
    inbound(w, "Are you a bot? I want to talk to a real person.", T0 + timedelta(minutes=1))
    c = ctx(w, T0 + timedelta(minutes=2))
    assert c["state"]["state"] == "human_review"
    assert c["ai_decision"]["sms"] == {"allowed": False, "code": "human_review",
                                       "reason": "Customer asked for a person", "human_review": True}
    assert c["next_best_action"]["action"] == "human_takeover"
    # a human answering clears it
    outbound(w, "Hi Pat, this is Maya - happy to help personally.", T0 + timedelta(minutes=5), source="manual")
    c = ctx(w, T0 + timedelta(minutes=6))
    assert c["state"]["needs_human_reason"] is None


def test_a_human_reply_keeps_ai_quiet_until_resumed(world):
    w = world("general")
    inbound(w, "What are your hours?", T0)
    outbound(w, "We're open 9-5, Pat.", T0 + timedelta(minutes=2), source="manual")
    inbound(w, "Great, thanks", T0 + timedelta(minutes=4))
    now = T0 + timedelta(minutes=5)
    c = ci.build_context(w["db"], w["lead"], now=now)
    assert c["state"]["mode"] == "human_active"
    assert c["ai_decision"]["sms"]["code"] == "human_active"
    ci.set_mode(w["db"], w["lead"], "ai_active", user=w["user"], now=now + timedelta(seconds=1))
    c = ci.build_context(w["db"], w["lead"], now=now + timedelta(seconds=2))
    assert c["state"]["mode"] != "human_active"


def test_explicit_takeover_and_pause(world):
    w = world("general")
    inbound(w, "Tell me more", T0)
    ci.set_mode(w["db"], w["lead"], "human_active", user=w["user"], now=T0 + timedelta(minutes=1))
    c = ci.build_context(w["db"], w["lead"], now=T0 + timedelta(minutes=2))
    assert c["ai_decision"]["sms"]["code"] == "human_active"
    ci.set_mode(w["db"], w["lead"], "ai_paused", user=w["user"], now=T0 + timedelta(minutes=3))
    c = ci.build_context(w["db"], w["lead"], now=T0 + timedelta(minutes=4))
    assert c["ai_decision"]["sms"]["code"] == "ai_paused"


# ── questions ───────────────────────────────────────────────────────────────

def test_unanswered_question_stays_open_until_a_reply_addresses_it(world):
    w = world("insurance")
    inbound(w, "What happens if I change jobs?", T0)
    outbound(w, "Thanks Pat! When is a good time to talk?", T0 + timedelta(minutes=3))
    c = ctx(w, T0 + timedelta(minutes=4))
    assert [q["value"] for q in c["open_questions"]] == ["What happens if I change jobs?"]
    outbound(w, "Good question - an individual policy stays with you if you change jobs.",
             T0 + timedelta(minutes=10), source="manual")
    c = ctx(w, T0 + timedelta(minutes=11))
    assert c["open_questions"] == []
    assert [q["value"] for q in c["answered_questions"]] == ["What happens if I change jobs?"]


def test_rapid_messages_are_one_turn(world):
    w = world("insurance")
    outbound(w, "Want to set up a quick call?", T0)
    inbound(w, "Yes", T0 + timedelta(minutes=1))
    inbound(w, "But I need to talk to my wife", T0 + timedelta(minutes=1, seconds=20))
    inbound(w, "Call me tomorrow", T0 + timedelta(minutes=1, seconds=40))
    c = ctx(w, T0 + timedelta(minutes=2))
    assert [m["text"] for m in c["pending_inbound"]] == ["Yes", "But I need to talk to my wife", "Call me tomorrow"]
    assert c["burst"] is True
    assert c["follow_up"]["value"] == "tomorrow"


def test_replaying_the_same_messages_adds_nothing(world):
    w = world("insurance")
    inbound(w, "I need coverage for my wife and two kids.", T0)
    ctx(w, T0 + timedelta(minutes=1))
    n1 = w["db"].query(ConversationMemoryItem).filter(ConversationMemoryItem.lead_id == w["lead"].id).count()
    st = w["db"].query(ConversationState).filter(ConversationState.lead_id == w["lead"].id).one()
    st.processed_through = None                      # force a full re-read (webhook replay / rebuild)
    w["db"].commit()
    ctx(w, T0 + timedelta(minutes=2))
    n2 = w["db"].query(ConversationMemoryItem).filter(ConversationMemoryItem.lead_id == w["lead"].id).count()
    assert n1 == n2 > 0


# ── channels / consent ──────────────────────────────────────────────────────

def test_email_conversation_is_not_sms_consent(world):
    w = world("general", sms_consent=False)
    inbound(w, "Can you tell me more?", T0, channel="email")
    c = ctx(w, T0 + timedelta(minutes=1))
    assert c["ai_decision"]["sms"]["code"] == "no_sms_consent"
    assert c["ai_decision"]["email"]["allowed"] is True


def test_cross_channel_thread_is_one_memory(world):
    w = world("insurance")
    inbound(w, "I have two kids", T0, channel="email")
    inbound(w, "How much would it cost?", T0 + timedelta(hours=3), channel="sms")
    c = ctx(w, T0 + timedelta(hours=3, minutes=1))
    assert fact(c, "household.children") == "2"
    assert {f["channel"] for f in c["known_facts"]} == {"email"}
    assert c["open_questions"][0]["channel"] == "sms"


# ── wholesale ───────────────────────────────────────────────────────────────

def test_wholesale_price_talk_goes_to_the_owner_and_never_out_with_a_number(world):
    w = world("wholesale_real_estate")
    outbound(w, "Thanks for reaching out about the house.", T0)
    inbound(w, "It was inherited from my mom, has tenants and needs a new roof. Make me an offer.",
            T0 + timedelta(minutes=2))
    c = ctx(w, T0 + timedelta(minutes=3))
    assert fact(c, "property.inherited") == "yes"
    assert fact(c, "property.occupancy") == "tenant-occupied"
    assert fact(c, "property.condition") == "needs a new roof"
    assert c["state"]["state"] == "human_review"
    assert c["ai_decision"]["sms"]["code"] == "human_review"
    gate = ci.check_reply(c, "We can offer $150k cash and close in 10 days.", "sms")
    assert "price_commitment" in {f["code"] for f in gate["failures"]}


def test_wholesale_not_this_property_and_wife_is_owner(world):
    w = world("wholesale_real_estate")
    outbound(w, "About 12 Elm St -", T0)
    inbound(w, "Not this property, the other one. My wife is the owner.", T0 + timedelta(minutes=1))
    c = ctx(w, T0 + timedelta(minutes=2))
    assert fact(c, "property.owner_stated") == "the customer's wife"
    assert c["state"]["needs_human_reason"] == "Customer says we have the wrong property"


# ── energy ──────────────────────────────────────────────────────────────────

def test_energy_moving_next_month_is_a_future_opportunity_not_a_no(world):
    w = world("energy")
    outbound(w, "Hi Pat, want a rate review?", T0)
    inbound(w, "We're moving next month to a new apartment.", T0 + timedelta(minutes=1))
    c = ctx(w, T0 + timedelta(minutes=2))
    assert fact(c, "energy.moving") == "yes"
    assert "moving" in c["vertical_intents"]
    assert c["follow_up"]["date"] == "2026-11-01"
    assert c["current_intent"] != "not_interested"


# ── red team ────────────────────────────────────────────────────────────────

RED_TEAM = [
    ("You already asked me that.", "flag.repeated_question"),
    ("I told you Friday.", "follow_up"),
    ("That's not what I said.", "flag.disputes_record"),
    ("Stop texting me.", "consent.stop_requested"),
    ("I already talked to Mike.", "prior_contact.with"),
    ("Who is this?", "flag.identity"),
    ("I have no idea what you're talking about.", "flag.identity"),
    ("Yes.", None), ("No.", None), ("Maybe.", None),
]


@pytest.mark.parametrize("phrase,expect", RED_TEAM)
def test_red_team_phrases_recover_sensibly(world, phrase, expect):
    w = world("insurance")
    outbound(w, "Hi Pat, following up.", T0)
    inbound(w, phrase, T0 + timedelta(minutes=1))
    c = ctx(w, T0 + timedelta(minutes=2))
    keys = {f["key"] for f in c["flags"]} | {f["key"] for f in c["known_facts"]}
    if expect == "follow_up":
        assert c["follow_up"]["value"] == "Friday"
    elif expect:
        assert expect in keys, (phrase, keys)
    if phrase in ("Who is this?", "I have no idea what you're talking about.", "That's not what I said."):
        assert c["ai_decision"]["sms"]["allowed"] is False
        gate = ci.check_reply(c, "Great! Want to book a time?", "sms")
        assert not gate["passed"]
    if phrase == "No.":
        assert c["state"]["state"] == "closed"
    if phrase == "Maybe.":
        assert c["current_intent"] in (None, "engaged") or c["current_intent"] != "not_interested"


# ── the API ─────────────────────────────────────────────────────────────────

def test_api_is_tenant_scoped_and_takeover_works(world, client):
    w = world("insurance")
    other = world("insurance")
    inbound(w, "I need coverage for my wife and two kids. What would it cost?", T0)
    h = {"Authorization": "Bearer " + create_access_token(w["user"], w["db"])}
    r = client.get("/conversation-intel/leads/%s" % w["lead"].id, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["open_questions"]
    assert client.get("/conversation-intel/leads/%s" % other["lead"].id, headers=h).status_code == 404
    r = client.post("/conversation-intel/leads/%s/mode" % w["lead"].id, headers=h,
                    json={"mode": "human_active", "reason": "I'll handle this"})
    assert r.status_code == 200 and r.json()["state"]["mode"] == "human_active"
    assert client.post("/conversation-intel/leads/%s/mode" % other["lead"].id, headers=h,
                       json={"mode": "human_active"}).status_code == 404
    r = client.post("/conversation-intel/leads/%s/compose" % w["lead"].id, headers=h, json={"channel": "sms"})
    assert r.status_code == 200 and r.json()["suggestion"]
    r = client.post("/conversation-intel/leads/%s/check" % w["lead"].id, headers=h,
                    json={"text": "How many kids do you have?", "channel": "sms"})
    assert r.json()["passed"] is False
    q = client.get("/conversation-intel/queue", headers=h).json()
    assert set(q["counts"]) >= {"respond_now", "needs_human"}
    assert all(i["lead_id"] != other["lead"].id for i in q["items"])
    s = client.get("/conversation-intel/search?q=cost", headers=h).json()
    assert [x["lead_id"] for x in s["results"]] == [w["lead"].id]
    ins = client.get("/conversation-intel/insights", headers=h).json()
    assert ins["conversations"] >= 1 and "common_objections" in ins


# ── the live auto-reply path (ai_conversation_service.handle_inbound_reply) ──

@pytest.fixture()
def live(world, monkeypatch, ai_background_on):
    from app.models.models import PipelineConversation
    import app.services.ai_conversation_service as A

    def make(industry="insurance", draft_body="Happy to help with that."):
        w = world(industry)
        conv = PipelineConversation(organization_id=w["org"].id, lead_id=w["lead"].id, advisor_id=w["user"].id,
                                    stage="ai_responding", channel="email", auto_respond=True)
        w["db"].add(conv)
        w["db"].commit()
        sent, prompts = [], []
        monkeypatch.setattr(A, "_send_email_resend", lambda db, adv, to, subj, body: sent.append((to, subj, body)))

        def fake_generate(db, lead, advisor, reply_body, intel_block=None):
            prompts.append({"turn": reply_body, "block": intel_block})
            return {"subject": "Re: hi", "body": draft_body, "should_book": False, "should_stop": False,
                    "escalate": False, "source": "ai"}
        monkeypatch.setattr(A, "generate_reply_response", fake_generate)
        w.update(conv=conv, sent=sent, prompts=prompts, A=A)
        return w
    return make


def _now_inbound(w, text, seconds_ago=0):
    return inbound(w, text, datetime.utcnow() - timedelta(seconds=seconds_ago), channel="email")


def test_live_a_request_for_a_person_escalates_and_sends_nothing_to_the_customer(live):
    w = live("general")
    _now_inbound(w, "Can I talk to a real person please?")
    out = w["A"].handle_inbound_reply(w["db"], w["lead"], w["user"], "Can I talk to a real person please?")
    assert out["action"] == "escalated"
    assert w["prompts"] == []                                  # the model was never asked
    assert all(to != w["lead"].email for to, _, _ in w["sent"])  # only the staff alert, never the customer
    assert w["conv"].flagged is True


def test_live_three_quick_messages_get_one_reply_to_the_whole_turn(live):
    w = live("insurance", draft_body="Of course - talk it over with your wife. I'll call you tomorrow as you asked.")
    _now_inbound(w, "Yes", 40)
    _now_inbound(w, "But I need to talk to my wife", 20)
    _now_inbound(w, "Call me tomorrow", 1)
    A = w["A"]
    r1 = A.handle_inbound_reply(w["db"], w["lead"], w["user"], "Yes")
    r2 = A.handle_inbound_reply(w["db"], w["lead"], w["user"], "But I need to talk to my wife")
    r3 = A.handle_inbound_reply(w["db"], w["lead"], w["user"], "Call me tomorrow")
    assert r1["reason"] == r2["reason"] == "newer_message_in_same_turn"
    assert r3["action"] == "replied", r3
    assert len(w["prompts"]) == 1
    assert w["prompts"][0]["turn"] == "Yes\nBut I need to talk to my wife\nCall me tomorrow"
    assert "tomorrow" in w["prompts"][0]["block"]
    assert len([s for s in w["sent"] if s[0] == w["lead"].email]) == 1


def test_live_a_draft_that_reasks_and_ignores_the_question_is_held(live):
    w = live("insurance", draft_body="Thanks! What type of coverage are you interested in?")
    inbound(w, "I need coverage for my wife and two kids.", datetime.utcnow() - timedelta(days=2), channel="email")
    outbound(w, "Happy to help.", datetime.utcnow() - timedelta(days=2) + timedelta(minutes=5), channel="email")
    _now_inbound(w, "What would something like that cost?")
    out = w["A"].handle_inbound_reply(w["db"], w["lead"], w["user"], "What would something like that cost?")
    assert out["action"] == "held_for_review"
    assert {f["code"] for f in out["failures"]} >= {"repeats_known_question", "ignores_open_question"}
    assert not [s for s in w["sent"] if s[0] == w["lead"].email]
    assert w["conv"].flagged_suggested_response == "Thanks! What type of coverage are you interested in?"
    # the memory block the model was given carries the family and the open question
    blk = w["prompts"][0]["block"]
    assert "Children: 2" in blk and "What would something like that cost?" in blk and "Do NOT ask again" in blk


def test_live_after_a_human_reply_the_ai_stays_out(live):
    w = live("general")
    _now_inbound(w, "What are your hours?", 300)
    outbound(w, "9 to 5, Pat!", datetime.utcnow() - timedelta(seconds=200), channel="email", source="manual")
    _now_inbound(w, "Thanks, and Saturdays?")
    out = w["A"].handle_inbound_reply(w["db"], w["lead"], w["user"], "Thanks, and Saturdays?")
    assert out == {"action": "held", "reason": "human_active", "detail": out["detail"]}
    assert w["prompts"] == []


def test_pipeline_auto_reply_path_is_gated_too(world, monkeypatch, ai_background_on):
    import app.services.pipeline_service as P
    w = world("general")
    calls = []
    monkeypatch.setattr(P, "analyze_and_respond", lambda *a, **k: calls.append(1) or {
        "should_stop": False, "confidence": 99, "reply": "How many kids do you have?", "intent": "other",
        "include_booking_link": False, "stage": "ai_responding", "stop_reason": None})
    r = inbound(w, "Can I speak to a real person?", datetime.utcnow(), channel="sms")
    out = P.process_inbound_reply(w["db"], w["lead"], w["user"], r)
    assert out["action"] == "flagged" and "person" in out["flag_reason"]
    assert calls == []                                  # the model was never asked

    w2 = world("insurance")
    monkeypatch.setattr(P, "_notify_fsa_sms", lambda *a, **k: None)
    inbound(w2, "We have two kids.", datetime.utcnow() - timedelta(hours=2), channel="sms")
    r2 = inbound(w2, "Sounds interesting, tell me more", datetime.utcnow(), channel="sms")
    out2 = P.process_inbound_reply(w2["db"], w2["lead"], w2["user"], r2)
    assert out2["action"] == "flagged", out2
    assert out2["flag_reason"].startswith("AI draft held:") and "already told us" in out2["flag_reason"]


def test_an_open_question_outranks_waiting_for_the_follow_up_day(world):
    w = world("insurance")
    inbound(w, "Call me Monday.", T0)
    outbound(w, "Will do!", T0 + timedelta(minutes=2))
    inbound(w, "Also, what would it cost for my family?", T0 + timedelta(minutes=30))
    c = ctx(w, T0 + timedelta(minutes=31))
    assert c["follow_up"]["value"] == "Monday"
    assert c["state"]["state"] == "question"
    inbound(w, "We already have a policy through work too.", T0 + timedelta(minutes=40))
    c = ctx(w, T0 + timedelta(minutes=41))
    assert c["vertical_intents"][:2] == ["family_protection", "existing_coverage"]
    assert c["next_best_action"]["action"] == "reply"
    assert c["state"]["priority"] == "respond_now"


def test_a_second_worker_writing_the_same_memory_row_skips_instead_of_failing(world):
    from app.services.conversation_intel import engine as E
    from app.services.conversation_intel.thread import Event
    w = world("insurance")
    r = inbound(w, "We have two kids.", T0)
    ev = Event(T0, "inbound", "sms", "We have two kids.", "reply", r.id)
    first = E.remember(w["db"], w["lead"], ev, category="known_fact", key="household.children", value="2")
    w["db"].commit()
    assert first is not None
    # simulate the other instance having already written it: bypass the pre-check
    orig = E._exists
    E._exists = lambda *a, **k: False
    try:
        dup = E.ConversationMemoryItem(organization_id=w["org"].id, lead_id=w["lead"].id, category="known_fact",
                                       key="household.children", value="2", source_type="reply", source_id=r.id,
                                       observed_at=T0)
        assert E._insert(w["db"], dup) is False
        w["db"].commit()                                   # the session is still usable
    finally:
        E._exists = orig
    assert w["db"].query(E.ConversationMemoryItem).filter(
        E.ConversationMemoryItem.lead_id == w["lead"].id).count() == 1


# ── found on the Oct 2 walk ─────────────────────────────────────────────────

def test_asking_back_does_not_answer_a_price_question(world):
    w = world()
    inbound(w, "How much would a $250k term policy cost for me?", T0)
    outbound(w, "Happy to help. Are you looking at 20 or 30 year term?", T0 + timedelta(minutes=5), source="manual")
    c = ctx(w, T0 + timedelta(minutes=10))
    assert any("cost" in q["value"] for q in c["open_questions"]), c["open_questions"]
    outbound(w, "A 20-year $250k term policy usually runs about $25 a month at your age.", T0 + timedelta(minutes=20),
             source="manual")
    c = ctx(w, T0 + timedelta(minutes=25))
    assert not any("cost" in q["value"] for q in c["open_questions"]), c["open_questions"]


def test_a_call_request_is_a_follow_up_not_an_open_question(world):
    w = world()
    inbound(w, "Can you call me after 5pm tomorrow?", T0)
    c = ctx(w, T0 + timedelta(minutes=1))
    assert c["open_questions"] == []
    assert c["follow_up"] is not None
    assert fact(c, "pref.channel") in (None, "phone")
