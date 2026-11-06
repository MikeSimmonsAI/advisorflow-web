# -*- coding: utf-8 -*-
"""VOLT support mailbox (support@voltmarketing.live) - connection, sending,
duplicate suppression, token refresh / revocation, inbound import, threaded
staff replies, isolation, and the VOLT password-reset link.

Microsoft is a fake here: no network, no real tokens. Synthetic people only.
"""
import base64
import json
import uuid
from datetime import datetime, timedelta

import pytest

from app.models.inbound_mailbox_models import InboundMailbox
from app.models.oauth_models import OAuthAuthorizationTransaction
from app.models.volt_models import VoltApplication, VoltEmail, VoltProgram, VoltRep, VoltSupportMessage
from app.services.volt import mail

from tests.test_volt_recruiting import SLUG, H, _complete, volt  # noqa: F401 - fixture


class _R:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data or {}, text or json.dumps(data or {})

    def json(self):
        return self._data


class FakeGraph:
    """Just enough of Microsoft's token service and Graph for these tests."""

    def __init__(self, signed_in="support@voltmarketing.live"):
        self.signed_in = signed_in
        self.revoked = False
        self.send_status = 202
        self.sent = []          # (to, subject, html, save, replyTo)
        self.replies = []       # (graph id, comment)
        self.inbox = []
        self.token_calls = 0
        self.refresh_seen = []

    def post(self, url, data=None, json=None, headers=None, timeout=None, **kw):
        if url.endswith("/oauth2/v2.0/token"):
            self.token_calls += 1
            if data.get("grant_type") == "refresh_token":
                self.refresh_seen.append(data.get("refresh_token"))
                if self.revoked:
                    return _R(400, {"error": "invalid_grant"})
                return _R(200, {"access_token": "at-%d" % self.token_calls, "refresh_token": "rt-%d" % self.token_calls,
                                "scope": mail.SCOPES})
            return _R(200, {"access_token": "at-0", "refresh_token": "rt-0", "scope": mail.SCOPES})
        if url.endswith("/me/sendMail"):
            m = json["message"]
            self.sent.append((m["toRecipients"][0]["emailAddress"]["address"], m["subject"], m["body"]["content"],
                              json["saveToSentItems"], m["replyTo"][0]["emailAddress"]["address"]))
            return _R(self.send_status, {"error": "nope"} if self.send_status != 202 else None)
        if url.endswith("/reply"):
            self.replies.append((url.split("/messages/")[1].split("/reply")[0], json["comment"]))
            return _R(202)
        raise AssertionError("unexpected POST " + url)

    def get(self, url, headers=None, params=None, timeout=None, **kw):
        if url.endswith("/me"):
            return _R(200, {"mail": self.signed_in, "userPrincipalName": self.signed_in, "displayName": "VOLT Marketing"})
        if "/mailFolders/" in url:
            return _R(200, {"id": "folder-" + url.rsplit("/", 1)[1]})
        if url.endswith("/me/messages"):
            return _R(200, {"value": list(self.inbox)})
        if url.endswith("/attachments"):
            return _R(200, {"value": [{"id": "att1", "name": "resume.pdf", "size": 1200, "contentType": "application/pdf",
                                       "isInline": False},
                                      {"id": "inl", "name": "logo.png", "size": 10, "isInline": True}]})
        if "/attachments/" in url:
            return _R(200, {"@odata.type": "#microsoft.graph.fileAttachment",
                            "contentBytes": base64.b64encode(b"%PDF-1.4 fake").decode()})
        raise AssertionError("unexpected GET " + url)


@pytest.fixture()
def graph(monkeypatch):
    from app.services import microsoft_email_service as M
    monkeypatch.setattr(M, "MICROSOFT_CLIENT_ID", "test-client")
    monkeypatch.setattr(M, "MICROSOFT_CLIENT_SECRET", "test-secret")
    g = FakeGraph()
    monkeypatch.setattr(mail, "_http", g)
    return g


