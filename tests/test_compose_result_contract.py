"""Dependency-free contract: AI compose results are normalized to usable text or
a specific, retryable/actionable reason - never invented content and never the
opaque "AI returned no message". Synthetic data only; no network, no DB."""
import importlib.util
import os
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "compose_result_under_test", os.path.join(_ROOT, "app", "services", "compose_result.py"))
CR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(CR)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


class Parse(unittest.TestCase):
    def test_valid_json_text(self):
        r = CR.parse_compose_output('{"subject": "Hi", "body": " Hello there. "}')
        self.assertEqual((r["status"], r["body"], r["subject"]), ("ok", "Hello there.", "Hi"))

    def test_fenced_json(self):
        r = CR.parse_compose_output('```json\n{"body": "Fenced ok"}\n```')
        self.assertEqual((r["status"], r["body"]), ("ok", "Fenced ok"))

    def test_plain_prose_is_kept(self):
        r = CR.parse_compose_output("Hi Sam, just checking in.")
        self.assertEqual((r["status"], r["body"]), ("ok", "Hi Sam, just checking in."))

    def test_structured_alternatives(self):
        r = CR.parse_compose_output('{"alternatives": ["", {"message": "Option two"}]}')
        self.assertEqual((r["status"], r["body"]), ("ok", "Option two"))

    def test_alternate_key(self):
        self.assertEqual(CR.parse_compose_output('{"message": "m"}')["body"], "m")

    def test_whitespace_and_empty(self):
        for raw in ("   \n", "", None, '{"body": "  "}', '{}'):
            r = CR.parse_compose_output(raw)
            self.assertEqual((r["status"], r["body"]), ("empty", ""), raw)

    def test_refusal(self):
        r = CR.parse_compose_output("I'm sorry, but I can't help with that.")
        self.assertEqual((r["status"], r["body"]), ("refusal", ""))
        r = CR.parse_compose_output('{"refusal": "safety"}')
        self.assertEqual((r["status"], r["body"]), ("refusal", ""))

    def test_stop(self):
        r = CR.parse_compose_output('{"should_stop": true, "stop_reason": "not interested"}')
        self.assertEqual((r["status"], r["stop_reason"]), ("stopped", "not interested"))

    def test_truncated_json_never_sent_as_prose(self):
        self.assertEqual(CR.parse_compose_output('{"body": "cut off')["body"], "")


class Failure(unittest.TestCase):
    def test_specific_reasons(self):
        for kind in ("empty_generation", "refusal", "AuthenticationError", "RateLimitError",
                     "APITimeoutError", "AIDisabled", "SpendLimitReached", "JSONDecodeError"):
            d = CR.describe_failure(kind)
            self.assertEqual(d["error_kind"], kind)
            self.assertTrue(d["error_message"] and d["action"])
            self.assertNotIn("no message", d["error_message"].lower())

    def test_retryability(self):
        self.assertTrue(CR.describe_failure("APITimeoutError")["retryable"])
        self.assertFalse(CR.describe_failure("AuthenticationError")["retryable"])
        self.assertFalse(CR.describe_failure("refusal")["retryable"])

    def test_unknown_and_none_still_actionable(self):
        self.assertTrue(CR.describe_failure("WeirdError")["action"])
        self.assertEqual(CR.describe_failure(None)["error_kind"], "empty_generation")


class Wiring(unittest.TestCase):
    def test_service_uses_helpers_and_exposes_reason(self):
        s = _src("app/services/ai_conversation_service.py")
        self.assertIn("compose_result.parse_compose_output(raw)", s)
        self.assertIn("compose_result.describe_failure(kind)", s)
        self.assertIn('"error_message": result.get("error_message")', s)

    def test_batch_router_keeps_per_lead_reason(self):
        s = _src("app/routers/ai_conversation_router.py")
        self.assertIn('ai_result.get("error_message")', s)
        self.assertIn('"retryable"', s)

    def test_ui_shows_specific_reason(self):
        s = _src("frontend/src/pages/Leads.jsx")
        self.assertIn("result.error_message", s)
        self.assertIn("r.action ?", s)
        self.assertNotIn("AI returned an empty message", s)


if __name__ == "__main__":
    unittest.main()
