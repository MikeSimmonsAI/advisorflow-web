"""ONE CLICK, ONE CAMPAIGN - and a finding about the Campaign Builder route.

Both campaign send doors now refuse an identical repeat inside
BUILDER_DUPLICATE_WINDOW_S (409) before anything is written or sent, held in
action_leases so it holds across instances. No provider is contacted:
send_sms is replaced with a recorder.

FINDING (left for Mike, not changed): POST /campaigns/builder/send - the
Campaign Builder's Send button - is SHADOWED by POST /campaigns/{campaign_id}/send,
which is declared first, so "builder" is taken as a campaign id: advisors get
403 "Admin access required" and admins get 422. The builder send has never
been reachable. Re-ordering the routes would switch on a bulk SMS/email
sender that has never run in production, so it is a decision, not a fix.
test_builder_send_is_currently_shadowed pins today's behaviour so the change,
when made, is deliberate.
"""
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.models.models import Campaign, Lead, User


def _leads(db, org, advisor, n=2):
    out = []
    for i in range(n):
        lead = Lead(organization_id=org.id, assigned_to_id=advisor.id, first_name="Dup%d" % i,
                    last_name="Test", phone="1214555%04d" % (7100 + i), status="new", sms_consent=True)
        db.add(lead)
        out.append(lead)
    db.commit()
    return [lead.id for lead in out]


def _admin(db):
    return db.query(User).filter(User.email == "admin@restland.com").one()


def test_builder_send_is_currently_shadowed(client, db_session, sample_org, sample_advisor,
                                            auth_headers, admin_auth_headers):
    ids = _leads(db_session, sample_org, sample_advisor)
    body = {"name": "x", "message_template": "hi", "lead_ids": ids, "channel": "sms"}
    with patch("app.services.sms_service.send_sms") as sent:
        as_advisor = client.post("/campaigns/builder/send", json=body, headers=auth_headers)
        as_admin = client.post("/campaigns/builder/send", json=body, headers=admin_auth_headers)
    assert as_advisor.status_code == 403
    assert as_admin.status_code == 422
    sent.assert_not_called()


def test_campaign_send_double_click_sends_once(client, db_session, sample_org, sample_advisor,
                                               admin_auth_headers):
    _leads(db_session, sample_org, sample_advisor)
    camp = Campaign(organization_id=sample_org.id, name="Dup", created_by_id=_admin(db_session).id,
                    filter_criteria="{}")
    db_session.add(camp)
    db_session.commit()
    body = {"campaign_id": camp.id, "message": "Hello {first_name}"}
    with patch("app.services.sms_service.send_sms") as sent:
        first = client.post("/campaigns/%s/send" % camp.id, json=body, headers=admin_auth_headers)
        n = sent.call_count
        second = client.post("/campaigns/%s/send" % camp.id, json=body, headers=admin_auth_headers)
    assert first.status_code == 200, first.text
    assert n >= 1
    assert second.status_code == 409
    assert sent.call_count == n


def _builder(db, user, ids, msg="Quick check-in from our office."):
    from app.routers.campaign_router import BuilderSendRequest, builder_send
    req = BuilderSendRequest(name="Dup test", message_template=msg, lead_ids=ids,
                             include_booking_link=False, channel="sms")
    return builder_send(req=req, db=db, current_user=user)


def test_builder_function_refuses_an_identical_repeat(db_session, sample_org, sample_advisor):
    ids = _leads(db_session, sample_org, sample_advisor)
    with patch("app.services.sms_service.send_sms") as sent:
        _builder(db_session, sample_advisor, ids)
        n = sent.call_count
        with pytest.raises(HTTPException) as exc:
            _builder(db_session, sample_advisor, ids)
        _builder(db_session, sample_advisor, ids, "A different note.")   # not a duplicate
        _builder(db_session, sample_advisor, ids[:1])                      # different people
    assert exc.value.status_code == 409
    assert sent.call_count == n * 2 + 1
    assert db_session.query(Campaign).filter(Campaign.name == "Dup test").count() == 3
