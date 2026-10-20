"""Roofing conversion analytics - every number says what it is divided by.

POPULATION: the homeowners the caller may see (lead scope: an owner sees the
company, a salesperson sees their own book) who were IMPORTED inside the date
range. Every rate below is a share of a named subset of that population, and
the denominator travels with the number so the screen can print it.

A TOUCH is one outbound text or email that actually left (blocked or failed
sends are not touches). A reply is credited to the most recent touch before it.
Nothing here is estimated: an empty range returns zeros and has_data = False.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.models import BookingLink, Lead
from app.services.roofing import common as C
from app.services.roofing import journey as J


def _rate(n: int, d: int) -> Optional[float]:
    return round(100.0 * n / d, 1) if d else None


def _metric(key, label, value, denom=None, denom_label=None, hint=None):
    out = {"key": key, "label": label, "value": value, "hint": hint}
    if denom is not None:
        out.update({"denominator": denom, "denominator_label": denom_label,
                    "pct": _rate(value, denom)})
    return out


def parse_range(start: Optional[str], end: Optional[str], days: int = 30):
    today = datetime.utcnow().date()
    try:
        d_end = date.fromisoformat(end) if end else today
    except ValueError:
        d_end = today
    try:
        d_start = date.fromisoformat(start) if start else d_end - timedelta(days=days - 1)
    except ValueError:
        d_start = d_end - timedelta(days=days - 1)
    if d_start > d_end:
        d_start, d_end = d_end, d_start
    if (d_end - d_start).days > 366:
        d_start = d_end - timedelta(days=366)
    return d_start, d_end


def compute(db: Session, org, leads: List[Lead], d_start: date, d_end: date,
            now: Optional[datetime] = None) -> Dict:
    now = now or datetime.utcnow()
    lo = datetime(d_start.year, d_start.month, d_start.day)
    hi = datetime(d_end.year, d_end.month, d_end.day) + timedelta(days=1)
    cohort = [l for l in leads if l.created_at is not None and lo <= l.created_at < hi]
    ids = [l.id for l in cohort]
    sig = J.Signals(db, ids)
    opps = J.sync(db, org.id, cohort, now=now, sig=sig) if cohort else {}
    current = {o.id: o.stage for o in opps.values()}
    best = J.reached(db, org.id, list(current), current) if current else {}
    by_lead_rank = {lid: best.get(o.id, -1) for lid, o in opps.items()}

    def reached_n(stage):
        r = C.RANK[stage]
        return sum(1 for v in by_lead_rank.values() if v >= r)

    n = len(cohort)
    contacted = [i for i in ids if sig.contacted(i)]
    n_contacted = len(contacted)
    responded = [i for i in contacted if sig.replies.get(i)]
    engaged = [i for i in contacted if sig.engaged(i)]
    status = {l.id: l.status for l in cohort}
    opted = [i for i in ids if sig.opted_out(i) or status.get(i) == "dnc"]
    active_cutoff = now - timedelta(days=14)
    active = [i for i in engaged
              if opps[i].stage not in (C.WON, C.LOST)
              and any((r.received_at or datetime.min) >= active_cutoff for r in sig.replies[i])]

    offered_n = reached_n(C.OFFERED)
    booked_n = reached_n(C.BOOKED)
    est_n = reached_n(C.ESTIMATE_SENT)
    won_n = reached_n(C.WON)
    lost_n = sum(1 for o in opps.values() if o.stage == C.LOST)

    kpis = [
        _metric("imported", "Imported leads", n, hint="Homeowners imported in this date range"),
        _metric("contacted", "Contacted homeowners", n_contacted, n, "of imported leads",
                "At least one text or email actually went out"),
        _metric("active_conversations", "Active conversations", len(active), None, None,
                "Engaged homeowners who replied in the last 14 days and are still open"),
        _metric("response_rate", "Response rate", len(responded), n_contacted, "of contacted",
                "Replied at all, including opt-outs"),
        _metric("engagement", "Engagement", len(engaged), n_contacted, "of contacted",
                "Replied with something other than an opt-out or wrong number"),
        _metric("offered", "Appointments offered", offered_n, n, "of imported leads"),
        _metric("booked", "Inspections booked", booked_n, n, "of imported leads"),
        _metric("estimates", "Estimates sent", est_n, n, "of imported leads"),
        _metric("won", "Jobs won", won_n, n, "of imported leads"),
        _metric("opt_outs", "Opt-outs", len(opted), n_contacted, "of contacted",
                "Replied STOP or was marked do-not-contact"),
    ]

    funnel = []
    prev = None
    for key in C.ORDER:
        v = reached_n(key)
        funnel.append({"key": key, "label": C.STAGE_LABEL[key], "count": v,
                       "pct_of_imported": _rate(v, n),
                       "pct_of_previous": _rate(v, prev) if prev is not None else None})
        prev = v

    # Response by touch: 1, 2, 3, 4+.
    buckets = {k: {"touch": k, "received": 0, "replied": 0, "opted_out": 0} for k in ("1", "2", "3", "4+")}
    for i in contacted:
        times = sig.outbound[i]
        reps = sig.replies.get(i, [])
        for k, t in enumerate(times, start=1):
            nxt = times[k] if k < len(times) else None
            b = buckets[str(k) if k < 4 else "4+"]
            b["received"] += 1
            window = [r for r in reps if (r.received_at or datetime.min) >= t
                      and (nxt is None or (r.received_at or datetime.min) < nxt)]
            if any(r.classification not in J.OPT_OUT_CLASSES for r in window):
                b["replied"] += 1
            if any(r.classification in J.OPT_OUT_CLASSES for r in window):
                b["opted_out"] += 1
    by_touch = []
    for b in buckets.values():
        b["reply_rate"] = _rate(b["replied"], b["received"])
        b["opt_out_rate"] = _rate(b["opted_out"], b["received"])
        b["denominator_label"] = "homeowners who received touch %s" % b["touch"]
        by_touch.append(b)

    # Daily (or weekly, for long ranges) engagement trend.
    span = (d_end - d_start).days + 1
    weekly = span > 45
    trend_map = defaultdict(lambda: {"sent": 0, "replies": 0, "booked": 0})

    def bucket_key(dt: datetime) -> str:
        d = dt.date()
        if weekly:
            d = d - timedelta(days=d.weekday())
        return d.isoformat()

    for i in ids:
        for t in sig.outbound.get(i, ()):
            if lo <= t < hi:
                trend_map[bucket_key(t)]["sent"] += 1
        for r in sig.replies.get(i, ()):
            if r.received_at and lo <= r.received_at < hi and r.classification not in J.OPT_OUT_CLASSES:
                trend_map[bucket_key(r.received_at)]["replies"] += 1
        for b in sig.bookings.get(i, ()):
            if b.status in ("booked", "confirmed") and b.created_at and lo <= b.created_at < hi:
                trend_map[bucket_key(b.created_at)]["booked"] += 1
    trend = []
    d = d_start - timedelta(days=d_start.weekday()) if weekly else d_start
    step = timedelta(days=7 if weekly else 1)
    while d <= d_end:
        k = d.isoformat()
        trend.append({"period": k, **trend_map.get(k, {"sent": 0, "replies": 0, "booked": 0})})
        d += step

    booking = {
        "engaged": len(engaged), "offered": offered_n, "booked": booked_n,
        "offered_of_engaged": _rate(offered_n, len(engaged)),
        "booked_of_offered": _rate(booked_n, offered_n),
        "booked_of_engaged": _rate(booked_n, len(engaged)),
    }
    follow_up = {
        "touches_sent": sum(len(sig.outbound.get(i, ())) for i in ids),
        "avg_touches_per_contacted": round(sum(len(sig.outbound[i]) for i in contacted) / n_contacted, 1)
        if n_contacted else None,
        "opt_out_rate": _rate(len(opted), n_contacted),
        "lost": lost_n,
    }
    stage_now = defaultdict(int)
    for o in opps.values():
        stage_now[o.stage] += 1

    sample = bool(getattr(org, "is_demo", False)) or (n > 0 and all(bool(l.is_test) for l in cohort))
    return {
        "range": {"start": d_start.isoformat(), "end": d_end.isoformat(), "grain": "week" if weekly else "day"},
        "population": "Homeowners imported %s to %s that you can see" % (d_start.isoformat(), d_end.isoformat()),
        "has_data": n > 0,
        "sample_data": sample,
        "kpis": kpis,
        "funnel": funnel,
        "by_touch": by_touch,
        "trend": trend,
        "booking": booking,
        "follow_up": follow_up,
        "stage_now": [{"key": k, "label": C.STAGE_LABEL[k], "count": stage_now.get(k, 0)} for k in C.STAGE_KEYS],
    }
