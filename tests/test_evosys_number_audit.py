"""Number audit reads the provider and reports where texts really came from."""
from types import SimpleNamespace as NS

import pytest
from fastapi import HTTPException

from app.models.evosys_sales_models import EvoSysOutreachMessage as M
from app.services.evosys_sales import number_audit as NA
from app.services.evosys_sales import sms_setup

from tests.test_sales_workspace import platform, brand, manager, _make_sales_user, _SEQ  # noqa: F401
from tests.test_evosys_sales_texting import _ready, _lead, _brand_env  # noqa: F401


class _List:
    def __init__(self, rows): self.rows = rows
    def list(self, **k): return self.rows


def test_audit_reports_capabilities_and_real_senders(db_session, brand, manager, monkeypatch):
    s = _ready(db_session, brand)
    o = _lead(db_session, brand, manager, "Shine", phone="2145550401")
    for i, (lab, step) in enumerate([(None, 1), ("ai_answer", None), ("booking_confirmation", None)]):
        db_session.add(M(brand_sales_org_id=brand.id, opportunity_id=o.id, direction="out", channel="text",
                         body="x", status="sent", label=lab, step_no=step, provider_message_id="SM%d" % i))
    db_session.commit()
    num = NA and NS(phone_number="+18664870628", sid="PN1", account_sid="AC1", friendly_name="EvoSys",
                    capabilities={"voice": True, "sms": True, "mms": False}, sms_url="https://x/sms",
                    voice_url="", status_callback="", voice_application_sid="", sms_application_sid="", trunk_sid=None)
    msg = lambda sid: NS(fetch=lambda: NS(from_="+18664870628", to="+12145550401", status="delivered",
                                          error_code=None, date_created="2026-11-20"))
    client = NS(api=NS(v2010=NS(accounts=lambda sid: NS(fetch=lambda: NS(sid="AC1", friendly_name="EVOSYS Pro",
                                                                         type="Full", status="active", owner_account_sid="AC1")))),
                messaging=NS(v1=NS(tollfree_verifications=_List([NS(tollfree_phone_number_sid="PN1", status="TWILIO_APPROVED")]))),
                messages=lambda sid=None: msg(sid), calls=_List([]))
    client.messages = type("Msgs", (), {"__call__": lambda self, sid: msg(sid), "list": lambda self, **k: []})()
    monkeypatch.setattr(sms_setup, "_client", lambda: client)
    monkeypatch.setattr(sms_setup, "_number", lambda c, s: (num, None))
    out = NA.run(db_session, s)
    assert out["number"]["voice"] is True and out["number"]["voice_url"] is None
    assert out["tollfree_verification"][0]["status"] == "TWILIO_APPROVED"
    assert set(out["evosys_texts_sent_from"]) == {"campaign", "ai_answer", "booking_confirmation"}
    assert out["all_customer_texts_from_this_number"] is True
    assert out["evosys_texts_sent_from"]["campaign"][0]["to"] == "•••0401"


def test_audit_is_owner_only(db_session, brand, manager):
    from app.routers import evosys_sales_router as R
    with pytest.raises(HTTPException) as e:
        R.texting_audit(R.Ctx(manager, brand.id, True), db_session)
    assert e.value.status_code == 403
