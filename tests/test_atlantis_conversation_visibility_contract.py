"""Dependency-free contract: a stored email Reply renders as an EMAIL inbound
event in the /timeline conversation feed (it was hardcoded "sms").
Synthetic data only; no network."""
import importlib.util
import os
import unittest
from datetime import datetime
from types import SimpleNamespace

_P = os.path.join(os.path.dirname(__file__), "..", "app", "services", "reply_timeline.py")
_spec = importlib.util.spec_from_file_location("reply_timeline_under_test", _P)
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)


def _reply(**kw):
    base = dict(id="r1", body="Yes, call me.", source="email", is_hot=False,
                received_at=datetime(2026, 10, 7, 12, 0, 0))
    base.update(kw)
    return SimpleNamespace(**base)


class ReplyTimelineContract(unittest.TestCase):
    def test_email_reply_is_email_inbound_with_identity_and_timestamp(self):
        e = RT.inbound_reply_event(_reply())
        self.assertEqual((e["type"], e["channel"], e["id"]), ("inbound", "email", "r1"))
        self.assertEqual(e["timestamp"], datetime(2026, 10, 7, 12, 0, 0))
        self.assertEqual(e["body"], "Yes, call me.")

    def test_sms_and_legacy_null_source_stay_sms(self):
        self.assertEqual(RT.inbound_reply_event(_reply(source="sms"))["channel"], "sms")
        self.assertEqual(RT.inbound_reply_event(_reply(source=None))["channel"], "sms")
        self.assertEqual(RT.inbound_reply_event(_reply(source="weird"))["channel"], "sms")

    def test_preview_truncates_and_tolerates_empty_body(self):
        long = "x" * 500
        e = RT.inbound_reply_event(_reply(body=long))
        self.assertEqual(len(e["body_preview"]), RT.PREVIEW_CHARS)
        self.assertEqual(e["body"], long)
        self.assertEqual(RT.inbound_reply_event(_reply(body=None))["body_preview"], "")

    def test_routers_use_the_shared_serializer_not_a_hardcoded_channel(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        for rel in ("app/routers/leads_detail_router.py", "leads_router.py"):
            src = open(os.path.join(root, rel)).read()
            self.assertIn("inbound_reply_event(r)", src, rel)
            self.assertNotIn('"type": "inbound",\n            "channel": "sms"', src, rel)


if __name__ == "__main__":
    unittest.main()
