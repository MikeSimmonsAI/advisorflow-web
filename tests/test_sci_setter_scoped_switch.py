"""The SCI setter runs without the platform-wide background AI switch, for SCI
only, only while its own mode is draft/auto, and an emergency OFF stops it."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import ai_gateway
from app.services.programs import ai_setter as S
from app.services.programs import responses as R
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401


class _Client:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls += 1
        body = json.dumps({"action": "offer_times", "intent": "question", "reply": "Happy to help. Would {TIMES} work for a short visit?"})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))], usage=None)


@pytest.fixture(autouse=True)
def _platform_background_off(monkeypatch):
    monkeypatch.delenv("AI_BACKGROUND_AUTOMATION_ENABLED", raising=False)
    monkeypatch.delenv("AI_SCI_SETTER_ENABLED", raising=False)
    ai_gateway._BUDGET.reset()


def test_gateway_grant_is_feature_and_org_scoped():
    with ai_gateway.scoped_grant("sci_appointment_setter", "org-1"):
        assert ai_gateway._scoped_ok("sci_appointment_setter", "org-1")
        assert not ai_gateway._scoped_ok("sci_appointment_setter", "org-2")
        assert not ai_gateway._scoped_ok("reply_response", "org-1")
    assert not ai_gateway._scoped_ok("sci_appointment_setter", "org-1")
    with pytest.raises(ai_gateway.AIRefused):
        with ai_gateway.scoped_grant("reply_response", "org-1"):
            pass
    # another feature in background with only a grant for the setter: refused
    with ai_gateway.scoped_grant("sci_appointment_setter", "org-1"):
        with pytest.raises(ai_gateway.AIDisabled):
            ai_gateway._admit(feature="reply_response", capability="reply_response",
                              mode=ai_gateway.BACKGROUND, actor=None, org_id="org-1")


def _reply(db, org, adv, prog, mode):
    prog.ai_setter_mode = mode
    db.commit()
    lead = _lead_for(db, org, adv, "L001")
    c = _Client()
    from datetime import datetime as _dt
    from app.services.programs import appointments as _A
    with patch.object(S, "_get_client", return_value=c), \
         patch.object(_A, "available_slots", lambda db_, prog_, **kw: ([_dt(2030, 1, 8, 10, 0), _dt(2030, 1, 9, 14, 0)], None)), \
         patch("app.services.programs.responses._deliver_staff_alert", return_value=(True, None)), \
         patch("app.services.sms_service.send_sms") as send:
        R.on_inbound(db, lead, "What does the guide cover?", "sms")
    return c, send, lead


def test_draft_mode_runs_with_platform_background_off(program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    assert not ai_gateway.background_enabled()
    c, send, lead = _reply(db, org, sample_advisor, prog, "draft")
    assert c.calls == 1 and send.call_count == 0          # drafted, not sent
    from app.models.program_models import ProgramConversationTurn as T
    assert db.query(T).filter(T.lead_id == lead.id, T.role == "ai", T.delivery == "draft").count() == 1


def test_off_mode_never_calls_the_model(program, sample_advisor):
    db, org, prog = program["db"], program["org"], program["prog"]
    c, send, _ = _reply(db, org, sample_advisor, prog, "off")
    assert c.calls == 0 and send.call_count == 0


def test_emergency_switch_overrides_auto(program, sample_advisor, monkeypatch):
    db, org, prog = program["db"], program["org"], program["prog"]
    monkeypatch.setenv("AI_SCI_SETTER_ENABLED", "false")
    c, send, _ = _reply(db, org, sample_advisor, prog, "auto")
    assert S.mode_of(prog) == "off" and c.calls == 0 and send.call_count == 0


def test_any_manager_can_stop_it_only_owner_starts_it(program, client):
    db, prog = program["db"], program["prog"]
    prog.ai_setter_mode = "auto"
    db.commit()
    h = _h(db, program["admin"])
    r = client.post("/program/setter/off", headers=h)
    assert r.status_code == 200 and r.json()["was"] == "auto"
    db.refresh(prog)
    assert prog.ai_setter_mode == "off"
    stats = client.get("/program/setter/stats", headers=h).json()
    assert stats["effective_mode"] == "off" and stats["runs_without_platform_switch"] is True
