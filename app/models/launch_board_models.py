"""CONTROL ROOM LAUNCH BOARD - durable storage (issue #21).

Two narrow tables, god-only, written only by app/services/launch_board_store.py:

  launch_board_projects  one row per project. `data` is the project's JSON state
                         (everything except history); name/lane/priority are
                         denormalised for ordering and the unique name rule.
                         `version` is the optimistic-concurrency counter: every
                         write is `UPDATE ... WHERE id = ? AND version = ?`, so a
                         writer holding stale state matches no row and is refused
                         instead of silently overwriting a newer change.
  launch_board_events    append-only history. PK (project_id, seq): two writers
                         racing for the same seq collide instead of interleaving.
                         `request_key` is unique when present, which makes a
                         replayed request a no-op. Application code never
                         updates or deletes an event; archiving a project is a
                         lane change, not a delete.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func

from app.models.models import Base


class LaunchBoardProject(Base):
    __tablename__ = "launch_board_projects"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    name_key = Column(String(200), nullable=False, unique=True)   # lower(name): case-insensitive uniqueness
    lane = Column(String(16), nullable=False, default="backlog")
    priority = Column(Integer, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    data = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (Index("ix_launch_board_projects_order", "priority", "id"),)


class LaunchBoardEvent(Base):
    __tablename__ = "launch_board_events"

    project_id = Column(Integer, ForeignKey("launch_board_projects.id"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    at = Column(String(40), nullable=False)
    actor = Column(String(200), nullable=False)
    action = Column(String(40), nullable=False)
    detail = Column(Text, nullable=False, default="")
    request_key = Column(String(120), nullable=True, unique=True)
