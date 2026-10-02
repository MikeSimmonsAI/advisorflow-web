"""THE EMERGENCY BRAKE - one switch, every provider, no network I/O when on."""
import httpx
import pytest

from app.services import outbound_brake as B


@pytest.fixture(autouse=True)
def _installed():
    B.install()
    yield


def test_predicates_only_match_sends():
    assert B.twilio_blocks("POST", "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json")
    assert B.twilio_blocks("POST", "https://api.twilio.com/2010-04-01/Accounts/AC1/Calls.json")
    assert not B.twilio_blocks("GET", "https://api.twilio.com/2010-04-01/Accounts/AC1/Messages.json")
    assert not B.twilio_blocks("POST", "https://api.twilio.com/2010-04-01/Accounts/AC1/IncomingPhoneNumbers.json")
    assert not B.twilio_blocks("GET", "https://lookups.twilio.com/v2/PhoneNumbers/+12145550100")
    assert B.httpx_blocks("POST", httpx.URL("https://graph.microsoft.com/v1.0/me/sendMail"))
    assert B.httpx_blocks("POST", httpx.URL("https://api.retellai.com/v2/create-phone-call"))
    assert not B.httpx_blocks("GET", httpx.URL("https://graph.microsoft.com/v1.0/me/messages"))
    assert not B.httpx_blocks("POST", httpx.URL("https://graph.microsoft.com.evil.test/v1.0/me/sendMail"))


def test_released_by_default(monkeypatch):
    monkeypatch.delenv(B.ENV, raising=False)
    assert B.engaged() is False
    for v in ("0", "false", "", "no"):
        monkeypatch.setenv(B.ENV, v)
        assert B.engaged() is False


def test_engaged_twilio_sms_and_call_refused_before_any_network(monkeypatch):
    """At the transport every Twilio client in the platform uses (the suite
    forbids constructing a real REST client, so the transport is driven
    directly - it is exactly what Client.messages.create calls)."""
    monkeypatch.setenv(B.ENV, "1")
    from twilio.http.http_client import TwilioHttpClient
    tw = TwilioHttpClient()
    base = "https://api.twilio.com/2010-04-01/Accounts/ACtest00000000000000000000000000/"
    for ep in ("Messages.json", "Calls.json"):
        with pytest.raises(B.OutboundStopped):
            tw.request("POST", base + ep, data={"Body": "hi"})


def test_engaged_graph_and_retell_refused(monkeypatch):
    monkeypatch.setenv(B.ENV, "true")
    with pytest.raises(B.OutboundStopped):
        httpx.post("https://graph.microsoft.com/v1.0/me/sendMail", json={})
    with httpx.Client() as cl, pytest.raises(B.OutboundStopped):
        cl.post("https://api.retellai.com/v2/create-phone-call", json={})


def test_engaged_resend_refused(monkeypatch):
    monkeypatch.setenv(B.ENV, "on")
    import resend
    with pytest.raises(B.OutboundStopped):
        resend.Emails.send({"from": "a@x.test", "to": ["b@x.test"], "subject": "s", "html": "h"})


def test_released_calls_straight_through(monkeypatch):
    """With the brake off the wrapper is transparent: a mocked transport is reached."""
    monkeypatch.delenv(B.ENV, raising=False)
    seen = []
    transport = httpx.MockTransport(lambda req: (seen.append(str(req.url)), httpx.Response(202))[1])
    with httpx.Client(transport=transport) as cl:
        assert cl.post("https://graph.microsoft.com/v1.0/me/sendMail", json={}).status_code == 202
    assert seen == ["https://graph.microsoft.com/v1.0/me/sendMail"]


def test_status_is_reported(monkeypatch, client, db_session):
    monkeypatch.setenv(B.ENV, "1")
    from app.services import readiness
    p = readiness.providers(db_session)
    assert p[0]["key"] == "emergency_stop" and p[0]["status"] == "disabled"
    assert B.status()["engaged"] is True


def test_twilio_requests_get_a_default_timeout(monkeypatch):
    """A stalled Twilio connection must not hold a worker forever."""
    monkeypatch.delenv(B.ENV, raising=False)
    from twilio.http.http_client import TwilioHttpClient
    seen = {}

    class _Resp:
        status_code = 200
        text = "{}"
        content = b"{}"
        headers = {}

    def fake_send(self, prepared, **kw):
        seen["timeout"] = kw.get("timeout")
        return _Resp()
    import requests
    monkeypatch.setattr(requests.Session, "send", fake_send)
    tw = TwilioHttpClient()
    try:
        tw.request("GET", "https://api.twilio.com/2010-04-01/Accounts/ACx.json")
    except Exception:                                            # noqa: BLE001
        pass                                                     # response parsing is not the point
    assert seen.get("timeout") == B.twilio_timeout() == 20.0
    monkeypatch.setenv("TWILIO_HTTP_TIMEOUT_SECONDS", "7")
    seen.clear()
    try:
        TwilioHttpClient(timeout=3).request("GET", "https://api.twilio.com/x.json")
    except Exception:                                            # noqa: BLE001
        pass
    assert seen.get("timeout") == 3                              # an explicit timeout is kept