@pytest.fixture()
def flushed(monkeypatch, volt):  # noqa: F811
    """Background sends use the test session (TestClient runs them inline)."""
    monkeypatch.setattr(mail, "flush_in_background", lambda ids: mail.flush(volt["db"], ids))
    return True


def _connect(v, g, signed_in=None):
    c, db, org = v["c"], v["db"], v["org"]
    if signed_in:
        g.signed_in = signed_in
    r = c.post("/volt/ops/%s/email/connect" % org.id, headers=H(db, v["staff"]))
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    assert "Mail.Send" in url and "Mail.Read" in url and "ReadWrite" not in url
    assert "login_hint=support%40voltmarketing.live" in url
    state = url.split("state=")[1].split("&")[0]
    from urllib.parse import unquote
    return c.get("/microsoft/oauth/callback", params={"state": unquote(state), "code": "abc"},
                 follow_redirects=False)


def _box(db, org_id):
    return db.query(InboundMailbox).filter(InboundMailbox.organization_id == org_id,
                                           InboundMailbox.purpose == "volt_support").first()


# ── connection ──────────────────────────────────────────────────────────────

def test_seed_sets_volt_identity(volt):  # noqa: F811
    prog = volt["db"].get(VoltProgram, volt["org"].id)
    assert prog.support_email == "support@voltmarketing.live"
    assert prog.sender_name == "VOLT Marketing"
    assert prog.public_origin == "https://voltmarketing.live"


def test_connect_only_as_the_support_mailbox(volt, graph):  # noqa: F811
    db, org = volt["db"], volt["org"]
    r = _connect(volt, graph, signed_in="mike@example.com")
    assert r.status_code in (302, 307) and "mailbox_error=wrong_account" in r.headers["location"]
    assert _box(db, org.id) is None                      # nothing stored for the wrong account
    r = _connect(volt, graph, signed_in="support@voltmarketing.live")
    assert "mailbox_connected=support%40voltmarketing.live" in r.headers["location"]
    box = _box(db, org.id)
    assert box is not None and box.refresh_token_encrypted and "rt-0" not in box.refresh_token_encrypted
    # Status never exposes the token.
    s = volt["c"].get("/volt/ops/%s/email" % org.id, headers=H(db, volt["staff"]))
    assert s.status_code == 200 and s.json()["state"] == "connected"
    assert "refresh" not in s.text and "rt-" not in s.text and "at-" not in s.text


def test_mailbox_status_and_connect_are_volt_staff_only(volt, graph):  # noqa: F811
    c, db, org = volt["c"], volt["db"], volt["org"]
    for who in ("outsider", "other_staff"):
        assert c.get("/volt/ops/%s/email" % org.id, headers=H(db, volt[who])).status_code == 403
        assert c.post("/volt/ops/%s/email/connect" % org.id, headers=H(db, volt[who])).status_code == 403
        assert c.get("/volt/ops/%s/inbox" % org.id, headers=H(db, volt[who])).status_code == 403
    assert c.post("/volt/ops/%s/email/connect" % org.id, headers=H(db, volt["viewer"])).status_code == 403
    v = c.get("/volt/ops/%s/email" % org.id, headers=H(db, volt["viewer"]))
    assert v.status_code == 200 and v.json()["can_connect"] is False
    # An applicant token never reaches staff mail.
    _b, tok, _o = volt["register"]()
    assert c.get("/volt/ops/%s/inbox" % org.id, headers=tok).status_code in (401, 403)


def test_crm_poller_never_routes_the_volt_mailbox(volt, graph, monkeypatch):  # noqa: F811
    from app.services import inbound_mailbox_service as IMS
    db, org = volt["db"], volt["org"]
    _connect(volt, graph)
    called = []
    monkeypatch.setattr(IMS, "poll_mailbox", lambda db, box, **kw: called.append(box.address) or
                        {"checked": 0, "matched": 0, "errors": 0})
    res = IMS.poll_all_mailboxes(db)
    assert "support@voltmarketing.live" not in called
    assert res.get("volt_mailboxes") == 1


# ── sending ─────────────────────────────────────────────────────────────────

