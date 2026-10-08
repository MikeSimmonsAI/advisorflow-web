"""Durable, tenant-scoped storage for local invoice drafts.

Rules live in app/services/invoice_draft.py (pure). This module makes every write safe:

  * one mutation = one transaction; any refusal rolls back, nothing partial is saved
  * every read and write is filtered by organization_id; another tenant's id is
    NotFoundError, indistinguishable from an id that does not exist
  * the draft row is written with `WHERE id = :id AND organization_id = :org AND
    version = :version`; a stale writer matches no row -> StaleError (HTTP 409)
  * `expected_version` is checked before the rules run
  * events are append-only, keyed (draft_id, seq); (organization_id, request_key) is
    unique, so a replayed request is a no-op that returns the current draft
  * missing tables / unreachable DB -> StorageError (fail closed, HTTP 503)

SQL uses only `:name` binds and portable statements so the same text runs through
SQLAlchemy `text()` in production and through stdlib sqlite3 in tests.
"""
import copy
from typing import Callable, Dict, List, Optional

from app.services import invoice_draft as inv
from app.services.invoice_draft import InvoiceError, NotFoundError, StaleError, StorageError


class ConflictError(Exception):
    """Adapter-level unique/PK violation (raised by Tx implementations)."""


class SqlAlchemyTx:
    def __init__(self, session):
        self.s = session

    def query(self, sql, params=None):
        return [dict(r._mapping) for r in self.s.execute(_text(sql), params or {})]

    def execute(self, sql, params=None):
        return self.s.execute(_text(sql), params or {}).rowcount

    def commit(self):
        self.s.commit()

    def rollback(self):
        self.s.rollback()

    def close(self):
        self.s.close()

    @staticmethod
    def is_conflict(exc) -> bool:
        from sqlalchemy.exc import IntegrityError
        return isinstance(exc, IntegrityError)


def _text(sql):
    from sqlalchemy import text
    return text(sql)


def _default_tx_factory():
    from app.deps import SessionLocal
    return SqlAlchemyTx(SessionLocal())


_MISSING = ("no such table", "does not exist", "undefinedtable", "undefined table", "doesn't exist")


def _storage_error(exc) -> StorageError:
    if any(m in str(exc).lower() for m in _MISSING):
        return StorageError("invoice draft tables are missing: run `alembic upgrade head` "
                            "(revision 6c8e0a2d4f1b) before using invoice drafts")
    return StorageError("invoice draft database unavailable: %s" % type(exc).__name__)


_DRAFT_COLS = ("id, organization_id, state, version, currency, customer_name, memo, discount_cents, "
               "tax_cents, next_line_no, created_by")


def _load_one(tx, org_id: str, draft_id: str) -> Dict:
    rows = tx.query("SELECT %s FROM invoice_drafts WHERE id = :id AND organization_id = :org"
                    % _DRAFT_COLS, {"id": draft_id, "org": org_id})
    if not rows:
        raise NotFoundError("invoice draft not found")
    d = rows[0]
    d["lines"] = tx.query("SELECT line_no, description, quantity, unit_price_cents FROM invoice_draft_lines "
                          "WHERE draft_id = :id ORDER BY line_no", {"id": draft_id})
    d["events"] = tx.query("SELECT at, actor, action, detail FROM invoice_draft_events WHERE draft_id = :id "
                           "AND organization_id = :org ORDER BY seq", {"id": draft_id, "org": org_id})
    return d


def _insert_events(tx, d: Dict, start: int, request_key: Optional[str]) -> None:
    for i, e in enumerate(d["events"][start:], start=start + 1):
        tx.execute("INSERT INTO invoice_draft_events (draft_id, seq, organization_id, at, actor, action, detail, "
                   "request_key) VALUES (:id, :seq, :org, :at, :actor, :action, :detail, :rk)",
                   {"id": d["id"], "seq": i, "org": d["organization_id"], "at": e["at"], "actor": e["actor"],
                    "action": e["action"], "detail": e["detail"], "rk": request_key if i == start + 1 else None})


def _persist_new(tx, d: Dict, request_key: Optional[str]) -> None:
    tx.execute("INSERT INTO invoice_drafts (id, organization_id, state, version, currency, customer_name, memo, "
               "discount_cents, tax_cents, next_line_no, created_by) VALUES (:id, :org, :state, 1, :currency, "
               ":cn, :memo, :disc, :tax, :nl, :cb)",
               {"id": d["id"], "org": d["organization_id"], "state": d["state"], "currency": d["currency"],
                "cn": d["customer_name"], "memo": d["memo"], "disc": d["discount_cents"], "tax": d["tax_cents"],
                "nl": d["next_line_no"], "cb": d["created_by"]})
    _insert_events(tx, d, 0, request_key)


