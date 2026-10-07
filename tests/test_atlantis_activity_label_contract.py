"""Dependency-free contract: /leads/{id}/activity labels a stored email Reply as
an EMAIL reply (it was hardcoded sms_reply / "Reply received"), and the three
read paths (/timeline, /history, /activity) share one channel normalizer.
Synthetic data only; no network, no DB."""
import importlib.util
import os
import re
import unittest
from datetime import datetime
from types import SimpleNamespace

_ROOT = os.path.join(os.path.dirname(__file__), "..")
_spec = importlib.util.spec_from_file_location(
    "reply_timeline_under_test_activity",
    os.path.join(_ROOT, "app", "services", "reply_timeline.py"))
RT = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(RT)


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _reply(**kw):
    base = dict(id="r1", body="Yes, call me.", source="email", is_hot=False,
                hot_reason=None, classification=None, reviewed_at=None,
                received_at=datetime(2026, 10, 7, 12, 0, 0))
    base.update(kw)
    return SimpleNamespace(**base)


def _fmt(dt):
    return None if dt is None else dt.isoformat() + "Z"


class ActivityReplyEvent(unittest.TestCase):
    def test_email_reply_is_email_reply(self):
        e = RT.reply_activity_event(_reply(), _fmt)
        self.assertEqual(e["type"], "email_reply")
        self.assertEqual(e["label"], "Email reply received")
        self.assertEqual(e["id"], "reply-r1")
        self.assertEqual(e["ts"], "2026-10-07T12:00:00Z")
        self.assertEqual(e["meta"]["channel"], "email")
        self.assertEqual(e["meta"]["source"], "email")

    def test_hot_email_reply_label(self):
        e = RT.reply_activity_event(_reply(is_hot=True))
        self.assertEqual(e["type"], "email_reply")
        self.assertIn("Hot email reply received", e["label"])

    def test_sms_reply_shape_unchanged(self):
        e = RT.reply_activity_event(_reply(source="sms"), _fmt)
        self.assertEqual((e["type"], e["label"]), ("sms_reply", "Reply received"))
        self.assertEqual(e["meta"]["source"], "sms")
        hot = RT.reply_activity_event(_reply(source="sms", is_hot=True))
        self.assertIn("Hot reply received", hot["label"])

    def test_null_and_unknown_source_use_legacy_sms_fallback(self):
        for src in (None, "", "weird"):
            e = RT.reply_activity_event(_reply(source=src))
            self.assertEqual((e["type"], e["label"]), ("sms_reply", "Reply received"), src)
            self.assertEqual(e["meta"]["source"], src)  # raw value, nothing invented

    def test_legacy_fields_preserved(self):
        e = RT.reply_activity_event(_reply(classification="HOT", hot_reason="asked"))
        self.assertEqual(set(e), {"id", "type", "ts", "label", "body", "meta"})
        self.assertTrue({"is_hot", "hot_reason", "classification", "reviewed_at",
                         "source"} <= set(e["meta"]))
        self.assertEqual(e["meta"]["classification"], "HOT")

    def test_all_three_paths_agree_on_channel(self):
        for src in ("email", "sms", None, "weird"):
            r = _reply(source=src)
            ch = RT.reply_channel(r)
            self.assertEqual(RT.inbound_reply_event(r)["channel"], ch)
            self.assertEqual(RT.reply_activity_event(r)["meta"]["channel"], ch)


class SourceWiring(unittest.TestCase):
    def test_activity_uses_shared_serializer_and_scoped_loader(self):
        s = _src("app/routers/timeline_router.py")
        self.assertIn("reply_activity_event(r, _fmt)", s)
        self.assertNotIn('"type": "sms_reply"', s)
        self.assertIn("load_lead_in_scope(db, current_user, lead_id)", s)
        self.assertNotIn('Lead.id == lead_id).first()', s)
        # route dependency: tenant users only (same as /history)
        m = re.search(r"def get_lead_timeline\((.*?)\):", s, re.S)
        self.assertIn("Depends(require_tenant_user)", m.group(1))

    def test_timeline_and_history_use_shared_helpers(self):
        for rel in ("app/routers/leads_detail_router.py", "leads_router.py"):
            self.assertIn("inbound_reply_event(r)", _src(rel), rel)
        h = _src("app/services/communication_history.py")
        self.assertIn("channel = reply_channel(r)", h)
        self.assertNotIn('r.source or "sms"', h)

    def test_history_route_is_tenant_scoped(self):
        s = _src("app/routers/leads_detail_router.py")
        i = s.index('"/{lead_id}/history"')
        block = s[i:i + 900]
        self.assertIn("require_tenant_user", block)
        self.assertIn("load_lead_in_scope", s[i:i + 2500])

    def test_loader_is_one_404_for_missing_and_cross_tenant(self):
        s = _src("app/services/lead_scope.py")
        i = s.index("def load_lead_in_scope")
        body = s[i:i + 1200]
        self.assertIn("authorized_lead_query(db, user", body)
        self.assertIn("Lead.id == lead_id", body)
        self.assertEqual(body.count("raise HTTPException"), 1)
        self.assertIn("status_code=404", body)

    def test_frontend_styles_email_reply(self):
        self.assertIn("email_reply:", _src("frontend/src/pages/LeadDetail.jsx"))


if __name__ == "__main__":
    unittest.main()