def _test_person(**kw):
    """A test applicant at a deliverable test inbox (+volt-test), so it is sent."""
    return dict(kw, email="mike+volt-test-%s@example.com" % uuid.uuid4().hex[:6])


def test_transactional_flow_sends_once_each(volt, graph, flushed):  # noqa: F811
    c, db, org = volt["c"], volt["db"], volt["org"]
    _connect(volt, graph)
    b, tok, out = volt["register"](**_test_person())
    sent = [s for s in graph.sent if s[0] == b["email"]]
    assert [s[1] for s in sent] == ["Your VOLT Marketing account is ready"]
    assert sent[0][4] == "support@voltmarketing.live" and sent[0][3] is True
    assert "https://voltmarketing.live/volt/me/applications/" in sent[0][2]
    app_id = out["application_id"]
    _complete(c, tok, app_id)
    # Submitting again changes nothing and sends nothing.
    c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, app_id), headers=tok)
    receipts = [s for s in graph.sent if s[0] == b["email"] and s[1].startswith("We received")]
    assert len(receipts) == 1
    sh = H(db, volt["staff"])
    # Needs update, saved twice with the same reason -> one email.
    for _ in range(2):
        r = c.post("/volt/ops/%s/applications/%s/status" % (org.id, app_id), headers=sh,
                   json={"status": "needs_update", "reason": "Add your <b>license</b> status & ZIP"})
        assert r.status_code == 200
    upd = [s for s in graph.sent if s[1].startswith("Action needed")]
    assert len(upd) == 1
    assert "&lt;b&gt;license&lt;/b&gt;" in upd[0][2]          # staff text is escaped, never HTML
    # A corrected reason is a real change -> one more.
    c.post("/volt/ops/%s/applications/%s/status" % (org.id, app_id), headers=sh,
           json={"status": "needs_update", "reason": "Add your ZIP code"})
    assert len([s for s in graph.sent if s[1].startswith("Action needed")]) == 2
    # Resubmit -> a second receipt; approve twice -> one approval.
    c.post("/volt/rep/%s/applications/%s/submit" % (SLUG, app_id), headers=tok)
    assert len([s for s in graph.sent if s[1].startswith("We received")]) == 2
    for _ in range(2):
        c.post("/volt/ops/%s/applications/%s/status" % (org.id, app_id), headers=sh, json={"status": "approved"})
    assert len([s for s in graph.sent if s[1].endswith("was approved")]) == 1
    rows = db.query(VoltEmail).filter(VoltEmail.organization_id == org.id, VoltEmail.to_email == b["email"]).all()
    assert all(r.state == "sent" for r in rows) and len(rows) == len([s for s in graph.sent if s[0] == b["email"]])


def test_example_test_applicants_are_never_emailed(volt, graph, flushed):  # noqa: F811
    _connect(volt, graph)
    b, tok, out = volt["register"]()                       # @example.test
    assert not [s for s in graph.sent if s[0] == b["email"]]
    row = volt["db"].query(VoltEmail).filter(VoltEmail.to_email == b["email"]).one()
    assert row.state == "skipped"


def test_no_mailbox_records_failure_and_retries_later(volt, graph, flushed):  # noqa: F811
    db = volt["db"]
    b, tok, out = volt["register"](**_test_person())
    row = db.query(VoltEmail).filter(VoltEmail.to_email == b["email"]).one()
    assert row.state == "failed" and "not connected" in row.last_error and row.next_attempt_at
    assert not graph.sent
    _connect(volt, graph)
    row.next_attempt_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert mail.retry_due(db) == 1
    db.refresh(row)
    assert row.state == "sent" and len([s for s in graph.sent if s[0] == b["email"]]) == 1
    assert mail.retry_due(db) == 0                          # sent rows are never resent


