"""EvoSys Sales text inbox overhaul (owner, 2026-11-20). Each test is a real
case from Nov 18-20: RR Cleaner got the same text 3 times, AI answers stayed
in the queue and were sent again by hand, STOPs sat in the queue, Ana was
asked for cities she'd already given and answered in English after asking for
Spanish, Cendejas was told the businesses were 'verified'."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.evosys_sales_models import EvoSysOutreachMessage as M, EvoSysOutreachState
from app.services.evosys_sales import ai as AI
from app.services.evosys_sales import inbox as IB
from app.services.evosys_sales import outreach as O
from app.services.evosys_sales import sms_inbound as IN

from tests.test_sales_workspace import (  # noqa: F401  (fixtures)
    platform, brand, manager, manager_headers, _make_sales_user, _add_membership, _token_headers, _SEQ,
)
from tests.test_evosys_sales_texting import (  # noqa: E402,F401
    _auto_world, _lead, _ready, TEAM_NO, _brand_env,
)


def _msg(db, brand, o, direction, body, ago_min, label=None, actor=None, step=None, status=None):
    m = M(brand_sales_org_id=brand.id, opportunity_id=o.id, direction=direction, channel="text", body=body,
          label=label, actor_user_id=actor, step_no=step, status=status or ("received" if direction == "in" else "sent"),
          created_at=datetime.utcnow() - timedelta(minutes=ago_min))
    db.add(m); db.commit()
    return m


def test_ai_answer_counts_as_answered_and_stops_stay_out_of_the_queue(db_session, brand, manager):
    since = datetime.utcnow() - timedelta(days=7)
    yes = _lead(db_session, brand, manager, "Magiclean", phone="2145550301")
    _msg(db_session, brand, yes, "out", "Hi, 3 free appointments?", 100, step=1)
    _msg(db_session, brand, yes, "in", "Yes", 90, label="interested")
    _msg(db_session, brand, yes, "out", "Great to hear it! Pick a time", 89, label="ai_answer")
    stop = _lead(db_session, brand, manager, "Spotless", phone="2145550302")
    _msg(db_session, brand, stop, "in", "Stop", 80, label="unsubscribe")
    ana = _lead(db_session, brand, manager, "Ana's", phone="2145550303")
    _msg(db_session, brand, ana, "in", "Spanish is my preferred language", 70)
    _msg(db_session, brand, ana, "in", "I already provided the cities", 60, label="question")
    booked = _lead(db_session, brand, manager, "Natasha's", phone="2145550304")
    _msg(db_session, brand, booked, "in", "Yes", 50, label="interested")
    _msg(db_session, brand, booked, "out", "Reminder: your call is tomorrow", 40, label="rd:202611202145")
    g = IB.group(db_session, [yes.id, stop.id, ana.id, booked.id], set(), since)
    assert [m.opportunity_id for m, _ in g["handled"]] == [yes.id]
    assert [m.opportunity_id for m, _ in g["opted_out"]] == [stop.id]
    needs = [m for m, _ in g["needs_you"]]
    assert [m.opportunity_id for m in needs] == [booked.id, ana.id]              # one row each, newest first
    assert needs[1].body == "I already provided the cities"                      # Ana: her latest text only
    assert IB.who_sent(g["handled"][0][1]) == "ai"


def test_the_same_words_never_go_to_the_same_business_twice(db_session, brand, manager, monkeypatch):
    from app.routers import evosys_sales_work_router as W
    s = _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "RR Cleaner", phone="2145550305")
    _msg(db_session, brand, o, "out", "Hi! Great to hear you're interested. Book: https://a.test/b/1", 30,
         label="ai_answer")
    assert IB.duplicate_of(db_session, o.id, "Hi!  Great to hear you're interested.  Book: https://other.test/x")
    sent = []
    monkeypatch.setattr(O, "_send_text", lambda *a: sent.append(a) or {"success": True})
    from app.routers.evosys_sales_router import Ctx
    c = Ctx(manager, brand.id, True)
    with pytest.raises(HTTPException) as e:
        W._send_text_reply(db_session, c, s, o, None,
                           W.ReplyIn(body="Hi! Great to hear you're interested. Book: https://a.test/b/1", channel="text"))
    assert e.value.status_code == 409 and "already got this exact text" in e.value.detail and not sent


def test_two_quick_texts_get_one_answer(db_session, brand, manager, monkeypatch):
    ans = {"category": "interested", "summary": "", "simple": True, "draft": "Great - here is a time to talk: https://x.test"}
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, ans)
    fake.answers = [dict(ans), dict(ans, draft="Something else entirely: https://x.test")]
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="Sim", message_sid="SMq1")
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO, body="Yes", message_sid="SMq2")
    assert len(sent) == 1                                       # RR Cleaner got two; now one


def test_no_ai_text_to_someone_already_booked(db_session, brand, manager, monkeypatch):
    from app.models.scheduling_models import SalesAppointment
    ans = {"category": "question", "summary": "", "simple": True, "draft": "Let's set up a quick call: https://x.test"}
    o, fake, sent = _auto_world(db_session, brand, manager, monkeypatch, ans)
    t = datetime.utcnow() + timedelta(days=1)
    db_session.add(SalesAppointment(brand_sales_org_id=brand.id, opportunity_id=o.id, title="Business Growth Call",
                                    starts_at=t, ends_at=t + timedelta(minutes=30), status="scheduled"))
    db_session.commit()
    IN.handle(db_session, from_number="+12145550120", to_number=TEAM_NO,
              body="I am currently at work doing a client house", message_sid="SMb1")
    assert not sent and not fake.seen                           # Natasha: waits for Mike, no AI call at all


def test_language_follows_what_they_asked_for():
    ana = [{"from": "us", "text": "Hi Ana, 3 free appointments?"},
           {"from": "them", "text": "I would prefer text because my English is not very good"},
           {"from": "them", "text": "Spanish is my preferred language, but we can continue by text."},
           {"from": "them", "text": "I already provided the cities we serve."}]
    assert AI.preferred_language(ana, ana[-1]["text"]) == "Spanish"
    omar = [{"from": "them", "text": "Hello, good afternoon. What are the requirements?"}]
    assert AI.preferred_language(omar, omar[0]["text"]) == "English"
    assert AI.preferred_language([{"from": "them", "text": "Sí, me interesa"}], "Sí, me interesa") == "Spanish"


def _draft(db_session, brand, manager, monkeypatch, their_text, answer, convo=None):
    s = _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Cendejas Cleaning", phone="2145550306")
    for d, b in (convo or []):
        _msg(db_session, brand, o, "out" if d == "us" else "in", b, 60)
    r = _msg(db_session, brand, o, "in", their_text, 1)
    import json
    from types import SimpleNamespace as NS
    fake = NS(chat=NS(completions=NS(create=lambda **k: NS(choices=[NS(message=NS(content=json.dumps(answer)))],
                                                                usage=None))))
    monkeypatch.setattr(AI, "_get_client", lambda: fake)
    return AI.text_reply(db_session, s, None, o, r, IN.conversation(db_session, o.id), manager.id).output


def test_unapproved_claims_and_hard_questions_wait_for_mike(db_session, brand, manager, monkeypatch):
    out = _draft(db_session, brand, manager, monkeypatch, "How do you get these leads? Are they real?",
                 {"category": "question", "summary": "", "simple": True,
                  "draft": "Yes, they are verified and actively looking for cleaning help! https://x.test"})
    assert out["auto_ok"] is False and any("claim" in n for n in out["needs_person"])
    out = _draft(db_session, brand, manager, monkeypatch, "Can you send me your website and details in writing?",
                 {"category": "question", "summary": "", "simple": True, "draft": "Sure! Book a call: https://x.test"})
    assert out["auto_ok"] is False and any("only you can answer" in n for n in out["needs_person"])


def test_a_draft_that_repeats_an_earlier_text_is_not_sent(db_session, brand, manager, monkeypatch):
    pitch = ("Hi! Great to hear you're interested. Our goal is to book you at least 3 appointments in the next "
             "30 days with businesses that need cleaning, completely free.")
    # (a plain "Yes" gets the standard booking text instead - see test_evosys_sales_texting)
    out = _draft(db_session, brand, manager, monkeypatch, "Yes, tell me more",
                 {"category": "interested", "summary": "", "simple": True, "draft": pitch + " https://x.test/2"},
                 convo=[("us", pitch + " https://x.test/1")])
    assert out["auto_ok"] is False and any("repeats" in n for n in out["needs_person"])


def test_a_text_that_starts_with_stop_is_an_opt_out():
    # Stacey, Nov 19: "STOP I DONT NEED ANYMORE SCAMS..." was not treated as STOP
    from app.services.optout_parser import contains_hard_stop_language as f
    for t in ("STOP I DONT NEED ANYMORE SCAMS", "Stop. Not interested", "stop please", "STOP", "Stopall"):
        assert f(t), t
    for t in ("can I stop by Friday?", "Stop by Friday at 3", "stop in tomorrow?", "Yes", "I will stop working at 5",
              "stopping by later", "Stop over after lunch"):
        assert not f(t), t


def test_a_business_on_do_not_contact_is_never_in_needs_you(db_session, brand, manager):
    from app.routers import evosys_sales_router as R
    from app.services import sales_suppression as SDNC
    _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Stacey's", phone="2145550307")
    _msg(db_session, brand, o, "in", "I don't need this anymore", 30, label="not_interested")
    SDNC.record(db_session, o, manager, "asked us to stop")
    db_session.commit()
    out = R.overview(R.Ctx(manager, brand.id, True), db_session)
    assert not [m for m in out["replies"] if m["lead"]["id"] == o.id]
    assert [m for m in out["opted_out"] if m["lead"]["id"] == o.id]


def test_a_spanish_draft_always_comes_with_english(db_session, brand, manager, monkeypatch):
    monkeypatch.setattr(AI, "translate", lambda texts, *a, **k: ["Hi! Yes, we can keep talking by text."])
    out = _draft(db_session, brand, manager, monkeypatch, "Prefiero español, por favor. ¿Cuáles son los pasos?",
                 {"category": "question", "summary": "", "simple": True,
                  "draft": "¡Hola! Sí, podemos seguir por texto. ¿Qué zonas atiende?"})
    assert out["draft"].startswith("¡Hola!") and out["draft_english"] == "Hi! Yes, we can keep talking by text."


def test_never_says_we_have_no_website(db_session, brand, manager, monkeypatch):
    out = _draft(db_session, brand, manager, monkeypatch, "Sí, cuénteme más",
                 {"category": "interested", "summary": "", "simple": True,
                  "draft": "¡Hola! No tenemos un sitio web oficial, pero visite https://evosyspro.live"})
    assert out["auto_ok"] is False and any("claim" in n for n in out["needs_person"])


def test_a_plain_yes_never_carries_a_bad_draft(db_session, brand, manager, monkeypatch):
    # owner, Nov 23: a plain yes always gets the booking link, never waits
    out = _draft(db_session, brand, manager, monkeypatch, "Sí",
                 {"category": "unclear", "summary": "", "simple": False,
                  "draft": "¡Hola! No tenemos un sitio web oficial, pero visite https://evosyspro.live"})
    assert out["category"] == "interested" and out["auto_ok"] is True
    assert "sitio web" not in out["draft"] and "calendario" in out["draft"]
