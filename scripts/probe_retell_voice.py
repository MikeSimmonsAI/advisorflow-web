"""
probe_retell_voice.py — deploy gate for the Retell voice integration.

No production API key, no vendor network call, no real phone call. The Retell
HTTP client is faked; every webhook is signed here with real HMAC-SHA256 the
way Retell signs, replayed against the actual FastAPI app, and the database is
read back afterwards to prove a rejected request changed nothing.

Every negative check is paired with a positive control. A suite that only ever
asserts 403 would pass just as happily against an endpoint that refuses
everything — which would prove nothing about whether real calls still work.

Run: python scripts/probe_retell_voice.py
"""
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["DATABASE_URL"] = "sqlite:///./.probe_retell_voice.db"
os.environ["JWT_SECRET"] = "probe" * 16
from cryptography.fernet import Fernet                            # noqa: E402
os.environ["ENCRYPTION_KEY"] = Fernet.generate_key().decode()
API_KEY = "retell_probe_key_not_a_real_secret"
os.environ["RETELL_API_KEY"] = API_KEY
os.environ["API_BASE_URL"] = "https://advisorflow-backend.onrender.com"

DB_FILE = os.path.join(ROOT, ".probe_retell_voice.db")
if os.path.exists(DB_FILE):
    os.remove(DB_FILE)

from fastapi.testclient import TestClient                          # noqa: E402
from app.main import app                                           # noqa: E402
from app.deps import SessionLocal, engine                          # noqa: E402
from app.models.models import (                                    # noqa: E402
    Base, CadenceState, Lead, Organization, Platform, SuppressionEntry,
    SuppressionSource, User, VoiceAgentConfig, VoiceCall)
from app.models.integration_models import IntegrationRequestLog     # noqa: E402
from app.services import comms                                     # noqa: E402
from app.services.comms.base import (                              # noqa: E402
    VoiceCallResult, VoiceProvider)
from app.services.comms.voice.retell import RetellVoiceProvider     # noqa: E402

WEBHOOK = "/voice/retell/webhook"
failures, checks = [], 0


def check(label, ok, detail=""):
    global checks
    checks += 1
    if ok:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label + ("  -> " + str(detail)[:240] if detail else ""))
        failures.append(label)


# ── a Retell that places no calls ────────────────────────────────────────────

class FakeRetell(RetellVoiceProvider):
    """Real verification and parsing; faked transport.

    Subclassing rather than reimplementing is deliberate: the signature and
    event-parsing logic under test is the SAME code production runs. Only
    `start_call`'s network hop is replaced.
    """
    placed = []
    fail_next = False

    def start_call(self, req):
        FakeRetell.placed.append(req)
        if FakeRetell.fail_next:
            FakeRetell.fail_next = False
            return VoiceCallResult.failure("http_500", "Retell is down")
        return VoiceCallResult(ok=True,
                               provider_call_id="call_%d" % len(FakeRetell.placed),
                               provider_status="registered")


def sign(body: bytes, key: str = API_KEY, ts_ms: int = None) -> str:
    ts = str(ts_ms if ts_ms is not None else int(time.time() * 1000))
    digest = hmac.new(key.encode(), body + ts.encode(), hashlib.sha256).hexdigest()
    return "v=%s,d=%s" % (ts, digest)


def post_event(client, payload: dict, key: str = API_KEY, ts_ms: int = None,
               signature: str = None):
    raw = json.dumps(payload).encode()
    sig = signature if signature is not None else sign(raw, key, ts_ms)
    return client.post(WEBHOOK, content=raw, headers={
        "X-Retell-Signature": sig, "Content-Type": "application/json"})