def test_refresh_rotation_and_revoked_signin(volt, graph, flushed):  # noqa: F811
    db, org = volt["db"], volt["org"]
    _connect(volt, graph)
    box = _box(db, org.id)
    first = box.refresh_token_encrypted
    mail._token(db, box)
    db.commit()
    assert box.refresh_token_encrypted != first             # rotated, still encrypted
    graph.revoked = True
    b, tok, out = volt["register"](**_test_person())
    row = db.query(VoltEmail).filter(VoltEmail.to_email == b["email"]).one()
    assert row.state == "failed" and "Reconnect" in row.last_error
    db.refresh(box)
    assert box.last_status == "auth_error"
    s = volt["c"].get("/volt/ops/%s/email" % org.id, headers=H(db, volt["staff"])).json()
    assert s["state"] == "reconnect" and "invalid_grant" in s["last_error"]


def test_provider_refusal_backs_off_then_stops(volt, graph, flushed):  # noqa: F811
    db = volt["db"]
    _connect(volt, graph)
    graph.send_status = 503
    b, tok, out = volt["register"](**_test_person())
    row = db.query(VoltEmail).filter(VoltEmail.to_email == b["email"]).one()
    assert row.state == "failed" and row.attempts == 1 and row.next_attempt_at > datetime.utcnow()
    for _ in range(10):
        if row.next_attempt_at is None:
            break
        row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        db.commit()
        mail.retry_due(db)
        db.refresh(row)
    assert row.attempts == mail.MAX_ATTEMPTS and row.next_attempt_at is None
    assert mail.retry_due(db) == 0


# ── password reset ──────────────────────────────────────────────────────────

def test_volt_password_reset_uses_volt_domain_and_keeps_no_copy(volt, graph, flushed):  # noqa: F811
    c, db = volt["c"], volt["db"]
    _connect(volt, graph)
    b, tok, out = volt["register"](**_test_person())
    graph.sent.clear()
    assert c.post("/auth/forgot-password", json={"email": b["email"]}).status_code == 200
    assert len(graph.sent) == 1
    to, subject, html, save, reply_to = graph.sent[0]
    assert subject == "Reset your VOLT Marketing password" and save is False
    assert "https://voltmarketing.live/activate?token=" in html and "next=/volt/sign-in" in html.replace("%2F", "/")
    row = db.query(VoltEmail).filter(VoltEmail.kind == "password_reset", VoltEmail.to_email == b["email"]).one()
    assert row.body_html is None and row.sensitive is True   # the link is never stored


def test_staff_password_reset_is_unchanged(volt, graph, monkeypatch):  # noqa: F811
    from app.services import email_service
    calls = []
    monkeypatch.setattr(email_service, "send_email_via_provider", lambda to, subj, html, *a, **k: calls.append((to, subj, html)))
    _connect(volt, graph)
    graph.sent.clear()
    assert volt["c"].post("/auth/forgot-password", json={"email": volt["staff"].email}).status_code == 200
    assert not graph.sent and calls and "EvoSys Pro" in calls[0][1] and "voltmarketing" not in calls[0][2]


# ── inbound + threaded replies ──────────────────────────────────────────────

