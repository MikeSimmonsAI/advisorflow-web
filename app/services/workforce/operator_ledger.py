"""OPERATOR LEDGER — one read decision for AI Workforce work items and runs.

CANONICAL SOURCES (no second store): `ai_work_items` (+ state machine in
constants.py) and `ai_employee_runs` (shaped by run_evidence.shape_run). This
module only NORMALISES them into the operator vocabulary
  queued / running / blocked / review_required / completed / failed / cancelled
and decides what a mutation would do. It performs no I/O, imports no ORM and
can send, charge, deploy or call nothing.

TRUTH RULES
  * A fact the source does not store (commit, tests, deploy, live
    verification, last durable checkpoint on a work item) is
    {"status": "unavailable"} — never 0, never "passed".
  * The four delivery phases (source_complete / tests_complete / deployed /
    live_verified) are separate. `done` is True only when all four are
    evidenced; a completed work-item state alone never makes it true.
  * Scope is the caller's: every row carries the organization the query was
    filtered to, and `scope_ok` drops anything else (defence in depth).
  * Unknown source states stay "unknown"; they are not mapped to queued.
"""
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.services.workforce import run_evidence

UNAVAILABLE = {"status": "unavailable"}

OPERATOR_STATES = ("queued", "running", "blocked", "review_required",
                   "completed", "failed", "cancelled")

# work-item state -> (operator state, reason shown when not self-explanatory)
WORK_ITEM_MAP = {
    "assigned": ("queued", None),
    "eligibility_pending": ("queued", None),
    "eligible": ("queued", None),
    "working": ("running", None),
    "waiting_for_response": ("queued", "Waiting for the contact to respond"),
    "needs_review": ("review_required", "A person must review this item"),
    "human_handoff": ("review_required", "Handed to a person"),
    "paused": ("blocked", "Paused"),
    "failed": ("failed", None),
    "qualified": ("completed", None),
    "appointment_booked": ("completed", None),
    "not_interested": ("completed", None),
    "exhausted": ("completed", None),
    "do_not_contact": ("cancelled", "Contact opted out; work closed"),
    "bad_contact": ("cancelled", "Contact unreachable; work closed"),
}

# run_evidence state -> operator state
RUN_MAP = {
    "queued": "queued", "accepted": "queued", "running": "running",
    "paused": "blocked", "blocked": "blocked", "stale": "blocked",
    "completed": "completed", "failed": "failed",
    "cancelled": "cancelled", "skipped": "cancelled",
}

PHASES = ("source_complete", "tests_complete", "deployed", "live_verified")

# Mutations. Only `review` has persistence + a route today.
MUTATION_BLOCKERS = {
    "assign": "Assignment is a bulk admin action on an employee; per-item assign is not supported.",
    "reprioritize": "No audited priority-change route exists yet.",
    "cancel": "The work-item state machine has no cancelled state; cancel is not supported.",
    "retry": "No audited retry route exists yet (failed items can be sent to review).",
}


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    dt = _naive(dt)
    return None if dt is None else dt.isoformat() + "Z"


def operator_state(source_state: Any) -> str:
    entry = WORK_ITEM_MAP.get(source_state) if isinstance(source_state, str) else None
    return entry[0] if entry else "unknown"


def version_token(item: Any) -> str:
    """Optimistic-concurrency token from persisted columns (no migration).

    Changes whenever state, attempts, priority or updated_at changes, so a
    decision made against a stale view is detectable.
    """
    raw = "|".join(str(x) for x in (
        getattr(item, "id", None), getattr(item, "state", None),
        getattr(item, "attempts", None), getattr(item, "priority", None),
        _iso(getattr(item, "updated_at", None))))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def phases_unavailable(reason: str) -> Dict[str, Any]:
    return {p: {"status": "unavailable", "reason": reason} for p in PHASES}


def done_claim(phases: Dict[str, Any]) -> bool:
    """True only if EVERY phase is evidenced as passed."""
    return all((phases.get(p) or {}).get("status") == "passed" for p in PHASES)


