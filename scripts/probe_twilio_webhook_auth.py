"""
probe_twilio_webhook_auth.py — deploy gate for Twilio webhook authentication.

THE BUG THIS EXISTS TO PREVENT COMING BACK
------------------------------------------
On 2026-08-28 production returned HTTP 200 to an UNSIGNED, forged POST at
/sms/webhook/status-callback. Signature validation had a fail-open branch that
fired whenever no auth token was configured — and none ever was, because this
platform stores Twilio credentials per-advisor and per-org, not in one env var.

So these checks are not "does the code have a validate() call in it". They sign
real requests with real HMAC-SHA1 the way Twilio does, replay them against the
actual FastAPI app, and read the database rows back afterwards to prove that a
rejected request changed nothing.

Every negative test is paired with a positive control. A test that only ever
asserts 403 would still pass if the endpoint were broken and 403'd everything,
which would prove nothing about whether real Twilio traffic still works.

Run: python scripts/probe_twilio_webhook_auth.py
"""
import base64
import hashlib
import hmac
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Isolated DB + deterministic secrets BEFORE app import. The pop() must happen
# after the import of anything that calls load_dotenv(), so we set explicit
# values here instead of popping and hoping.
os.environ["DATABASE_URL"] = "sqlite:///./.probe_twilio_auth.db"
os.environ["JWT_SECRET"] = "probe" * 16
from cryptography.fernet import Fernet                       # noqa: E402
_KEY = Fernet.generate_key().decode()
os.environ["ENCRYPTION_KEY"] = _KEY
os.environ.pop("TWILIO_AUTH_TOKEN", None)      # the global must stay absent
os.environ["API_BASE_URL"] = "https://advisorflow-backend.onrender.com"

DB_FILE = os.path.join(ROOT, ".probe_twilio_auth.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from fastapi.testclient import TestClient                     # noqa: E402
from app.main import app                                      # noqa: E402
from app.deps import SessionLocal, engine                      # noqa: E402
from app.models.models import (                               # noqa: E402
    Base, User, Lead, Message, Organization, Reply, Platform)
from app.utils.crypto import encrypt_value                    # noqa: E402

# The global token must be absent for the whole run — that is the exact
# production condition. If it leaked back in via load_dotenv, the fail-closed
# assertions would pass for the wrong reason.
os.environ.pop("TWILIO_AUTH_TOKEN", None)

failures = []
checks = 0


def check(label, condition, detail=""):
    global checks
    checks += 1
    if condition:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + detail if detail else ""))
        failures.append(label)


PUBLIC = "https://advisorflow-backend.onrender.com"
STATUS_EP = "/sms/webhook/status-callback"
INBOUND_EP = "/sms/webhook/inbound"

A_SID, A_TOKEN = "ACaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "org-a-auth-token-secret"
B_SID, B_TOKEN = "ACbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "org-b-auth-token-secret"
# Stored in the NORMALIZED form the app actually persists and looks up:
# dedup_service.normalize_phone strips to 11 digits, no "+". Seeding "+1..."
# here would make the inbound handler miss the advisor and silently drop the
# message — the positive-path test would then pass on a 200 that did nothing.
A_PHONE, B_PHONE = "15550000001", "15550000002"
A_LEAD_PHONE, B_LEAD_PHONE = "15551110001", "15551110002"


def sign(token: str, url: str, params: dict) -> str:
    """Exactly Twilio's algorithm: HMAC-SHA1 over url + sorted k+v, base64."""
    s = url + "".join(f"{k}{v}" for k, v in sorted(params.items()))
    return base64.b64encode(
        hmac.new(token.encode(), s.encode(), hashlib.sha1).digest()).decode()


