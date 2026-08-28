"""GATE 31 - TWILIO DELIVERY RECEIPTS ACTUALLY PERSIST.

Production ran for 30 days with 3,583 outbound messages and ZERO delivery
receipts. Not one message had ever left `delivery_status='pending'`.

THE CAUSE WAS NOT THE WEBHOOK. The endpoint existed, was public, parsed the
payload correctly and wrote the row. Twilio was simply never told to call it:

  * `sms_service` attached `status_callback` only `if os.environ["API_BASE_URL"]`,
    and that variable is declared NOWHERE for the backend service in
    render.yaml. Only `VITE_API_BASE_URL` existed, which is a build-time
    variable belonging to the static FRONTEND. So the value was always "" and
    the parameter was dropped on every send, silently.
  * `cadence_service` — the highest-volume sender — never passed one at all.

Both failures are invisible: a message with no receipt requested looks exactly
like a message whose receipt has not arrived yet. That is why this gate asserts
the REQUEST as well as the handling. Checking only that the webhook works would
have passed for the entire 30 days the platform was broken.

NO MESSAGE IS SENT ANYWHERE. The Twilio client is replaced with a fake that
records what it was asked to do, and the callback is replayed against the real
endpoint with a real signature computed from a test auth token.
"""
import base64
import hashlib
import hmac
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

TMP = tempfile.mkdtemp(prefix="receipts_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db").replace("\\", "/")
os.environ["JWT_SECRET"] = "probe" + "0" * 59
os.environ["SECRET_KEY"] = "probe" + "0" * 59
os.environ.pop("APP_ENV", None)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from fastapi.testclient import TestClient                            # noqa: E402
from app.main import app                                             # noqa: E402

# CLEARED AFTER THE IMPORT, ON PURPOSE.
#
# app/main.py calls load_dotenv() at import time, and this developer machine has
# a .env containing API_BASE_URL=http://localhost:8000. Clearing these BEFORE
# the import would simply let dotenv put them back, and the gate would then be
# testing the local .env rather than the production environment.
#
# That difference is the whole bug: locally the variable is present and the
# callback is attached, so this worked perfectly on a laptop. Render has no .env
# file and render.yaml never declared the variable, so in production it resolved
# to "" and the parameter was silently dropped on every message.
for _v in ("API_BASE_URL", "BACKEND_URL", "PUBLIC_API_BASE_URL",
           "GOOGLE_REDIRECT_URI", "MICROSOFT_REDIRECT_URI", "TWILIO_AUTH_TOKEN"):
    os.environ.pop(_v, None)
from app.deps import SessionLocal, engine                            # noqa: E402
from app.models.models import (                                      # noqa: E402
    Base, Platform, Organization, User, Lead, Message,
)
from app.services.auth_service import hash_password                  # noqa: E402
from app.services import twilio_callbacks as tc                      # noqa: E402

PW = "ProbeTest!2026"
FAIL, PASSED = [], []
CB_PATH = "/sms/webhook/status-callback"
TEST_TOKEN = "test_auth_token_not_a_real_secret"


def check(label, ok, detail=""):
    print("  %s %s%s" % ("ok   " if ok else "FAIL ", label,
                         ("\n          -> " + str(detail)[:260]) if detail else ""))
    (PASSED if ok else FAIL).append(label)


def section(t):
    print("\n--- %s " % t + "-" * max(0, 62 - len(t)))


# ══ a Twilio client that sends nothing ══════════════════════════════════════

class FakeMessage:
    def __init__(self, sid):
        self.sid = sid
        self.status = "queued"


class FakeMessages:
    def __init__(self, sink):
        self.sink = sink

    def create(self, **kwargs):
        self.sink.append(kwargs)
        return FakeMessage("SM%032x" % (len(self.sink) + 1))


class FakeTwilio:
    """Records the create() payload. Sends nothing, reaches no network."""
    def __init__(self, sink):
        self.messages = FakeMessages(sink)


def build():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt", name="EvoSys Pro", slug="evo-receipts"))
    db.flush()
    db.add(Organization(id="org-a", name="Customer A", slug="cust-a-receipts",
                        platform_id="plt", is_active=True))
    db.flush()
    db.add(User(id="u-adv", organization_id="org-a", email="adv@probe.test",
                full_name="Advisor", password_hash=hash_password(PW),
                role="advisor", must_change_password=False, is_active=True,
                twilio_phone_number="+15550000001"))
    db.add(Lead(id="lead-1", organization_id="org-a", first_name="Test",
                last_name="Lead", phone="+15550000099", status="new",
                assigned_to_id="u-adv", created_at=datetime.utcnow()))
    db.commit()
    db.close()


