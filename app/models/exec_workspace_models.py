"""
Executive Workspace models — deal-room content owned by a customer organization.

THREE TABLES, ONE INVARIANT
───────────────────────────
Every row in every table here carries BOTH organization_id AND platform_id.
That is not redundancy; it is the join that makes every executive query safe:
an organization belongs to exactly one platform, so the pair enforces that a
workspace item can never appear across a brand boundary even if a query
mistakenly joins only on platform.

AUTHORITY IS NOT HERE
─────────────────────
These models store ownership. Authority — which executive may see which
organization — is decided by executive_authority.py exclusively. Nothing in
this file re-derives or caches that decision.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, LargeBinary,
    String, Text, CheckConstraint,
)
from sqlalchemy.orm import relationship

from app.models.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ExecWorkspaceItem(Base):
    """A deal-room item — the top-level container for a workspace entry."""

    __tablename__ = "exec_workspace_items"

    id              = Column(String, primary_key=True, default=_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    platform_id     = Column(String, ForeignKey("platforms.id"),     nullable=False, index=True)

    title         = Column(String,  nullable=False)
    status        = Column(String,  nullable=False, default="Draft")
    working_notes = Column(Text,    nullable=True)

    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    updated_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('Draft','Partner Review','Approved','Final')",
            name="exec_workspace_items_status_check",
        ),
    )

    files    = relationship("ExecWorkspaceFile",    back_populates="item",
                            foreign_keys="ExecWorkspaceFile.item_id",
                            cascade="all, delete-orphan")
    versions = relationship("ExecWorkspaceVersion", back_populates="item",
                            cascade="all, delete-orphan")


class ExecWorkspaceFile(Base):
    """A file attached to a workspace item — blob stored in the database."""

    __tablename__ = "exec_workspace_files"

    id              = Column(String, primary_key=True, default=_uuid)
    item_id         = Column(String, ForeignKey("exec_workspace_items.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, index=True)
    platform_id     = Column(String, ForeignKey("platforms.id"),     nullable=False)

    filename        = Column(String,  nullable=False)
    content_type    = Column(String,  nullable=False, default="application/octet-stream")
    file_size       = Column(Integer, nullable=False, default=0)
    file_data       = Column(LargeBinary, nullable=False)

    is_current      = Column(Boolean, nullable=False, default=True)
    replaces_file_id = Column(String, ForeignKey("exec_workspace_files.id"), nullable=True)

    uploaded_by = Column(String, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    item     = relationship("ExecWorkspaceItem", back_populates="files",
                            foreign_keys=[item_id])
    previous = relationship("ExecWorkspaceFile", remote_side="ExecWorkspaceFile.id",
                            foreign_keys=[replaces_file_id])


class ExecWorkspaceVersion(Base):
    """Automatic point-in-time snapshot of an item's title/status/notes."""

    __tablename__ = "exec_workspace_versions"

    id              = Column(String, primary_key=True, default=_uuid)
    item_id         = Column(String, ForeignKey("exec_workspace_items.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    platform_id     = Column(String, ForeignKey("platforms.id"),     nullable=False)

    snapshot_title  = Column(String, nullable=True)
    snapshot_notes  = Column(Text,   nullable=True)
    snapshot_status = Column(String, nullable=True)
    trigger         = Column(String, nullable=False, default="patch")   # "patch" | "create"

    saved_by = Column(String, ForeignKey("users.id"), nullable=True)
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    item = relationship("ExecWorkspaceItem", back_populates="versions")