def post(client, ep, params, token=None, sig=None, url_for_sig=None,
         proxy=True):
    headers = {}
    if proxy:
        headers["X-Forwarded-Proto"] = "https"
        headers["X-Forwarded-Host"] = "advisorflow-backend.onrender.com"
    if sig is not None:
        headers["X-Twilio-Signature"] = sig
    elif token is not None:
        headers["X-Twilio-Signature"] = sign(
            token, url_for_sig or (PUBLIC + ep), params)
    return client.post(ep, data=params, headers=headers)


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    plat = Platform(id="plt-test", name="TestPlat", slug="testplat")
    db.add(plat)
    orgA = Organization(id="org-a", name="Org A", slug="org-a",
                        platform_id="plt-test")
    orgB = Organization(id="org-b", name="Org B", slug="org-b",
                        platform_id="plt-test")
    db.add_all([orgA, orgB])
    db.flush()

    advA = User(id="adv-a", email="a@example.test", full_name="Advisor A",
                password_hash="x", role="advisor", organization_id="org-a",
                twilio_account_sid=A_SID,
                twilio_auth_token_encrypted=encrypt_value(A_TOKEN),
                twilio_phone_number=A_PHONE)
    advB = User(id="adv-b", email="b@example.test", full_name="Advisor B",
                password_hash="x", role="advisor", organization_id="org-b",
                twilio_account_sid=B_SID,
                twilio_auth_token_encrypted=encrypt_value(B_TOKEN),
                twilio_phone_number=B_PHONE)
    db.add_all([advA, advB])
    db.flush()

    leadA = Lead(id="lead-a", organization_id="org-a", first_name="Lead",
                 last_name="A", phone=A_LEAD_PHONE, status="contacted",
                 assigned_to_id="adv-a")
    leadB = Lead(id="lead-b", organization_id="org-b", first_name="Lead",
                 last_name="B", phone=B_LEAD_PHONE, status="contacted",
                 assigned_to_id="adv-b")
    db.add_all([leadA, leadB])
    db.flush()

    # Active cadences so "a forged reply must not stop a cadence" has something
    # real to fail to stop. CadenceState.status is a plain VARCHAR.
    from app.models.models import CadenceState
    db.add_all([
        CadenceState(lead_id="lead-a", status="active"),
        CadenceState(lead_id="lead-b", status="active"),
    ])
    db.flush()

    msgA = Message(id="msg-a", lead_id="lead-a", sender_id="adv-a",
                   body="hello from A", twilio_sid="SM" + "a" * 32,
                   delivery_status="pending")
    msgB = Message(id="msg-b", lead_id="lead-b", sender_id="adv-b",
                   body="hello from B", twilio_sid="SM" + "b" * 32,
                   delivery_status="pending")
    db.add_all([msgA, msgB])
    db.commit()
    db.close()


def row(msg_id):
    db = SessionLocal()
    try:
        m = db.query(Message).filter(Message.id == msg_id).first()
        return (m.delivery_status, m.twilio_status, m.delivery_status_at)
    finally:
        db.close()


def lead_state(lead_id):
    db = SessionLocal()
    try:
        l = db.query(Lead).filter(Lead.id == lead_id).first()
        n_replies = db.query(Reply).filter(Reply.lead_id == lead_id).count()
        return (l.status, n_replies)
    finally:
        db.close()


seed()
client = TestClient(app)
SID_A = "SM" + "a" * 32
SID_B = "SM" + "b" * 32

print("\n[0] the production precondition holds for this whole run")
check("global TWILIO_AUTH_TOKEN is absent",
      not os.environ.get("TWILIO_AUTH_TOKEN"),
      "the fail-closed tests would pass for the wrong reason if it were set")

print("\n[1] valid signed status callback -> ACCEPTED and persisted")
p = {"AccountSid": A_SID, "MessageSid": SID_A, "MessageStatus": "delivered"}
r = post(client, STATUS_EP, p, token=A_TOKEN)
check("signed callback returns 200", r.status_code == 200,
      f"got {r.status_code} {r.text[:200]}")
ds, ts, at = row("msg-a")
check("delivery_status persisted as 'delivered'", ds == "delivered", str(ds))
check("twilio_status persisted as 'delivered'", ts == "delivered", str(ts))
check("delivery_status_at was set", at is not None, str(at))

print("\n[2] forged status callback (bad signature) -> 403, row UNCHANGED")
before = row("msg-a")
p2 = {"AccountSid": A_SID, "MessageSid": SID_A, "MessageStatus": "failed"}
r = post(client, STATUS_EP, p2, sig="Zm9yZ2VkLXNpZ25hdHVyZQ==")
check("forged callback returns 403", r.status_code == 403, str(r.status_code))
check("row is byte-for-byte unchanged after forgery", row("msg-a") == before,
      f"{before} -> {row('msg-a')}")

print("\n[3] MISSING signature -> 403, row UNCHANGED")
before = row("msg-a")
r = post(client, STATUS_EP, p2)          # no token, no sig header at all
check("unsigned callback returns 403", r.status_code == 403, str(r.status_code))
check("row unchanged after unsigned callback", row("msg-a") == before)

