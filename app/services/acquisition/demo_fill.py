"""EXAMPLE DATA FOR A DEMO COMPANY (owner request, 2026-11-18).

Fills ONE service company - chosen by the platform owner, from God Mode -
with example records so a sales demo shows every client tool working:
booked appointments and reported results, customers and locations, a crew,
jobs (one finished, one today, recurring service), an estimate, and invoices
(one paid, one outstanding).

Every record goes through the same functions the real screens use, so it
behaves like real data. Every name says "(Example)". Nothing is sent: no
email, text, calendar invite or outreach - team members get a login with no
setup link handed out. Runs once per company (a second run reports what it
did the first time).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.models.acquisition_models import AcquisitionEvent, AcquisitionOpportunity
from app.models.models import BookingLink, Lead, Organization, User
from app.services.acquisition import common as C
from app.services.acquisition import engine as E

_log = logging.getLogger(__name__)
KIND = "demo_filled"
X = " (Example)"


def already(db: Session, org_id: str) -> bool:
    return db.query(AcquisitionEvent.id).filter(AcquisitionEvent.organization_id == org_id,
                                                AcquisitionEvent.kind == KIND).first() is not None


def _admin(db: Session, org: Organization) -> User:
    u = (db.query(User).filter(User.organization_id == org.id, User.is_active.isnot(False),
                               User.role == "org_admin").order_by(User.created_at.asc()).first())
    if u is None:
        raise ValueError("This company has no administrator login yet - invite one first")
    return u


def _wall(days: int, hour: int) -> datetime:
    """Appointment time in the slot engine's wall clock (how bookings are stored)."""
    from app.services.acquisition import acq_time as T
    now = T.convert(datetime.utcnow(), "UTC", T.ENGINE_TZ)
    return (now + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def fill(db: Session, org: Organization, actor: User) -> Dict[str, Any]:
    if already(db, org.id):
        return {"status": "already_filled", "done": [], "skipped": []}
    admin = _admin(db, org)
    done: List[str] = []
    skipped: List[str] = []

    def step(label, fn):
        try:
            out = fn()
            db.commit()
            done.append(label)
            return out
        except Exception as e:  # noqa: BLE001 - one part failing never blocks the rest
            db.rollback()
            _log.warning("demo fill step failed org=%s step=%s", org.id, label, exc_info=True)
            skipped.append("%s: %s" % (label, getattr(e, "detail", None) or str(e)[:160]))
            return None

    # ── appointments EvoSys "booked", and what came of them ─────────────────
    def appt(name, seg, city, days, hour, note):
        opp = E.add_prospect(db, org, actor, {"business_name": name + X, "segment": seg, "city": city,
                                               "website": None,
                                               "source_url": "https://example.com/listing", "is_practice": False,
                                               "contact_name": note.get("contact")})
        lead = db.query(Lead).get(opp.lead_id)
        if lead is not None and note.get("contact"):
            first, _, last = note["contact"].partition(" ")
            lead.first_name, lead.last_name = first, last or None
            if hasattr(lead, "phone") and not lead.phone:
                lead.phone = "555-01%02d" % (len(opps) + 10)
        bl = BookingLink(lead_id=opp.lead_id, user_id=admin.id, status="booked", booked_time=_wall(days, hour),
                         appt_label="Walkthrough", appt_duration=45, calendar_event_id="example-%s" % opp.id[:8])
        db.add(bl)
        db.flush()
        opp.booking_link_id = bl.id
        db.commit()
        E.owner_move(db, opp, actor, {"stage": C.INTERESTED, "note": "Example: wants a walkthrough"})
        E.owner_move(db, opp, actor, {"stage": C.BOOKED})
        return opp

    opps = {}
    plan = [
        ("upcoming1", "Poplar Medical Plaza", "medical_dental", "Mansfield", 1, 10, {"contact": "Dana Whitfield", "notes": "Nightly office cleaning, about 9,000 sq ft"}),
        ("upcoming2", "Riverside Professional Center", "office", "Arlington", 2, 14, {"contact": "Marcus Hale", "notes": "3 floors, recurring weekly"}),
        ("upcoming3", "Cedar Ridge Property Group", "property_management", "Grand Prairie", 3, 11, {"contact": "Lena Ortiz", "notes": "Move-out and common areas"}),
        ("held", "Union Dental Group", "medical_dental", "Mansfield", -6, 9, {"contact": "Dr. Patel", "notes": "Wants a quote for 3x weekly"}),
        ("quoted", "Midtown Law Offices", "office", "Arlington", -8, 15, {"contact": "Ruth Kim", "notes": "Evening cleaning, 2 suites"}),
        ("won_recurring", "Bartlett Family Dental", "medical_dental", "Mansfield", -12, 10, {"contact": "Amy Bartlett", "notes": "Recurring weekly cleaning"}),
        ("won_once", "Lakeside Lofts", "property_management", "Grand Prairie", -10, 13, {"contact": "Tom Reyes", "notes": "One-time post-construction clean"}),
    ]
    for key, name, seg, city, d, h, note in plan:
        opps[key] = step("Appointment: %s" % name, lambda n=name, s=seg, c=city, dd=d, hh=h, nn=note: appt(n, s, c, dd, hh, nn))

    def outcome(key, *bodies):
        o = opps.get(key)
        if o is None:
            raise ValueError("appointment missing")
        for b in bodies:
            E.client_outcome(db, o, admin, b)

    step("Result: visit held", lambda: outcome("held", {"outcome": C.HELD, "note": "Example: walked the site"}))
    step("Result: estimate sent", lambda: outcome("quoted", {"outcome": C.QUOTED, "quote_amount": 1800}))
    step("Result: recurring contract won", lambda: outcome(
        "won_recurring", {"outcome": C.HELD},
        {"outcome": C.WON, "contract_type": "recurring", "monthly_value": 2400,
         "contract_start": (datetime.utcnow() + timedelta(days=3)).strftime("%Y-%m-%d")}))
    step("Result: one-time contract won", lambda: outcome(
        "won_once", {"outcome": C.HELD},
        {"outcome": C.WON, "contract_type": "one_time", "contract_value": 3500,
         "contract_start": (datetime.utcnow() - timedelta(days=4)).strftime("%Y-%m-%d")}))

    def hours():
        from app.services.acquisition import calendar_setup as CAL
        cur = CAL.config(db, org)
        if any(cur.get("booking_windows", {}).values()):
            return   # the client already set real hours - keep them
        wk = [["09:00", "12:00"], ["13:00", "17:00"]]
        CAL.update(db, org, admin, {"booking_windows": {"mon": wk, "tue": wk, "wed": wk, "thu": wk, "fri": [["09:00", "15:00"]],
                                                         "sat": [], "sun": []},
                                    "appointment_minutes": 45, "min_notice_hours": 24, "buffer_minutes": 15,
                                    "meeting_location": "At the customer's building"})
    step("Example open hours (Mon-Fri)", hours)

    # ── the company's own operations (Service Operations) ───────────────────
    from app.services.service_ops import billing as BL
    from app.services.service_ops import core as K
    from app.services.service_ops import jobs as J
    from app.services.service_ops import recurring as R
    s = K.settings(db, org.id)
    if s is None or not s.ops_enabled:
        skipped.append("Service Operations is off for this company - jobs, team and invoices were not added")
    else:
        ctx = K.OpsCtx(db, admin, org, "company_admin", None, settings_row=s)
        step("Invoice details", lambda: K.save_settings(db, org.id, admin, {
            "business_name": org.name, "billing_address": "100 Example St, Mansfield, TX 76063",
            "billing_phone": "555-0100", "payment_terms_days": 15,
            "payment_instructions": "Example: pay by check to %s or by Zelle." % org.name}, entitlements=False))
        team = []
        for nm, role in (("Rosa Martinez", "team_lead"), ("Jamal Brooks", "field_worker")):
            r = step("Team: %s" % nm, lambda n=nm, ro=role: K.invite_member(ctx, {
                "full_name": n + X, "email": "%s.%s@example.com" % (n.split()[0].lower(), org.id[:6]), "role": ro}))
            if r:
                mid = r.get("id")
                if mid:
                    team.append(mid)
        db.commit()

        def convert(key, location, job):
            o = opps.get(key)
            if o is None:
                raise ValueError("contract missing")
            return J.convert_opportunity(ctx, o.id, {"location": location, "job": dict(job, assign_member_ids=team[:1])})

        def local(days, hour):
            return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0,
                                                                   microsecond=0).strftime("%Y-%m-%dT%H:%M")
        rec = step("Job: Bartlett Family Dental (recurring)", lambda: convert(
            "won_recurring", {"name": "Main office", "address": "200 Example Ave", "city": "Mansfield", "state": "TX",
                              "postal_code": "76063", "service_category": "cleaning", "standard_duration_min": 120,
                              "access_instructions": "Example: alarm code from the front desk"},
            {"local_start": local(3, 18), "duration_min": 120, "price": 600, "service_type": "Weekly office cleaning"}))
        if rec:
            step("Recurring service: weekly", lambda: R.create_schedule(ctx, {
                "location_id": rec["location_id"], "opportunity_id": opps["won_recurring"].id, "frequency": "weekly",
                "start_date": (datetime.now() + timedelta(days=10)).strftime("%Y-%m-%d"), "start_time": "18:00",
                "duration_min": 120, "price_per_visit": 600, "expected_monthly_value": 2400,
                "service_type": "Weekly office cleaning", "assigned_member_ids": team[:1]}))
        once = step("Job: Lakeside Lofts (finished)", lambda: convert(
            "won_once", {"name": "Building B", "address": "55 Example Blvd", "city": "Grand Prairie", "state": "TX",
                         "postal_code": "75050", "service_category": "cleaning"},
            {"local_start": local(-3, 9), "duration_min": 240, "price": 3500, "service_type": "Post-construction clean"}))
        if once and once.get("job"):
            jid = once["job"]["id"]
            def finish():
                J.start_job(ctx, jid)
                db.commit()
                from app.models.service_ops_models import ServiceJob
                job = db.query(ServiceJob).get(jid)
                for it in K.jload(job.checklist, []):
                    J.tick(ctx, jid, it.get("key"), True)
                db.commit()
                J.complete_job(ctx, jid, {
                    "notes": "Example: all units cleaned, windows and floors done. Customer walked through and signed off."})
                job = db.query(ServiceJob).get(jid)
                if job.scheduled_start:   # an example finished on time, not "late by days"
                    job.actual_start = job.scheduled_start + timedelta(minutes=5)
                    job.actual_end = job.scheduled_start + timedelta(minutes=(job.duration_min or 240))
                db.commit()
            step("Job finished with notes", finish)

            def bill():
                inv, _ = BL.create_invoice(ctx, {"job_id": jid})
                BL.issue_invoice(ctx, inv.id)
                p = BL.report_payment(ctx, inv.id, {"amount": 3500, "method": "check", "reference": "Example #1042"})
                BL.confirm_payment(ctx, inv.id, p.id, True)
            step("Invoice: paid", bill)

        def extra_customer():
            c = K.create_customer(ctx, {"name": "Harbor Point Clinic" + X, "kind": "business", "contact_name": "Grace Lee",
                                        "contact_email": "grace@example.com", "contact_phone": "555-0111",
                                        "billing_address": "9 Example Way, Arlington, TX 76010"})
            loc = K.create_location(ctx, c.id, {"name": "Clinic", "address": "9 Example Way", "city": "Arlington",
                                                "state": "TX", "postal_code": "76010", "service_category": "cleaning",
                                                "standard_duration_min": 90})
            db.commit()
            return c, loc
        hc = step("Customer: Harbor Point Clinic", extra_customer)
        if hc:
            c, loc = hc
            step("Job: today", lambda: J.new_job(ctx, {"location_id": loc.id, "local_start": local(0, 17), "duration_min": 90,
                                                        "price": 275, "service_type": "Evening clinic cleaning",
                                                        "assign_member_ids": team, "idempotency_key": "example-today"}))

            def estimate():
                est = BL.create_estimate(ctx, {"customer_id": c.id, "location_id": loc.id, "notes": "Example estimate",
                                               "lines": [{"description": "Nightly cleaning (monthly)", "quantity": 1, "rate": 1650},
                                                         {"description": "Floor strip and wax (one-time)", "quantity": 1, "rate": 450}]})
                db.commit()
                BL.set_estimate_status(ctx, est.id, "sent")
            step("Estimate: sent", estimate)

            def unpaid():
                inv, _ = BL.create_invoice(ctx, {"customer_id": c.id, "location_id": loc.id,
                                                 "lines": [{"description": "Deep clean - exam rooms", "quantity": 1, "rate": 480}]})
                BL.issue_invoice(ctx, inv.id)
            step("Invoice: outstanding", unpaid)

    db.add(AcquisitionEvent(organization_id=org.id, kind=KIND, actor_id=actor.id,
                            note="Example data added for a demo: %d parts" % len(done)))
    db.commit()
    return {"status": "filled", "done": done, "skipped": skipped}
