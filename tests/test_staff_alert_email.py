"""Staff alerts go out from the 365 mailbox, and are never lost to it."""
from app.models.inbound_mailbox_models import InboundMailbox
from app.services import email_service, staff_alert_email
from app.services import inbound_mailbox_service as IMS
from app.utils.crypto import encrypt_value

BOX = "support@evosyspro.live"


class _Org:
    resolved = True
    from_email = BOX
    from_name = "EvoSys Pro"
    reply_to_email = None


def _box(db):
    db.add(InboundMailbox(address=BOX, is_active=True, refresh_token_encrypted=encrypt_value("rt")))
    db.commit()


def _spy_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(email_service, "send_email_via_provider",
                        lambda to, subj, html, **kw: calls.append(to) or {"success": True, "provider": "resend"})
    return calls


def test_alert_goes_out_from_the_connected_mailbox(db_session, monkeypatch):
    _box(db_session)
    calls = _spy_provider(monkeypatch)
    sent = []
    monkeypatch.setattr(IMS, "send_mail", lambda db, box, **kw: sent.append((box.address, kw["to_email"]))
                        or {"success": True, "provider": "microsoft365"})
    out = staff_alert_email.send("owner@example.com", "HUMAN HANDOFF", "<p>x</p>", org=_Org(), db=db_session)
    assert out["provider"] == "microsoft365" and sent == [(BOX, "owner@example.com")] and calls == []


def test_a_refused_365_send_falls_back_so_the_alert_is_not_lost(db_session, monkeypatch):
    _box(db_session)
    calls = _spy_provider(monkeypatch)
    monkeypatch.setattr(IMS, "send_mail", lambda db, box, **kw: {"success": False, "error": "quota"})
    out = staff_alert_email.send("owner@example.com", "s", "<p>x</p>", org=_Org(), db=db_session)
    assert out["provider"] == "resend" and calls == ["owner@example.com"]


def test_an_error_talking_to_microsoft_falls_back(db_session, monkeypatch):
    _box(db_session)
    calls = _spy_provider(monkeypatch)

    def boom(db, box, **kw):
        raise RuntimeError("network")
    monkeypatch.setattr(IMS, "send_mail", boom)
    staff_alert_email.send("owner@example.com", "s", "<p>x</p>", org=_Org(), db=db_session)
    assert calls == ["owner@example.com"]


def test_no_connected_mailbox_uses_the_email_service(db_session, monkeypatch):
    calls = _spy_provider(monkeypatch)
    staff_alert_email.send("owner@example.com", "s", "<p>x</p>", org=_Org(), db=db_session)
    assert calls == ["owner@example.com"]


def test_switch_off_sends_every_alert_through_the_email_service(db_session, monkeypatch):
    _box(db_session)
    monkeypatch.setenv("STAFF_ALERTS_VIA_MAILBOX", "0")
    calls = _spy_provider(monkeypatch)
    monkeypatch.setattr(IMS, "send_mail", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not use 365")))
    staff_alert_email.send("owner@example.com", "s", "<p>x</p>", org=_Org(), db=db_session)
    assert calls == ["owner@example.com"]
