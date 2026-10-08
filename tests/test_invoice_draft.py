"""Local invoice draft core (stdlib only).

Run: python3 -m unittest tests.test_invoice_draft

Rules and the store's portable SQL run for real here (stdlib sqlite3 through a small Tx
adapter). NOT executed here (no FastAPI/SQLAlchemy/Postgres in this environment): the
SqlAlchemyTx wrapper, the router under HTTP, and the Alembic run. Those are checked
statically (route/guard/model/migration wiring and DDL parity) and remain unexecuted.
"""
import ast
import os
import re
import sqlite3
import tempfile
import unittest

from app.services import invoice_draft as inv
from app.services import invoice_draft_store as st

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

DDL = """
CREATE TABLE invoice_drafts (id VARCHAR(36) PRIMARY KEY, organization_id VARCHAR NOT NULL, state VARCHAR(16) NOT NULL,
  version INTEGER NOT NULL, currency VARCHAR(3) NOT NULL, customer_name VARCHAR(200) NOT NULL, memo TEXT NOT NULL,
  discount_cents BIGINT NOT NULL, tax_cents BIGINT NOT NULL, next_line_no INTEGER NOT NULL,
  created_by VARCHAR(200) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE invoice_draft_lines (draft_id VARCHAR(36) NOT NULL REFERENCES invoice_drafts(id), line_no INTEGER NOT NULL,
  description VARCHAR(200) NOT NULL, quantity INTEGER NOT NULL, unit_price_cents BIGINT NOT NULL,
  PRIMARY KEY (draft_id, line_no));
CREATE TABLE invoice_draft_events (draft_id VARCHAR(36) NOT NULL REFERENCES invoice_drafts(id), seq INTEGER NOT NULL,
  organization_id VARCHAR NOT NULL, at VARCHAR(40) NOT NULL, actor VARCHAR(200) NOT NULL, action VARCHAR(40) NOT NULL,
  detail TEXT NOT NULL, request_key VARCHAR(120), PRIMARY KEY (draft_id, seq),
  UNIQUE (organization_id, request_key));
"""