def _incoming(gid, sender, subject="Question about my application", conv="conv-1", body="Hi, <script>x</script> when?",
              atts=False, later=0):
    return {"id": gid, "internetMessageId": "<%s@mail>" % gid, "conversationId": conv, "subject": subject,
            "from": {"emailAddress": {"address": sender, "name": "Pat Applicant"}},
            "toRecipients": [{"emailAddress": {"address": "support@voltmarketing.live"}}],
            "receivedDateTime": (datetime.utcnow() + timedelta(seconds=later)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "body": {"contentType": "text", "content": body}, "hasAttachments": atts,
            "parentFolderId": "folder-inbox"}


def test_inbound_import_dedupe_and_threaded_reply(volt, graph, flushed):  # noqa: F811
    c, db, org = volt["c"], volt["db"], volt["org"]
    _connect(volt, graph)
    b, tok, out = volt["register"](**_test_person())
    graph.inbox = [_incoming("g1", b["email"], atts=True),
                   _incoming("own", "support@voltmarketing.live", conv="conv-own"),
                   _incoming("ndr", "postmaster@outlook.com", subject="Undeliverable: hello", conv="conv-ndr",
                             body="Your message couldn't be delivered to nobody@example.org. 5.1.1 user unknown")]
    box = _box(db, org.id)
    r1 = mail.poll(db, box)
    r2 = mail.poll(db, box)                                   # same messages again
    assert r1["imported"] == 2 and r2["imported"] == 0
    assert db.query(VoltSupportMessage).filter(VoltSupportMessage.organization_id == org.id).count() == 2
    sh = H(db, volt["staff"])
    threads = c.get("/volt/ops/%s/inbox" % org.id, headers=sh).json()["threads"]
    t = next(x for x in threads if x["conversation_id"] == "conv-1")
    assert t["rep_code"] == out["rep"]["rep_code"] and t["has_attachments"] and t["open"]
    assert next(x for x in threads if x["conversation_id"] == "conv-ndr")["kind"] == "bounce"
    th = c.get("/volt/ops/%s/inbox/thread" % org.id, headers=sh, params={"conversation": "conv-1"}).json()
    msg = th["messages"][0]
    assert msg["body_text"] == "Hi, <script>x</script> when?"   # kept as text; the UI renders text, not HTML
    assert [a["name"] for a in msg["attachments"]] == ["resume.pdf"]   # inline images are not listed
    # Attachment: forced download, never rendered.
    a = c.get("/volt/ops/%s/inbox/messages/%s/attachments/att1" % (org.id, msg["id"]), headers=sh)
    assert a.status_code == 200 and a.headers["content-type"] == "application/octet-stream"
    assert a.headers["content-disposition"].startswith("attachment;") and a.headers["x-content-type-options"] == "nosniff"
    # Viewer can read but not reply.
    assert c.post("/volt/ops/%s/inbox/reply" % org.id, headers=H(db, volt["viewer"]),
                  json={"conversation_id": "conv-1", "text": "x"}).status_code == 403
    rr = c.post("/volt/ops/%s/inbox/reply" % org.id, headers=sh,
                json={"conversation_id": "conv-1", "text": "Thanks <Pat>,\nwe'll review it today."})
    assert rr.status_code == 200, rr.text
    assert graph.replies == [("g1", "Thanks &lt;Pat&gt;,<br>we&#x27;ll review it today.")]   # Graph /reply keeps the thread
    th = rr.json()
    assert [m["direction"] for m in th["messages"]] == ["in", "out"] and th["open"] is False
    # The applicant answers in the same conversation: it lands in the same thread.
    graph.inbox.append(_incoming("g2", b["email"], subject="RE: Question about my application", body="Great, thanks",
                                  later=5))
    mail.poll(db, box)
    th = c.get("/volt/ops/%s/inbox/thread" % org.id, headers=sh, params={"conversation": "conv-1"}).json()
    assert [m["direction"] for m in th["messages"]] == ["in", "out", "in"] and th["open"] is True
    # A bounce cannot be replied to.
    assert c.post("/volt/ops/%s/inbox/reply" % org.id, headers=sh,
                  json={"conversation_id": "conv-ndr", "text": "hello"}).status_code == 409


def test_inbox_is_tenant_isolated(volt, graph, flushed):  # noqa: F811
    c, db, org = volt["c"], volt["db"], volt["org"]
    _connect(volt, graph)
    graph.inbox = [_incoming("g9", "someone@example.org", conv="conv-9")]
    mail.poll(db, _box(db, org.id))
    other = volt["other"]
    osh = H(db, volt["other_staff"])
    assert c.get("/volt/ops/%s/inbox" % other.id, headers=osh).json()["threads"] == []
    r = c.get("/volt/ops/%s/inbox/thread" % other.id, headers=osh, params={"conversation": "conv-9"})
    assert r.status_code == 404
    m = db.query(VoltSupportMessage).filter(VoltSupportMessage.graph_message_id == "g9").one()
    assert c.get("/volt/ops/%s/inbox/messages/%s/attachments/x" % (other.id, m.id), headers=osh).status_code == 404
