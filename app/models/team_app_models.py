"""EVOSYS PRO MOBILE - Team Chat storage.

The app opens Programs (the existing Program Access answer), Chat, Activity and
Account behind ONE EvoSys login. This module holds only what nothing in the
platform already stored: chat rooms, messages, attachments and read positions.
Activity reads existing records (bookings, sales appointments, chat mentions)
and stores nothing.

WHO MAY USE CHAT IS NOT STORED HERE. It is decided by
app/services/team_app_access.py from things that already exist:
  * users.role (god_admin / super_admin are the platform),
  * memberships (sales reps and managers of the platform's brand sales teams),
  * user_capability_grants scoped to the platform (team_chat,
    team_chat_manage, team_announce) - the same table and scope vocabulary
    the Demo Suite grants already use.

ADDITIVE ONLY. Every table is new; create_all() is the whole migration.
Timestamps are naive UTC written by the server (datetime.utcnow).
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


class TeamAppPin(Base):
    """EvoSys Pro Mobile: the (at most two) programs a person pinned on the
    phone's Programs screen. A preference only - it never grants anything; the
    app shows a pin only while /auth/my-contexts still lists that program for
    this login. Kept apart from the desktop launcher's pins (launcher_pins),
    whose list is longer and is replaced as a whole on every desktop save, so
    neither screen can overwrite the other's choices."""
    __tablename__ = "team_app_pins"

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    item_key = Column(String(160), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_team_app_pin_once"),)
