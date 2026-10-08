"""Durable storage for the Control Room launch board (issue #21).

The rules live in app/services/launch_board.py (pure functions over a board dict).
This module only decides WHERE the board lives and makes every write safe:

  * `db` (the default, and the only mode a deployed app should run): the tables in
    app/models/launch_board_models.py. One mutation = one transaction. A refusal
    (any exception from the rule functions) rolls back, so nothing partial is saved.
  * `file` (LAUNCH_BOARD_BACKEND=file): the old JSON file, kept as an explicit
    local/test adapter. It is never chosen implicitly - an unset or unknown backend
    means `db`, and a missing/broken database is an error, not a reason to fall
    back to the filesystem.

Concurrency: every changed project is written with `WHERE id = :id AND version =
:version`; a writer holding stale state matches no row and gets StaleError (HTTP
409) instead of overwriting. History rows are keyed (project_id, seq) so racing
appends collide. An optional request_key makes a replayed request a no-op.

SQL here uses only `:name` binds and portable statements, so the same text runs
through SQLAlchemy `text()` in production and through stdlib sqlite3 in tests.
"""
import copy
import json
import os
from typing import Callable, Dict, List, Optional

from app.services import launch_board as lb

BACKENDS = ("db", "file")
_DATA_SKIP = ("history", "id", "version")


class StorageError(lb.BoardError):
    """The store itself is unavailable or not migrated. Fail closed, say why."""


class StaleError(lb.BoardError):
    """The board changed under this writer; reload and retry."""


class ConflictError(Exception):
    """Adapter-level unique/PK violation (raised by Tx implementations)."""


def backend() -> str:
    return "file" if (os.environ.get("LAUNCH_BOARD_BACKEND") or "").strip().lower() == "file" else "db"


# ── transaction adapters ────────────────────────────────────────────────────────

class SqlAlchemyTx:
    """Production Tx over a SQLAlchemy Session (the app's SessionLocal)."""

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


# ── error classification ────────────────────────────────────────────────────────

_MISSING = ("no such table", "does not exist", "undefinedtable", "undefined table", "doesn't exist")


def _storage_error(exc) -> StorageError:
    msg = str(exc).lower()
    if any(m in msg for m in _MISSING):
        return StorageError("launch board tables are missing: run `alembic upgrade head` "
                            "(revision 5b7d9f1a3c2e) before using the board")
    return StorageError("launch board database unavailable: %s" % type(exc).__name__)


# ── DB store ────────────────────────────────────────────────────────────────────

def _project_data(p: Dict) -> str:
    return json.dumps(dict((k, v) for k, v in p.items() if k not in _DATA_SKIP), sort_keys=True)


def _load_board(tx) -> Dict:
    rows = tx.query("SELECT id, version, data FROM launch_board_projects ORDER BY id")
    events = tx.query("SELECT project_id, seq, at, actor, action, detail FROM launch_board_events "
                      "ORDER BY project_id, seq")
    by_pid: Dict[int, List[Dict]] = {}
    for e in events:
        by_pid.setdefault(e["project_id"], []).append(
            {"at": e["at"], "actor": e["actor"], "action": e["action"], "detail": e["detail"]})
    projects = []
    for r in rows:
        try:
            p = json.loads(r["data"])
        except ValueError:
            raise StorageError("launch board row %s holds unreadable data" % r["id"])
        p["id"], p["version"], p["history"] = r["id"], r["version"], by_pid.get(r["id"], [])
        projects.append(p)
    return {"projects": projects, "next_id": 1 + max([p["id"] for p in projects] or [0])}


