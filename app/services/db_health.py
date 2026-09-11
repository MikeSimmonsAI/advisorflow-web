"""Read-only database health: the signals that predicted the outage.

═══════════════════════════════════════════════════════════════════════════
WHAT THIS IS FOR
═══════════════════════════════════════════════════════════════════════════
A session sat `idle in transaction` holding a lock on `organizations`.
Startup DDL queued behind it. Nothing anywhere reported either fact, so the
first evidence anyone had was a platform that would not boot.

Every number here was visible in `pg_stat_activity` the whole time. This
module reads them, and ONE implementation serves both callers — the
pre-deploy preflight and the God diagnostic — because two copies of a
health check are two answers that eventually disagree.

═══════════════════════════════════════════════════════════════════════════
STRICTLY READ-ONLY, AND STRICTLY NON-DESTRUCTIVE
═══════════════════════════════════════════════════════════════════════════
Nothing here terminates, cancels, or modifies anything. `pg_terminate_backend`
and `pg_cancel_backend` appear nowhere in this file ON PURPOSE: an automated
process that kills database sessions when it thinks it sees trouble is a
worse outage than the one it is trying to prevent. Recovery stays a
deliberate, human, evidence-based act.

NO SECRETS, NO QUERY TEXT. Query text can carry customer data and, in the
wrong query, credentials. This reports counts, ages, states and table names —
enough to act on, nothing that leaks.
"""

import logging
import os
from typing import Any, Dict, Optional

from sqlalchemy import text

log = logging.getLogger("db_health")

# ── Thresholds ──────────────────────────────────────────────────────────────
# A transaction idle for a couple of minutes is not automatically wrong — a
# request waiting on a slow external call looks exactly like this. It becomes
# a problem when it outlives anything legitimate, so the threshold sits well
# above normal and below the point where it can block a deploy.
IDLE_TXN_WARN_SECONDS = int(os.environ.get("DB_IDLE_TXN_WARN_SECONDS", "120"))

# A lock wait of a few seconds is ordinary contention. Thirty is not.
LOCK_WAIT_WARN_SECONDS = int(os.environ.get("DB_LOCK_WAIT_WARN_SECONDS", "30"))

# Report connection pressure before it becomes a refusal.
CONNECTION_WARN_RATIO = 0.8


def _is_postgres(engine) -> bool:
    return not str(engine.url).startswith("sqlite")


def inspect(engine, *, idle_txn_seconds: Optional[int] = None,
            lock_wait_seconds: Optional[int] = None) -> Dict[str, Any]:
    """Everything worth knowing about the database's current state.

    Never raises for a database reason. A health check that throws is a
    health check that takes down whatever called it — including, in the
    preflight's case, a deploy that might otherwise have been fine.
    """
    idle_s = IDLE_TXN_WARN_SECONDS if idle_txn_seconds is None else idle_txn_seconds
    lock_s = LOCK_WAIT_WARN_SECONDS if lock_wait_seconds is None else lock_wait_seconds

    out: Dict[str, Any] = {
        "reachable": False,
        "backend": "postgres" if _is_postgres(engine) else "sqlite",
        "thresholds": {"idle_in_transaction_seconds": idle_s,
                       "lock_wait_seconds": lock_s},
        "idle_in_transaction": {"over_threshold": 0, "oldest_seconds": None,
                                "tables": []},
        "lock_waits": {"over_threshold": 0, "longest_seconds": None,
                       "tables": []},
        "connections": {"used": None, "limit": None, "ratio": None},
        "blockers": [],
        "unavailable_reason": None,
    }

    if not _is_postgres(engine):
        # SQLite has no session catalogue and no concurrent deploy. Reported
        # as reachable-but-not-applicable rather than as a false all-clear.
        out["reachable"] = True
        out["unavailable_reason"] = (
            "Session and lock inspection is PostgreSQL-only; this process is "
            "on SQLite.")
        return out

    try:
        with engine.connect() as conn, conn.begin():
            # A health check must not become the thing that hangs. If the
            # database is so busy it cannot answer these in five seconds,
            # that IS the answer.
            #
            # `SET LOCAL`, inside an explicit transaction, for the same reason
            # `app/deps.py` uses it: a plain `SET` is a SESSION setting that
            # survives the `with` block and rides the pooled connection to
            # whoever borrows it next. This check runs from the deploy preflight
            # and from God diagnostics, both of which share the application's
            # engine — a five-second ceiling leaking onto a connection a
            # background job then picks up would cancel legitimate long work,
            # which is precisely the pooled-connection contamination the request
            # timeout fix exists to stop. PostgreSQL reverts this at COMMIT.
            conn.execute(text("SET LOCAL statement_timeout = '5s'"))
            out["reachable"] = True

            # ── abandoned transactions ────────────────────────────────────
            # `state_change` rather than `xact_start`: how long it has been
            # SITTING, which is the thing that matters. A transaction that
            # has been open a while but is actively working is not this.
            rows = conn.execute(text("""
                SELECT COUNT(*) AS n,
                       COALESCE(MAX(EXTRACT(EPOCH FROM (now() - state_change))), 0) AS oldest
                  FROM pg_stat_activity
                 WHERE datname = current_database()
                   AND pid <> pg_backend_pid()
                   AND state = 'idle in transaction'
                   AND state_change < now() - (:s || ' seconds')::interval
            """), {"s": idle_s}).first()
            if rows is not None:
                out["idle_in_transaction"]["over_threshold"] = int(rows[0] or 0)
                out["idle_in_transaction"]["oldest_seconds"] = (
                    int(rows[1]) if rows[0] else None)

            # ── lock waits ────────────────────────────────────────────────
            # The table name comes from pg_locks, so a blocked statement is
            # actionable ("organizations is locked") without exposing what
            # the statement was.
            locks = conn.execute(text("""
                SELECT COALESCE(c.relname, '(non-table)') AS rel,
                       COUNT(*) AS n,
                       MAX(EXTRACT(EPOCH FROM (now() - a.query_start))) AS longest
                  FROM pg_stat_activity a
                  JOIN pg_locks l ON l.pid = a.pid AND NOT l.granted
             LEFT JOIN pg_class c ON c.oid = l.relation
                 WHERE a.datname = current_database()
                   AND a.pid <> pg_backend_pid()
                   AND a.wait_event_type = 'Lock'
                   AND a.query_start < now() - (:s || ' seconds')::interval
              GROUP BY 1
              ORDER BY 3 DESC
                 LIMIT 10
            """), {"s": lock_s}).fetchall()
            if locks:
                out["lock_waits"]["over_threshold"] = sum(int(r[1]) for r in locks)
                out["lock_waits"]["longest_seconds"] = int(max(r[2] or 0 for r in locks))
                out["lock_waits"]["tables"] = [
                    {"table": r[0], "waiters": int(r[1]),
                     "longest_seconds": int(r[2] or 0)} for r in locks]

            # ── connection pressure ───────────────────────────────────────
            used = conn.execute(text(
                "SELECT COUNT(*) FROM pg_stat_activity "
                "WHERE datname = current_database()")).scalar()
            limit = conn.execute(text(
                "SELECT setting::int FROM pg_settings "
                "WHERE name = 'max_connections'")).scalar()
            if used is not None and limit:
                out["connections"] = {
                    "used": int(used), "limit": int(limit),
                    "ratio": round(int(used) / int(limit), 3)}
    except Exception as e:                                  # noqa: BLE001
        out["unavailable_reason"] = (
            "Could not read database health: %s" % str(e)[:200])
        log.warning("db_health: inspection failed: %s", str(e)[:200])
        # FALL THROUGH TO _blockers RATHER THAN RETURNING HERE. Returning
        # early left `blockers` empty, so a database that could not be reached
        # at all came back as "no blockers" and `preflight` called it safe to
        # migrate — a preflight that waves through the one condition it most
        # needs to stop. Caught by its own test.

    out["blockers"] = _blockers(out)
    return out