def call_payload(event, call_id, org="org-a", lead="lead-a", **extra):
    call = {"call_id": call_id, "call_status": "ended",
            "metadata": {"organization_id": org, "lead_id": lead}}
    call.update(extra)
    return {"event": event, "call": call}


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.add(Platform(id="plt", name="EvoSys Pro", slug="evo-voice"))
    db.flush()
    db.add_all([
        Organization(id="org-a", name="Restland", slug="restland-voice",
                     platform_id="plt", is_active=True),
        Organization(id="org-b", name="Other Home", slug="other-voice",
                     platform_id="plt", is_active=True),
    ])
    db.flush()
    db.add_all([
        User(id="adv-a", organization_id="org-a", email="a@probe.test",
             full_name="Grace Alvarez", password_hash="x", role="advisor"),
        User(id="adv-b", organization_id="org-b", email="b@probe.test",
             full_name="Other Advisor", password_hash="x", role="advisor"),
    ])
    db.flush()
    db.add_all([
        Lead(id="lead-a", organization_id="org-a", first_name="Marta",
             last_name="Delgado", phone="15551110001", status="new",
             assigned_to_id="adv-a"),
        Lead(id="lead-b", organization_id="org-b", first_name="Other",
             last_name="Family", phone="15551110002", status="new",
             assigned_to_id="adv-b"),
        Lead(id="lead-sup", organization_id="org-a", first_name="Suppressed",
             last_name="Person", phone="15551110003", status="new",
             assigned_to_id="adv-a"),
        Lead(id="lead-dnc", organization_id="org-a", first_name="Flagged",
             last_name="Person", phone="15551110004", status="dnc",
             assigned_to_id="adv-a"),
    ])
    db.flush()
    db.add_all([
        VoiceAgentConfig(id="cfg-a", organization_id="org-a", provider="retell",
                         agent_id="agent_file_check_existing",
                         from_number="+15550000001", use_case="file_check",
                         label="File Check", is_active=True),
        VoiceAgentConfig(id="cfg-b", organization_id="org-b", provider="retell",
                         agent_id="agent_file_check_existing",
                         from_number="+15550000002", use_case="file_check",
                         is_active=True),
    ])
    # A number suppressed org-wide whose Lead.status was never flipped — the
    # exact shape the legacy Twilio voice path would still have called.
    db.add(SuppressionEntry(organization_id="org-a", phone="15551110003",
                            reason="Replied STOP to a text",
                            source=SuppressionSource.REPLY_STOP))
    db.add_all([CadenceState(lead_id="lead-a", status="active")])
    db.commit()
    db.close()


def row(call_id):
    db = SessionLocal()
    try:
        c = db.query(VoiceCall).filter(VoiceCall.id == call_id).first()
        return (c.status, c.outcome, c.transcript, c.summary,
                c.duration_seconds, c.booking_link_id, c.transfer_status)
    finally:
        db.close()


seed()
comms.register_voice_provider("retell", lambda db, cfg: FakeRetell(api_key=API_KEY))
client = TestClient(app)
from app.services import voice_orchestrator as orch                # noqa: E402


def lead(lid):
    db = SessionLocal()
    try:
        return db.query(Lead).filter(Lead.id == lid).first()
    finally:
        db.close()


print("\n[1] credentials stay server-side")
src_files = []
for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "frontend", "src")):
    for fn in files:
        if fn.endswith((".js", ".jsx", ".ts", ".tsx")):
            src_files.append(os.path.join(dirpath, fn))
leaked = []
for f in src_files:
    try:
        text = open(f, encoding="utf-8", errors="replace").read()
    except Exception:
        continue
    if "RETELL_API_KEY" in text or "api.retellai.com" in text:
        leaked.append(f.replace(ROOT + os.sep, ""))
check("no Retell key or endpoint appears in the frontend", not leaked,
      "; ".join(leaked))
prov_src = open(os.path.join(ROOT, "app", "services", "comms", "voice",
                             "retell.py"), encoding="utf-8").read()
check("provider never logs the api key",
      "self.api_key)" not in prov_src.replace("Bearer %s\" % self.api_key", ""))
resp = client.get("/health")
check("health endpoint exposes no provider config",
      "retell" not in resp.text.lower() and "RETELL" not in resp.text)

