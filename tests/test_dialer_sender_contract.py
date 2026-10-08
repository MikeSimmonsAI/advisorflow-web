"""Dependency-free contract: Human Dialer caller-identity selection + replay guard.

Executes the production pure module (dialer_sender.py) plus source-wiring checks.
Synthetic data only; nothing here can reach a provider.
"""
import importlib.util
import os
import re
import unittest
from datetime import datetime, timedelta

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_s = importlib.util.spec_from_file_location(
    "dialer_sender", os.path.join(_ROOT, "app", "services", "dialer_sender.py"))
D = importlib.util.module_from_spec(_s)
_s.loader.exec_module(D)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def N(i, e164, org="A", ws=None, active=True, voice=True, label=None, at="2026-01-01"):
    return {"id": i, "e164": e164, "organization_id": org, "workspace_id": ws,
            "is_active": active, "cap_voice_outbound": voice, "label": label, "created_at": at}


class Select(unittest.TestCase):
    def test_one_approved_number_is_used(self):
        opts = D.eligible_identities([N("1", "+12145550101", label="Main")], "A")
        r = D.choose_sender(opts)
        self.assertTrue(r["ok"])
        self.assertEqual((r["e164"], r["number_id"], r["label"]), ("+12145550101", "1", "Main"))

    def test_multiple_requires_explicit_choice(self):
        opts = D.eligible_identities([N("1", "+12145550101"), N("2", "+12145550102")], "A")
        r = D.choose_sender(opts)
        self.assertFalse(r["ok"])
        self.assertTrue(r["needs_selection"])
        self.assertEqual(r["reason"], D.NEEDS_SELECTION)
        self.assertEqual(D.choose_sender(opts, "2")["e164"], "+12145550102")

    def test_none_fails_closed(self):
        r = D.choose_sender(D.eligible_identities([], "A"))
        self.assertFalse(r["ok"])
        self.assertIsNone(r["e164"])
        self.assertEqual(r["reason"], D.NO_IDENTITY)

    def test_never_a_fallback_literal(self):
        r = D.choose_sender([])
        self.assertIsNone(r["e164"])
        self.assertIsNone(r["number_id"])

    def test_other_tenant_number_not_offered_or_selectable(self):
        rows = [N("1", "+12145550101"), N("9", "+12145550999", org="B")]
        opts = D.eligible_identities(rows, "A")
        self.assertEqual([o["id"] for o in opts], ["1"])
        r = D.choose_sender(opts, "9")          # client-supplied foreign id
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], D.BAD_SELECTION)

    def test_pool_and_ownerless_numbers_excluded(self):
        self.assertEqual(D.eligible_identities([N("p", "+12145550001", org=None)], "A"), [])
        self.assertEqual(D.eligible_identities([N("1", "+12145550101")], None), [])

    def test_inactive_or_non_voice_excluded(self):
        rows = [N("1", "+12145550101", active=False), N("2", "+12145550102", voice=False)]
        self.assertEqual(D.eligible_identities(rows, "A"), [])

    def test_malformed_caller_id_excluded(self):
        for bad in (None, "", "2145550101", "+1214555", "+11145550101", "+12145550101x", 5551234):
            self.assertEqual(D.eligible_identities([N("1", bad)], "A"), [], bad)
            self.assertFalse(D.valid_caller_id(bad))

    def test_selection_of_unknown_id_never_substituted(self):
        opts = D.eligible_identities([N("1", "+12145550101")], "A")
        r = D.choose_sender(opts, "nope")
        self.assertFalse(r["ok"])
        self.assertIsNone(r["e164"])

    def test_workspace_number_preferred_and_other_workspace_hidden(self):
        rows = [N("o", "+12145550101"), N("w", "+12145550102", ws="W1"),
                N("x", "+12145550103", ws="W2")]
        opts = D.eligible_identities(rows, "A", "W1")
        self.assertEqual({o["id"] for o in opts}, {"o", "w"})
        self.assertEqual(D.choose_sender(opts, None, "W1")["number_id"], "w")
        self.assertEqual(D.choose_sender(opts, "w", None)["number_id"], "w")
        no_ws = D.eligible_identities(rows, "A", None)
        self.assertEqual([o["id"] for o in no_ws], ["o"])


class Replay(unittest.TestCase):
    now = datetime(2026, 10, 7, 12, 0, 0)

    def call(self, status="ringing_user", lead="L", user="U", age=5):
        return {"lead_id": lead, "advisor_id": user, "status": status,
                "created_at": self.now - timedelta(seconds=age)}

    def test_double_click_detected(self):
        self.assertIsNotNone(D.find_inflight([self.call()], "L", "U", self.now))
        self.assertIsNotNone(D.find_inflight([self.call("initiating")], "L", "U", self.now))

    def test_finished_other_lead_other_user_or_stale_not_blocking(self):
        for c in (self.call("completed"), self.call("failed"), self.call(lead="M"),
                  self.call(user="V"), self.call(age=3600)):
            self.assertIsNone(D.find_inflight([c], "L", "U", self.now))


class Wiring(unittest.TestCase):
    def test_service_uses_tenant_inventory_not_global_resolver(self):
        s = _src("app/services/telephony_service.py")
        start = s[s.index("def start_human_call"):s.index("def bridge_twiml")]
        self.assertNotIn("resolve_voice_number", start)
        self.assertIn("human_sender(", start)
        self.assertIn("find_inflight", start)
        ready = s[s.index("def human_call_readiness"):s.index("def start_human_call")]
        self.assertNotIn("resolve_voice_number", ready)

    def test_no_hardcoded_sender_in_initiation_path(self):
        for rel in ("app/services/telephony_service.py", "app/services/dialer_sender.py"):
            self.assertIsNone(re.search(r"[\"']\+1[2-9]\d{9}[\"']", _src(rel)), rel)

    def test_router_passes_selection_and_audits_sender(self):
        r = _src("app/routers/telephony_router.py")
        self.assertIn("number_id: Optional[str]", r)
        self.assertIn("start_human_call(db, lead, user, req.number_id)", r)
        self.assertIn('"from_phone": call.from_phone', r)
        self.assertIn("_lead_in_scope(db, user, req.lead_id)", r)

    def test_ui_selects_explicitly_and_blocks_double_submit(self):
        j = _src("frontend/src/components/telephony/CallButton.jsx")
        self.assertIn("from_options", j)
        self.assertIn("busy.current", j)
        self.assertIn("number_id: pick || undefined", j)

    def test_pure_module_cannot_dial(self):
        s = _src("app/services/dialer_sender.py")
        self.assertNotIn("create_call", s)
        self.assertNotIn("import twilio", s)


if __name__ == "__main__":
    unittest.main()