def _blockers(report: Dict[str, Any]) -> list:
    """Conditions that should stop a migration, each with what to do about it.

    A preflight that says "unhealthy" and nothing else gets overridden by
    whoever is trying to ship. Every entry here names the condition, the
    table where practical, and the actual next step.
    """
    out = []

    idle = report["idle_in_transaction"]
    if idle["over_threshold"]:
        out.append({
            "code": "idle_in_transaction_sessions",
            "message": (
                "%d session(s) have been idle inside a transaction for longer "
                "than %ds (oldest %ds). A session in this state holds its locks "
                "until it ends, and startup schema changes queue behind it."
                % (idle["over_threshold"],
                   report["thresholds"]["idle_in_transaction_seconds"],
                   idle["oldest_seconds"] or 0)),
            "action": (
                "Find the owner before doing anything: these are usually a "
                "container replaced mid-request. Confirm in pg_stat_activity, "
                "then have a person end that session deliberately. Do not "
                "script it."),
        })

    locks = report["lock_waits"]
    if locks["over_threshold"]:
        tables = ", ".join(t["table"] for t in locks["tables"]) or "unknown"
        out.append({
            "code": "long_lock_waits",
            "message": (
                "%d statement(s) have been waiting on a lock for longer than "
                "%ds (longest %ds) on: %s."
                % (locks["over_threshold"],
                   report["thresholds"]["lock_wait_seconds"],
                   locks["longest_seconds"] or 0, tables)),
            "action": (
                "Migrating now would add more waiters to the same queue. Wait "
                "for the contention to clear, or resolve whatever holds the "
                "lock, then deploy again."),
        })

    conns = report["connections"]
    if conns.get("ratio") is not None and conns["ratio"] >= CONNECTION_WARN_RATIO:
        out.append({
            "code": "connection_pressure",
            "message": ("%d of %d connections are in use (%.0f%%)."
                        % (conns["used"], conns["limit"], conns["ratio"] * 100)),
            "action": ("A migration needs a connection of its own. Free some "
                       "before deploying."),
        })

    if not report["reachable"]:
        out.append({
            "code": "database_unreachable",
            "message": report.get("unavailable_reason") or "No answer from the database.",
            "action": "Do not deploy. Establish connectivity first.",
        })

    return out


def preflight(engine) -> Dict[str, Any]:
    """Is it safe to run schema changes right now?

    FAILS THE DEPLOY, NEVER FIXES THE DATABASE. It returns a verdict and the
    evidence behind it; acting on that evidence is a person's job.

    A report it could not gather is NOT a blocker on its own — refusing every
    deploy because the health check itself had a bad moment would be its own
    outage. Unreachable IS a blocker, and that is caught above.
    """
    report = inspect(engine)
    report["safe_to_migrate"] = not report["blockers"]
    return report