class SqliteTx:
    def __init__(self, path):
        self.c = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.c.row_factory = sqlite3.Row
        self.began = False

    def query(self, sql, params=None):
        return [dict(r) for r in self.c.execute(sql, params or {}).fetchall()]

    def execute(self, sql, params=None):
        if not self.began:
            self.c.execute("BEGIN IMMEDIATE")
            self.began = True
        try:
            return self.c.execute(sql, params or {}).rowcount
        except sqlite3.IntegrityError as e:
            raise st.ConflictError(str(e))

    def commit(self):
        if self.began:
            self.c.execute("COMMIT")

    def rollback(self):
        if self.began:
            self.c.execute("ROLLBACK")
            self.began = False

    def close(self):
        self.c.close()


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class Rules(unittest.TestCase):
    def draft(self):
        return inv.create("org-a", "Acme Co", "", "ann@a.test")

    def test_cent_math_is_exact(self):
        d = self.draft()
        inv.add_line(d, "Widget", 3, 1999, "x")      # 5997
        inv.add_line(d, "Setup", 1, 10, "x")         # 10
        inv.set_adjustments(d, 7, 1, "x")
        self.assertEqual(inv.totals(d), {"subtotal_cents": 6007, "discount_cents": 7, "tax_cents": 1,
                                         "total_cents": 6001})
        v = inv.view(d)
        self.assertEqual([l["line_total_cents"] for l in v["lines"]], [5997, 10])
        self.assertTrue(all(isinstance(v[k], int) for k in ("subtotal_cents", "total_cents")))

    def test_invalid_lines_refused(self):
        d = self.draft()
        bad = [("", 1, 1), ("x", 0, 1), ("x", -1, 1), ("x", 1, -1), ("x", 1.5, 100), ("x", 1, 10.5),
               ("x", "2", 100), ("x", 1, "100"), ("x", True, 100), ("x", 1, True), ("x", inv.MAX_QTY + 1, 1),
               ("x", 1, inv.MAX_UNIT_CENTS + 1), (None, 1, 1), ("x" * 201, 1, 1), ("x", None, 1)]
        for desc, q, u in bad:
            with self.assertRaises(inv.InvoiceError, msg=repr((desc, q, u))):
                inv.add_line(d, desc, q, u, "x")

    def test_overflow_and_negative_totals_refused(self):
        d = self.draft()
        with self.assertRaises(inv.InvoiceError):
            inv.add_line(d, "huge", inv.MAX_QTY, inv.MAX_UNIT_CENTS, "x")     # 1e15 > cap
        self.assertEqual((d["lines"], d["next_line_no"], len(d["events"])), ([], 1, 1))   # refusal left it untouched
        inv.add_line(d, "a", 1, 1000, "x")
        for disc, tax in [(1001, 0), (-1, 0), (0, -1), (0.5, 0), (0, 1.5), (True, 0), (10 ** 20, 0)]:
            with self.assertRaises(inv.InvoiceError, msg=repr((disc, tax))):
                inv.set_adjustments(d, disc, tax, "x")
        self.assertEqual((d["discount_cents"], d["tax_cents"]), (0, 0))

    def test_removing_line_cannot_orphan_discount(self):
        d = self.draft()
        n1 = inv.add_line(d, "a", 1, 1000, "x")
        inv.add_line(d, "b", 1, 100, "x")
        inv.set_adjustments(d, 500, 0, "x")
        with self.assertRaises(inv.InvoiceError):
            inv.remove_line(d, n1, "x")
        with self.assertRaises(inv.InvoiceError):
            inv.remove_line(d, 99, "x")

    def test_approval_requires_lines_and_positive_total(self):
        d = self.draft()
        with self.assertRaises(inv.InvoiceError):
            inv.transition(d, "approval_ready", "", "x")
        inv.add_line(d, "free", 1, 0, "x")
        self.assertIn("greater than zero", inv.view(d)["refusal_reason"])
        with self.assertRaises(inv.InvoiceError):
            inv.transition(d, "approval_ready", "", "x")
        inv.add_line(d, "paid", 1, 500, "x")
        inv.transition(d, "approval_ready", "", "x")
        self.assertEqual(d["state"], "approval_ready")

    def test_state_locks_and_legal_transitions(self):
        d = self.draft()
        inv.add_line(d, "a", 1, 500, "x")
        inv.transition(d, "approval_ready", "", "x")
        for fn in (lambda: inv.add_line(d, "b", 1, 1, "x"), lambda: inv.remove_line(d, 1, "x"),
                   lambda: inv.set_adjustments(d, 0, 0, "x")):
            with self.assertRaises(inv.InvoiceError):
                fn()
        with self.assertRaises(inv.InvoiceError):
            inv.transition(d, "approval_ready", "", "x")      # same-state
        for illegal in ("sent", "paid", "finalized", "open", "", None):
            with self.assertRaises(inv.InvoiceError):
                inv.transition(d, illegal, "", "x")
        inv.transition(d, "draft", "fix qty", "x")
        inv.add_line(d, "b", 1, 1, "x")                       # editable again
        with self.assertRaises(inv.InvoiceError):
            inv.transition(d, "void", "", "x")                # void needs a reason
        inv.transition(d, "void", "duplicate", "x")
        v = inv.view(d)
        self.assertEqual((v["editable"], v["allowed_transitions"]), (False, []))
        for to in ("draft", "approval_ready", "void"):
            with self.assertRaises(inv.InvoiceError):
                inv.transition(d, to, "r", "x")

    def test_view_is_truthful_about_provider_and_tax(self):
        v = inv.view(self.draft())
        self.assertEqual(v["provider_status"], "not_started")
        self.assertIn("not calculated or attested", v["tax_note"])
        for k in ("subtotal_cents", "discount_cents", "tax_cents", "total_cents", "state", "version", "editable",
                  "refusal_reason"):
            self.assertIn(k, v)


