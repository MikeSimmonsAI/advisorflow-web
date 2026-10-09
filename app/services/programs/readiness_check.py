"""SCI Controlled Test console: launch gates and the synthetic readiness run.

Pure logic: stdlib plus the other pure decision modules, so the same code runs
in the app and in scripts/sci_readiness_harness.py. Nothing here sends, calls,
touches a database, reads a customer record or uses a secret.

The ten checks run deterministic synthetic fixtures (invented names, no real
numbers or addresses) through the REAL decision modules the webhooks use.
`LAUNCH_GATES` mirrors handoff/SCI_GO_NO_GO_CHECKLIST.md: it is a written
record, not a live probe, and must be edited when a gate changes.
"""
from __future__ import annotations

import ast
import os
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

from app.services import optout_parser as op
from app.services.programs import regional_pools as rp
from app.services.programs import webhook_simulation as ws
from app.services.programs import reply_rules as rr

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Synthetic fixtures only. Never a real SCI record.
SYNTH_HOT = "Yes please, can we schedule a visit tomorrow?"
SYNTH_ACTIVE = "What does the package include?"
SYNTH_FIRST = "Pat"
SYNTH_CONTACT = "Test Contact"
SYNTH_LOCATION = "Synthetic Test Location"
SYNTH_KNOWN_LOCATION = "Synthetic Known Location"


def _eq(got, want):
    assert got == want, "got %r, want %r" % (got, want)


def _raises_lookup(fn, *a):
    try:
        fn(*a)
    except LookupError:
        return
    raise AssertionError("expected no sender, but one resolved")


# ── the ten checks ───────────────────────────────────────────────────────────

def _hot():
    _eq(rr.classify(SYNTH_HOT)["class"], rr.HOT)
    _eq(rr.alert_plan(rr.HOT)["management"], True)


def _active():
    _eq(rr.classify(SYNTH_ACTIVE)["class"], rr.ACTIVE)


def _stop():
    _eq(op.contains_hard_stop_language("STOP"), True)
    _eq(rr.classify("STOP")["class"], rr.OPT_OUT)
    _eq(rr.cadence_action(rr.OPT_OUT, "active")["new_status"], "stopped_dnc")


def _stop_by():
    _eq(op.contains_hard_stop_language("Can I stop by Friday?"), False)
    _eq(rr.classify("Can I stop by Friday?")["class"], rr.HOT)


def _known():
    d = rp.route_inbound(SYNTH_KNOWN_LOCATION, rp.POOLS["205"]["pool_id"])
    _eq((d["location"], d["queue"]), (SYNTH_KNOWN_LOCATION, None))


def _unknown():
    for p in rp.POOLS.values():
        d = rp.route_inbound(None, p["pool_id"])
        _eq((d["location"], d["queue"]), (None, "regional_review:" + p["pool_id"]))


def _oaklawn():
    # Oaklawn has no area code (unverified): no pool, no sender, nothing guessed.
    _eq(rp.pool_for_area_code(""), None)
    _raises_lookup(rp.sender_pool, "")
    _raises_lookup(rp.sender_pool, None)


def _toll_free():
    # 844-917-2171 is THE SCI line (revised 2026-10-08): its own location-less
    # pool, never an area-code pool, and unknown senders go to its review queue.
    _eq(rp.TOLL_FREE, "+18449172171")
    _eq(rp.pool_for_area_code("844"), None)
    _raises_lookup(rp.sender_pool, "844")
    assert not any("844" in p["pool_id"] for p in rp.POOLS.values())
    d = rp.route_inbound(None, rp.TOLL_FREE_POOL["pool_id"])
    _eq((d["location"], d["queue"]), (None, "regional_review:" + rp.TOLL_FREE_POOL["pool_id"]))


_NO_SEND_MODULES = ("app/services/programs/reply_rules.py", "app/services/optout_parser.py",
                    "app/services/programs/regional_pools.py", "app/services/programs/alias_rules.py")
_BANNED_IMPORTS = ("twilio", "sms_service", "email_service", "requests", "httpx", "openai", "ai_gateway",
                   "sqlalchemy", "smtplib", "socket")


