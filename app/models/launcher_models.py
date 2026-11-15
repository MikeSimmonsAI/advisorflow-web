"""Program Access launcher preferences: which authorized workspaces a person
pinned. A pin is a preference only - it never grants anything. The launcher
shows a pin only while the server still lists that workspace for the login."""
from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.models.models import Base, gen_uuid


class LauncherPin(Base):
    __tablename__ = "launcher_pins"
    __table_args__ = (UniqueConstraint("user_id", "item_key", name="uq_launcher_pin_once"),)

    id = Column(String, primary_key=True, default=gen_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    item_key = Column(String(160), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
