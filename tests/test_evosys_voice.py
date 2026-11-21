"""Calling from the EvoSys Sales number: caller ID is always the team number,
Do Not Contact is refused, incoming calls ring the owner, everything lands on
the lead's timeline, webhooks must be signed by the platform account."""
from types import SimpleNamespace as NS

import pytest

from app.models.evosys_sales_models import EvoSysOutreachMessage as M
from app.services.evosys_sales import voice as V
from app.services.evosys_sales import sms_inbound as SI

from tests.test_sales_workspace import platform, brand, manager, _make_sales_user, _SEQ  # noqa: F401
from tests.test_evosys_sales_texting import _ready, _lead, _brand_env, TEAM_NO  # noqa: F401


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    monkeypatch.setattr(V, "_base", lambda: "https://api.test")


def test_outbound_dials_the_lead_from_the_team_number(db_session, brand, manager):
    s = _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Ana's", phone="2145550501")
    xml = V.outbound(db_session, s, {"From": "client:evs_" + manager.id, "lead_id": o.id, "CallSid": "CA1"})
    assert 'callerId="%s"' % TEAM_NO in xml and "+12145550501" in xml and "<Number" in xml
    row = db_session.query(M).filter_by(provider_message_id="CA1").one()
    assert row.channel == "call" and row.actor_user_id == manager.id and row.opportunity_id == o.id
    V.status_update(db_session, {"ParentCallSid": "CA1", "CallSid": "CA2", "CallStatus": "completed", "CallDuration": "42"})
    db_session.refresh(row)
    assert row.status == "completed" and row.body.endswith("42s")
    V.record_outcome(db_session, o, row.id, "talked", "Wants Tuesday", manager)
    assert db_session.get(M, row.id).label == "talked"


def test_outbound_refuses_dnc_strangers_and_other_brands(db_session, brand, manager):
    from app.services import sales_suppression as SDNC
    s = _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Stacey's", phone="2145550502")
    SDNC.record(db_session, o, manager, "stop"); db_session.commit()
    assert "Do Not Contact" in V.outbound(db_session, s, {"From": "client:evs_" + manager.id, "lead_id": o.id})
    assert "can't be placed" in V.outbound(db_session, s, {"From": "client:someone", "lead_id": o.id})
    assert "can't be placed" in V.outbound(db_session, s, {"From": "client:evs_" + manager.id, "lead_id": "nope"})
    assert db_session.query(M).filter_by(channel="call").count() == 0


def test_a_callback_rings_the_owner_and_a_missed_one_alerts(db_session, brand, manager, monkeypatch):
    s = _ready(db_session, brand)
    s.alert_phone = "+18435328405"; db_session.commit()
    o = _lead(db_session, brand, manager, "Natasha's", phone="2145550503")
    xml = V.inbound(db_session, s, {"From": "+12145550503", "To": TEAM_NO, "CallSid": "CA9"})
    assert "+18435328405" in xml and 'callerId="+12145550503"' in xml
    assert db_session.query(M).filter_by(provider_message_id="CA9", direction="in", channel="call").one()
    alerts = []
    monkeypatch.setattr(SI, "notify_owner", lambda s, line, subj, html: alerts.append(line))
    xml = V.inbound_done(db_session, s, {"CallSid": "CA9", "DialCallStatus": "no-answer", "From": "+12145550503"})
    assert "missed you" in xml and alerts and "Natasha's" in alerts[0]
    s.voice_forward_to = "+12145550999"; db_session.commit()
    assert "+12145550999" in V.inbound(db_session, s, {"From": "+19995550000", "CallSid": "CA10"})


def test_token_is_refused_until_calling_is_set_up(db_session, brand, manager):
    s = _ready(db_session, brand)
    out = V.token(s, manager)
    assert out["ready"] is False and "set up" in out["blocker"]


def test_call_webhooks_need_the_platform_signature(db_session, brand, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACplatform")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    c = TestClient(app)
    r = c.post("/evosys-sales-public/voice/outbound", data={"AccountSid": "ACother", "From": "client:evs_x"})
    assert r.status_code == 403
    r = c.post("/evosys-sales-public/voice/inbound", data={"AccountSid": "ACplatform", "From": "+1"})
    assert r.status_code == 403                                  # no signature