def sign(token, url, params):
    s = url + "".join("%s%s" % (k, v) for k, v in sorted(params.items()))
    return base64.b64encode(
        hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


def main():
    print("=" * 78)
    print("GATE 31 - TWILIO DELIVERY RECEIPTS ACTUALLY PERSIST")
    print("=" * 78)
    build()
    c = TestClient(app)

    # ══ 1. THE PRODUCTION MISCONFIGURATION IS DETECTED, NOT ABSORBED ════════
    section("MISCONFIGURED: no public base URL of any spelling")
    check("no callback URL can be built", tc.status_callback_url() is None,
          tc.status_callback_url())
    check("...and the resolver reports no public base", tc.public_api_base() == "")

    # ══ 2. EVERY RECORDED SEND PATH REQUESTS A RECEIPT ══════════════════════
    section("CONFIGURED: every path that records a Message asks for a receipt")
    os.environ["API_BASE_URL"] = "https://advisorflow-backend.onrender.com"
    expected_cb = "https://advisorflow-backend.onrender.com" + CB_PATH
    check("the resolver now builds the callback URL",
          tc.status_callback_url() == expected_cb, tc.status_callback_url())

    sent = []
    import app.services.sms_service as sms_service
    sms_service._resolve_twilio_creds = lambda advisor, db: (
        FakeTwilio(sent), "+15550000001", None)

    db = SessionLocal()
    advisor = db.query(User).filter(User.id == "u-adv").first()
    lead = db.query(Lead).filter(Lead.id == "lead-1").first()
    msg = sms_service.send_sms(db, advisor, lead, "Hello {first_name}",
                               include_booking_link=False)
    msg_id, msg_sid = msg.id, msg.twilio_sid
    db.close()

    check("send_sms reached Twilio exactly once (and sent nothing real)",
          len(sent) == 1, len(sent))
    check("...passing status_callback",
          sent and sent[0].get("status_callback") == expected_cb,
          sent[0].get("status_callback") if sent else None)
    check("...and the Message row starts at 'pending'",
          msg.delivery_status == "pending" if msg else False)

    # The cadence path is asserted at the source, because standing up the whole
    # cadence engine here would test the fixture more than the fix. What matters
    # is that it can no longer call create() without going through the resolver.
    with open(os.path.join(REPO, "app", "services", "cadence_service.py"),
              encoding="utf-8") as fh:
        cad = fh.read()
    check("cadence_service requests a receipt too",
          "apply_status_callback" in cad,
          "the highest-volume sender must not send un-tracked messages")
    check("...and has no bare messages.create left",
          "client.messages.create(body=body," not in cad)

    # ══ 3. A REPLAYED TWILIO CALLBACK PERSISTS THE RECEIPT ══════════════════
    section("THE RECEIPT PERSISTS (replayed callback, real endpoint)")
    r = c.post(CB_PATH, data={"MessageSid": msg_sid, "MessageStatus": "delivered"})
    check("the endpoint exists and is publicly reachable",
          r.status_code == 200, "%s %s" % (r.status_code, r.text[:120]))
    check("...and reports the message updated",
          r.json().get("status") == "updated", r.json())

    db = SessionLocal()
    fresh = db.query(Message).filter(Message.id == msg_id).first()
    got_status, got_at = fresh.delivery_status, fresh.delivery_status_at
    twilio_status = fresh.twilio_status
    db.close()
    check("delivery_status IS PERSISTED as 'delivered'", got_status == "delivered", got_status)
    check("...twilio_status is kept in sync", twilio_status == "delivered", twilio_status)
    check("...and delivery_status_at is stamped", got_at is not None, got_at)

    # A failure must land too — a receipt that only records success is useless.
    r = c.post(CB_PATH, data={"MessageSid": msg_sid, "MessageStatus": "failed"})
    db = SessionLocal()
    fresh = db.query(Message).filter(Message.id == msg_id).first()
    failed_status = fresh.delivery_status
    db.close()
    check("a 'failed' receipt persists as well", failed_status == "failed", failed_status)

    # ══ 4. IT SURVIVES SIGNATURE VALIDATION BEHIND RENDER'S PROXY ═══════════
    section("SIGNATURE VALIDATION behind a TLS-terminating proxy")
    # Turning the token on is what would have started rejecting every genuine
    # callback: Twilio signs the https URL, and this app sees http behind
    # Render's proxy because uvicorn is not started with --forwarded-allow-ips.
    os.environ["TWILIO_AUTH_TOKEN"] = TEST_TOKEN
    import importlib
    import app.utils.twilio_security as tsec
    importlib.reload(tsec)

    params = {"MessageSid": msg_sid, "MessageStatus": "delivered"}
    https_url = "https://advisorflow-backend.onrender.com" + CB_PATH
    good_sig = sign(TEST_TOKEN, https_url, params)

    r = c.post(CB_PATH, data=params, headers={
        "X-Twilio-Signature": good_sig,
        "X-Forwarded-Proto": "https",
        "Host": "advisorflow-backend.onrender.com",
    })
    check("a genuine https-signed callback is ACCEPTED behind the proxy",
          r.status_code == 200, "%s %s" % (r.status_code, r.text[:140]))

    # Same request without the proxy header — the configured-origin candidate
    # must still recognise it, since that is the URL we handed Twilio.
    r = c.post(CB_PATH, data=params, headers={
        "X-Twilio-Signature": good_sig,
        "Host": "advisorflow-backend.onrender.com",
    })
    check("...and still accepted when the proto header is absent",
          r.status_code == 200, r.status_code)

    # And a forgery is still refused.
    r = c.post(CB_PATH, data=params, headers={
        "X-Twilio-Signature": "not-a-real-signature",
        "X-Forwarded-Proto": "https",
        "Host": "advisorflow-backend.onrender.com",
    })
    check("a FORGED signature is refused", r.status_code == 403, r.status_code)
    r = c.post(CB_PATH, data=params, headers={
        "X-Forwarded-Proto": "https",
        "Host": "advisorflow-backend.onrender.com",
    })
    check("an unsigned callback is refused once a token is set",
          r.status_code == 403, r.status_code)
    os.environ.pop("TWILIO_AUTH_TOKEN", None)
    importlib.reload(tsec)

    # ══ 5. THE DASHBOARD READS WHAT WAS PERSISTED ═══════════════════════════
    section("PLATFORM HEALTH reflects the receipt, not a guess")
    db = SessionLocal()
    db.add(User(id="u-god", organization_id=None, email="god@probe.test",
                full_name="Owner", password_hash=hash_password(PW),
                role="god_admin", must_change_password=False, is_active=True))
    db.commit()
    db.close()
    tok = c.post("/auth/login", data={"username": "god@probe.test", "password": PW})
    god = {"Authorization": "Bearer " + tok.json()["access_token"]}

    diag = c.get("/god/twilio-diagnostics", headers=god)
    check("the diagnostic endpoint answers the owner", diag.status_code == 200,
          diag.status_code)
    d = diag.json() if diag.status_code == 200 else {}
    check("...and reports that receipts CAN now be received",
          d.get("can_receive_receipts") is True, d.get("verdict"))
    check("...naming the callback URL", d.get("callback_url") == expected_cb,
          d.get("callback_url"))
    check("...and counting the message that now has a receipt",
          d.get("messages_with_a_receipt", 0) >= 1,
          d.get("messages_by_delivery_status"))
    check("the diagnostic never returns a secret value",
          TEST_TOKEN not in diag.text,
          "tokens are reported as configured / not configured, never by value")
    check("...and it is owner-only",
          c.get("/god/twilio-diagnostics").status_code in (401, 403))

    health = c.get("/god/platform-health", headers=god).json()
    msec = {s["key"]: s for s in health["sections"]}["messaging"]
    check("messaging health now reports a real delivery figure",
          "%" in (msec.get("headline") or ""), msec.get("headline"))

    print("\n" + "=" * 78)
    print("checks passed: %d" % len(PASSED))
    if FAIL:
        print("\nFAILURES (%d):" % len(FAIL))
        for f in FAIL:
            print("  - %s" % f)
        print("=" * 78)
        return 1
    print("\nDELIVERY RECEIPTS ARE REQUESTED, VALIDATED, PERSISTED AND READ.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
