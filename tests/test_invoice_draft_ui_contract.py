"""Dependency-free contract for the local invoice draft UI.

Executes the pure helper's node tests through subprocess (no npm install, no
browser, no network) and asserts source-level wiring: route/nav authority,
concurrency + idempotency wiring, absence of provider actions, responsive CSS.
"""
import os
import re
import shutil
import subprocess
import unittest

_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _src(rel):
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


PAGE = "frontend/src/pages/InvoiceDrafts.jsx"


class HelperExecuted(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_node_helper_suite_passes(self):
        r = subprocess.run(["node", "--test", "frontend/tests/invoiceDraft.test.mjs"], cwd=_ROOT,
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"# fail 0")
        self.assertRegex(r.stdout, r"# pass [1-9]")


class Wiring(unittest.TestCase):
    def test_route_and_nav_are_admin_only(self):
        app = _src("frontend/src/App.jsx")
        self.assertRegex(app, r'path="/invoice-drafts" element=\{<ProtectedRoute requireAdmin><InvoiceDrafts />')
        nav = _src("frontend/src/components/Layout.jsx")
        line = [l for l in nav.splitlines() if "to: '/invoice-drafts'" in l]
        self.assertEqual(len(line), 1)
        self.assertIn("adminOnly: true", line[0])

    def test_every_mutation_sends_version_and_idempotency_key(self):
        s = _src(PAGE)
        self.assertIn("expected_version: draft ? draft.version : undefined", s)
        self.assertEqual(s.count("'Idempotency-Key'"), 2)           # shared mutate + create
        posts = re.findall(r"api\.post\(", s)
        self.assertEqual(len(posts), 2)
        for m in re.finditer(r"api\.post\([^\n]*\n?", s):
            self.assertIn("headers", s[m.start():m.start() + 200])
        self.assertIn("busyRef.current", s)                          # double-click guard
        self.assertIn("disabled={busy}", s)
        client = _src("frontend/src/api/client.js")
        self.assertIn("opts.headers", client)

    def test_refusal_reloads_and_never_shows_success(self):
        s = _src(PAGE)
        catch = s.split("} catch (e) {\n      setNotice({ kind: 'refused', text: refusalMessage(e), refusal")[1]
        self.assertIn("openDraft(draft.id)", catch.split("finally")[0])
        # 'ok' notices are only set after an awaited call resolved
        self.assertEqual(len(re.findall(r"setNotice\(\{ kind: 'ok'", s)), 2)

    def test_no_provider_actions_or_float_money(self):
        s = _src(PAGE) + _src("frontend/src/utils/invoiceDraft.js")
        for bad in (r"/send\b", r"/finalize", r"/pay\b", r"stripe", r"payment.?link", r"parseFloat",
                    r"toFixed", r"Intl\."):
            self.assertIsNone(re.search(bad, s, re.I), bad)
        buttons = re.findall(r">([^<>{}]*)</button>", _src(PAGE))
        for label in buttons:
            self.assertNotRegex(label, r"(?i)\b(send|pay|finalize|charge)\b")
        self.assertIn("not sent, finalized, charged", _src("frontend/src/utils/invoiceDraft.js"))
        self.assertIn("NOT_SENT_NOTICE", _src(PAGE))
        self.assertIn("operator-entered", _src(PAGE))

    def test_totals_come_from_server_fields(self):
        s = _src(PAGE)
        for f in ("subtotal_cents", "discount_cents", "tax_cents", "total_cents", "line_total_cents"):
            self.assertIn(f, s)
        self.assertIn("allowed_transitions", _src("frontend/src/utils/invoiceDraft.js"))
        for f in ("provider_status", "draft.version", "draft.memo", "draft.events", "customer_name"):
            self.assertIn(f, s)

    def test_mobile_responsive_and_theme_tokens(self):
        css = _src("frontend/src/pages/InvoiceDrafts.css")
        self.assertIn("@media (max-width: 720px)", css)
        self.assertIn("grid-template-columns: 1fr", css)
        self.assertIn("overflow-x: auto", css)
        self.assertIsNone(re.search(r"#(fff|000)\b|background:\s*#", css, re.I))


if __name__ == "__main__":
    unittest.main()
