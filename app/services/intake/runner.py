"""Background execution for analysis and commit.

A 15,000-row analysis or commit must not hold an HTTP request open, must not
block the web server's event loop, and must not require the browser tab to
stay open. So the route records the intent on the batch, starts a worker
THREAD with its own database session, and returns 202 immediately. The UI
polls `/intake/batches/{id}` for `stage` / `progress_pct`.

Durability: everything the worker does is written to the batch row as it
goes (`stage`, `progress_pct`, `heartbeat_at`). If the process dies mid-run
the batch is reported `interrupted` once its heartbeat is older than
engine.STALE_AFTER, and re-running is safe - analysis replaces its staged
rows, and commit skips rows already IMPORTED.

Inline mode: under SQLite (tests, local dev) or INTAKE_INLINE_JOBS=1 the job
runs synchronously on the request's own session, because an in-memory SQLite
database is not visible to a second connection.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable

log = logging.getLogger(__name__)
_running: dict = {}
_lock = threading.Lock()


def inline() -> bool:
    flag = os.environ.get("INTAKE_INLINE_JOBS")
    if flag == "1":
        return True
    if flag == "0":
        return False
    try:
        from app.deps import _is_sqlite
        return bool(_is_sqlite)
    except Exception:  # noqa: BLE001
        return False


def is_running_here(batch_id: str) -> bool:
    with _lock:
        t = _running.get(batch_id)
        return bool(t and t.is_alive())


def launch(batch_id: str, fn: Callable, *args, db=None, **kwargs):
    """Run fn(session, *args, **kwargs) in the background (or inline)."""
    if inline():
        return fn(db, *args, **kwargs)

    def _work():
        from app.deps import SessionLocal
        session = SessionLocal()
        try:
            fn(session, *args, **kwargs)
        except Exception:  # noqa: BLE001 - the job records its own failure
            log.exception("intake background job failed for batch %s", batch_id)
        finally:
            session.close()
            with _lock:
                _running.pop(batch_id, None)

    with _lock:
        if _running.get(batch_id) and _running[batch_id].is_alive():
            return None
        t = threading.Thread(target=_work, name=f"intake-{batch_id[:8]}", daemon=True)
        _running[batch_id] = t
    t.start()
    return None
