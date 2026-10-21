"""SCI POC (Oct 21): a reply the AI has already answered is not shown as a
family waiting on staff. Root cause found in production: a reply written
outside contact hours was HELD, then sent by the held-reply sweep, and the
family's message stayed "new" - so SCI Live said "families need a reply" and
Pipeline Health said "HOT reply past its response time" for families the AI
had already answered. The sweep now marks the message answered; screens also
recognise replies answered that way before this fix."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.program_models import ProgramConversationTurn, ProgramResponse
from app.services.programs import ai_setter as S
from app.services.programs import pipeline_health as PH
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401 (fixture)
from tests.test_sci_appointment_setter import _Fake, _d, _run, setter  # noqa: F401 (fixture)


@pytest.fixture
def auto(setter, monkeypatch):
    """The AI answers for real (auto mode); the text provider is mocked."""
    setter["prog"].ai_setter_mode = "auto"
    setter["db"].commit()
    sent = []
    monkeypatch.setattr("app.services.sms_service.send_sms",
                        lambda *a, **k: sent.append(a) or SimpleNamespace(id="SM-mock-%d" % len(sent)))
    return dict(setter, sent=sent)


def _home(client, db, admin):
    return client.get("/program/command", headers=_h(db, admin)).json()


def _real(db, lead):
    lead.is_test = False
    db.commit()
    return lead


def test_a_held_reply_sent_later_marks_the_family_answered(client, auto, sample_advisor, monkeypatch):
    db, org, admin, prog = auto["db"], auto["org"], auto["admin"], auto["prog"]
    lead = _real(db, _lead_for(db, org, sample_advisor, "L001"))
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": False})
    _run(db, lead, "Yes I'd like to come in to talk about a plan", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    resp = db.query(ProgramResponse).filter_by(lead_id=lead.id).one()
    assert resp.handling_status == "new"                             # held overnight: not answered yet
    assert _home(client, db, admin)["counts"]["needs_attention"] == 1
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": True})
    S.sweep(db)
    db.refresh(resp)
    assert len(auto["sent"]) == 1 and resp.handling_status == "responded"
    home = _home(client, db, admin)
    assert home["counts"]["needs_attention"] == 0 and home["filter_counts"]["needs_reply"] == 0
    rep = PH.report(db, prog, now=datetime.utcnow() + timedelta(hours=2))
    assert "hot_past_sla" not in {p["key"] for p in rep["problems"]}


def test_replies_answered_before_the_fix_are_recognised(client, auto, sample_advisor, monkeypatch):
    """Existing production rows: the AI's held reply went out but the message stayed 'new'."""
    db, org, admin, prog = auto["db"], auto["org"], auto["admin"], auto["prog"]
    lead = _real(db, _lead_for(db, org, sample_advisor, "L001"))
    monkeypatch.setattr("app.services.contact_hours.check", lambda lead, now=None: {"permitted": False})
    _run(db, lead, "Yes I'd like to come in to talk about a plan", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    t = db.query(ProgramConversationTurn).filter_by(lead_id=lead.id, role="ai").one()
    t.delivery = "sent"                                              # sent by the old sweep, message left 'new'
    db.commit()
    home = _home(client, db, admin)
    item = next(i for i in home["inbox"] if i["lead_id"] == lead.id)
    assert item["ai_answered"] is True and item["sla_breached"] is False
    assert home["counts"]["needs_attention"] == 0
    rep = PH.report(db, prog, now=datetime.utcnow() + timedelta(hours=2))
    assert "hot_past_sla" not in {p["key"] for p in rep["problems"]}


def test_family_handed_to_a_person_still_needs_a_reply(client, setter, sample_advisor):
    db, org, admin, prog = setter["db"], setter["org"], setter["admin"], setter["prog"]
    lead = _real(db, _lead_for(db, org, sample_advisor, "L001"))
    _run(db, lead, "Can someone call me about my mother's arrangements? I'd like to come in",
         _Fake([_d("handoff", "Thank you - I'll have someone reach out to you shortly.", reason="needs a person")]))
    home = _home(client, db, admin)
    item = next(i for i in home["inbox"] if i["lead_id"] == lead.id)
    assert item["ai_answered"] is False and home["counts"]["needs_attention"] == 1
    rep = PH.report(db, prog, now=datetime.utcnow() + timedelta(hours=2))
    assert "hot_past_sla" in {p["key"] for p in rep["problems"]}


def test_a_draft_is_not_an_answer(client, setter, sample_advisor):
    db, org, admin = setter["db"], setter["org"], setter["admin"]                # setter fixture = draft mode
    lead = _real(db, _lead_for(db, org, sample_advisor, "L001"))
    _run(db, lead, "Yes I'd like to come in", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    item = next(i for i in _home(client, db, admin)["inbox"] if i["lead_id"] == lead.id)
    assert item["ai_answered"] is False and _home(client, db, admin)["counts"]["needs_attention"] == 1


def test_a_new_message_after_the_ai_answered_needs_attention_again(client, auto, sample_advisor):
    db, org, admin = auto["db"], auto["org"], auto["admin"]
    lead = _real(db, _lead_for(db, org, sample_advisor, "L001"))
    _run(db, lead, "Yes I'd like to come in", _Fake([_d("offer_times", "Would {TIMES} work?")]))
    auto["prog"].ai_setter_mode = "off"                              # nothing answers the next one
    db.commit()
    _run(db, lead, "Actually can you call me instead?", _Fake([]))
    item = next(i for i in _home(client, db, admin)["inbox"] if i["lead_id"] == lead.id)
    assert item["ai_answered"] is False and _home(client, db, admin)["counts"]["needs_attention"] == 1
