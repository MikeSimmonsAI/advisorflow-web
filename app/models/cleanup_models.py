"""THE CLEANUP RECEIPT — a row, not a response body.

The first production cleanup exposed the gap. The manifest describing what was
about to be deleted existed in exactly one place: the browser tab that requested
it. The tab reloaded, the manifest went with it, and the only surviving record
of which 29 leads were removed was a count. That is not a receipt, it is a
rumour.

WHY A TABLE AND NOT JUST AN AUDIT ROW. `AuditLogEntry.details` is a Text column
holding JSON, fine for "who changed what" but wrong for a list of record ids
that has to be queryable, and wrong for a two-phase operation. A cleanup has a
BEFORE state (this is what I intend to delete) and an AFTER state (this is what
actually went), and the whole point is being able to compare them. So the plan
gets a row when it is previewed, and the same row is updated when it executes.

THE ID TIES THE THREE TOGETHER. Preview returns `execution_id`; execute is
performed against that id; the audit entry records it. Three artifacts, one
identifier, so "what did this audit line actually delete" has an answer.

A FAILURE IS ALSO A RECEIPT. The first two production attempts raised
IntegrityError and rolled back. Nothing was deleted, which was correct - and
nothing was recorded, which was not. A rolled-back attempt now leaves a row
saying it was attempted, why it failed, and that actual_total is 0. A cleanup
system that only writes history when it succeeds will eventually let somebody
believe a deletion happened that did not.
"""

from datetime import datetime
import json
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.models.models import Base

# Status values. `previewed` means a plan exists and nothing has been touched.
STATUS_PREVIEWED = "previewed"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"          # raised; transaction rolled back; nothing deleted
STATUS_SUPERSEDED = "superseded"  # the candidate set changed before execution

CLEANUP_STATUSES = (STATUS_PREVIEWED, STATUS_SUCCEEDED, STATUS_FAILED, STATUS_SUPERSEDED)


def gen_uuid() -> str:
    return str(uuid.uuid4())


class CleanupExecution(Base):
    """One cleanup plan, and what became of it."""

    __tablename__ = "cleanup_executions"

    id = Column(String, primary_key=True, default=gen_uuid)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    executed_at = Column(DateTime, nullable=True)

    # Who. Never nullable - an unattributed deletion plan is not acceptable.
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=False)
    actor_email = Column(String, nullable=True)   # denormalised so history survives

    status = Column(String, nullable=False, default=STATUS_PREVIEWED)

    # What was asked for.
    rules = Column(Text, nullable=False)              # JSON list
    org_ids = Column(Text, nullable=True)             # JSON list, [] = all
    import_batches = Column(Text, nullable=True)      # JSON list

    # The exact phrase the server demanded, and what the operator actually sent.
    # Both, because "they typed the right thing" is a claim worth being able to
    # check later rather than infer from the fact that it worked.
    confirmation_phrase = Column(String, nullable=False)
    confirmation_received = Column(String, nullable=True)

    # THE MANIFEST. Exact ids, so "which records went" is answerable forever.
    target_lead_ids = Column(Text, nullable=False)    # JSON list of lead ids
    manifest = Column(Text, nullable=True)            # JSON: full per-lead detail

    expected_counts = Column(Text, nullable=False)    # JSON: per-table
    expected_total = Column(Integer, nullable=False)

    actual_counts = Column(Text, nullable=True)       # JSON: per-table, after
    actual_total = Column(Integer, nullable=True)

    # What was deliberately NOT touched, recorded on the row rather than only in
    # a docstring, so the guarantee is auditable.
    excluded = Column(Text, nullable=True)            # JSON list of strings

    error = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_cleanup_actor_created", "actor_user_id", "created_at"),
        Index("ix_cleanup_status_created", "status", "created_at"),
    )

    # ── convenience ────────────────────────────────────────────────────────
    @staticmethod
    def _load(raw, default):
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return default

    def as_dict(self, include_manifest: bool = False) -> dict:
        out = {
            "execution_id": self.id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "actor_user_id": self.actor_user_id,
            "actor_email": self.actor_email,
            "rules": self._load(self.rules, []),
            "org_ids": self._load(self.org_ids, []),
            "import_batches": self._load(self.import_batches, []),
            "confirmation_phrase": self.confirmation_phrase,
            "confirmation_received": self.confirmation_received,
            "target_lead_ids": self._load(self.target_lead_ids, []),
            "target_lead_count": len(self._load(self.target_lead_ids, [])),
            "expected_counts": self._load(self.expected_counts, {}),
            "expected_total": self.expected_total,
            "actual_counts": self._load(self.actual_counts, None),
            "actual_total": self.actual_total,
            "excluded": self._load(self.excluded, []),
            "error": self.error,
            # The check that matters when reading history back: did the number
            # the operator confirmed equal the number that happened?
            "counts_match": (self.actual_total == self.expected_total
                             if self.actual_total is not None else None),
        }
        if include_manifest:
            out["manifest"] = self._load(self.manifest, [])
        return out
