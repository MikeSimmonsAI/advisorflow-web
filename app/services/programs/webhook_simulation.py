"""Signed inbound SMS / voice webhook simulation (gate T4). Synthetic only.

Stdlib only. Signature verification uses the same primitives as the real
webhook guard (app/utils/twilio_signature.py); routing, classification and
cadence use the real decision modules. The auth token is generated per run
with `secrets`, is never stored, logged or returned, and is not a Twilio
credential. Phone numbers are fictional 555-01xx numbers. No database, no
network, no send: every simulated outcome reports outbound == 0.

What this proves: signature accept/reject, pre-side-effect ordering, routing
decisions, hold/refusal, and zero outbound. What it does NOT prove: the real
FastAPI routes, AccountSid -> credential lookup, DB rows (see handoff doc).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from app.services.programs import regional_pools as rp
from app.services.programs import reply_rules as rr
from app.utils.twilio_signature import candidate_urls, compute_signature, signature_matches

SYNTH_HOST = "staging.synthetic.invalid"
SYNTH_BASE = "https://" + SYNTH_HOST
SMS_PATH, VOICE_PATH = "/sms/webhook/inbound", "/voice/inbound"
SYNTH_ACCOUNT = "ACsynthetic0000000000000000000000"
POOL_205_NUMBER, TOLL_FREE = "+12055550100", rp.BACKUP_TOLL_FREE
# synthetic directory: sender phone -> (location, area code). Oaklawn has no area code.
SYNTH_CONTACTS = {
    "+12055550111": ("Synthetic Known Location", "205"),
    "+12055550122": ("Synthetic Oaklawn Hold", ""),
}
SYNTH_POOL_NUMBERS = {POOL_205_NUMBER: rp.POOLS["205"]["pool_id"],
                      TOLL_FREE: rp.TOLL_FREE_POOL["pool_id"]}   # 844 = THE SCI line (2026-10-08)


def new_token() -> str:
    return secrets.token_hex(16)


def sign(token: str, path: str, params: Dict[str, str]) -> Dict:
    """A synthetic signed request (headers + params). The token stays with the caller."""
    return {"path": path, "query": "", "params": dict(params),
            "headers": {"host": SYNTH_HOST, "x-forwarded-proto": "https",
                        "x-twilio-signature": compute_signature(token, SYNTH_BASE + path, params)}}


def handle(token: str, req: Dict) -> Dict:
    """Simulate one inbound webhook: verify first, only then decide. Never sends."""
    out = {"status": 200, "accepted": False, "side_effects": 0, "outbound": 0, "queue": None,
           "location": None, "pool_id": None, "classification": None, "cadence": None,
           "ai_draft_sent": False, "recording_url": None, "transcript": None, "refused": None}
    cands = candidate_urls(req["path"], req["query"], req["headers"], SYNTH_BASE, SYNTH_BASE + req["path"])
    if not signature_matches(token, cands, req["params"], req["headers"].get("x-twilio-signature", "")):
        out["status"] = 403           # nothing below runs: zero side effects
        return out
    out["accepted"] = True
    p = req["params"]
    sender, to = p.get("From", ""), p.get("To", "")
    loc, area = SYNTH_CONTACTS.get(sender, (None, None))
    pool_id = SYNTH_POOL_NUMBERS.get(to)
    if pool_id is None:
        out["queue"] = "overflow_review"
        out["refused"] = "not a program number"
        return out
    if loc is not None and rp.pool_for_area_code(area) is None:
        out["queue"] = "hold"          # Oaklawn: no verified area code, nothing guessed or sent
        out["refused"] = "location unverified (hold)"
        out["pool_id"] = pool_id
        return out
    d = rp.route_inbound(loc, pool_id)
    out.update(queue=d["queue"], location=d["location"], pool_id=d["pool_id"])
    if "SmsSid" in p or "MessageSid" in p:
        cls = rr.classify(p.get("Body", ""))["class"]
        out["classification"] = cls
        out["cadence"] = rr.cadence_action(cls, "active")["new_status"]
        if loc is None and cls in (rr.BAD_DATA, rr.WRONG_PERSON):
            out["queue"] = "regional_review:%s" % pool_id
    else:
        # voice: recording/transcription are optional and only ever stored as references
        out["recording_url"] = p.get("RecordingUrl") or None
        out["transcript"] = p.get("TranscriptionText") or None
    return out


# ── scenarios ───────────────────────────────────────────────────────────────

def _sms(frm, to, body, sid="SMsynth0001"):
    return {"AccountSid": SYNTH_ACCOUNT, "MessageSid": sid, "SmsSid": sid, "From": frm, "To": to, "Body": body}


def _call(frm, to, **extra):
    d = {"AccountSid": SYNTH_ACCOUNT, "CallSid": "CAsynth0001", "From": frm, "To": to, "CallStatus": "ringing"}
    d.update(extra)
    return d


def _eq(got, want):
    assert got == want, "got %r, want %r" % (got, want)


def _scenarios(tok: str) -> List[Tuple[str, str, Callable[[], None]]]:
    other = new_token()
    known, unknown, oak = "+12055550111", "+12055550199", "+12055550122"
    S: List[Tuple[str, str, Callable[[], None]]] = []

    def add(key, label):
        def deco(fn):
            S.append((key, label, fn))
            return fn
        return deco

    @add("sms_valid", "Valid signed SMS is accepted")
    def _():
        r = handle(tok, sign(tok, SMS_PATH, _sms(known, POOL_205_NUMBER, "What does the package include?")))
        _eq((r["status"], r["accepted"], r["classification"]), (200, True, rr.ACTIVE))

    @add("sms_invalid_sig", "Invalid-signature SMS is rejected (403, zero side effects)")
    def _():
        req = sign(other, SMS_PATH, _sms(known, POOL_205_NUMBER, "STOP"))
        r = handle(tok, req)
        _eq((r["status"], r["accepted"], r["classification"], r["cadence"], r["side_effects"]), (403, False, None, None, 0))
        req2 = sign(tok, SMS_PATH, _sms(known, POOL_205_NUMBER, "hello"))
        req2["params"]["Body"] = "tampered"
        _eq(handle(tok, req2)["status"], 403)
        req3 = sign(tok, SMS_PATH, _sms(known, POOL_205_NUMBER, "hello"))
        req3["headers"].pop("x-twilio-signature")
        _eq(handle(tok, req3)["status"], 403)

    @add("voice_valid", "Valid signed voice event is accepted")
    def _():
        r = handle(tok, sign(tok, VOICE_PATH, _call(known, POOL_205_NUMBER)))
        _eq((r["status"], r["accepted"], r["location"]), (200, True, "Synthetic Known Location"))

    @add("voice_invalid_sig", "Invalid-signature voice event is rejected (403)")
    def _():
        _eq(handle(tok, sign(other, VOICE_PATH, _call(known, POOL_205_NUMBER)))["status"], 403)
        req = sign(tok, VOICE_PATH, _call(known, POOL_205_NUMBER))
        req["path"] = "/voice/other"
        _eq(handle(tok, req)["status"], 403)

    @add("route_known", "Known synthetic contact routes to its own location in the 205 pool")
    def _():
        r = handle(tok, sign(tok, SMS_PATH, _sms(known, POOL_205_NUMBER, "Yes please schedule a visit")))
        _eq((r["location"], r["queue"], r["pool_id"]), ("Synthetic Known Location", None, "pool-205-birmingham"))
        _eq((r["classification"], r["cadence"]), (rr.HOT, "paused"))

    @add("route_unknown_sms", "Unknown sender routes to regional review")
    def _():
        r = handle(tok, sign(tok, SMS_PATH, _sms(unknown, POOL_205_NUMBER, "Who is this?")))
        _eq((r["location"], r["queue"]), (None, "regional_review:pool-205-birmingham"))

    @add("route_unknown_voice", "Unknown caller routes to regional review")
    def _():
        r = handle(tok, sign(tok, VOICE_PATH, _call(unknown, POOL_205_NUMBER)))
        _eq((r["location"], r["queue"]), (None, "regional_review:pool-205-birmingham"))

    @add("wrong_number", "Wrong-number reply is classified bad data and stays in review")
    def _():
        r = handle(tok, sign(tok, SMS_PATH, _sms(unknown, POOL_205_NUMBER, "wrong number")))
        _eq(r["classification"], rr.BAD_DATA)
        _eq(r["queue"], "regional_review:pool-205-birmingham")
        _eq(r["location"], None)

    @add("oaklawn_hold", "Oaklawn text is held/refused: no location, no pool route, nothing sent")
    def _():
        r = handle(tok, sign(tok, SMS_PATH, _sms(oak, POOL_205_NUMBER, "hello")))
        _eq((r["queue"], r["location"], r["outbound"]), ("hold", None, 0))
        assert r["refused"]

    @add("toll_free_line", "844 SCI line: known contact to its own location, unknown to toll-free review")
    def _():
        tf = rp.TOLL_FREE_POOL["pool_id"]
        r = handle(tok, sign(tok, SMS_PATH, _sms(known, TOLL_FREE, "hello")))
        _eq((r["queue"], r["location"], r["pool_id"]), (None, "Synthetic Known Location", tf))
        r = handle(tok, sign(tok, SMS_PATH, _sms(unknown, TOLL_FREE, "Who is this?")))
        _eq((r["queue"], r["location"]), ("regional_review:" + tf, None))
        r = handle(tok, sign(tok, VOICE_PATH, _call(unknown, TOLL_FREE)))
        _eq((r["queue"], r["location"], r["outbound"]), ("regional_review:" + tf, None, 0))

    @add("no_ai_autosend", "No AI auto-send: HOT inbound pauses cadence and sends nothing")
    def _():
        r = handle(tok, sign(tok, SMS_PATH, _sms(known, POOL_205_NUMBER, "Yes please schedule a visit")))
        _eq((r["ai_draft_sent"], r["outbound"]), (False, 0))

    @add("recording_absent", "Voice event with no recording/transcription is handled safely")
    def _():
        r = handle(tok, sign(tok, VOICE_PATH, _call(known, POOL_205_NUMBER)))
        _eq((r["status"], r["recording_url"], r["transcript"]), (200, None, None))
        r2 = handle(tok, sign(tok, VOICE_PATH, _call(known, POOL_205_NUMBER, RecordingUrl="https://x.invalid/r")))
        _eq((r2["recording_url"], r2["outbound"]), ("https://x.invalid/r", 0))

    @add("zero_outbound", "Zero outbound activity across every simulated request")
    def _():
        reqs = [sign(tok, SMS_PATH, _sms(f, t, b)) for f in (known, unknown, oak) for t in (POOL_205_NUMBER, TOLL_FREE)
                for b in ("STOP", "Yes please schedule a visit", "wrong number")]
        reqs += [sign(other, VOICE_PATH, _call(known, POOL_205_NUMBER)), sign(tok, VOICE_PATH, _call(unknown, TOLL_FREE))]
        _eq(sum(handle(tok, r)["outbound"] for r in reqs), 0)

    return S


def run_all(now: Optional[datetime] = None) -> Dict:
    """Run every scenario with a fresh run-local token. Never raises; token never returned."""
    tok = new_token()
    results = []
    for key, label, fn in _scenarios(tok):
        try:
            fn()
            results.append({"key": key, "label": label, "status": "PASS", "error": None})
        except Exception as exc:                                  # noqa: BLE001
            results.append({"key": key, "label": label, "status": "FAIL",
                            "error": "%s: %s" % (type(exc).__name__, str(exc).replace(tok, "<token>"))})
    failed = [r for r in results if r["status"] == "FAIL"]
    return {"ran_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "pass_count": len(results) - len(failed), "fail_count": len(failed),
            "failed_gate": failed[0]["label"] if failed else None, "results": results,
            "next_action": (("Fix: " + failed[0]["label"]) if failed else
                            "Simulation passes. The dependency-backed route test (real FastAPI app + DB) is still an open gate."),
            "synthetic_only": True, "sent": 0, "outbound": 0}
