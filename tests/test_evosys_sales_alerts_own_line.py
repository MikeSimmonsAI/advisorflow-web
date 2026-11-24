"""Owner, Nov 23: EvoSys Sales alerts go out from the sales line, never SCI's."""
from types import SimpleNamespace

from app.services.evosys_sales import outreach, sms_inbound


def test_sales_alert_texts_use_the_sales_number(monkeypatch):
    sent = []
    monkeypatch.setattr(outreach, "_send_text", lambda to, body, s: sent.append((to, body, s.sms_from_number))
                        or {"success": True})
    import app.services.sms_programs as sp
    monkeypatch.setattr(sp, "sci_sender", lambda: (_ for _ in ()).throw(AssertionError("SCI line used")))
    s = SimpleNamespace(alert_phone="+18435328405", alert_email=None, alert_also=[],
                        sms_from_number="+18664870628")
    monkeypatch.setattr(sms_inbound, "_email_alert", lambda *a, **k: None, raising=False)
    sms_inbound.notify_owner(s, "Acme Cleaning replied", "subj", "<p>x</p>")
    assert sent == [("+18435328405", "Acme Cleaning replied", "+18664870628")]
