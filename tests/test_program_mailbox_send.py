"""Program email can go out from the program's own Microsoft 365 mailbox."""
from app.models.inbound_mailbox_models import InboundMailbox
from app.models.program_models import OutreachProgram
from app.services import inbound_mailbox_service as S
from app.services.programs import mailbox_send
from app.utils.crypto import encrypt_value
from tests.test_outreach_program import _h, _lead_for, program  # noqa: F401

BOX = "support@evosyspro.live"


class _R:
    def __init__(self, status, data=None, text=""):
        self.status_code, self._data, self.text = status, data or {}, text

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _box(db):
    b = InboundMailbox(address=BOX, is_active=True, refresh_token_encrypted=encrypt_value("rt"))
    db.add(b)
    db.commit()
    return b


def test_connecting_asks_permission_to_send(monkeypatch):
    from app.services import microsoft_email_service as M
    monkeypatch.setattr(M, "MICROSOFT_CLIENT_ID", "cid")
    assert "Mail.Send" in S.CONNECT_SCOPES and "Mail.ReadWrite" in S.CONNECT_SCOPES
    assert "Mail.Send" in S.authorization_url("st")
    assert "Mail.Send" not in S.MAILBOX_SCOPES          # reading still works for an older sign-in


def test_send_mail_posts_to_graph(program, monkeypatch):
    db = program["db"]
    box = _box(db)
    seen = {}
    monkeypatch.setattr(S, "_refresh", lambda b, scope: _R(200, {"access_token": "tok", "scope": scope}))

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, json=json)
        return _R(202)
    monkeypatch.setattr(S.httpx, "post", fake_post)
    out = S.send_mail(db, box, to_email="family@example.com", subject="Hi", body_html="<p>x</p>",
                      reply_to="kerry@evosyspro.live")
    assert out["success"] is True and out["provider"] == "microsoft365" and out["from_email"] == BOX
    assert seen["url"].endswith("/me/sendMail")
    msg = seen["json"]["message"]
    assert msg["toRecipients"][0]["emailAddress"]["address"] == "family@example.com"
    assert msg["replyTo"][0]["emailAddress"]["address"] == "kerry@evosyspro.live"
    assert seen["json"]["saveToSentItems"] is True


def test_a_mailbox_without_send_permission_fails_closed(program, monkeypatch):
    db = program["db"]
    box = _box(db)
    monkeypatch.setattr(S, "_refresh", lambda b, scope: _R(400, text="invalid_grant"))
    out = S.send_mail(db, box, to_email="family@example.com", subject="Hi", body_html="x")
    assert out["success"] is False and "Reconnect" in out["error"]


def test_program_email_goes_out_from_the_mailbox(program, sample_advisor, monkeypatch):
    db, org = program["db"], program["org"]
    _box(db)
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    prog.send_via_mailbox = BOX
    db.commit()
    lead = _lead_for(db, org, sample_advisor, "L001")
    calls = []
    monkeypatch.setattr(S, "send_mail", lambda db_, box, **kw: calls.append(kw) or {
        "success": True, "provider_message_id": None, "error": None, "provider": "microsoft365",
        "from_email": BOX, "audit_bcc_applied": False})

    def no_resend(**kw):
        raise AssertionError("must not use the email service when a mailbox is set")
    monkeypatch.setattr("app.services.email_service.send_email_via_provider", no_resend)
    from app.services import email_service
    email_service.send_email_to_lead(db, sample_advisor, lead, subject="Hi", body_html="<p>x</p>",
                                     send_source="manual")
    assert len(calls) == 1 and calls[0]["to_email"] == "ollie@example.com"
    assert "/email/unsubscribe/" in calls[0]["body_html"]


def test_unconnected_mailbox_never_falls_back(program):
    db, org = program["db"], program["org"]
    prog = db.query(OutreachProgram).filter_by(organization_id=org.id).one()
    prog.send_via_mailbox = "nobody@evosyspro.live"
    db.commit()
    out = mailbox_send.send(db, prog, to_email="x@example.com", subject="s", body_html="b")
    assert out["success"] is False and "not connected" in out["error"]
    prog.send_via_mailbox = None
    assert mailbox_send.send(db, prog, to_email="x@example.com", subject="s", body_html="b") is None


def test_setting_needs_a_connected_mailbox(client, program):
    db = program["db"]
    h = _h(db, program["admin"])
    bad = client.patch("/program/settings", json={"send_via_mailbox": BOX}, headers=h)
    assert bad.status_code == 422
    _box(db)
    ok = client.patch("/program/settings", json={"send_via_mailbox": BOX.upper()}, headers=h)
    assert ok.status_code == 200, ok.text
    db.expire_all()
    assert db.query(OutreachProgram).filter_by(organization_id=program["org"].id).one().send_via_mailbox == BOX