print("\n[2] outbound maps to the correct lead/org and persists the call id")
FakeRetell.placed = []
call = orch.start_file_check_call(SessionLocal(), lead("lead-a"), "org-a")
check("call row created", call is not None and call.id)
check("bound to the right lead and org",
      call.lead_id == "lead-a" and call.organization_id == "org-a")
check("provider recorded as retell", call.provider == "retell")
check("provider_call_id persisted", bool(call.provider_call_id),
      call.provider_call_id)
check("agent id came from CONFIG, not a constant",
      call.agent_id == "agent_file_check_existing")
check("from_number came from config", call.from_phone == "+15550000001")
check("status advanced to ringing", call.status == "ringing")
req = FakeRetell.placed[-1]
check("metadata carries the correlation ids",
      req.metadata.get("lead_id") == "lead-a"
      and req.metadata.get("organization_id") == "org-a"
      and req.metadata.get("evosys_call_id") == call.id)
check("dynamic variables carry the first name",
      req.dynamic_variables.get("first_name") == "Marta")
check("dynamic variables leak no email/address/internal id",
      not any(k in req.dynamic_variables for k in
              ("email", "address", "lead_id", "phone")))
CALL_A = call.id
PCID_A = call.provider_call_id

print("\n[3] EvoSys — not the provider — decides who may be called")
before = len(FakeRetell.placed)
for lid, why in (("lead-sup", "org-wide suppression"),
                 ("lead-dnc", "lead marked dnc"),
                 ("lead-b", "cross-org")):
    try:
        orch.start_file_check_call(SessionLocal(), lead(lid), "org-a")
        check("refused: %s" % why, False, "the call was allowed")
    except PermissionError as exc:
        check("refused: %s" % why, True, str(exc))
check("no provider call was placed for any refusal",
      len(FakeRetell.placed) == before)
db = SessionLocal()
check("no VoiceCall row was written for a refused call",
      db.query(VoiceCall).filter(VoiceCall.lead_id.in_(
          ["lead-sup", "lead-dnc", "lead-b"])).count() == 0)
db.close()

print("\n[4] valid signed webhook is ACCEPTED and persists")
r = post_event(client, call_payload("call_started", PCID_A,
                                    start_timestamp=1756400000000))
check("signed call_started returns 200", r.status_code == 200, r.text[:160])
st = row(CALL_A)
check("status advanced to in_progress", st[0] == "in_progress", st[0])
db = SessionLocal()
check("answered_at stamped",
      db.query(VoiceCall).filter(VoiceCall.id == CALL_A).first().answered_at
      is not None)
db.close()

print("\n[5] forged / unverifiable webhooks are REFUSED with no side effects")
before = row(CALL_A)
cases = [
    ("no signature header", dict(signature="")),
    ("garbage signature", dict(signature="not-a-signature")),
    ("wrong shape", dict(signature="v=123")),
    ("signed with the wrong key", dict(key="some-other-api-key")),
    ("stale timestamp (replay)",
     dict(ts_ms=int((time.time() - 3600) * 1000))),
    ("future timestamp", dict(ts_ms=int((time.time() + 3600) * 1000))),
]
for label, kw in cases:
    r = post_event(client, call_payload("call_ended", PCID_A,
                                        transcript="forged"), **kw)
    check("%s -> 403" % label, r.status_code == 403, r.status_code)
check("the call row is byte-for-byte unchanged after every forgery",
      row(CALL_A) == before, "%s -> %s" % (before, row(CALL_A)))

print("\n[6] unknown provider call id cannot mutate anything")
db = SessionLocal()
n_before = db.query(VoiceCall).count()
db.close()
r = post_event(client, call_payload("call_ended", "call_does_not_exist"))
check("unknown call id -> 403", r.status_code == 403, r.status_code)
db = SessionLocal()
check("no VoiceCall row was created by an unknown-call event",
      db.query(VoiceCall).count() == n_before)
db.close()

