"""
Reports / Analytics router.

Per Mike's explicit request: a dedicated space to look at "conversions
versus engagements" and revenue, with the ability to pick a date range -
something NO existing analytics endpoint supported (admin_router.py's
dashboard/metrics, dashboard/funnel, dashboard/revenue, and
leads_router.py's engagement-breakdown/status-funnel are all all-time
totals with zero filtering).

This is intentionally additive, not a replacement: the existing
dashboard endpoints stay exactly as they are (Admin.jsx's tabs and
Overview.jsx's charts keep working unchanged). This module adds:
  1. A shared date-range query pattern other reports can reuse.
  2. A conversion trend over time (replies -> booked -> sold, by day).
  3. An engagement-vs-conversion comparison per advisor (are they
     getting replies but not closing, or closing well despite low
     engagement - two very different coaching conversations).

REVENUE HONESTY CONSTRAINT, same as admin_router.py's dashboard/revenue:
LeadOutcome.sale_amount is a free-text sales note an advisor types in,
not a structured currency column (see that column's own comment in
models.py). This module reports SALE COUNTS only, never a parsed/summed
dollar total - matching the constraint already established and tested
in dashboard_revenue.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, distinct
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models.models import (
    User, Lead, LeadStatus, Message, Reply, ReplyClassification,
    BookingLink, LeadOutcome,
)

router = APIRouter(prefix="/reports", tags=["reports"])

HOT_REPLY_CLASSIFICATIONS = (ReplyClassification.INTERESTED, ReplyClassification.CALLBACK)


def _resolve_date_range(start_date: Optional[str], end_date: Optional[str]) -> tuple[datetime, datetime]:
    """
    Shared date-range resolution for every report endpoint below.

    Defaults to the last 30 days (not all-time) when nothing is
    specified, since "what's been happening lately" is the far more
    common question than "show me everything since the beginning" - an
    admin can always widen the range explicitly.

    Dates are accepted as plain YYYY-MM-DD strings (what a native HTML
    date input sends) rather than full ISO timestamps, since this is
    meant to be driven by a simple date-range picker on the frontend,
    not a developer hand-crafting ISO strings.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if end_date:
        try:
            end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            raise HTTPException(status_code=400, detail="end_date must be in YYYY-MM-DD format")
    else:
        end = now

    if start_date:
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="start_date must be in YYYY-MM-DD format")
    else:
        start = end - timedelta(days=30)

    if start > end:
        raise HTTPException(status_code=400, detail="start_date must be before end_date")

    return start, end


