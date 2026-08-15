"""
AdvisorFlow Platform Event System
----------------------------------
Each spinoff platform (BookaBoost, EvoSys Pro, Harmony Hustle) emits
standardized events through emit(). AdvisorFlow ingests these events
from the platform_events table to power its Command Center dashboard —
revenue tracking, lead intelligence, AI operations, system health, etc.

ISOLATION GUARANTEE:
  BookaBoost and EvoSys Pro never query each other's data.
  Both emit into the same platform_events table tagged with their
  platform slug. AdvisorFlow reads the table. No cross-platform
  DB access ever happens.

USAGE:
  from app.events import emit, EventType

  emit(db, EventType.LEAD_CREATED, org_id=org.id, platform="bookaboost",
       data={"lead_id": lead.id, "source": "facebook", "tier": lead.tier})

  emit(db, EventType.APPOINTMENT_BOOKED, org_id=org.id, platform="bookaboost",
       data={"lead_id": lead.id, "advisor_id": user.id})
"""

from app.events.emit import emit
from app.events.schema import EventType

__all__ = ["emit", "EventType"]