class StoreBase(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.path = os.path.join(self.t.name, "db.sqlite")
        c = sqlite3.connect(self.path)
        c.executescript(DDL)
        c.close()
        self.s = st.DbStore(lambda: SqliteTx(self.path))

    def count(self, table):
        c = sqlite3.connect(self.path)
        try:
            return c.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        finally:
            c.close()

    def new(self, org="org-a", key=None):
        return self.s.create(org, "Acme", "", "ann", key)[0]

    def line(self, d, v=None, key=None, org="org-a", price=1000):
        return self.s.mutate(org, d["id"], lambda x: inv.add_line(x, "w", 1, price, "ann"),
                             d["version"] if v is None else v, key, "line_added")


class StoreBehaviour(StoreBase):
    def test_round_trip_and_versions(self):
        d = self.new()
        self.assertEqual((d["state"], d["version"]), ("draft", 1))
        d2, rep = self.line(d)
        self.assertEqual((d2["version"], rep), (2, False))
        got = self.s.get("org-a", d["id"])
        self.assertEqual((got["version"], len(got["lines"]), [e["action"] for e in got["events"]]),
                         (2, 1, ["created", "line_added"]))
        self.s.mutate("org-a", d["id"], lambda x: inv.transition(x, "approval_ready", "", "ann"), 2, None,
                      "state_changed")
        fresh = self.s.get("org-a", d["id"])
        self.assertEqual((fresh["state"], fresh["version"]), ("approval_ready", 3))
        self.assertEqual(inv.view(fresh)["total_cents"], 1000)

    def test_cross_tenant_ids_are_not_found_and_untouched(self):
        d = self.new("org-a")
        with self.assertRaises(inv.NotFoundError):
            self.s.get("org-b", d["id"])
        with self.assertRaises(inv.NotFoundError):
            self.line(d, org="org-b")
        with self.assertRaises(inv.NotFoundError):
            self.s.get("org-a", "no-such-id")
        self.assertEqual(self.s.list("org-b"), [])
        self.assertEqual([x["id"] for x in self.s.list("org-a")], [d["id"]])
        self.assertEqual(self.count("invoice_draft_lines"), 0)
        self.assertEqual(self.s.get("org-a", d["id"])["version"], 1)

    def test_stale_write_refused_and_nothing_overwritten(self):
        d = self.new()
        self.line(d)                                  # now v2
        with self.assertRaises(inv.StaleError):
            self.line(d, v=1)                         # holder of v1
        got = self.s.get("org-a", d["id"])
        self.assertEqual((got["version"], len(got["lines"]), len(got["events"])), (2, 1, 2))

    def test_idempotent_replay_is_a_noop(self):
        d = self.new(key="k-create")
        again, rep = self.s.create("org-a", "Acme", "", "ann", "k-create")
        self.assertEqual((again["id"], rep), (d["id"], True))
        self.assertEqual(self.count("invoice_drafts"), 1)
        self.line(d, key="k1")
        d3, rep = self.line(d, key="k1")              # repeated click, same (now stale) version
        self.assertTrue(rep)
        self.assertEqual((len(d3["lines"]), d3["version"], len(d3["events"])), (1, 2, 2))
        self.assertEqual(self.count("invoice_draft_lines"), 1)
        self.assertEqual(self.count("invoice_draft_events"), 2)

    def test_idempotency_key_misuse_refused(self):
        a, b = self.new(), self.new()
        self.line(a, key="k")
        with self.assertRaises(inv.StaleError):
            self.line(b, key="k")                     # same key, different draft
        with self.assertRaises(inv.StaleError):
            self.s.mutate("org-a", a["id"], lambda x: inv.set_adjustments(x, 0, 0, "ann"), 2, "k", "adjustments_set")
        self.assertEqual(self.s.get("org-a", b["id"])["version"], 1)
        # keys are per tenant: org-b may reuse the same string
        self.new("org-b", key="k")

    def test_refusal_rolls_back_everything(self):
        d = self.new()
        d, _ = self.line(d)
        before = (self.count("invoice_draft_lines"), self.count("invoice_draft_events"))
        for fn in (lambda x: inv.add_line(x, "bad", 1, -5, "ann"),
                   lambda x: inv.set_adjustments(x, 10 ** 6, 0, "ann"),
                   lambda x: inv.transition(x, "paid", "", "ann")):
            with self.assertRaises(inv.InvoiceError):
                self.s.mutate("org-a", d["id"], fn, 2, "kk", "x")
        self.assertEqual((self.count("invoice_draft_lines"), self.count("invoice_draft_events")), before)
        got = self.s.get("org-a", d["id"])
        self.assertEqual((got["version"], got["discount_cents"]), (2, 0))
        # the refused key was not consumed
        self.s.mutate("org-a", d["id"], lambda x: inv.set_adjustments(x, 1, 0, "ann"), 2, "kk", "adjustments_set")

    def test_locked_draft_refuses_money_edits_through_store(self):
        d = self.new()
        d, _ = self.line(d)
        d, _ = self.s.mutate("org-a", d["id"], lambda x: inv.transition(x, "approval_ready", "", "a"), 2, None,
                             "state_changed")
        with self.assertRaises(inv.InvoiceError):
            self.line(d)
        self.assertEqual(len(self.s.get("org-a", d["id"])["lines"]), 1)

    def test_events_are_append_only_and_never_rewritten(self):
        d = self.new()
        d, _ = self.line(d)
        first = self.s.get("org-a", d["id"])["events"][0]
        d, _ = self.s.mutate("org-a", d["id"], lambda x: inv.remove_line(x, 1, "ann"), 2, None, "line_removed")
        ev = self.s.get("org-a", d["id"])["events"]
        self.assertEqual(ev[0], first)
        self.assertEqual([e["action"] for e in ev], ["created", "line_added", "line_removed"])
        # a mutation that tampers with history, or adds none, is refused
        def tamper(x):
            inv.add_line(x, "w", 1, 5, "ann")
            x["events"][0]["actor"] = "evil"
        with self.assertRaises(inv.InvoiceError):
            self.s.mutate("org-a", d["id"], tamper, 3, None, "line_added")
        with self.assertRaises(inv.InvoiceError):
            self.s.mutate("org-a", d["id"], lambda x: None, 3, None, "noop")
        self.assertEqual(self.s.get("org-a", d["id"])["version"], 3)
        src = _read("app", "services", "invoice_draft_store.py")
        self.assertFalse(re.search(r"(UPDATE|DELETE FROM)\s+invoice_draft_events", src))

    def test_missing_schema_and_unreachable_db_fail_closed(self):
        empty = os.path.join(self.t.name, "empty.sqlite")
        sqlite3.connect(empty).close()
        s = st.DbStore(lambda: SqliteTx(empty))
        for call in (lambda: s.list("o"), lambda: s.get("o", "i"), lambda: s.create("o", "A", "", "x"),
                     lambda: s.mutate("o", "i", lambda d: None, 1)):
            with self.assertRaises(inv.StorageError) as cm:
                call()
            self.assertIn("alembic upgrade head", str(cm.exception))

        def boom():
            raise OSError("connection refused")
        with self.assertRaises(inv.StorageError):
            st.DbStore(boom).list("o")


class StaticWiring(unittest.TestCase):
    def test_router_guards_and_has_no_provider_actions(self):
        src = _read("app", "routers", "invoice_draft_router.py")
        tree = ast.parse(src)
        routes = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for dec in fn.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in ("get", "post"):
                    routes.append((dec.args[0].value, fn))
        self.assertEqual(len(routes), 7)
        for path, fn in routes:
            self.assertIn("require_admin", ast.dump(fn.args), path)     # existing authority, not broadened
        paths = " ".join(p for p, _ in routes)
        for banned in ("send", "finalize", "pay", "charge", "refund", "stripe"):
            self.assertNotIn(banned, paths)
        self.assertNotRegex(src, r"(?i)import\s+(stripe|smtplib|twilio)|billing_models|BillingInvoice|requests|httpx")
        self.assertNotRegex(src, r"(?m)^from __future__")
        self.assertEqual(src.count("expected_version: int"), 4)         # required on every mutation body
        self.assertNotIn("require_god", src)

    def test_main_and_registry_wired(self):
        main = _read("app", "main.py")
        self.assertIn("from app.routers.invoice_draft_router import router as invoice_draft_router", main)
        self.assertIn("app.include_router(invoice_draft_router)", main)
        self.assertIn("invoice_draft_models", _read("app", "models", "registry.py"))

    def test_store_filters_every_statement_by_tenant(self):
        src = _read("app", "services", "invoice_draft_store.py")
        self.assertIn("WHERE id = :id AND organization_id = :org", src)
        self.assertIn("AND version = :version", src)
        self.assertNotRegex(src, r"(?i)stripe|smtp|twilio|BillingInvoice")

    def test_migration_matches_model_and_is_reversible(self):
        mig = _read("alembic", "versions", "6c8e0a2d4f1b_add_invoice_draft_tables.py")
        model = _read("app", "models", "invoice_draft_models.py")
        self.assertIn('down_revision = "5b7d9f1a3c2e"', mig)
        self.assertIn("def downgrade", mig)
        for tbl in ("invoice_drafts", "invoice_draft_lines", "invoice_draft_events"):
            self.assertIn('create_table(\n        "%s"' % tbl, mig)
            self.assertIn('op.drop_table("%s")' % tbl, mig)
            self.assertIn('__tablename__ = "%s"' % tbl, model)
        cols = lambda s: set(re.findall(r'(?:sa\.)?Column\(\s*"(\w+)"|^\s{4}(\w+) = Column\(', s, re.M))
        mig_cols = set(re.findall(r'sa\.Column\("(\w+)"', mig))
        model_cols = set(re.findall(r"^\s{4}(\w+) = Column\(", model, re.M))
        self.assertEqual(mig_cols, model_cols)
        # single Alembic head: nothing else revises the same parent
        heads = [f for f in os.listdir(os.path.join(ROOT, "alembic", "versions"))
                 if f.endswith(".py") and 'down_revision = "5b7d9f1a3c2e"' in _read("alembic", "versions", f)]
        self.assertEqual(heads, ["6c8e0a2d4f1b_add_invoice_draft_tables.py"])

    def test_ddl_in_tests_matches_migration_columns(self):
        mig_cols = set(re.findall(r'sa\.Column\("(\w+)"', _read("alembic", "versions",
                                                              "6c8e0a2d4f1b_add_invoice_draft_tables.py")))
        ddl_cols = set(re.findall(r"^\s*(?:CREATE TABLE \w+ \()?(\w+) (?:VARCHAR|INTEGER|BIGINT|TEXT|DATETIME)",
                                  DDL.replace(", ", ",\n"), re.M))
        self.assertEqual(mig_cols, ddl_cols)


if __name__ == "__main__":
    unittest.main()