def _persist_change(tx, before: Dict, after: Dict, request_key: Optional[str]) -> None:
    if after["events"][:len(before["events"])] != before["events"] or len(after["events"]) <= len(before["events"]):
        raise InvoiceError("a change must append to the audit history, never rewrite it")
    n = tx.execute("UPDATE invoice_drafts SET state = :state, memo = :memo, discount_cents = :disc, "
                   "tax_cents = :tax, next_line_no = :nl, version = version + 1 "
                   "WHERE id = :id AND organization_id = :org AND version = :version",
                   {"state": after["state"], "memo": after["memo"], "disc": after["discount_cents"],
                    "tax": after["tax_cents"], "nl": after["next_line_no"], "id": after["id"],
                    "org": after["organization_id"], "version": before["version"]})
    if n != 1:
        raise StaleError("invoice draft changed since it was loaded; reload and retry")
    old = set(l["line_no"] for l in before["lines"])
    new = dict((l["line_no"], l) for l in after["lines"])
    for no in sorted(old - set(new)):
        tx.execute("DELETE FROM invoice_draft_lines WHERE draft_id = :id AND line_no = :no", {"id": after["id"], "no": no})
    for no in sorted(set(new) - old):
        l = new[no]
        tx.execute("INSERT INTO invoice_draft_lines (draft_id, line_no, description, quantity, unit_price_cents) "
                   "VALUES (:id, :no, :desc, :qty, :unit)",
                   {"id": after["id"], "no": no, "desc": l["description"], "qty": l["quantity"],
                    "unit": l["unit_price_cents"]})
    _insert_events(tx, after, len(before["events"]), request_key)


class DbStore:
    def __init__(self, tx_factory: Optional[Callable] = None):
        self._factory = tx_factory or _default_tx_factory

    def _open(self):
        try:
            return self._factory()
        except Exception as e:  # noqa: BLE001 - any connect failure is a storage failure
            raise _storage_error(e)

    def _read(self, fn):
        tx = self._open()
        try:
            return fn(tx)
        except InvoiceError:
            raise
        except Exception as e:  # noqa: BLE001
            raise _storage_error(e)
        finally:
            tx.close()

    def get(self, org_id: str, draft_id: str) -> Dict:
        return self._read(lambda tx: _load_one(tx, org_id, draft_id))

    def list(self, org_id: str) -> List[Dict]:
        def go(tx):
            ids = tx.query("SELECT id FROM invoice_drafts WHERE organization_id = :org ORDER BY created_at DESC, id",
                           {"org": org_id})
            return [_load_one(tx, org_id, r["id"]) for r in ids]
        return self._read(go)

    def create(self, org_id: str, customer_name, memo, actor: str, request_key: Optional[str] = None):
        """Returns (draft, replayed)."""
        return self._write(org_id, None, request_key, "created",
                           lambda: inv.create(org_id, customer_name, memo, actor), None)

    def mutate(self, org_id: str, draft_id: str, fn, expected_version: int, request_key: Optional[str] = None,
               action: str = ""):
        """Apply fn(draft) in one transaction. Returns (draft, replayed)."""
        return self._write(org_id, draft_id, request_key, action, fn, expected_version)

    def _write(self, org_id, draft_id, request_key, action, fn, expected_version):
        tx = self._open()
        try:
            if request_key:
                seen = tx.query("SELECT draft_id, action FROM invoice_draft_events WHERE organization_id = :org "
                                "AND request_key = :rk", {"org": org_id, "rk": request_key})
                if seen:
                    return self._replay(tx, org_id, draft_id, action, seen[0])
            if draft_id is None:
                after = fn()
                _persist_new(tx, after, request_key)
            else:
                before = _load_one(tx, org_id, draft_id)
                if before["version"] != expected_version:
                    raise StaleError("invoice draft is at version %s, not %s; reload and retry"
                                     % (before["version"], expected_version))
                after = copy.deepcopy(before)
                fn(after)
                _persist_change(tx, before, after, request_key)
                after["version"] = before["version"] + 1
            tx.commit()
            return after, False
        except InvoiceError:
            tx.rollback()
            raise
        except Exception as e:  # noqa: BLE001
            tx.rollback()
            if isinstance(e, ConflictError) or (hasattr(tx, "is_conflict") and tx.is_conflict(e)):
                if request_key:
                    try:
                        tx2 = self._open()
                        try:
                            seen = tx2.query("SELECT draft_id, action FROM invoice_draft_events WHERE "
                                             "organization_id = :org AND request_key = :rk",
                                             {"org": org_id, "rk": request_key})
                            if seen:
                                return self._replay(tx2, org_id, draft_id, action, seen[0])
                        finally:
                            tx2.close()
                    except InvoiceError:
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                raise StaleError("invoice draft changed concurrently; reload and retry")
            raise _storage_error(e)
        finally:
            tx.close()

    @staticmethod
    def _replay(tx, org_id, draft_id, action, seen):
        """A repeated key returns the current draft untouched, but only for the same request shape."""
        if seen["action"] != action or (draft_id is not None and seen["draft_id"] != draft_id):
            raise StaleError("Idempotency-Key was already used for a different request")
        return _load_one(tx, org_id, seen["draft_id"]), True


def get_store() -> DbStore:
    return DbStore()
