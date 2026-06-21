"""
Engagement Temperature Classification

Computes hot/warm/cold for a lead based on real signals - separate axis
from LeadTier (which describes lead source/type). This is the web
equivalent of the desktop app's HOT/WARM/COLD tabs on the Re-Engagement
screen, which the web version never had until now.

Classification rules (checked in this priority order):
  1. HOT: has at least one reply marked is_hot=True, OR status is BOOKED,
     OR tier is IMMINENT with any reply at all. These are leads that
     showed real, recent interest signal.
  2. WARM: actively in cadence (CadenceState.status == ACTIVE) and has
     been touched at least once, but no hot signal yet. Still alive,
     still worth working.
  3. COLD: cadence is completed/stopped without booking, OR the lead has
     gone untouched for 30+ days with zero reply, OR status is DEAD.
  4. UNKNOWN: brand new, not yet classified (e.g. just imported, no
     cadence started yet).

This is intentionally a pure function over a Lead's current state - it
can be called any time (after a reply arrives, after a cadence touch
sends, or on a periodic recompute job) and will always produce the
correct classification for THAT MOMENT, rather than trying to track
incremental state transitions that could drift out of sync.
"""

from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from app.models.models import Lead, LeadStatus, LeadTier, EngagementTemperature, Reply, CadenceState, CadenceStatus

COLD_AFTER_DAYS_NO_REPLY = 30


def classify_lead_temperature(db: Session, lead: Lead) -> EngagementTemperature:
    """
    Computes the correct engagement_temperature for a single lead based
    on its current state. Does NOT save it - caller decides whether to
    persist (see recompute_and_save below).
    """
    if lead.status == LeadStatus.BOOKED:
        return EngagementTemperature.HOT

    has_hot_reply = (
        db.query(Reply)
        .filter(Reply.lead_id == lead.id, Reply.is_hot == True)
        .first()
        is not None
    )
    if has_hot_reply:
        return EngagementTemperature.HOT

    has_any_reply = (
        db.query(Reply).filter(Reply.lead_id == lead.id).first() is not None
    )
    if has_any_reply and lead.tier == LeadTier.IMMINENT:
        # Imminent-need leads who reply at all get treated as hot - the
        # urgency of the tier itself elevates any engagement signal.
        return EngagementTemperature.HOT

    if lead.status == LeadStatus.DEAD or lead.status == LeadStatus.DNC:
        return EngagementTemperature.COLD

    cadence = lead.cadence_state
    if cadence is not None:
        if cadence.status == CadenceStatus.ACTIVE:
            return EngagementTemperature.WARM
        if cadence.status == CadenceStatus.COMPLETED:
            # Finished all 9 touches with zero resolution - genuinely cold
            return EngagementTemperature.COLD
        if cadence.status == CadenceStatus.STOPPED_DNC:
            return EngagementTemperature.COLD
        # STOPPED_REPLIED / STOPPED_BOOKED fall through to the reply/booked
        # checks above, which already handle those cases correctly.

    if lead.last_contact_date:
        days_since_contact = (datetime.now(timezone.utc) - lead.last_contact_date.replace(tzinfo=timezone.utc)).days
        if days_since_contact >= COLD_AFTER_DAYS_NO_REPLY and not has_any_reply:
            return EngagementTemperature.COLD

    return EngagementTemperature.UNKNOWN


def recompute_and_save(db: Session, lead: Lead) -> EngagementTemperature:
    """Computes and persists the temperature for one lead. Call after any state-changing event."""
    temp = classify_lead_temperature(db, lead)
    lead.engagement_temperature = temp
    db.commit()
    return temp


def recompute_for_organization(db: Session, organization_id: str) -> dict:
    """
    Batch recompute for every lead in an org - intended for a periodic
    job (e.g. nightly) to catch the time-based COLD transition (30+ days
    no reply), which wouldn't otherwise be triggered by any single event.
    """
    leads = db.query(Lead).filter(Lead.organization_id == organization_id).all()
    counts = {"hot": 0, "warm": 0, "cold": 0, "unknown": 0}
    for lead in leads:
        temp = classify_lead_temperature(db, lead)
        lead.engagement_temperature = temp
        counts[temp.value] += 1
    db.commit()
    return counts