@router.get("/conversion-trend")
def conversion_trend(
    start_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to 30 days before end_date"),
    end_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to today"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Day-by-day conversion trend across the selected date range: how many
    replies came in, how many of those were hot, how many bookings
    happened, how many sales closed - one row per day so it can render
    as a line/bar chart showing the shape of the funnel over time, not
    just a single all-time total.

    Each metric is counted on ITS OWN natural date, not all anchored to
    lead-creation date: a reply counts on the day it was received, a
    booking on the day it was booked, a sale on the day the outcome was
    recorded. This means a single lead can contribute to multiple
    different days across the trend, which is correct - the trend is
    answering "what happened on this day," not "what happened to leads
    created on this day."
    """
    start, end = _resolve_date_range(start_date, end_date)
    org_id = current_user.organization_id

    replies = (
        db.query(Reply, Lead.id)
        .join(Lead, Reply.lead_id == Lead.id)
        .filter(Lead.organization_id == org_id, Reply.received_at >= start, Reply.received_at <= end)
        .all()
    )
    bookings = (
        db.query(BookingLink)
        .join(Lead, BookingLink.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id, BookingLink.status == "booked",
            BookingLink.booked_time.isnot(None), BookingLink.booked_time >= start, BookingLink.booked_time <= end,
        )
        .all()
    )
    sales = (
        db.query(LeadOutcome)
        .join(Lead, LeadOutcome.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id, LeadOutcome.resulted_in_sale == True,
            LeadOutcome.created_at >= start, LeadOutcome.created_at <= end,
        )
        .all()
    )

    by_day: dict[str, dict[str, int]] = {}

    def _bucket(day_key: str) -> dict[str, int]:
        return by_day.setdefault(day_key, {"replies": 0, "hot_replies": 0, "booked": 0, "sold": 0})

    for reply, _lead_id in replies:
        if reply.received_at:
            day = reply.received_at.strftime("%Y-%m-%d")
            _bucket(day)["replies"] += 1
            if reply.classification in HOT_REPLY_CLASSIFICATIONS or reply.is_hot:
                _bucket(day)["hot_replies"] += 1

    for booking in bookings:
        day = booking.booked_time.strftime("%Y-%m-%d")
        _bucket(day)["booked"] += 1

    for outcome in sales:
        day = (outcome.created_at or start).strftime("%Y-%m-%d")
        _bucket(day)["sold"] += 1

    trend = [
        {"date": day, **counts}
        for day, counts in sorted(by_day.items())
    ]

    return {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "trend": trend,
        "totals": {
            "replies": sum(d["replies"] for d in by_day.values()),
            "hot_replies": sum(d["hot_replies"] for d in by_day.values()),
            "booked": sum(d["booked"] for d in by_day.values()),
            "sold": sum(d["sold"] for d in by_day.values()),
        },
    }


@router.get("/engagement-vs-conversion")
def engagement_vs_conversion(
    start_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to 30 days before end_date"),
    end_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to today"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Per-advisor comparison of engagement (are people responding to them)
    against conversion (are those responses turning into bookings and
    sales) - two genuinely different signals that get muddied together
    in a single "messages sent" number. An advisor with a high hot-reply
    rate but a low booking rate is a different coaching conversation
    than one with the reverse, and this surfaces that distinction
    directly instead of requiring someone to mentally cross-reference
    two separate tables.

    Scoped by the messages SENT within the date range (the activity that
    happened in this window), then engagement/conversion are measured
    against THOSE specific leads' downstream replies/bookings/sales,
    regardless of whether those downstream events also fall inside the
    window - a message sent on day 29 of a 30-day window might not get
    a reply until day 32, and that reply should still count toward this
    advisor's engagement rate for the leads they worked in this window.
    """
    start, end = _resolve_date_range(start_date, end_date)
    org_id = current_user.organization_id

    advisors = db.query(User).filter(User.organization_id == org_id, User.role.in_(["advisor", "org_admin"])).all()

    rows = []
    for advisor in advisors:
        messaged_lead_ids = (
            db.query(distinct(Message.lead_id))
            .join(Lead, Message.lead_id == Lead.id)
            .filter(Lead.organization_id == org_id, Message.sender_id == advisor.id,
                     Message.sent_at >= start, Message.sent_at <= end)
            .all()
        )
        messaged_lead_ids = [row[0] for row in messaged_lead_ids]
        messaged_count = len(messaged_lead_ids)

        if messaged_count == 0:
            rows.append({
                "advisor_id": advisor.id, "advisor_name": advisor.full_name,
                "leads_messaged": 0, "replies": 0, "hot_replies": 0,
                "booked": 0, "sold": 0,
                "engagement_rate": 0.0, "conversion_rate": 0.0,
            })
            continue

        replied_count = (
            db.query(func.count(distinct(Reply.lead_id)))
            .filter(Reply.lead_id.in_(messaged_lead_ids))
            .scalar() or 0
        )
        hot_replied_count = (
            db.query(func.count(distinct(Reply.lead_id)))
            .filter(
                Reply.lead_id.in_(messaged_lead_ids),
                (Reply.classification.in_(HOT_REPLY_CLASSIFICATIONS)) | (Reply.is_hot == True),
            )
            .scalar() or 0
        )
        booked_count = (
            db.query(func.count(distinct(BookingLink.lead_id)))
            .filter(BookingLink.lead_id.in_(messaged_lead_ids), BookingLink.status == "booked")
            .scalar() or 0
        )
        sold_count = (
            db.query(func.count(distinct(LeadOutcome.lead_id)))
            .filter(LeadOutcome.lead_id.in_(messaged_lead_ids), LeadOutcome.resulted_in_sale == True)
            .scalar() or 0
        )

        rows.append({
            "advisor_id": advisor.id,
            "advisor_name": advisor.full_name,
            "leads_messaged": messaged_count,
            "replies": replied_count,
            "hot_replies": hot_replied_count,
            "booked": booked_count,
            "sold": sold_count,
            "engagement_rate": round((replied_count / messaged_count) * 100, 1),
            "conversion_rate": round((booked_count / messaged_count) * 100, 1),
        })

    rows.sort(key=lambda r: r["leads_messaged"], reverse=True)

    return {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "advisors": rows,
    }


@router.get("/revenue-by-period")
def revenue_by_period(
    start_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to 30 days before end_date"),
    end_date: Optional[str] = Query(default=None, description="YYYY-MM-DD, defaults to today"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """
    Date-range-filterable version of admin_router.py's dashboard_revenue -
    same sale-count-not-dollar-total constraint, just scoped to a
    specific window so an admin can compare e.g. this month vs last
    month, which the existing all-time-only endpoint can't do.

    Counts sales by outcome.created_at (when the sale was actually
    logged), not appointment_date, since appointment_date can be in the
    future relative to when the sale was recorded and is sometimes null.
    """
    start, end = _resolve_date_range(start_date, end_date)
    org_id = current_user.organization_id

    sale_outcomes = (
        db.query(LeadOutcome)
        .join(Lead, LeadOutcome.lead_id == Lead.id)
        .filter(
            Lead.organization_id == org_id, LeadOutcome.resulted_in_sale == True,
            LeadOutcome.created_at >= start, LeadOutcome.created_at <= end,
        )
        .all()
    )

    sales_by_advisor: dict[str, int] = {}
    for outcome in sale_outcomes:
        sales_by_advisor[outcome.recorded_by_id] = sales_by_advisor.get(outcome.recorded_by_id, 0) + 1

    advisor_ids = list(sales_by_advisor.keys())
    advisors_by_id = {}
    if advisor_ids:
        advisors_by_id = {a.id: a.full_name for a in db.query(User).filter(User.id.in_(advisor_ids)).all()}

    by_advisor = sorted(
        [
            {"advisor_id": advisor_id, "advisor_name": advisors_by_id.get(advisor_id, "Unknown"), "sale_count": count}
            for advisor_id, count in sales_by_advisor.items()
        ],
        key=lambda row: row["sale_count"],
        reverse=True,
    )

    product_mix = {
        "funeral_arrangement": sum(1 for o in sale_outcomes if o.has_funeral_arrangement),
        "cemetery_property": sum(1 for o in sale_outcomes if o.has_cemetery_property),
        "marker": sum(1 for o in sale_outcomes if o.has_marker),
        "memorial": sum(1 for o in sale_outcomes if o.has_memorial),
    }

    return {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "total_sales": len(sale_outcomes),
        "by_advisor": by_advisor,
        "product_mix": product_mix,
    }