def _no_auto_send():
    draft = rr.suggested_reply(rr.HOT, [rr.APPOINTMENT], first_name=SYNTH_FIRST, contact=SYNTH_CONTACT,
                               location=SYNTH_LOCATION, channel="sms")
    assert isinstance(draft, str) and draft, "HOT reply must produce a draft string"
    _eq(rr.suggested_reply(rr.OPT_OUT, [], first_name=SYNTH_FIRST, contact=SYNTH_CONTACT,
                           location=SYNTH_LOCATION, channel="sms"), None)
    for rel in _NO_SEND_MODULES:
        tree = ast.parse(open(os.path.join(_ROOT, rel), encoding="utf-8").read())
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                names.add(n.module or "")
        hits = {i for i in names if any(b in i for b in _BANNED_IMPORTS)}
        assert not hits, "%s imports a send/network/DB module: %s" % (rel, sorted(hits))
    src = open(os.path.join(_ROOT, "app/services/programs/responses.py"), encoding="utf-8").read()
    calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
             for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call)}
    bad = calls & {"send_sms", "send_message", "send_sms_via_provider", "send_to_lead"}
    assert not bad, "responses.py calls a customer-send function: %s" % sorted(bad)


def _cadence():
    _eq(rr.cadence_action(rr.HOT, "active"), {"new_status": "paused", "paused": True})
    _eq(rr.cadence_action(rr.ACTIVE, "active")["new_status"], "paused")
    _eq(rr.cadence_action(rr.HOT, "stopped_dnc")["new_status"], "stopped_dnc")


# key, label, function, what to do if it fails
CHECKS: List[Tuple[str, str, Callable[[], None], str]] = [
    ("hot_reply", "Synthetic HOT appointment reply is classified HOT", _hot,
     "Fix reply_rules.classify HOT patterns before any controlled test."),
    ("active_reply", "Synthetic ACTIVE information reply is classified ACTIVE", _active,
     "Fix reply_rules.classify ACTIVE patterns."),
    ("optout_stop", "\"STOP\" is an opt-out and ends the cadence", _stop,
     "STOP must always suppress; fix optout_parser / cadence_action. Send nothing until fixed."),
    ("optout_stop_by", "\"Can I stop by Friday?\" is NOT an opt-out", _stop_by,
     "False opt-outs lose real families; fix optout_parser scheduling language."),
    ("route_known", "Known contact routes to its own location", _known,
     "Fix regional_pools.route_inbound known-sender path."),
    ("route_unknown", "Unknown sender/caller goes to regional review (all six pools)", _unknown,
     "Unknown senders must never get a guessed location; fix route_inbound."),
    ("oaklawn_hold", "Oaklawn refuses/holds: no area code, no pool, no sender", _oaklawn,
     "Oaklawn must never get a sender; fix sender_pool and keep its 9 contacts out of every send."),
    ("toll_free_line", "844 is the SCI toll-free line: location-less, unknown senders to review", _toll_free,
     "The toll-free line must never name a location; fix regional_pools TOLL_FREE_POOL / route_inbound."),
    ("no_auto_send", "No AI auto-send: drafts only, decision modules have no send path", _no_auto_send,
     "Remove the send/network import or call named in the failure; drafts must stay drafts."),
    ("cadence_pause", "Meaningful reply pauses cadence (never resumes a stopped one)", _cadence,
     "Fix reply_rules.cadence_action."),
]


def run_all(now: Optional[datetime] = None) -> Dict:
    """Execute every check. Never raises; failures are reported by gate."""
    ts = now or datetime.now(timezone.utc)
    results = []
    for key, label, fn, fix in CHECKS:
        try:
            fn()
            results.append({"key": key, "label": label, "status": "PASS", "error": None, "next_action": None})
        except Exception as exc:                                  # noqa: BLE001
            results.append({"key": key, "label": label, "status": "FAIL",
                            "error": "%s: %s" % (type(exc).__name__, exc), "next_action": fix})
    failed = [r for r in results if r["status"] == "FAIL"]
    proof = ws.run_all(ts)          # signed SMS/voice simulation, run-local synthetic token
    return {
        "webhook_proof": proof,
        "ran_at": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pass_count": len(results) - len(failed),
        "fail_count": len(failed),
        "failed_gate": failed[0]["label"] if failed else None,
        "results": results,
        "next_action": (failed[0]["next_action"] if failed else
                        "All synthetic checks pass. Clear the external blockers below; none need new code."),
        "synthetic_only": True,
        "sent": 0,
    }


