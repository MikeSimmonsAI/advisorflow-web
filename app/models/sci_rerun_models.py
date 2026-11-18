"""SCI RERUN QUEUE (owner, Nov 17 2026).

Families who did not reply to their first journey start it over, in the
Re-engagement campaign's wording, through the standing daily batch (40 a day,
never-reached families first). One row per family per rerun; every value the
start changes is kept in before_json so it can be undone and audited.
"""
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.models.models import Base, gen_uuid

QUEUED, STARTED, SKIPPED = "queued", "started", "skipped"
NEVER_REACHED, NO_REPLY = "never_reached", "no_reply"


class ProgramRerunMember(Base):
    __tablename__ = "program_rerun_members"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, nullable=False, index=True)
    rerun_key = Column(String, nullable=False)          # e.g. "sci-rerun-2026-11-20"
    lead_id = Column(String, nullable=False)
    group = Column("group_name", String, nullable=False)  # never_reached | no_reply
    priority = Column(Integer, nullable=False, default=1)
    status = Column(String, nullable=False, default=QUEUED)
    not_before = Column(DateTime, nullable=False)
    reason = Column(String, nullable=True)                # why skipped at start time
    mode = Column(String, nullable=True)                  # text_email | email_only
    before_json = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("rerun_key", "lead_id", name="uq_rerun_member"),
                      Index("ix_rerun_org_status", "organization_id", "status"))