def decision_options(item: Any, *, is_admin: bool,
                     observation: bool = False) -> Dict[str, Any]:
    """What this caller could do to this item, with explicit blockers."""
    review_ok = getattr(item, "state", None) == "needs_review"
    if observation:
        why = "Read-only (observation mode)."
    elif not is_admin:
        why = "Only a workspace admin can decide a review."
    elif not review_ok:
        why = "Only items in review can be decided."
    else:
        why = None
    out: Dict[str, Any] = {"review": {"available": why is None, "blocker": why}}
    for k, v in MUTATION_BLOCKERS.items():
        out[k] = {"available": False, "blocker": v}
    return out


def shape_work_item(item: Any, *, employee: Any = None, runs: Iterable[Any] = (),
                    organization_id: str, is_admin: bool = False,
                    observation: bool = False,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    src = getattr(item, "state", None)
    op = operator_state(src)
    mapped = WORK_ITEM_MAP.get(src)
    reason = getattr(item, "state_reason", None) or (mapped[1] if mapped else None)
    shaped_runs = [run_evidence.shape_run(r, now=now) for r in runs]
    run_evidence.apply_lineage(shaped_runs)
    shaped_runs.sort(key=lambda r: (r.get("started_at") or "", r["run_id"]),
                     reverse=True)
    last = shaped_runs[0] if shaped_runs else None
    checkpoint = ({"status": "available", "summary": last["checkpoint_summary"],
                   "run_id": last["run_id"], "at": last["updated_at"]}
                  if last and last.get("checkpoint_summary") else
                  {"status": "unavailable",
                   "reason": "No run recorded a checkpoint"})
    phases = phases_unavailable("Not recorded for AI work items")
    objective = getattr(employee, "objective", None) if employee else None
    entry: Dict[str, Any] = {
        "kind": "work_item",
        "id": item.id,
        "parent_id": None,
        "child_run_ids": [r["run_id"] for r in shaped_runs],
        "organization_id": organization_id,
        "scope": {"organization_id": organization_id},
        "worker": {
            "employee_id": getattr(item, "employee_id", None),
            "name": getattr(employee, "name", None) if employee else None,
            "capability": getattr(item, "job_key", None),
        },
        "goal": run_evidence.redact(objective, 200),
        # Opaque reference only: type + id, never a name, phone or e-mail.
        "source_ref": {"type": getattr(item, "subject_type", None),
                       "id": getattr(item, "subject_id", None)},
        "state": op,
        "source_state": src,
        "priority": getattr(item, "priority", None),
        "created_at": _iso(getattr(item, "created_at", None)),
        "started_at": (_iso(getattr(item, "claimed_at", None))
                       or (last["started_at"] if last else None)),
        "updated_at": _iso(getattr(item, "updated_at", None)),
        "finished_at": (_iso(getattr(item, "terminal_at", None))
                        if op in ("completed", "cancelled", "failed") else None),
        "attempts": getattr(item, "attempts", None),
        "last_checkpoint": checkpoint,
        "blocker": ({"reason": run_evidence.redact(reason, 160)}
                    if reason and op in ("blocked", "review_required",
                                         "failed", "cancelled") else None),
        "outcome": getattr(item, "outcome", None),
        "commit": UNAVAILABLE, "tests": UNAVAILABLE, "deploy": UNAVAILABLE,
        "evidence": {"kind": "work_item_record", "source": "ai_work_items"},
        "phases": phases,
        "done": done_claim(phases),
        "version": version_token(item),
        "decisions": decision_options(item, is_admin=is_admin,
                                      observation=observation),
    }
    if op == "unknown":
        entry["blocker"] = {"reason": "Unrecognised source state; not shown as queued"}
    return entry


def shape_run_entry(run: Any, *, organization_id: str, employee: Any = None,
                    now: Optional[datetime] = None,
                    superseded_by: Optional[str] = None) -> Dict[str, Any]:
    s = run_evidence.shape_run(run, now=now, superseded_by=superseded_by)
    op = RUN_MAP.get(s["state"], "unknown")
    phases = phases_unavailable("Not recorded for AI runs")
    if op == "blocked":
        blocker = {"reason": s["stale_reason"] or s["ended_because"] or "Blocked"}
    elif op == "failed":
        blocker = {"reason": s["failure_summary"] or s["ended_because"]
                   or "Failed; no reason recorded"}
    else:
        blocker = None
    return {
        "kind": "run", "id": s["run_id"], "parent_id": s["work_item_id"],
        "child_run_ids": [], "organization_id": organization_id,
        "scope": {"organization_id": organization_id},
        "worker": {"employee_id": s["employee_id"],
                   "name": getattr(employee, "name", None) if employee else None,
                   "capability": s["task_label"]},
        "goal": run_evidence.redact(getattr(run, "objective", None), 200),
        "source_ref": ({"type": "work_item", "id": s["work_item_id"]}
                       if s["work_item_id"] else UNAVAILABLE),
        "state": op, "source_state": s["source_state"],
        "stale": s["stale"], "simulated": s["simulated"],
        "created_at": s["started_at"], "started_at": s["started_at"],
        "updated_at": s["updated_at"], "finished_at": s["ended_at"],
        "attempts": UNAVAILABLE,
        "last_checkpoint": ({"status": "available",
                             "summary": s["checkpoint_summary"],
                             "at": s["updated_at"]}
                            if s["checkpoint_summary"] else
                            {"status": "unavailable",
                             "reason": "No checkpoint recorded"}),
        "blocker": blocker,
        "commit": UNAVAILABLE, "tests": UNAVAILABLE, "deploy": UNAVAILABLE,
        "evidence": s["evidence"], "phases": phases,
        "done": done_claim(phases),
        "version": None, "decisions": {},
    }


def scope_ok(entry: Dict[str, Any], organization_id: str) -> bool:
    return entry.get("organization_id") == organization_id


def sort_key(entry: Dict[str, Any]):
    """Deterministic: newest update first, then kind, then id."""
    return (entry.get("updated_at") or "", entry["kind"], entry["id"])


def build_ledger(entries: Iterable[Dict[str, Any]], *, organization_id: str,
                 state: Optional[str] = None, limit: int = 100,
                 source_errors: Optional[List[str]] = None) -> Dict[str, Any]:
    rows = [e for e in entries if scope_ok(e, organization_id)]
    rows.sort(key=sort_key, reverse=True)
    counts = {s: 0 for s in OPERATOR_STATES}
    counts["unknown"] = 0
    for e in rows:
        counts[e["state"]] = counts.get(e["state"], 0) + 1
    if state in OPERATOR_STATES or state == "unknown":
        rows = [e for e in rows if e["state"] == state]
    n = max(1, min(int(limit), 200))
    errs = list(source_errors or [])
    return {
        "organization_id": organization_id,
        "items": rows[:n],
        "counts": counts,
        "truncated": len(rows) > n,
        "partial": bool(errs),
        "source_errors": errs,
        "mutations_enabled": ["review"],
        "mutation_blockers": dict(MUTATION_BLOCKERS),
    }


# ── review decision planning (pure) ─────────────────────────────────────────

def plan_review(*, current_state: str, current_version: str,
                target_state: str, expected_version: Optional[str],
                reachable: bool) -> Dict[str, Any]:
    """Decide what a review click does BEFORE any write.

    outcome: apply | replay | conflict | illegal | version_required
    `replay` = the item is already in the target state: the earlier click won,
    so this one is a no-op success (double-click protection), not a 409.
    """
    if not expected_version:
        return {"outcome": "version_required", "write": False}
    if current_state == target_state:
        # The caller's view is older than the item and the item already sits
        # where their click would put it: the earlier click won. If the view
        # is current, the item was never in review, so there is nothing to do.
        if expected_version != current_version:
            return {"outcome": "replay", "write": False}
        return {"outcome": "illegal", "write": False}
    if expected_version != current_version:
        return {"outcome": "conflict", "write": False}
    if not reachable:
        return {"outcome": "illegal", "write": False}
    return {"outcome": "apply", "write": True}
