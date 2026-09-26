"""Import audit events.

Every import-related action lands in the platform's one audit log
(`audit_log_entries`, via `log_action`) - not in a second log nobody reads.
Each event carries who acted, in what role, and whether a platform owner was
acting on behalf of the organization, so "who imported this, and as whom?"
is answerable from the log alone.

Event names (target_type = "import_batch", target_id = batch id):
    intake.file_uploaded        intake.mapping_saved
    intake.analysis_started     intake.analysis_completed    intake.analysis_failed
    intake.classification_saved intake.row_overridden        intake.rows_bulk_resolved
    intake.decision_stage_only  intake.batch_committed       intake.commit_failed
    intake.batch_rolled_back    intake.batch_cancelled
Row-level bulk actions are ONE event with counts, not thousands of rows.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


def record(db, ctx, action: str, batch_id: str, details: Optional[dict] = None,
           *, note: Optional[str] = None, commit: bool = False,
           before: Any = None, after: Any = None):
    from app.routers.audit_log_router import log_action
    payload = dict(ctx.audit_details())
    payload.update(details or {})
    try:
        return log_action(db, ctx.org_id, ctx.actor_id, action, "import_batch",
                          batch_id, details=payload, note=note, before=before,
                          after=after, commit=commit)
    except Exception:  # noqa: BLE001 - an audit failure must be loud, not fatal
        log.exception("intake audit write failed: %s batch=%s", action, batch_id)
        return None
