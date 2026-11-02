# -*- coding: utf-8 -*-
"""Issue #452 P3 - outreach email + reply handling. A fake mailer stands in for
Microsoft 365: these tests make no network calls."""
import json
import uuid

import pytest
from fastapi import HTTPException

from app.models.acquisition_models import AcquisitionOpportunity
from app.models.models import EmailMessage, Lead, Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E
from app.services.acquisition import outreach as O
from app.services.auth_service import hash_password


def _org(db, name):
    o = Organization(name=name, slug="ot-%s" % uuid.uuid4().hex[:8], plan="enterprise", industry="cleaning",
                     is_active=True, enabled_features=json.dumps(["leads"]))
    db.add(o)
    db.commit()
    return o


def _god(db):
    u = User(organization_id=None, email="ot-%s@example.com" % uuid.uuid4().hex[:8], full_name="G",
             password_hash=hash_password("Pass12345!"), role="god_admin", is_active=True, must_change_password=False)
    db.add(u)
    db.commit()
    return u


class Mailer:
    def __init__(self, ok=True):
        self.ok, self.sent = ok, []

    def __call__(self, to_email, subject, body_html):
        self.sent.append((to_email, subject, body_html))
        return {"success": self.ok, "from_email": O.SEND_FROM, "error": None if self.ok else "refused"}


@pytest.fixture()
def setup(db_session, monkeypatch):
    db = db_session
    org = _org(db, "Outreach Test Cleaning")
    god = _god(db)
    E.enable(db, org, god)
    monkeypatch.setenv("ACQUISITION_OUTREACH_ENABLED", "1")
    monkeypatch.setenv("ACQUISITION_TEST_RECIPIENTS", "owner@example.com, owner2@example.com")
    return db, org, god


def _prospect(db, org, god, email, practice=True, name=None):
    o = E.add_prospect(db, org, god, {"business_name": name or "Biz %s" % uuid.uuid4().hex[:6],
                                      "website": "https://%s.example" % uuid.uuid4().hex[:8],
                                      "email": email, "segment": "medical_dental", "is_practice": practice})
    return o


def test_practice_send_only_to_test_inboxes(setup):
    db, org, god = setup
    ok = _prospect(db, org, god, "owner@example.com")
    bad = _prospect(db, org, god, "someone@realbiz.example")
    m = Mailer()
    res = O.send(db, org, ok, god, {}, mailer=m)
    assert res["stage"] == C.CONTACTED and not res["pilot_started"]
    assert m.sent[0][0] == "owner@example.com" and "reply STOP" in m.sent[0][2]
    with pytest.raises(HTTPException) as e:
        O.send(db, org, bad, god, {}, mailer=m)
    assert "test inboxes" in e.value.detail and len(m.sent) == 1
    # never twice without a reply
    with pytest.raises(HTTPException):
        O.send(db, org, ok, god, {}, mailer=m)
    assert db.query(EmailMessage).filter(EmailMessage.send_source == O.SEND_SOURCE).count() == 1
    assert E.get_profile(db, org.id).pilot_started_at is None


def test_switched_off_sends_nothing(setup, monkeypatch):
    db, org, god = setup
    monkeypatch.setenv("ACQUISITION_OUTREACH_ENABLED", "0")
    opp = _prospect(db, org, god, "owner@example.com")
    m = Mailer()
    with pytest.raises(HTTPException):
        O.send(db, org, opp, god, {}, mailer=m)
    assert m.sent == []


def test_real_send_gates_and_pilot_clock(setup):
    db, org, god = setup
    opp = _prospect(db, org, god, None, practice=False, name="Real Dental")
    lead = db.query(Lead).filter(Lead.id == opp.lead_id).first()
    lead.email = "office@realdental.example"
    db.commit()
    why = O.refusals(db, org, opp, lead)
    assert any("paused" in w for w in why) and any("mailing address" in w for w in why)
    assert any("verified" in w for w in why) and any("Contact verified" in w for w in why)
    E.update_profile(db, org.id, {"mailing_address": "1 Test St, Dallas, TX 75201"}, owner=True)
    E.set_paused(db, org.id, god, False, None)
    E.add_evidence(db, org.id, opp, god, {"kind": "contact_email", "value": "office@realdental.example",
                                          "source_url": "https://realdental.example/contact", "verified": True})
    E.owner_move(db, opp, god, {"stage": C.CONTACT_VERIFIED})
    assert O.refusals(db, org, opp, lead) == []
    m = Mailer()
    res = O.send(db, org, opp, god, {}, mailer=m)
    assert res["pilot_started"] and E.get_profile(db, org.id).pilot_started_at is not None
    assert "1 Test St, Dallas" in m.sent[0][2]


