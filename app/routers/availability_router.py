"""
Advisor Availability Router
Manages when advisors are available for bookings.
Advisors and org_admins can block dates, slots, and recurring times.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime, timedelta

from app.deps import get_db, get_current_user
from app.models.models import User, AdvisorAvailabilityBlock, BlockType, BookingLink, Lead

router = APIRouter(prefix="/availability", tags=["availability"])

URGENT_TIERS = {"at_need", "atneed", "at-need", "imminent", "urgent"}

# ── Slot config — single source of truth ──────────────────────────────────────
SLOT_TIMES = [
    "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
    "15:00", "15:30", "16:00", "16:30", "17:00",
]
SLOT_DURATION_MINUTES = 60
DAYS_AHEAD = 14
MAX_PER_SLOT = 2


def _fmt_slot_label(d: date, time_str: str) -> str:
    """e.g. 'Monday, Jul 14 at 9:00 AM'"""
    from datetime import datetime as dt
    h, m = map(int, time_str.split(":"))
    t = dt(d.year, d.month, d.day, h, m)
    return f"{d.strftime('%A, %b %d')} at {t.strftime('%-I:%M %p').lstrip('0') or '12:00 AM'}"


def _is_blocked_by_rule(check_date: date, time_str: str, blocks: list) -> bool:
    """Check if a date+time is blocked by any advisor availability block."""
    for b in blocks:
        if b.block_type == BlockType.DATE_RANGE:
            if b.start_date and b.end_date:
                if b.start_date <= check_date <= b.end_date:
                    return True
        elif b.block_type == BlockType.SLOT:
            if b.block_date == check_date and b.block_time == time_str:
                return True
        elif b.block_type == BlockType.RECURRING:
            # day_of_week: 0=Mon, 6=Sun
            if b.recur_day_of_week is not None and check_date.weekday() != b.recur_day_of_week:
                continue
            if b.recur_after_time and time_str >= b.recur_after_time:
                return True
            if b.recur_before_time and time_str <= b.recur_before_time:
                return True
    return False


def _count_bookings_for_slot(db: Session, advisor_id: str, slot_date: date, slot_time: str) -> int:
    """Count confirmed BookaBoost bookings for a given slot."""
    from datetime import datetime as dt
    h, m = map(int, slot_time.split(":"))
    slot_start = dt(slot_date.year, slot_date.month, slot_date.day, h, m)
    slot_end = slot_start + timedelta(minutes=SLOT_DURATION_MINUTES)

    count = db.query(BookingLink).filter(
        BookingLink.user_id == advisor_id,
        BookingLink.status == "booked",
        BookingLink.booked_time >= slot_start,
        BookingLink.booked_time < slot_end,
    ).count()
    return count


def _check_outlook_conflict(access_token: str, slot_date: date, slot_time: str) -> bool:
    """Check Microsoft Graph for existing calendar events during a slot."""
    import httpx
    from datetime import datetime as dt

    h, m = map(int, slot_time.split(":"))
    slot_start = dt(slot_date.year, slot_date.month, slot_date.day, h, m)
    slot_end = slot_start + timedelta(minutes=SLOT_DURATION_MINUTES)

    # Microsoft Graph freeBusy check
    try:
        resp = httpx.post(
            "https://graph.microsoft.com/v1.0/me/calendar/getSchedule",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={
                "schedules": ["me"],
                "startTime": {"dateTime": slot_start.isoformat(), "timeZone": "America/Chicago"},
                "endTime":   {"dateTime": slot_end.isoformat(),   "timeZone": "America/Chicago"},
                "availabilityViewInterval": SLOT_DURATION_MINUTES,
            },
            timeout=10,
        )
        if resp.status_code == 200:
            schedules = resp.json().get("value", [])
            for s in schedules:
                items = s.get("scheduleItems", [])
                for item in items:
                    if item.get("status") in ("busy", "tentative", "oof"):
                        return True
    except Exception:
        pass  # If Outlook check fails, don't block the slot
    return False


@router.get("/slots/{advisor_id}")
def get_available_slots(advisor_id: str, db: Session = Depends(get_db)):
    """
    Public endpoint — no auth required.
    Returns available booking slots for an advisor for the next 14 days.
    Checks: advisor blocks, BookaBoost booking count (max 2), Outlook calendar.
    Called by Vercel booking app to show available times.
    """
    from datetime import date as date_cls, datetime as dt
    import os

    advisor = db.query(User).filter(User.id == advisor_id).first()
    if not advisor:
        raise HTTPException(status_code=404, detail="Advisor not found")

    # Get advisor's availability blocks
    blocks = db.query(AdvisorAvailabilityBlock).filter(
        AdvisorAvailabilityBlock.advisor_id == advisor_id,
    ).all()

    # Get Outlook access token if connected
    access_token = None
    if advisor.microsoft_365_connected and advisor.microsoft_oauth_refresh_token_encrypted:
        try:
            from app.services.microsoft_email_service import _get_fresh_access_token
            access_token = _get_fresh_access_token(advisor)
        except Exception:
            pass

    today = date_cls.today()
    slots = []

    for day_offset in range(0, DAYS_AHEAD + 1):
        check_date = today + timedelta(days=day_offset)

        for time_str in SLOT_TIMES:
            # Skip past slots for today
            if day_offset == 0:
                h, m = map(int, time_str.split(":"))
                now = dt.now()
                if dt(now.year, now.month, now.day, h, m) <= now:
                    continue

            # Check advisor blocks
            if _is_blocked_by_rule(check_date, time_str, blocks):
                continue

            # Check BookaBoost booking count
            booked_count = _count_bookings_for_slot(db, advisor_id, check_date, time_str)
            if booked_count >= MAX_PER_SLOT:
                continue

            # Check Outlook calendar conflict
            if access_token and _check_outlook_conflict(access_token, check_date, time_str):
                continue

            # Build slot label with urgency indicator
            spots_left = MAX_PER_SLOT - booked_count
            if booked_count == 1:
                urgency = "🔥 1 spot left"
            else:
                urgency = None

            # Format slot_id as YYYYMMDD_HHMM
            slot_id = f"{check_date.strftime('%Y%m%d')}_{time_str.replace(':', '')}"

            slots.append({
                "slot_id":    slot_id,
                "label":      _fmt_slot_label(check_date, time_str),
                "date":       check_date.strftime("%m/%d/%Y"),
                "time":       time_str,
                "spots_left": spots_left,
                "urgency":    urgency,
            })

    return {"slots": slots, "advisor_name": advisor.full_name}


# ── Block management ──────────────────────────────────────────────────────────

class DateRangeBlockRequest(BaseModel):
    start_date: date
    end_date: date
    reason: Optional[str] = None
    cancel_existing: bool = False


class SlotBlockRequest(BaseModel):
    block_date: date
    block_time: str
    reason: Optional[str] = None


class RecurringBlockRequest(BaseModel):
    recur_day_of_week: Optional[int] = None   # 0=Mon 6=Sun, None=every day
    recur_after_time: Optional[str] = None
    recur_before_time: Optional[str] = None
    reason: Optional[str] = None


@router.post("/block/date-range")
def block_date_range(
    req: DateRangeBlockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block a date range (vacation, days off). Optionally cancel existing bookings."""
    block = AdvisorAvailabilityBlock(
        advisor_id=current_user.id,
        organization_id=current_user.organization_id,
        block_type=BlockType.DATE_RANGE,
        start_date=req.start_date,
        end_date=req.end_date,
        reason=req.reason,
        cancel_existing=req.cancel_existing,
        created_by_id=current_user.id,
    )
    db.add(block)

    cancelled = []
    if req.cancel_existing:
        cancelled = _cancel_bookings_in_range(db, current_user, req.start_date, req.end_date)

    db.commit()
    return {"block_id": block.id, "cancelled_bookings": len(cancelled)}


