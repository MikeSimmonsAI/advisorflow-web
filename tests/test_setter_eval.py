"""The evaluation harness: 100 distinct scenarios; scoring rules; the run
uses synthetic contacts only and the real reply path."""
from unittest.mock import patch

from app.services.programs import ai_setter, setter_eval as E
from app.services.programs.setter_eval_scenarios import SCENARIOS
from tests.test_sci_appointment_setter import _Fake, _d, setter  # noqa: F401
from tests.test_outreach_program import _h, program  # noqa: F401


def test_one_hundred_varied_scenarios():
    assert len(SCENARIOS) == 100
    cats = {s["category"] for s in SCENARIOS}
    assert len(cats) >= 20
    assert sum(len(s["turns"]) > 1 for s in SCENARIOS) >= 35
    texts = [t["text"].lower() for s in SCENARIOS for t in s["turns"]]
    assert len(set(texts)) == len(texts)                     # no repeated family message


def test_scoring_catches_the_common_failures():
    assert "more than one question" in E.universal_checks({"reply": "Can I? Will you?"})
    assert "claims a booking that did not happen" in E.universal_checks({"reply": "You're all set for Friday", "booked": False})
    assert "should have booked" in E.score_turn({"acts": "book", "booked": True}, {"action": "book", "booked": False})
    assert E.score_turn({"acts": "offer_times"}, {"action": "offer_times", "reply": "Would Tuesday at 10 AM or Wednesday at 2 PM work?", "offered": ["x"]}) == []


def test_eval_endpoint_owner_only_and_synthetic(setter, client, monkeypatch):
    db, prog, admin = setter["db"], setter["prog"], setter["admin"]
    r = client.post("/program/setter/eval", headers=_h(db, admin), json={"scenario_id": "s001"})
    assert r.status_code == 403                               # a manager is not the owner
    admin.role = "god_admin"
    db.commit()
    fake = _Fake([_d("offer_times", "Would {TIMES} work for a visit?"), _d("book", "", slot_choice=1)])
    h = dict(_h(db, admin), **{"X-Org-Override": setter["org"].id})
    with patch.object(ai_setter, "_get_client", return_value=fake):
        r = client.post("/program/setter/eval", headers=h, json={"scenario_id": "s001"})
    out = r.json()
    assert r.status_code == 200 and out["passed"], out
    from app.models.models import Lead
    assert all(l.is_test and l.phone is None for l in db.query(Lead).filter(Lead.last_name.like("SIM-EVAL-%")))


def test_voice_check_catches_third_person_signed_replies():
    """Oct 16: 'Kerry Allan would be glad to help' went out signed Kerry Allan."""
    from app.services.programs import ai_setter
    assert ai_setter.third_person("If you have questions, Kerry Allan would be glad to help.", "Kerry Allan")
    assert ai_setter.third_person("so Kerry can go over it with you", "Kerry Allan")
    assert not ai_setter.third_person("Hi Ronald, this is Kerry with Alabama Heritage.", "Kerry Allan")
    assert not ai_setter.third_person("I'd be glad to go over it with you in person.", "Kerry Allan")
    assert "speaks about Kerry Allan in the third person" in E.universal_checks(
        {"reply": "Kerry Allan would be glad to help."}, "Kerry Allan")


def test_regression_scenarios_are_separate_and_reachable():
    from app.services.programs.setter_eval_scenarios import REGRESSIONS
    ids = {s["id"] for s in REGRESSIONS}
    assert {"r001", "r002", "r003", "r004"} <= ids and len(SCENARIOS) == 100
    assert E.scenario("r002")["turns"][0]["acts"] == "handoff"


def test_validator_rejects_third_person_reply(monkeypatch):
    from app.services.programs import ai_setter

    class C:
        id = "c"; offered_slots = None; state = "open"
    monkeypatch.setattr(ai_setter, "_last_ai_text", lambda conv: None)
    monkeypatch.setattr(ai_setter, "_strip_offered", lambda reply, conv: reply)
    d = {"action": "answer", "reply": "Kerry Allan would be glad to help with that."}
    probs = ai_setter.validate_decision(d, "thanks", C(), "Kerry Allan")
    assert any("third person" in p for p in probs)


def test_validator_requires_an_honest_answer_to_are_you_a_robot(monkeypatch):
    from app.services.programs import ai_setter

    class C:
        id = "c"; offered_slots = None; state = "open"
    monkeypatch.setattr(ai_setter, "_last_ai_text", lambda conv: None)
    monkeypatch.setattr(ai_setter, "_strip_offered", lambda reply, conv: reply)
    dodge = {"action": "answer", "reply": "This is Kerry with the family services team. How can I help?"}
    honest = {"action": "answer", "reply": "Fair question - an automated assistant helps me keep up with messages, and I see every reply."}
    assert any("automated" in p for p in ai_setter.validate_decision(dodge, "Is this a real person or a robot?", C()))
    assert ai_setter.validate_decision(honest, "Is this a real person or a robot?", C()) == []