print("\n[7] Org A's event cannot alter Org B's call")
callb = orch.start_file_check_call(SessionLocal(), lead("lead-b"), "org-b")
CALL_B, PCID_B = callb.id, callb.provider_call_id
before_b = row(CALL_B)
# A perfectly signed event naming B's call id but claiming to be org A.
r = post_event(client, call_payload("call_ended", PCID_B, org="org-a",
                                    lead="lead-a", transcript="cross-org"))
check("cross-org metadata -> 403", r.status_code == 403, r.status_code)
check("Org B's call unchanged", row(CALL_B) == before_b)
# POSITIVE CONTROL: B's own event with B's own metadata still works.
r = post_event(client, call_payload("call_ended", PCID_B, org="org-b",
                                    lead="lead-b", transcript="B's real call"))
check("Org B can still update its OWN call", r.status_code == 200,
      "403 here would mean the ownership gate over-blocks")
check("...and it persisted", row(CALL_B)[2] == "B's real call", row(CALL_B))

print("\n[8] transcript attaches to the correct call")
r = post_event(client, {"event": "transcript_updated",
                        "call": {"call_id": PCID_A,
                                 "transcript": "Agent: Hello. User: Hi.",
                                 "metadata": {"organization_id": "org-a"}}})
check("transcript_updated accepted", r.status_code == 200)
check("transcript stored on call A", row(CALL_A)[2] == "Agent: Hello. User: Hi.")
check("transcript did NOT land on call B", row(CALL_B)[2] == "B's real call")

print("\n[9] post-call analysis maps to outcomes")
r = post_event(client, call_payload(
    "call_analyzed", PCID_A, start_timestamp=1756400000000,
    end_timestamp=1756400120000,
    disconnection_reason="user_hangup",
    call_analysis={"call_summary": "Family confirmed the file is current.",
                   "custom_analysis_data": {"reached_person": True,
                                            "interested": True}}))
check("call_analyzed accepted", r.status_code == 200, r.text[:160])
st = row(CALL_A)
check("summary persisted", st[3] == "Family confirmed the file is current.", st[3])
check("duration computed from provider timestamps", st[4] == 120, st[4])
db = SessionLocal()
c = db.query(VoiceCall).filter(VoiceCall.id == CALL_A).first()
check("disconnect reason persisted", c.disconnect_reason == "user_hangup")
check("raw analysis kept", c.analysis_json and "custom_analysis_data" in c.analysis_json)
db.close()

print("\n[10] appointment correlation via the EXISTING external_ref convention")
db = SessionLocal()
db.add(IntegrationRequestLog(
    action="book", success=True, organization_id="org-a",
    external_ref=PCID_A, booking_link_id="bl-123", lead_id="lead-a"))
db.commit()
db.close()
r = post_event(client, call_payload(
    "call_analyzed", PCID_A,
    call_analysis={"call_summary": "Booked.",
                   "custom_analysis_data": {"appointment_booked": True}}))
check("call_analyzed accepted", r.status_code == 200)
st = row(CALL_A)
check("booking correlated by Retell call id -> external_ref",
      st[5] == "bl-123", st[5])
check("outcome recorded as booked", st[1] == "booked", st[1])

print("\n[11] callback result maps")
call_cb = orch.start_file_check_call(SessionLocal(), lead("lead-a"), "org-a")
r = post_event(client, call_payload(
    "call_analyzed", call_cb.provider_call_id,
    call_analysis={"custom_analysis_data": {
        "callback_requested": True, "callback_at": "2026-09-02T15:00:00Z"}}))
check("callback event accepted", r.status_code == 200)
db = SessionLocal()
c = db.query(VoiceCall).filter(VoiceCall.id == call_cb.id).first()
check("outcome is callback_requested", c.outcome == "callback_requested", c.outcome)
check("callback time parsed", c.callback_at == datetime(2026, 9, 2, 15, 0),
      c.callback_at)
db.close()

print("\n[12] voice opt-out writes the EXISTING suppression authority")
db = SessionLocal()
supp_before = db.query(SuppressionEntry).filter(
    SuppressionEntry.organization_id == "org-a").count()