@router.post("/block/slot")
def block_slot(
    req: SlotBlockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block a specific date+time slot."""
    block = AdvisorAvailabilityBlock(
        advisor_id=current_user.id,
        organization_id=current_user.organization_id,
        block_type=BlockType.SLOT,
        block_date=req.block_date,
        block_time=req.block_time,
        reason=req.reason,
        created_by_id=current_user.id,
    )
    db.add(block)
    db.commit()
    return {"block_id": block.id}


@router.post("/block/recurring")
def block_recurring(
    req: RecurringBlockRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Block recurring times (e.g. every Friday after 3pm)."""
    block = AdvisorAvailabilityBlock(
        advisor_id=current_user.id,
        organization_id=current_user.organization_id,
        block_type=BlockType.RECURRING,
        recur_day_of_week=req.recur_day_of_week,
        recur_after_time=req.recur_after_time,
        recur_before_time=req.recur_before_time,
        reason=req.reason,
        created_by_id=current_user.id,
    )
    db.add(block)
    db.commit()
    return {"block_id": block.id}


@router.get("/blocks")
def list_blocks(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List all availability blocks for the current advisor."""
    blocks = db.query(AdvisorAvailabilityBlock).filter(
        AdvisorAvailabilityBlock.advisor_id == current_user.id,
    ).order_by(AdvisorAvailabilityBlock.created_at.desc()).all()
    return blocks


@router.delete("/block/{block_id}")
def delete_block(
    block_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete an availability block."""
    block = db.query(AdvisorAvailabilityBlock).filter(
        AdvisorAvailabilityBlock.id == block_id,
        AdvisorAvailabilityBlock.advisor_id == current_user.id,
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    db.delete(block)
    db.commit()
    return {"deleted": True}


def _cancel_bookings_in_range(db: Session, advisor: User, start: date, end: date) -> list:
    """Cancel all bookings in a date range and notify leads via email/SMS."""
    from datetime import datetime as dt
    start_dt = dt(start.year, start.month, start.day, 0, 0, 0)
    end_dt   = dt(end.year, end.month, end.day, 23, 59, 59)

    bookings = db.query(BookingLink).filter(
        BookingLink.user_id == advisor.id,
        BookingLink.status == "booked",
        BookingLink.booked_time >= start_dt,
        BookingLink.booked_time <= end_dt,
    ).all()

    cancelled = []
    for booking in bookings:
        booking.status = "cancelled"
        lead = db.query(Lead).filter(Lead.id == booking.lead_id).first()
        if lead and lead.phone:
            try:
                from app.services.sms_service import send_raw_sms
                msg = (
                    f"Hi {lead.first_name or 'there'}, your appointment on "
                    f"{booking.booked_time.strftime('%A, %B %d at %-I:%M %p')} "
                    f"has been rescheduled. Your advisor will reach out to set a new time. "
                    f"We apologize for the inconvenience."
                )
                send_raw_sms(advisor, lead.phone, msg)
            except Exception:
                pass
        cancelled.append(booking.id)

    return cancelled
