"""ONE CLICK, ONE CALL - POST /voice/call/{lead_id}.

The redial cooldown reads the lead's past calls, so two clicks arriving
together both see none and both ring the person. A lease on the lead now
decides; it is released when no call was placed so a retry works at once.
No provider is contacted: eligibility and the call itself are stubbed.
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.models.models import Lead


def _lead(db, org, advisor):
    lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name="Call",
                last_name="Once", phone="12145557300", status="new")
    db.add(lead)
    db.commit()
    return lead


def _stubs(start):
    return (patch("app.services.voice_bulk_gate.call_refusal", return_value=None),
            patch("app.services.voice_orchestrator.check_call_eligibility",
                  return_value=SimpleNamespace(ok=True, reason=None)),
            patch("app.services.voice_orchestrator.start_file_check_call", side_effect=start))


def _call():
    return SimpleNamespace(id="c1", provider="retell", provider_call_id="p1", agent_id="a",
                           from_phone="+12145550000", call_number=1, status="initiating",
                           error_message=None)


def test_a_call_already_being_placed_refuses_the_second(client, db_session, sample_org,
                                                       sample_advisor, auth_headers):
    from app.services import action_lease
    lead = _lead(db_session, sample_org, sample_advisor)
    assert action_lease.acquire(db_session, "voice.call", lead.id, ttl_seconds=90)  # the other click
    a, b, c = _stubs(lambda *x, **k: _call())
    with a, b, c as start:
        r = client.post("/voice/call/%s" % lead.id, headers=auth_headers)
    assert r.status_code == 409
    start.assert_not_called()


def test_a_failed_attempt_releases_the_claim(client, db_session, sample_org, sample_advisor, auth_headers):
    lead = _lead(db_session, sample_org, sample_advisor)
    outcomes = [RuntimeError("provider down"), _call()]

    def start(*a, **k):
        o = outcomes.pop(0)
        if isinstance(o, Exception):
            raise o
        return o
    a, b, c = _stubs(start)
    with a, b, c:
        first = client.post("/voice/call/%s" % lead.id, headers=auth_headers)
        second = client.post("/voice/call/%s" % lead.id, headers=auth_headers)
        third = client.post("/voice/call/%s" % lead.id, headers=auth_headers)
    assert first.status_code == 502
    assert second.status_code == 200, second.text
    assert third.status_code == 409          # second succeeded; claim held