# ── launch gates (mirror of handoff/SCI_GO_NO_GO_CHECKLIST.md) ────────────────
# group: verified | test_now | external | approval
LAUNCH_GATES: List[Dict] = [
    {"group": "verified", "key": "A1", "text": "Contact cleanup and holds: 535 clean contacts, 9 held. Held records stay in place."},
    {"group": "verified", "key": "A2", "text": "39 entities / 30 campuses / six regional pools mapped (38 of 39 entities)."},
    {"group": "verified", "key": "A4", "text": "STOP works; \"Can I stop by Friday?\" is not an opt-out."},
    {"group": "verified", "key": "A5", "text": "HOT/ACTIVE/LOW, cadence pause on reply, no auto-send of drafts."},
    {"group": "verified", "key": "A7", "text": "116-scenario stdlib harness passes."},
    {"group": "verified", "key": "A11", "text": "Inbound HOT email proven on staging (alert, cadence pause, no auto-send)."},
    {"group": "verified", "key": "T4", "text": "Signed inbound SMS/voice simulation passes (pure helper, synthetic token; the real FastAPI route + DB test is still open under B1)."},
    {"group": "verified", "key": "A13", "text": "Real customer messaging is OFF; campaigns refuse to send."},
    {"group": "test_now", "key": "T1", "text": "Run the controlled readiness test (button above): ten synthetic checks, no sends."},
    {"group": "test_now", "key": "T2", "text": "Mailbox health on staging (B7): confirm the Health tab shows inbound sync working."},
    {"group": "test_now", "key": "T3", "text": "Inbound email from Mike's approved test address to a location alias."},
    {"group": "external", "key": "C2", "text": "Six Twilio numbers (205, 334, 850, 251, 706, 318) are not provisioned."},
    {"group": "external", "key": "C3", "text": "A2P / carrier path for real outbound SMS is unresolved."},
    {"group": "external", "key": "B1", "text": "Dependency-backed pytest and DB integration are not green (not run on the relay runner)."},
    {"group": "external", "key": "B2", "text": "Michael's first interactive login is unverified."},
    {"group": "external", "key": "B6", "text": "Email placement at Outlook, Yahoo and iCloud not checked (Gmail landed outside Inbox)."},
    {"group": "external", "key": "C1", "text": "Oaklawn Central Care Center location is unresolved; its 9 contacts stay unsendable."},
    {"group": "external", "key": "C6", "text": "SCI code is not in production; promotion to main is not done."},
    {"group": "approval", "key": "D1", "text": "Mike: authorize spend for six local numbers."},
    {"group": "approval", "key": "D2", "text": "Mike: confirm Kerry Allan's identity and signature (neutral Variant N is used until then)."},
    {"group": "approval", "key": "D3", "text": "Mike: A2P / carrier registration decision and attestation."},
    {"group": "approval", "key": "D4", "text": "Mike: production promotion of SCI code."},
    {"group": "approval", "key": "D5", "text": "Mike: explicit GO for the first ~10 live clean contacts."},
]

WHY_NO_GO: List[str] = [
    "Six Twilio numbers are not provisioned (needs your spend approval).",
    "No A2P / carrier path exists yet for real outbound SMS (your attestation).",
    "Dependency-backed pytest and DB integration are still not green.",
    "Michael's first interactive login is not yet verified.",
    "Email placement at Outlook, Yahoo and iCloud has not been checked.",
    "Oaklawn Central Care Center is unresolved with SCI.",
    "SCI is not promoted to production, and there is no GO from you yet.",
]


def verdict(last: Optional[Dict]) -> Dict:
    """READY / CONDITIONAL / BLOCKED for the staging build."""
    external = [g for g in LAUNCH_GATES if g["group"] in ("external", "approval")]
    if last is None:
        return {"status": "BLOCKED", "reason": "The controlled readiness test has not been run yet. Press Run."}
    if last["fail_count"]:
        return {"status": "BLOCKED", "reason": "Failed gate: %s." % last["failed_gate"]}
    if last.get("webhook_proof", {}).get("fail_count"):
        return {"status": "BLOCKED", "reason": "Failed webhook proof: %s." % last["webhook_proof"]["failed_gate"]}
    if external:
        return {"status": "CONDITIONAL",
                "reason": "Synthetic checks pass (%d/%d). Staging-only testing is fine; real customer contact is still NO-GO."
                          % (last["pass_count"], last["pass_count"] + last["fail_count"])}
    return {"status": "READY", "reason": "All checks pass and no external blockers remain."}


def console(last: Optional[Dict]) -> Dict:
    groups = {k: [g for g in LAUNCH_GATES if g["group"] == k] for k in ("verified", "test_now", "external", "approval")}
    return {"verdict": verdict(last), "groups": groups, "why_no_go": WHY_NO_GO, "last_run": last,
            "persisted": False,
            "note": "Synthetic data only. Nothing is sent. Results are kept in memory on this server and reset on restart."}
