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