def _persist(tx, before: Dict, after: Dict, request_key: Optional[str]) -> None:
    old = dict((p["id"], p) for p in before["projects"])
    first_event = True
    for p in after["projects"]:
        prev = old.get(p["id"])
        if prev is None:
            tx.execute("INSERT INTO launch_board_projects (id, name, name_key, lane, priority, version, data) "
                       "VALUES (:id, :name, :nk, :lane, :priority, 1, :data)",
                       {"id": p["id"], "name": p["name"], "nk": p["name"].lower(), "lane": p["lane"],
                        "priority": p["priority"], "data": _project_data(p)})
            p["version"], start = 1, 0
        else:
            start = len(prev["history"])
            if _project_data(p) != _project_data(prev):
                n = tx.execute(
                    "UPDATE launch_board_projects SET name = :name, name_key = :nk, lane = :lane, "
                    "priority = :priority, data = :data, version = version + 1 "
                    "WHERE id = :id AND version = :version",
                    {"id": p["id"], "name": p["name"], "nk": p["name"].lower(), "lane": p["lane"],
                     "priority": p["priority"], "data": _project_data(p), "version": prev["version"]})
                if n != 1:
                    raise StaleError("project %s changed since it was loaded; reload and retry" % p["id"])
                p["version"] = prev["version"] + 1
            else:
                p["version"] = prev["version"]
            if len(p["history"]) < start or p["history"][:start] != prev["history"]:
                raise lb.BoardError("history is append-only")
        for i, e in enumerate(p["history"][start:], start=start + 1):
            tx.execute("INSERT INTO launch_board_events (project_id, seq, at, actor, action, detail, request_key) "
                       "VALUES (:pid, :seq, :at, :actor, :action, :detail, :rk)",
                       {"pid": p["id"], "seq": i, "at": e["at"], "actor": e["actor"], "action": e["action"],
                        "detail": e["detail"], "rk": request_key if first_event else None})
            first_event = False
    if request_key and first_event:
        raise lb.BoardError("request produced no change to record")


def _check_expected(board: Dict, expected: Optional[Dict[int, int]]) -> None:
    for pid, ver in (expected or {}).items():
        for p in board["projects"]:
            if p["id"] == pid and p["version"] != ver:
                raise StaleError("project %s is at version %s, not %s; reload and retry" % (pid, p["version"], ver))


class DbStore:
    def __init__(self, tx_factory: Optional[Callable] = None):
        self._factory = tx_factory or _default_tx_factory

    def _open(self):
        try:
            return self._factory()
        except Exception as e:  # noqa: BLE001 - any connect failure is a storage failure
            raise _storage_error(e)

    def load(self) -> Dict:
        tx = self._open()
        try:
            return _load_board(tx)
        except lb.BoardError:
            raise
        except Exception as e:  # noqa: BLE001
            raise _storage_error(e)
        finally:
            tx.close()

    def mutate(self, fn, request_key: Optional[str] = None, expected: Optional[Dict[int, int]] = None):
        """Apply fn(board) in one transaction. Returns (result, replayed)."""
        tx = self._open()
        try:
            if request_key:
                seen = tx.query("SELECT project_id FROM launch_board_events WHERE request_key = :rk",
                                {"rk": request_key})
                if seen:
                    return seen[0]["project_id"], True
            before = _load_board(tx)
            _check_expected(before, expected)
            after = copy.deepcopy(before)
            result = fn(after)
            _persist(tx, before, after, request_key)
            tx.commit()
            return result, False
        except lb.BoardError:
            tx.rollback()
            raise
        except Exception as e:  # noqa: BLE001
            tx.rollback()
            if isinstance(e, ConflictError) or (hasattr(tx, "is_conflict") and tx.is_conflict(e)):
                if request_key:
                    seen = self._replay(request_key)
                    if seen is not None:
                        return seen, True
                raise StaleError("the board changed or the name already exists; reload and retry")
            raise _storage_error(e)
        finally:
            tx.close()

    def _replay(self, request_key):
        tx = self._open()
        try:
            r = tx.query("SELECT project_id FROM launch_board_events WHERE request_key = :rk", {"rk": request_key})
            return r[0]["project_id"] if r else None
        except Exception:  # noqa: BLE001
            return None
        finally:
            tx.close()


# ── file adapter (explicit local/test use only) ─────────────────────────────────

class FileStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path

    def load(self) -> Dict:
        return lb.load(self.path)

    def mutate(self, fn, request_key=None, expected=None):
        return lb.mutate(fn, self.path), False


def get_store():
    return FileStore() if backend() == "file" else DbStore()


def storage_status() -> Dict:
    if backend() == "file":
        return {"backend": "file", "durable": False,
                "warning": "Board is using the local file adapter (LAUNCH_BOARD_BACKEND=file) and is lost on "
                           "redeploy. Unset it to use the database."}
    return {"backend": "db", "durable": True, "warning": None}
