# Claude Review — Fix Applied

Reviewed against `CLAUDE_REVIEW_HANDOFF.md`. Full backend compile + pytest
suite (259 passed, 8 skipped, 0 failed) and frontend install + build both
verified clean by actually running them, not just reading the notes.

## Bug found and fixed

**File:** `app/routers/cadence_router.py`
**Endpoint:** `GET /cadence/health-summary`

The handoff doc's own spec defines a healthy active cadence as:

> next_touch_due_at is null OR next_touch_due_at >= now

But the code checked `state.next_touch_due_at and state.next_touch_due_at >= now`,
which is falsy when `next_touch_due_at` is `None` — so an active cadence with
nothing scheduled yet was incorrectly counted as **overdue** instead of
**healthy**, understating the health score shown in the Overview gauge.

### Fix
Changed the condition to:
```python
if state.next_touch_due_at is None or state.next_touch_due_at >= now:
    healthy_active_count += 1
else:
    overdue_active_count += 1
```

Also corrected the docstring and the `"formula"` string returned in the
response payload, both of which previously stated the inverse of the
intended (and now actual) behavior.

### Regression test added
`tests/test_overview_charts_router.py::test_cadence_health_summary_treats_unset_next_touch_due_at_as_healthy_not_overdue`

Creates one active `CadenceState` with `next_touch_due_at=None` and asserts
it counts as healthy (health_score == 100.0), not overdue.

## Notes on doc's own production to-dos

- **DB migrations for `audit_log_entries` and `campaigns`:** not actually
  needed. `app/main.py` calls `Base.metadata.create_all()` at startup, which
  creates new tables automatically. The migration script
  (`app/migrate_add_missing_columns.py`) only exists because `create_all()`
  does NOT add new *columns* to tables that already exist — these are new
  tables, not new columns, so they'll self-create on next deploy.
- Everything else in the handoff doc (org scoping, lead merge transaction
  safety, campaign DNC skip, AI draft reply fallback, route ordering) was
  spot-checked directly against the code and tests and held up as described.