print("\n[4] unknown AccountSid -> 403 (even with a technically valid HMAC)")
before = row("msg-a")
p4 = {"AccountSid": "ACdeadbeefdeadbeefdeadbeefdeadbeef",
      "MessageSid": SID_A, "MessageStatus": "failed"}
r = post(client, STATUS_EP, p4, token=A_TOKEN)
check("unknown AccountSid returns 403", r.status_code == 403,
      str(r.status_code))
check("row unchanged after unknown-account callback", row("msg-a") == before)

print("\n[5] valid signed INBOUND -> ACCEPTED, reply created")
before_status, before_replies = lead_state("lead-a")
pi = {"AccountSid": A_SID, "From": A_LEAD_PHONE, "To": A_PHONE,
      "Body": "sounds good, call me", "MessageSid": "SM" + "c" * 32}
r = post(client, INBOUND_EP, pi, token=A_TOKEN)
check("signed inbound returns 200", r.status_code == 200,
      f"got {r.status_code} {r.text[:200]}")
after_status, after_replies = lead_state("lead-a")
check("a Reply row was created", after_replies == before_replies + 1,
      f"{before_replies} -> {after_replies}")

print("\n[6] forged INBOUND -> 403, NOTHING created")
before = lead_state("lead-a")
pf = {"AccountSid": A_SID, "From": A_LEAD_PHONE, "To": A_PHONE,
      "Body": "forged message", "MessageSid": "SM" + "d" * 32}
r = post(client, INBOUND_EP, pf, sig="Zm9yZ2VkLWluYm91bmQ=")
check("forged inbound returns 403", r.status_code == 403, str(r.status_code))
check("no Reply created and lead.status unchanged", lead_state("lead-a") == before,
      f"{before} -> {lead_state('lead-a')}")

print("\n[7] forged STOP / opt-out CANNOT create a DNC")
db = SessionLocal()
lead_before = db.query(Lead).filter(Lead.id == "lead-b").first().status
db.close()
pstop = {"AccountSid": B_SID, "From": B_LEAD_PHONE, "To": B_PHONE,
         "Body": "STOP", "MessageSid": "SM" + "e" * 32}
r = post(client, INBOUND_EP, pstop, sig="Zm9yZ2VkLXN0b3A=")
check("forged STOP returns 403", r.status_code == 403, str(r.status_code))
db = SessionLocal()
lead_after = db.query(Lead).filter(Lead.id == "lead-b").first().status
try:
    from app.models.models import SuppressionEntry
    n_supp = db.query(SuppressionEntry).count()
except Exception:
    n_supp = None
db.close()
check("lead.status did NOT become 'dnc'", lead_after == lead_before and
      lead_after != "dnc", f"{lead_before} -> {lead_after}")
check("no suppression entry was written",
      n_supp in (0, None), str(n_supp))

print("\n[8] forged reply CANNOT stop a cadence")
from app.models.models import CadenceState                     # noqa: E402


def cadence_status(lead_id):
    db = SessionLocal()
    try:
        s = db.query(CadenceState).filter(
            CadenceState.lead_id == lead_id).first()
        return s.status if s else None
    finally:
        db.close()


check("lead-b's cadence is active before the forgery",
      cadence_status("lead-b") == "active", str(cadence_status("lead-b")))
pc = {"AccountSid": B_SID, "From": B_LEAD_PHONE, "To": B_PHONE,
      "Body": "not interested", "MessageSid": "SM" + "f" * 32}
r = post(client, INBOUND_EP, pc, sig="Zm9yZ2VkLWNhZGVuY2U=")
check("forged reply returns 403", r.status_code == 403, str(r.status_code))
check("cadence is STILL active after the forged reply",
      cadence_status("lead-b") == "active", str(cadence_status("lead-b")))

# POSITIVE CONTROL: the same message, correctly signed, MUST stop it.
# Without this the check above would also pass on an endpoint that simply
# never stops cadences at all.
r = post(client, INBOUND_EP, pc, token=B_TOKEN)
check("a correctly signed reply DOES stop the cadence",
      r.status_code == 200 and cadence_status("lead-b") != "active",
      f"http={r.status_code} status={cadence_status('lead-b')}")

print("\n[9] CROSS-ORG: Org B's valid signature against Org A's message -> 403")
before = row("msg-a")
# B signs correctly with B's own token, but names A's MessageSid.
pcross = {"AccountSid": B_SID, "MessageSid": SID_A, "MessageStatus": "failed"}
r = post(client, STATUS_EP, pcross, token=B_TOKEN)
check("cross-org status callback returns 403", r.status_code == 403,
      str(r.status_code))
