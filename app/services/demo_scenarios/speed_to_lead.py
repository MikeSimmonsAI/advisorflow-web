"""Scenario 2 - Speed to lead. A new enquiry, answered in seconds.

THE SAME INFRASTRUCTURE, A DIFFERENT STORY. This scenario deliberately reuses
the customer-side tables and the same step machinery as the reactivation
scenario. There is no second communication engine here and no second scheduler
- the brief was explicit about that, and it is also the honest thing to
demonstrate: the product does not have a separate speed-to-lead system, it has
one engine that responds faster when the trigger is newer.

WHAT THIS SHOWS THAT REACTIVATION DOES NOT. Reactivation is about a cold list.
This is about the ninety seconds after a web form is submitted at 9pm, which is
the single most common reason a funeral home loses an enquiry. The records are
the same kinds; the timestamps are minutes apart instead of days.
"""

from datetime import datetime, timedelta

from app.models.models import (
    Organization, User, Lead, Message, Reply, CadenceState, BookingLink,
    ReplyClassification, EngagementTemperature,
)
from app.services.auth_service import hash_password
from app.services.demo_scenarios.base import (
    Scenario, Step, DOMAIN_CUSTOMER, demo_phone, demo_email,
)
from app.services.demo_scenarios.customer_reactivation import (
    DEMO_PASSWORD, CHI,
)