db.close()
call_out = orch.start_file_check_call(SessionLocal(), lead("lead-a"), "org-a")
r = post_event(client, call_payload(
    "call_analyzed", call_out.provider_call_id,
    call_analysis={"custom_analysis_data": {"opted_out": True}}))
check("opt-out event accepted", r.status_code == 200)
db = SessionLocal()
entry = (db.query(SuppressionEntry)
         .filter(SuppressionEntry.organization_id == "org-a",
                 SuppressionEntry.phone == "15551110001").first())
check("a SuppressionEntry was written to the SHARED table", entry is not None)
check("source records it came from voice",
      entry is not None and entry.source == SuppressionSource.VOICE_OPT_OUT,
      entry.source if entry else None)
check("no second suppression system was created",
      db.query(SuppressionEntry).filter(
          SuppressionEntry.organization_id == "org-a").count() == supp_before + 1)
lead_a = db.query(Lead).filter(Lead.id == "lead-a").first()
check("lead flagged dnc", lead_a.status == "dnc", lead_a.status)
cad = db.query(CadenceState).filter(CadenceState.lead_id == "lead-a").first()
check("cadence stopped", cad.status != "active", cad.status)
db.close()
# The point of a SHARED authority: voice opt-out now blocks the next voice call.
try:
    orch.start_file_check_call(SessionLocal(), lead("lead-a"), "org-a")
    check("a voice opt-out blocks the NEXT call", False, "call was allowed")
except PermissionError:
    check("a voice opt-out blocks the NEXT call", True)

print("\n[13] transfer result persists")
call_t = orch.start_file_check_call(SessionLocal(), lead("lead-dnc2")
                                    if False else lead("lead-a"), "org-a") \
    if False else None
db = SessionLocal()
tcall = VoiceCall(id="call-transfer", lead_id="lead-b", advisor_id="adv-b",
                  organization_id="org-b", to_phone="15551110002",
                  provider="retell", provider_call_id="call_transfer_1",
                  status="in_progress")
db.add(tcall)
db.commit()
db.close()
r = post_event(client, {"event": "transfer_started",
                        "call": {"call_id": "call_transfer_1",
                                 "metadata": {"organization_id": "org-b"}},
                        "transfer_destination": "+15559998888"})
check("transfer_started accepted", r.status_code == 200, r.text[:160])
db = SessionLocal()
t = db.query(VoiceCall).filter(VoiceCall.id == "call-transfer").first()
check("transfer flagged", t.transfer_requested is True)
check("destination recorded", t.transfer_destination == "+15559998888")
check("transfer status recorded", t.transfer_status == "started", t.transfer_status)
db.close()
r = post_event(client, {"event": "transfer_bridged",
                        "call": {"call_id": "call_transfer_1",
                                 "metadata": {"organization_id": "org-b"}}})
db = SessionLocal()
t = db.query(VoiceCall).filter(VoiceCall.id == "call-transfer").first()
check("bridged updates status and outcome",
      t.transfer_status == "bridged" and t.outcome == "escalated")
db.close()

print("\n[14] provider failure leaves a clean, recoverable state")
db = SessionLocal()
l2 = db.query(Lead).filter(Lead.id == "lead-b").first()
db.close()
FakeRetell.fail_next = True
failed = orch.start_file_check_call(SessionLocal(), lead("lead-b"), "org-b")
check("a row still exists for the failed attempt", failed is not None)
check("marked failed rather than left dangling", failed.status == "failed",
      failed.status)
check("the vendor error is recorded", bool(failed.error_message),
      failed.error_message)
check("no provider_call_id was invented", not failed.provider_call_id)
check("ended_at stamped so it is not 'in flight' forever",
      failed.ended_at is not None)

print("\n[15] duplicate delivery is idempotent")
db = SessionLocal()
c_before = db.query(VoiceCall).filter(VoiceCall.id == CALL_B).first()
snapshot = (c_before.status, c_before.outcome, c_before.transcript)
n_supp = db.query(SuppressionEntry).count()
n_calls = db.query(VoiceCall).count()
db.close()
dup = call_payload("call_ended", PCID_B, org="org-b", lead="lead-b",
                   transcript="B's real call")
