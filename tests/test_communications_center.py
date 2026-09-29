"""Communications command center (WS5): queue, summary, reply actions, thread,
compose gate and the MANUAL send path. No test here reaches Twilio: sends are
either refused before send_sms, or send_sms runs against a patched
_resolve_twilio_creds (the same fake the sms_service tests use)."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.models.models import (Lead, Message, Organization, Reply, ReplyClassification,
                               SuppressionEntry, User)
from app.models.work_models import ReplyState
from app.services.auth_service import create_access_token, hash_password


def _mount():
    from app.main import app
    from app.routers.work_router import router
    if not any(getattr(r, "path", "") == "/communications/replies" for r in app.routes):
        app.include_router(router)


_mount()


@pytest.fixture(autouse=True)
def _no_twilio_client(monkeypatch):
    """Belt and braces: sms_service imports twilio's Client BY NAME, so the
    conftest guard on twilio.rest.Client does not cover it. Any test here that
    reaches a real client construction fails loudly instead of dialling out."""
    def _refuse(*a, **k):
        raise AssertionError("a test tried to build a real Twilio client")
    monkeypatch.setattr("app.services.sms_service.Client", _refuse)


@pytest.fixture()
def admin(db_session, sample_org):
    u = User(organization_id=sample_org.id, email="boss@restland.com",
             password_hash=hash_password("x"), full_name="Boss Admin", role="org_admin",
             must_change_password=False)
    db_session.add(u)
    db_session.commit()
    return u


def _h(db, user):
    return {"Authorization": "Bearer %s" % create_access_token(user, db)}


@pytest.fixture()
def other_org(db_session):
    org = Organization(name="Other Tenant Co", slug="other-tenant-co", plan="trial")
    db_session.add(org)
    db_session.commit()
    u = User(organization_id=org.id, email="x@other.com", password_hash=hash_password("x"),
             full_name="Other Admin", role="org_admin", must_change_password=False)
    db_session.add(u)
    db_session.commit()
    lead = Lead(organization_id=org.id, assigned_to_id=u.id, first_name="Foreign",
                last_name="Person", phone="+12145550000")
    db_session.add(lead)
    db_session.commit()
    r = Reply(lead_id=lead.id, body="other org reply", classification=ReplyClassification.INTERESTED)
    db_session.add(r)
    db_session.commit()
    return {"org": org, "user": u, "lead": lead, "reply": r}


def _reply(db, lead, body, cls=ReplyClassification.NEUTRAL, **kw):
    r = Reply(lead_id=lead.id, body=body, classification=cls, **kw)
    db.add(r)
    db.commit()
    return r


# ── queue ────────────────────────────────────────────────────────────────────

def test_replies_requires_auth(client):
    assert client.get("/communications/replies").status_code == 401


def test_pagination_goes_beyond_200(client, db_session, sample_lead, auth_headers):
    base = datetime(2026, 1, 1)
    db_session.add_all([Reply(lead_id=sample_lead.id, body="msg %d" % i,
                              received_at=base + timedelta(minutes=i)) for i in range(230)])
    db_session.commit()
    r = client.get("/communications/replies?page=3&page_size=100", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 230
    assert body["pages"] == 3
    assert len(body["items"]) == 30
    # newest first: page 3 holds the oldest 30
    assert body["items"][-1]["body"] == "msg 0"
    # page_size is capped
    assert client.get("/communications/replies?page_size=500", headers=auth_headers).status_code == 422


def test_search_body_name_phone_email(client, db_session, sample_org, sample_advisor, auth_headers):
    a = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Maria",
             last_name="Sanchez", phone="+14695551234", email="maria@example.com")
    b = Lead(organization_id=sample_org.id, assigned_to_id=sample_advisor.id, first_name="Robert",
             last_name="King", phone="+12145557777")
    db_session.add_all([a, b])
    db_session.commit()
    _reply(db_session, a, "What are my options for a 2-year plan?")
    _reply(db_session, b, "Call me this afternoon")

    def ids(q):
        return [i["contact_name"] for i in client.get("/communications/replies", params={"q": q},
                                                       headers=auth_headers).json()["items"]]
    assert ids("2-year") == ["Maria Sanchez"]
    assert ids("robert king") == ["Robert King"]
    assert ids("(214) 555-7777") == ["Robert King"]
    assert ids("maria@example") == ["Maria Sanchez"]
    assert ids("nobody-matches") == []


def test_filters(client, db_session, sample_lead, auth_headers):
    _reply(db_session, sample_lead, "yes please", ReplyClassification.INTERESTED)
    _reply(db_session, sample_lead, "call me", ReplyClassification.CALLBACK)
    _reply(db_session, sample_lead, "STOP", ReplyClassification.DNC)
    _reply(db_session, sample_lead, "by email", ReplyClassification.QUESTION, source="email")
    old = _reply(db_session, sample_lead, "old one", ReplyClassification.NEUTRAL)
    old.received_at = datetime(2025, 1, 1)
    db_session.commit()

    def n(**params):
        r = client.get("/communications/replies", params=params, headers=auth_headers)
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert n() == 5
    assert n(classification="interested,callback") == 2
    assert n(needs_attention="true") == 2
    assert n(callbacks="true") == 1
    assert n(dnc="true") == 1
    assert n(channel="email") == 1
    assert n(date_from="2025-01-01", date_to="2025-01-01") == 1
    assert n(reviewed="false") == 5
    assert client.get("/communications/replies?classification=bogus",
                      headers=auth_headers).status_code == 400
    assert client.get("/communications/replies?sort=bogus", headers=auth_headers).status_code == 400


def test_advisor_sees_only_own_leads(client, db_session, sample_org, second_advisor, sample_lead, auth_headers):
    other = Lead(organization_id=sample_org.id, assigned_to_id=second_advisor.id, first_name="Not",
                 last_name="Mine", phone="+12145550101")
    db_session.add(other)
    db_session.commit()
    theirs = _reply(db_session, other, "not yours", ReplyClassification.INTERESTED)
    _reply(db_session, sample_lead, "mine")
    body = client.get("/communications/replies", headers=auth_headers).json()
    assert [i["body"] for i in body["items"]] == ["mine"]
    assert client.post("/communications/replies/%s/review" % theirs.id, json={},
                       headers=auth_headers).status_code == 404
    assert client.get("/communications/thread/%s" % other.id, headers=auth_headers).status_code == 404


def test_cross_tenant_is_404(client, db_session, admin, other_org):
    h = _h(db_session, admin)
    assert client.get("/communications/replies", headers=h).json()["total"] == 0
    rid, lid = other_org["reply"].id, other_org["lead"].id
    assert client.post("/communications/replies/%s/review" % rid, json={}, headers=h).status_code == 404
    assert client.post("/communications/replies/%s/assign" % rid, json={"assigned_to_id": None},
                       headers=h).status_code == 404
    assert client.post("/communications/replies/%s/callback-task" % rid, json={},
                       headers=h).status_code == 404
    assert client.get("/communications/thread/%s" % lid, headers=h).status_code == 404
    assert client.get("/communications/compose-gate/%s" % lid, headers=h).status_code == 404
    r = client.post("/communications/send", json={"lead_id": lid, "body": "hi"}, headers=h)
    assert r.status_code == 404


# ── summary + actions ────────────────────────────────────────────────────────

def test_summary_counts_are_real_and_agree_with_legacy(client, db_session, sample_lead, auth_headers):
    hot = _reply(db_session, sample_lead, "yes", ReplyClassification.INTERESTED)
    _reply(db_session, sample_lead, "call", ReplyClassification.CALLBACK)
    _reply(db_session, sample_lead, "STOP", ReplyClassification.DNC)
    _reply(db_session, sample_lead, "ok", ReplyClassification.NEUTRAL)

    s = client.get("/communications/summary", headers=auth_headers).json()
    assert s == {**s, "needs_attention": 2, "callbacks": 1, "reviewed": 0, "dnc_stop": 1,
                 "total": 4, "unreviewed": 4, "ai_handling": 0}

    r = client.post("/communications/replies/%s/review" % hot.id, json={}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "reviewed"
    s = client.get("/communications/summary", headers=auth_headers).json()
    assert s["reviewed"] == 1 and s["needs_attention"] == 1
    legacy = client.get("/sms/replies/counts", headers=auth_headers).json()
    assert legacy["reviewed"] == 1 and legacy["needs_follow_up"] == 1
    db_session.refresh(hot)
    assert hot.reviewed_at is not None

    # re-open
    r = client.post("/communications/replies/%s/review" % hot.id, json={"reviewed": False},
                    headers=auth_headers)
    assert r.json()["status"] == "new"


def test_ai_handling_counts_ai_messages_after_reply(client, db_session, sample_lead, sample_advisor, auth_headers):
    r = _reply(db_session, sample_lead, "tell me more", ReplyClassification.QUESTION)
    r.received_at = datetime(2026, 5, 1, 12)
    db_session.add(Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="AI answer",
                           send_source="ai_conversation", sent_at=datetime(2026, 5, 1, 12, 5)))
    db_session.commit()
    assert client.get("/communications/summary", headers=auth_headers).json()["ai_handling"] == 1
    assert client.get("/communications/replies?ai_handling=true",
                      headers=auth_headers).json()["total"] == 1


def test_assign_and_status(client, db_session, admin, sample_lead, second_advisor, other_org):
    h = _h(db_session, admin)
    rep = _reply(db_session, sample_lead, "hello", ReplyClassification.QUESTION)
    r = client.post("/communications/replies/%s/assign" % rep.id,
                    json={"assigned_to_id": second_advisor.id}, headers=h)
    assert r.status_code == 200
    assert r.json()["assigned_to_name"] == "Advisor Two"
    # a person from another tenant cannot be assigned
    r = client.post("/communications/replies/%s/assign" % rep.id,
                    json={"assigned_to_id": other_org["user"].id}, headers=h)
    assert r.status_code == 400
    assert client.get("/communications/replies?assigned_to=%s" % second_advisor.id,
                      headers=h).json()["total"] == 1
    r = client.post("/communications/replies/%s/status" % rep.id, json={"status": "needs_attention"},
                    headers=h)
    assert r.status_code == 200
    assert client.get("/communications/summary", headers=h).json()["needs_attention"] == 1
    assert client.post("/communications/replies/%s/status" % rep.id, json={"status": "bogus"},
                       headers=h).status_code == 400
    assert db_session.query(ReplyState).filter_by(reply_id=rep.id).count() == 1


def test_callback_task_from_reply(client, db_session, sample_lead, auth_headers):
    rep = _reply(db_session, sample_lead, "call me tomorrow", ReplyClassification.QUESTION)
    r = client.post("/communications/replies/%s/callback-task" % rep.id,
                    json={"due_at": "2026-10-01T15:00:00"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["source"] == "callback" and t["reply_id"] == rep.id and t["lead_id"] == sample_lead.id
    assert client.get("/communications/summary", headers=auth_headers).json()["callbacks"] == 1
    tasks = client.get("/work/tasks?lead_id=%s" % sample_lead.id, headers=auth_headers).json()
    assert tasks["total"] == 1


# ── thread ───────────────────────────────────────────────────────────────────

def test_thread_merges_inbound_outbound_and_notes(client, db_session, sample_lead, sample_advisor, auth_headers):
    db_session.add(Message(lead_id=sample_lead.id, sender_id=sample_advisor.id, body="Hi Jane",
                           send_source="manual", sent_at=datetime(2026, 5, 1, 10)))
    rep = _reply(db_session, sample_lead, "Hi back", ReplyClassification.INTERESTED)
    rep.received_at = datetime(2026, 5, 1, 11)
    db_session.commit()
    client.post("/work/leads/%s/notes" % sample_lead.id, json={"body": "internal fyi", "kind": "internal"},
                headers=auth_headers)
    t = client.get("/communications/thread/%s" % sample_lead.id, headers=auth_headers).json()
    kinds = [(e["type"], e.get("direction")) for e in t["events"]]
    assert kinds[:2] == [("message", "outbound"), ("message", "inbound")]
    assert ("note", None) in kinds
    assert t["lead"]["name"] == "Jane Doe"
    assert t["lead"]["sms_consent"] is False
    assert t["compose"]["allowed"] is False


# ── compose gate + send ──────────────────────────────────────────────────────

MIDDAY_CT = datetime(2026, 6, 2, 18, 0)     # 13:00 in Texas
NIGHT_CT = datetime(2026, 6, 2, 8, 0)       # 03:00 in Texas


def _consenting(db, lead):
    lead.sms_consent = True
    lead.sms_consent_source = "test web form"
    lead.state = "TX"
    lead.phone = "+12145559999"
    db.commit()
    return lead


def _codes(resp):
    d = resp.json()["detail"] if "detail" in resp.json() else resp.json()
    return [r["code"] for r in d["reasons"]]


def _refuse_send(*a, **k):
    raise AssertionError("send_sms must not be reached when the gate refuses")


def test_send_refused_without_consent(client, db_session, sample_lead, auth_headers):
    sample_lead.state = "TX"
    db_session.commit()
    with patch("app.routers.work_router._utcnow", return_value=MIDDAY_CT), \
         patch("app.services.sms_service.send_sms", side_effect=_refuse_send):
        r = client.post("/communications/send", json={"lead_id": sample_lead.id, "body": "hello"},
                        headers=auth_headers)
    assert r.status_code == 409
    assert "NO_SMS_CONSENT" in _codes(r)
    assert db_session.query(Message).count() == 0


def test_send_refused_for_dnc_and_stop(client, db_session, sample_lead, auth_headers):
    _consenting(db_session, sample_lead)
    sample_lead.status = "dnc"
    db_session.commit()
    with patch("app.routers.work_router._utcnow", return_value=MIDDAY_CT), \
         patch("app.services.sms_service.send_sms", side_effect=_refuse_send):
        r = client.post("/communications/send", json={"lead_id": sample_lead.id, "body": "hello"},
                        headers=auth_headers)
    assert r.status_code == 409 and "DNC" in _codes(r)
    # consent is never touched, DNC never cleared
    db_session.refresh(sample_lead)
    assert sample_lead.status == "dnc" and sample_lead.sms_consent is True

    sample_lead.status = "replied"
    db_session.commit()
    _reply(db_session, sample_lead, "STOP", ReplyClassification.DNC)
    with patch("app.routers.work_router._utcnow", return_value=MIDDAY_CT):
        g = client.get("/communications/compose-gate/%s" % sample_lead.id, headers=auth_headers).json()
    assert g["allowed"] is False and "STOP_REPLY" in [x["code"] for x in g["reasons"]]
    assert db_session.query(Message).count() == 0


def test_send_refused_when_suppressed_or_quiet_hours(client, db_session, sample_org, sample_lead, auth_headers):
    _consenting(db_session, sample_lead)
    with patch("app.routers.work_router._utcnow", return_value=NIGHT_CT):
        r = client.post("/communications/send", json={"lead_id": sample_lead.id, "body": "hello"},
                        headers=auth_headers)
    assert r.status_code == 409 and "QUIET_HOURS" in _codes(r)

    db_session.add(SuppressionEntry(organization_id=sample_org.id, phone="12145559999", reason="test"))
    db_session.commit()
    with patch("app.routers.work_router._utcnow", return_value=MIDDAY_CT):
        r = client.post("/communications/send", json={"lead_id": sample_lead.id, "body": "hello"},
                        headers=auth_headers)
    assert r.status_code == 409 and "COMPLIANCE" in _codes(r)
    assert db_session.query(Message).count() == 0


@patch("app.services.sms_service._resolve_twilio_creds")
def test_send_goes_through_send_sms_as_manual(mock_creds, client, db_session, sample_lead,
                                              sample_advisor, auth_headers):
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(sid="SMfake1", status="queued",
                                                  error_code=None, error_message=None)
    mock_creds.return_value = (fake, "+12145551111", None)
    _consenting(db_session, sample_lead)
    rep = _reply(db_session, sample_lead, "Can you text me the rate?", ReplyClassification.QUESTION)

    with patch("app.routers.work_router._utcnow", return_value=MIDDAY_CT):
        g = client.get("/communications/compose-gate/%s" % sample_lead.id, headers=auth_headers).json()
        assert g["allowed"] is True, g
        r = client.post("/communications/send",
                        json={"lead_id": sample_lead.id, "body": "Sure - sending it now.",
                              "reply_id": rep.id}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["send_source"] == "manual"
    fake.messages.create.assert_called_once()
    msg = db_session.query(Message).one()
    assert msg.send_source == "manual" and msg.sent_by_user_id == sample_advisor.id
    # consent untouched
    db_session.refresh(sample_lead)
    assert sample_lead.sms_consent is True


def test_workspace_identity(client, db_session, sample_org, auth_headers):
    r = client.get("/work/identity", headers=auth_headers)
    assert r.status_code == 200
    d = r.json()
    assert d["organization_id"] == sample_org.id
    assert d["organization_name"] == sample_org.name
    assert d["user_full_name"] == "Advisor One"
    assert d["workspace_role"] == "advisor"
    assert d["is_home_workspace"] is True


def test_search_treats_like_wildcards_literally(client, db_session, sample_lead, auth_headers):
    _reply(db_session, sample_lead, "save 50% today")
    _reply(db_session, sample_lead, "account_id please")
    _reply(db_session, sample_lead, "plain words")

    def bodies(q):
        return sorted(i["body"] for i in client.get("/communications/replies", params={"q": q},
                                                     headers=auth_headers).json()["items"])
    assert bodies("%") == ["save 50% today"]
    assert bodies("_") == ["account_id please"]
    assert bodies("50%") == ["save 50% today"]
    assert bodies("pl_in") == []        # "_" is not "any character" (would match "plain")