check("Org A's row unchanged by Org B", row("msg-a") == before,
      f"{before} -> {row('msg-a')}")

print("\n[9b] CROSS-ORG: Org A signing for Org B's inbound number -> 403")
beforeB = lead_state("lead-b")
pcross2 = {"AccountSid": A_SID, "From": B_LEAD_PHONE, "To": B_PHONE,
           "Body": "injected into B", "MessageSid": "SM" + "0" * 32}
r = post(client, INBOUND_EP, pcross2, token=A_TOKEN)
check("cross-org inbound returns 403", r.status_code == 403, str(r.status_code))
check("Org B's lead untouched", lead_state("lead-b") == beforeB)

print("\n[9c] POSITIVE CONTROL: B's own message with B's token still works")
pb = {"AccountSid": B_SID, "MessageSid": SID_B, "MessageStatus": "delivered"}
r = post(client, STATUS_EP, pb, token=B_TOKEN)
check("Org B can still update its OWN message", r.status_code == 200,
      f"got {r.status_code} — 403 here would mean the cross-org gate "
      f"over-blocks and no receipts work at all")
check("Org B's row did persist", row("msg-b")[0] == "delivered",
      str(row("msg-b")))

print("\n[10] HTTPS/proxy URL reconstruction still validates")
# Twilio signs the https:// URL. Render terminates TLS and the app sees http://,
# so a signature over the https URL must still verify via X-Forwarded-Proto.
p10 = {"AccountSid": A_SID, "MessageSid": SID_A, "MessageStatus": "sent"}
r = post(client, STATUS_EP, p10, token=A_TOKEN,
         url_for_sig=PUBLIC + STATUS_EP, proxy=True)
check("signature over the https:// URL verifies behind the proxy",
      r.status_code == 200, f"got {r.status_code}")
check("that callback persisted", row("msg-a")[0] == "sent", str(row("msg-a")))
# Negative control: a signature computed over the WRONG scheme must fail.
p10b = {"AccountSid": A_SID, "MessageSid": SID_A, "MessageStatus": "failed"}
r = post(client, STATUS_EP, p10b, token=A_TOKEN,
         url_for_sig="http://evil.example.com" + STATUS_EP, proxy=True)
check("signature over a foreign URL is rejected", r.status_code == 403,
      str(r.status_code))

print("\n[11] no global-token bypass exists")
src = open(os.path.join(ROOT, "app", "utils", "twilio_security.py"),
           encoding="utf-8").read()
body = "\n".join(l for l in src.splitlines()
                 if not l.strip().startswith("#"))
check("validate_twilio_webhook no longer returns without validating",
      "skipping signature" not in body,
      "the fail-open branch is still present")

# Grep for an actual ENVIRONMENT READ, not for the string "TWILIO_AUTH_TOKEN" —
# the guard's docstring names that variable precisely to explain why it is not
# used, so a substring test would fail on its own documentation. Parse the
# module and look for os.environ / os.getenv instead.
import ast                                                     # noqa: E402
guard_path = os.path.join(ROOT, "app", "utils", "twilio_webhook_guard.py")
tree = ast.parse(open(guard_path, encoding="utf-8").read())
env_reads = []
for node in ast.walk(tree):
    if isinstance(node, ast.Attribute) and node.attr in ("environ", "getenv"):
        env_reads.append(node.attr)
    if isinstance(node, ast.Name) and node.id in ("environ", "getenv"):
        env_reads.append(node.id)
check("guard reads NO environment variable at all", not env_reads,
      "found %r — the per-account guard must never fall back to a global token"
      % (env_reads,))
check("guard resolves tokens from the database instead",
      "decrypt_value" in open(guard_path, encoding="utf-8").read())
router_src = open(os.path.join(ROOT, "app", "routers", "sms_router.py"),
                  encoding="utf-8").read()
check("sms_router uses the per-account guards, not the bare validator",
      "guard_status_callback(request, db)" in router_src
      and "guard_inbound(request, db)" in router_src
      and "validate_twilio_webhook(request)" not in router_src)

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
try:
    if os.path.exists(DB_FILE):
        engine.dispose()
        os.remove(DB_FILE)
except Exception:
    pass
if failures:
    for f in failures:
        print("  FAILED: " + f)
    sys.exit(1)
print("ALL TWILIO WEBHOOK AUTH CHECKS PASSED")
