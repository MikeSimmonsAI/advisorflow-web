"""EVOSYS PRO TEAM APP - Team Chat and Timekeeping for the mobile app.

Eagle Says Mobile App Prototype. The app opens Programs (the existing Program
Access answer), Chat and Time behind ONE EvoSys login. This module holds only
what nothing in the platform already stored: chat rooms, messages, attachments
and read positions, and time entries with their correction history.

WHO MAY USE CHAT AND TIME IS NOT STORED HERE. It is decided by
app/services/team_app_access.py from things that already exist:
  * users.role (god_admin / super_admin are the platform),
  * memberships (sales reps and managers of the platform's brand sales teams),
  * user_capability_grants scoped to the platform (team_chat, team_time,
    team_chat_manage, team_announce, team_time_manage) - the same table and
    scope vocabulary the Demo Suite grants already use.

The only authority table here is `team_time_scopes`: WHICH people a delegated
time manager reviews. A time-manager grant with no scope rows reviews nobody.

ADDITIVE ONLY. Every table is new; create_all() is the whole migration.

TIME IS UTC. Every DateTime column here holds naive UTC (the codebase's
convention, datetime.utcnow) written by the SERVER. A phone's clock is never
read. Display conversion happens in the service with zoneinfo.
"""
from datetime import datetime

from sqlalchemy import (Column, DateTime, ForeignKey, Index, Integer, String,
                        Text, UniqueConstraint)

from app.models.models import Base, gen_uuid

ROOM_DM = "dm"
ROOM_GROUP = "room"
ROOM_ANNOUNCEMENTS = "announcements"
ROOM_KINDS = (ROOM_DM, ROOM_GROUP, ROOM_ANNOUNCEMENTS)


class TeamChatRoom(Base):
    __tablename__ = "team_chat_rooms"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    kind = Column(String, nullable=False)             # ROOM_KINDS
    name = Column(String, nullable=True)              # NULL for a DM (named per viewer)
    # Stable key for the built-in rooms ("sales-team", "announcements").
    slug = Column(String, nullable=True)
    # A DM's two user ids, sorted and joined - one DM per pair, enforced by the DB.
    dm_key = Column(String, nullable=True)
    created_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_message_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("platform_id", "slug", name="uq_team_chat_room_slug"),
        UniqueConstraint("platform_id", "dm_key", name="uq_team_chat_room_dm"),
        Index("ix_team_chat_rooms_platform", "platform_id", "kind"),
    )


class TeamChatMember(Base):
    """Who is IN a DM or a group room. Announcements have no member rows:
    everyone with chat access reads them."""
    __tablename__ = "team_chat_members"

    id = Column(String, primary_key=True, default=gen_uuid)
    room_id = Column(String, ForeignKey("team_chat_rooms.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    added_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Removal is recorded, not deleted, so a removed person is not silently
    # re-added by the automatic sales-team enrollment.
    removed_at = Column(DateTime, nullable=True)
    removed_by = Column(String, ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("room_id", "user_id", name="uq_team_chat_member"),
        Index("ix_team_chat_members_user", "user_id"),
    )


class TeamChatMessage(Base):
    __tablename__ = "team_chat_messages"

    id = Column(String, primary_key=True, default=gen_uuid)
    room_id = Column(String, ForeignKey("team_chat_rooms.id", ondelete="CASCADE"), nullable=False)
    sender_id = Column(String, ForeignKey("users.id"), nullable=False)
    body = Column(Text, nullable=False, default="")
    reply_to_id = Column(String, ForeignKey("team_chat_messages.id"), nullable=True)
    mentions = Column(Text, nullable=True)            # JSON list of user ids
    # Retry safety: the phone sends a key per message; a retried send returns
    # the saved message instead of posting it twice.
    client_key = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("sender_id", "client_key", name="uq_team_chat_message_client_key"),
        Index("ix_team_chat_messages_room", "room_id", "created_at"),
    )


class TeamChatAttachment(Base):
    """A private file (bytes in the platform's private storage, served only
    through an authenticated endpoint) or a Google Drive link."""
    __tablename__ = "team_chat_attachments"

    id = Column(String, primary_key=True, default=gen_uuid)
    room_id = Column(String, ForeignKey("team_chat_rooms.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(String, ForeignKey("team_chat_messages.id", ondelete="CASCADE"), nullable=True)
    uploader_id = Column(String, ForeignKey("users.id"), nullable=False)
    kind = Column(String, nullable=False, default="file")   # file | drive_link
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=True)
    byte_size = Column(Integer, nullable=True)
    storage_backend = Column(String, nullable=True)
    storage_key = Column(String, nullable=True)            # never sent to a browser
    url = Column(String, nullable=True)                    # drive_link only
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_team_chat_attachments_message", "message_id"),)


class TeamChatRead(Base):
    __tablename__ = "team_chat_reads"

    id = Column(String, primary_key=True, default=gen_uuid)
    room_id = Column(String, ForeignKey("team_chat_rooms.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    last_read_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("room_id", "user_id", name="uq_team_chat_read"),)


class TeamTimeEntry(Base):
    __tablename__ = "team_time_entries"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    clock_in_at = Column(DateTime, nullable=False)       # UTC, server time
    clock_out_at = Column(DateTime, nullable=True)       # UTC, server time
    # ONE OPEN SHIFT PER PERSON, ENFORCED BY THE DATABASE. Holds the user id
    # while the shift is open and NULL once it is closed; a unique constraint
    # on it refuses a second open shift from a double tap or two phones at
    # once. (A plain unique column, not a partial index, so SQLite and
    # Postgres behave the same: both allow many NULLs.)
    open_marker = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("ix_team_time_entries_user", "user_id", "clock_in_at"),)


class TeamTimeCorrection(Base):
    """Who changed which entry, from what to what, when and why."""
    __tablename__ = "team_time_corrections"

    id = Column(String, primary_key=True, default=gen_uuid)
    entry_id = Column(String, ForeignKey("team_time_entries.id", ondelete="CASCADE"), nullable=False)
    changed_by = Column(String, ForeignKey("users.id"), nullable=False)
    reason = Column(Text, nullable=False)
    before_clock_in_at = Column(DateTime, nullable=True)
    before_clock_out_at = Column(DateTime, nullable=True)
    after_clock_in_at = Column(DateTime, nullable=True)
    after_clock_out_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (Index("ix_team_time_corrections_entry", "entry_id"),)


class TeamTimeScope(Base):
    """A delegated time manager reviews exactly these people."""
    __tablename__ = "team_time_scopes"

    id = Column(String, primary_key=True, default=gen_uuid)
    platform_id = Column(String, ForeignKey("platforms.id", ondelete="CASCADE"), nullable=False)
    manager_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    member_user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    granted_by = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("platform_id", "manager_user_id", "member_user_id",
                         name="uq_team_time_scope"),
    )