class SpeedToLead(Scenario):
    key = "speed_to_lead"
    name = "Speed to Lead - After-Hours Enquiry"
    domain = DOMAIN_CUSTOMER
    industry = "funeral"
    summary = ("A web enquiry at 9:14pm, answered automatically within a "
               "minute, in conversation before the office opens and booked "
               "the same night.")

    @property
    def org_id(self):
        return self.sid("org")

    @property
    def advisor_id(self):
        return self.sid("advisor")

    @property
    def lead_id(self):
        return self.sid("lead", "danforth")

    def seed(self, db, now: datetime) -> dict:
        # A SECOND, SEPARATE TENANT. Its existence is what lets the tenancy
        # tests prove that one demo funeral home cannot see another's families
        # - a claim that would be untestable with only one organization.
        org = Organization(
            id=self.org_id, name="Willow Bend Funeral Care",
            slug="demo-willow-bend", plan="standard", is_active=True,
            industry="funeral", brand_name="Willow Bend Funeral Care",
            org_address="811 Willow Bend Dr, Arlington, TX 76012",
            org_phone=demo_phone(300),
            appointment_types='["Arrangement Consultation", "Family File Review"]')
        db.add(org)
        db.flush()

        db.add(User(
            id=self.advisor_id, organization_id=self.org_id,
            email=demo_email("theo.marchetti", "example.com"),
            full_name="Theo Marchetti", role="advisor",
            password_hash=hash_password(DEMO_PASSWORD),
            must_change_password=False, is_active=True,
            booking_timezone=CHI, available_start_time="08:30",
            available_end_time="18:00", available_days="0,1,2,3,4,5",
            buffer_minutes=0, max_bookings_per_day=8))
        db.flush()

        # The enquiry landed last night at 9:14pm. Nobody was in the office.
        submitted = (now - timedelta(days=1)).replace(hour=21, minute=14,
                                                      second=0, microsecond=0)
        db.add(Lead(
            id=self.lead_id, organization_id=self.org_id,
            assigned_to_id=self.advisor_id,
            first_name="Priscilla", last_name="Danforth",
            phone=demo_phone(904), phone_raw="(555) 010-0904",
            email=demo_email("p.danforth", "example.com"),
            tier="at_need", contact_channel="sms", status="new",
            engagement_temperature=EngagementTemperature.HOT,
            source_file="willow-bend-website-form",
            last_action_raw="Website enquiry: 'need to make arrangements'",
            last_contact_date=submitted))
        db.commit()
        self._submitted = submitted
        return {"organization": org.name, "advisor": "Theo Marchetti",
                "lead": "Priscilla Danforth",
                "enquiry_at": submitted.strftime("%I:%M %p").lstrip("0")}

    def steps(self):
        return [
            Step("auto_respond", "Automatic first contact, 41 seconds later",
                 "Point at the two timestamps. The office was closed. Nobody "
                 "did this.",
                 self._auto_respond, provider="sms"),
            Step("reply", "She replies that night",
                 "The lead is already in conversation before anyone arrives "
                 "in the morning.",
                 self._reply, provider="sms"),
            Step("ai_qualify", "AI qualifies and offers times",
                 "The product asked the qualifying question a coordinator "
                 "would have asked nine hours later.",
                 self._ai_qualify, provider="sms"),
            Step("book", "She books the arrangement consultation",
                 "Booked at 9:23pm for the following morning. Show it in "
                 "Theo's My Day.",
                 self._book, provider="calendar"),
        ]

    def _submitted_at(self, db):
        lead = db.query(Lead).filter(Lead.id == self.lead_id).first()
        return lead.last_contact_date if lead else datetime.utcnow()

    def _auto_respond(self, db, now):
        t = self._submitted_at(db) + timedelta(seconds=41)
        db.add(CadenceState(
            id=self.sid("cadence"), lead_id=self.lead_id, status="active",
            current_touch_number=1, cadence_started_at=t,
            last_touch_sent_at=t, next_touch_due_at=t + timedelta(hours=12)))
        db.add(Message(
            id=self.sid("msg", "auto"), lead_id=self.lead_id,
            sender_id=self.advisor_id,
            body=("Hello Priscilla, this is Willow Bend Funeral Care. We've "
                  "received your enquiry and I'm very sorry for what you're "
                  "going through. Someone can speak with you tonight if that "
                  "helps, or first thing tomorrow - whichever you prefer."),
            twilio_sid="SIMULATED-DEMO-stl-auto", twilio_status="delivered",
            delivery_status="delivered", delivery_status_at=t, sent_at=t))
        lead = db.query(Lead).filter(Lead.id == self.lead_id).first()
        lead.status = "sent"
        db.commit()
        return "First contact at %s - 41 seconds after the form." % \
            t.strftime("%I:%M:%S %p").lstrip("0")

    def _reply(self, db, now):
        t = self._submitted_at(db) + timedelta(minutes=4)
        db.add(Reply(
            id=self.sid("reply", "1"), lead_id=self.lead_id,
            body=("Thank you for responding so quickly. My father passed this "
                  "evening. I don't know what I'm supposed to do first."),
            source="sms", twilio_sid="SIMULATED-DEMO-stl-reply",
            is_hot=True, hot_reason="At-need family, immediate arrangement",
            classification=ReplyClassification.INTERESTED,
            classification_confidence="high",
            classification_reasoning=("At-need enquiry with an immediate "
                                      "arrangement requirement."),
            received_at=t))
        state = db.query(CadenceState).filter(
            CadenceState.lead_id == self.lead_id).first()
        if state:
            state.status = "stopped_replied"
            state.completed_at = t
        lead = db.query(Lead).filter(Lead.id == self.lead_id).first()
        lead.status = "hot"
        db.commit()
        return "Reply received four minutes after first contact."

    def _ai_qualify(self, db, now):
        t = self._submitted_at(db) + timedelta(minutes=5)
        db.add(Message(
            id=self.sid("msg", "ai"), lead_id=self.lead_id,
            sender_id=self.advisor_id,
            body=("I'm so sorry, Priscilla. You don't need to work it out "
                  "tonight - we'll walk you through every step. Theo can sit "
                  "down with you tomorrow morning. Would nine o'clock or "
                  "eleven be easier?"),
            twilio_sid="SIMULATED-DEMO-stl-ai", twilio_status="delivered",
            delivery_status="delivered", delivery_status_at=t, sent_at=t))
        db.commit()
        return "AI qualified the enquiry and offered two times."

    def _book(self, db, now):
        submitted = self._submitted_at(db)
        booked_local = (submitted + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0)
        t = submitted + timedelta(minutes=9)
        db.add(BookingLink(
            id=self.sid("booking", "1"), lead_id=self.lead_id,
            user_id=self.advisor_id, token=self.sid("booking", "token"),
            status="booked", booked_time=booked_local,
            calendar_event_id="SIMULATED-DEMO-stl-event",
            confirmation_sent=True, created_at=t))
        lead = db.query(Lead).filter(Lead.id == self.lead_id).first()
        lead.status = "booked"
        db.commit()
        return ("Booked at %s for %s - nine minutes from enquiry to "
                "appointment." % (t.strftime("%I:%M %p").lstrip("0"),
                                  booked_local.strftime("%A %I:%M %p")))
