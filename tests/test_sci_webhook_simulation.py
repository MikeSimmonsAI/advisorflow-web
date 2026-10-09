"""Signed inbound SMS/voice simulation (gate T4): stdlib logic, no DB, no Twilio."""
from app.services.programs import readiness_check as rc
from app.services.programs import webhook_simulation as ws


def test_all_simulation_scenarios_pass_with_zero_outbound():
    out = ws.run_all()
    assert (out["fail_count"], out["failed_gate"], out["outbound"], out["sent"]) == (0, None, 0, 0)


def test_forged_signature_has_no_side_effects():
    tok = ws.new_token()
    req = ws.sign(ws.new_token(), ws.SMS_PATH, {"From": "+12055550111", "To": ws.POOL_205_NUMBER, "Body": "STOP"})
    r = ws.handle(tok, req)
    assert r["status"] == 403 and not r["accepted"] and r["cadence"] is None


def test_readiness_run_includes_webhook_proof():
    assert rc.run_all()["webhook_proof"]["fail_count"] == 0