def test_failed_send_is_recorded_not_contacted(setup):
    db, org, god = setup
    opp = _prospect(db, org, god, "owner@example.com")
    with pytest.raises(HTTPException):
        O.send(db, org, opp, god, {}, mailer=Mailer(ok=False))
    db.refresh(opp)
    assert opp.stage == C.DISCOVERED
    assert db.query(EmailMessage).filter(EmailMessage.status == "failed").count() == 1


def test_reply_moves_and_stop_suppresses(setup):
    db, org, god = setup
    a = _prospect(db, org, god, "owner@example.com")
    b = _prospect(db, org, god, "owner2@example.com")
    O.send(db, org, a, god, {}, mailer=Mailer())
    O.send(db, org, b, god, {}, mailer=Mailer())
    la = db.query(Lead).filter(Lead.id == a.lead_id).first()
    lb = db.query(Lead).filter(Lead.id == b.lead_id).first()
    assert O.on_reply(db, la, "Yes, we'd like a quote. Call me Tuesday.") == "replied"
    assert O.on_reply(db, lb, "STOP") == "opted_out"
    db.commit()
    db.refresh(a), db.refresh(b)
    assert a.stage == C.REPLIED and b.stage == C.DISQUALIFIED and lb.status == "dnc"
    assert "owner2@example.com" in C.jload(E.get_profile(db, org.id).exclusions, [])
    # the opted-out address can never be emailed again, even as a new prospect
    with pytest.raises(HTTPException):
        _prospect(db, org, god, "owner2@example.com")
    # a lead outside the pilot is ignored
    other = Lead(organization_id=org.id, first_name="X", email="x@example.com")
    db.add(other)
    db.commit()
    assert O.on_reply(db, other, "STOP") is None


def test_practice_can_reach_interested_but_never_client(setup):
    db, org, god = setup
    opp = _prospect(db, org, god, "owner@example.com")
    O.send(db, org, opp, god, {}, mailer=Mailer())
    O.on_reply(db, db.query(Lead).filter(Lead.id == opp.lead_id).first(), "Interested")
    db.commit()
    E.owner_move(db, opp, god, {"stage": C.INTERESTED, "note": "Said interested"})
    assert opp.stage == C.INTERESTED
    assert E.list_client(db, org.id) == []
    assert E.funnel(db, org.id, include_practice=False)["interested"] == 0


def test_catch_up_applies_stored_replies_once(setup):
    from datetime import datetime, timedelta
    from app.models.models import Reply
    db, org, god = setup
    a = _prospect(db, org, god, "owner@example.com")
    b = _prospect(db, org, god, "owner2@example.com")
    O.send(db, org, a, god, {}, mailer=Mailer())
    O.send(db, org, b, god, {}, mailer=Mailer())
    later = datetime.utcnow() + timedelta(seconds=5)
    db.add(Reply(lead_id=a.lead_id, body="Yes, let's do it", source="email", received_at=later))
    db.add(Reply(lead_id=b.lead_id, body="stop", source="email", received_at=later))
    db.commit()
    assert O.catch_up(db) == 2
    db.refresh(a), db.refresh(b)
    assert a.stage == C.REPLIED and b.stage == C.DISQUALIFIED
    assert O.catch_up(db) == 0


def test_stop_followed_by_signature_is_an_opt_out_and_caught_up(setup):
    from datetime import datetime, timedelta
    from app.models.models import Reply
    from app.services.acquisition import harvey_adapted as H
    sig = "STOP\nMike Simmons\n\u201cI am what I am today because of the choices I made yesterday.\u201c"
    assert H.opt_out_reason(sig)["rule"] == "first_line"
    assert H.opt_out_reason("Stop by our office Tuesday, we'd like a quote") is None
    assert H.opt_out_reason("Alrighty now\nMike") is None
    db, org, god = setup
    b = _prospect(db, org, god, "owner2@example.com")
    O.send(db, org, b, god, {}, mailer=Mailer())
    lb = db.query(Lead).filter(Lead.id == b.lead_id).first()
    O.on_reply(db, lb, "Alrighty now")
    db.commit()
    db.refresh(b)
    assert b.stage == C.REPLIED
    db.add(Reply(lead_id=b.lead_id, body=sig, source="email", received_at=datetime.utcnow() + timedelta(seconds=5)))
    db.commit()
    assert O.catch_up(db) == 1
    db.refresh(b)
    assert b.stage == C.DISQUALIFIED and lb.status == "dnc"
    assert O.catch_up(db) == 0