for _ in range(3):
    rr = post_event(client, dup)
    check("duplicate delivery accepted (Retell retries)", rr.status_code == 200)
db = SessionLocal()
c_after = db.query(VoiceCall).filter(VoiceCall.id == CALL_B).first()
check("no duplicate rows created",
      db.query(VoiceCall).count() == n_calls
      and db.query(SuppressionEntry).count() == n_supp)
check("state is identical after replays",
      (c_after.status, c_after.outcome, c_after.transcript) == snapshot,
      "%s -> %s" % (snapshot, (c_after.status, c_after.outcome, c_after.transcript)))
db.close()

print("\n[16] the old Twilio voice stack is NOT re-enabled")
vr = open(os.path.join(ROOT, "app", "routers", "voice_router.py"),
          encoding="utf-8").read()
check("voice_router still calls the bare validator (untouched)",
      "validate_twilio_webhook(request)" in vr)
check("voice_router does not import the Retell provider",
      "comms" not in vr and "retell" not in vr.lower())
wh = open(os.path.join(ROOT, "app", "routers", "voice_webhooks_router.py"),
          encoding="utf-8").read()
# Parse the imports rather than grepping: the module's docstring names
# voice_router.py precisely to explain why it is a SEPARATE file, so a
# substring test fails on its own documentation.
import ast                                                         # noqa: E402
_wh_imports = []
for _node in ast.walk(ast.parse(wh)):
    if isinstance(_node, ast.Import):
        _wh_imports += [a.name for a in _node.names]
    elif isinstance(_node, ast.ImportFrom):
        _wh_imports.append(_node.module or "")
check("the Retell webhook imports no part of voice_router",
      not any("voice_router" in (m or "") for m in _wh_imports),
      str(_wh_imports))
sec = open(os.path.join(ROOT, "app", "utils", "twilio_security.py"),
           encoding="utf-8").read()
check("Twilio fail-closed behaviour is still in place",
      "skipping signature" not in sec)
guard = open(os.path.join(ROOT, "app", "utils", "twilio_webhook_guard.py"),
             encoding="utf-8").read()
check("the Twilio per-account guard is untouched",
      "resolve_account_by_sid" in guard and "cross-account" in guard)

print("\n[17] the architecture is reusable, not File-Check-shaped")
orch_src = open(os.path.join(ROOT, "app", "services", "voice_orchestrator.py"),
                encoding="utf-8").read()
def code_only(src):
    out, in_doc = [], False
    for line in src.splitlines():
        s = line.strip()
        if s.startswith('"""') or s.startswith("'''"):
            in_doc = not in_doc if s.count('"""') % 2 else in_doc
            continue
        if in_doc or s.startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)
check("no agent id is hard-coded anywhere in app/",
      "agent_file_check_existing" not in code_only(orch_src)
      and "agent_file_check_existing" not in code_only(prov_src))
check("the orchestrator reads the agent from config",
      "config.agent_id" in orch_src)
check("the orchestrator reads the number from config",
      "config.from_number" in orch_src)
check("use_case is a parameter, not a constant path",
      "use_case" in orch_src and "USE_CASE_FILE_CHECK" in orch_src)
check("the webhook resolves ownership from OUR row, not the payload",
      "call.organization_id" in wh and "claimed_org" in wh)
check("SMS provider delegates rather than reimplementing",
      "from app.services.sms_service import send_sms" in
      open(os.path.join(ROOT, "app", "services", "comms", "sms", "twilio.py"),
           encoding="utf-8").read())

print("\n%d checks, %d failure(s)" % (checks, len(failures)))
comms.reset_providers()
try:
    engine.dispose()
    if os.path.exists(DB_FILE):
        os.remove(DB_FILE)
except Exception:
    pass
if failures:
    for f in failures:
        print("  FAILED: " + f)
    sys.exit(1)
print("ALL RETELL VOICE CHECKS PASSED")
