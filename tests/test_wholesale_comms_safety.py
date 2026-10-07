"""Wholesale seller comms safety: wrong number, STOP idempotency, bypass.

Real service logic, in-memory SQLite, counting fake provider. Nothing is sent.
"""
import pytest

from app.models.models import Lead, Organization, SuppressionEntry
from app.services import sms_service
from tests.test_wholesale_cadence import make_deal, ok
from tests.test_wholesale_seller_sms_program import (  # noqa: F401  (fixtures)
    _FakeTwilio, fake_twilio, world, enable, _advisor)


def _entries(db, org_id=None):
    q = db.query(SuppressionEntry)
    if org_id:
        q = q.filter(SuppressionEntry.organization_id == org_id)
    return q.all()


def _start(client, headers, deal_id):
    ok(client.post("/wholesale/deals/%s/cadence" % deal_id, headers=headers,
                   json={"action": "start"}))


def _reply(client, headers, deal_id, msg):
    return ok(client.post("/wholesale/deals/%s/seller-reply" % deal_id,
                          headers=headers, json={"message": msg}))


def _lead(db):
    return db.query(Lead).filter(Lead.phone.like("%5556100%")).first()


def _adv(db, lead):
    return _advisor(db, db.get(Organization, lead.organization_id))


def test_wrong_number_suppresses_the_phone_stops_cadence_and_is_idempotent(
        client, auth_headers, db_session, fake_twilio):
    prop = make_deal(client, auth_headers, address="301 Wrong St")
    deal_id = prop["deal"]["id"]
    _start(client, auth_headers, deal_id)
    out = _reply(client, auth_headers, deal_id, "wrong number")
    assert out["suppression"] and "suppressed" in out["suppression"]
    lead = _lead(db_session)
    ents = _entries(db_session)
    assert len(ents) == 1 and ents[0].organization_id == lead.organization_id
    assert "Wrong number" in ents[0].reason
    assert lead.status != "dnc"                      # not an owner refusal
    st = ok(client.get("/wholesale/deals/%s/cadence" % deal_id, headers=auth_headers))
    assert st["state"] == "stopped_deal_dead"
    # Replay of the same inbound: still exactly one suppression row.
    _reply(client, auth_headers, deal_id, "wrong number")
    assert len(_entries(db_session)) == 1
    # Send boundary refuses; provider never called.
    adv = _adv(db_session, lead)
    for src in (None, "manual", "cadence", "ai_conversation"):
        with pytest.raises(ValueError):
            sms_service.send_sms(db_session, adv, lead, "Hi", send_source=src)
    assert fake_twilio.sent == []


def test_wrong_number_does_not_suppress_other_tenants(client, auth_headers, db_session):
    prop = make_deal(client, auth_headers, address="302 Wrong St")
    _reply(client, auth_headers, prop["deal"]["id"], "you have the wrong person")
    ents = _entries(db_session)
    assert len(ents) == 1
    for o in db_session.query(Organization).all():
        if o.id != ents[0].organization_id:
            assert _entries(db_session, o.id) == []


def test_repeated_stop_is_idempotent_and_blocks_every_send_path(
        client, auth_headers, db_session, fake_twilio):
    prop = make_deal(client, auth_headers, address="303 Stop St")
    deal_id = prop["deal"]["id"]
    _start(client, auth_headers, deal_id)
    for _ in range(3):
        _reply(client, auth_headers, deal_id, "STOP")
    lead = _lead(db_session)
    assert len(_entries(db_session)) == 1
    assert lead.status == "dnc"
    st = ok(client.get("/wholesale/deals/%s/cadence" % deal_id, headers=auth_headers))
    assert st["state"] == "stopped_dnc"
    adv = _adv(db_session, lead)
    for src in (None, "manual", "cadence", "ai_conversation", "pipeline_auto_reply"):
        with pytest.raises(ValueError):
            sms_service.send_sms(db_session, adv, lead, "Hi", send_source=src)
    assert fake_twilio.sent == []


def test_noise_after_stop_does_not_resume_the_cadence(client, auth_headers, db_session):
    prop = make_deal(client, auth_headers, address="304 Noise St")
    deal_id = prop["deal"]["id"]
    _start(client, auth_headers, deal_id)
    _reply(client, auth_headers, deal_id, "STOP")
    for noise in ("ok", "Thanks", "Who is this?"):
        _reply(client, auth_headers, deal_id, noise)
    st = ok(client.get("/wholesale/deals/%s/cadence" % deal_id, headers=auth_headers))
    assert st["state"] == "stopped_dnc"
    assert len(_entries(db_session)) == 1
