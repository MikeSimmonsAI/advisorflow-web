"""Scenario 1 - Lead reactivation into a booked family file review.

THE STORY. A funeral home has a family in its database from four years ago.
Nobody has spoken to them since. EvoSys Pro re-engages: a cadence touch goes
out, the family replies, the AI reads the reply and answers it, a voice agent
follows up, and the family ends up with an appointment on an advisor's
calendar. The advisor sees it in My Day. Every step is a record the product
already renders.

WHAT IS REAL AND WHAT IS SIMULATED
----------------------------------
The RECORDS are real: `Lead`, `CadenceState`, `Message`, `Reply`,
`EmailMessage`, `VoiceCall`, `BookingLink` - the same rows the live product
writes, in the same tables its timeline reads. Nothing here is a special demo
shape.

The TRANSPORT is what is simulated. `sms_service.send_sms()` cannot be called,
because it builds its `Message` row out of `twilio_msg.sid` - the send and the
record are one statement. So these steps write the row directly with a
simulated provider id, and the firewall guarantees that no code path anywhere
reached a provider while doing it. The rows are indistinguishable from real
ones except for their `demo-` ids and their reserved 555-01xx contact details.

WHY THE INDUSTRY LIVES IN THIS FILE AND NOWHERE ELSE
----------------------------------------------------
Every funeral-specific word - the family's situation, the advisor's script,
the appointment name - is a string in THIS module. The scenario engine, the
step machinery, the reset and the control panel know nothing about funerals. A
roofing scenario is a sibling file with different strings and the same
structure; it needs no change to any service.
"""

from datetime import datetime, timedelta

from app.models.models import (
    Organization, User, Lead, Message, Reply, EmailMessage, CadenceState,
    BookingLink, VoiceCall, ReplyClassification, EngagementTemperature,
)
from app.services.auth_service import hash_password
from app.services.demo_scenarios.base import (
    Scenario, Step, DOMAIN_CUSTOMER, demo_phone, demo_email,
)

# The one password every demo identity uses. It only exists in the demo
# database, which holds no real person's data, and it is documented openly in
# claude/EVOSYS_DEMO_MODE.md so an operator never has to ask for it.
DEMO_PASSWORD = "EvoDemo2026!"
CHI = "America/Chicago"


class CustomerReactivation(Scenario):
    key = "customer_reactivation"
    name = "Lead Reactivation - Family File Review"
    domain = DOMAIN_CUSTOMER
    industry = "funeral"
    summary = ("A four-year-old family record with no recent activity, "
               "re-engaged through cadence, AI reply handling and a voice "
               "call, ending in a booked in-person review.")

    # ── ids, fixed so reset is exact and re-seeding cannot duplicate ────────

    @property
    def org_id(self):
        return self.sid("org")

    @property
    def advisor_id(self):
        return self.sid("advisor")

    @property
    def lead_id(self):
        return self.sid("lead", "harrelson")

    # ── the starting world ──────────────────────────────────────────────────

    def seed(self, db, now: datetime) -> dict:
        org = Organization(
            id=self.org_id,
            name="Cedar Hollow Memorial",
            slug="demo-cedar-hollow",
            plan="standard", is_active=True, industry="funeral",
            brand_name="Cedar Hollow Memorial",
            org_address="4120 Cedar Hollow Rd, Plano, TX 75024",
            org_phone=demo_phone(200),
            appointment_types='["Family File Review", "Pre-Need Consultation", '
                              '"Marker Selection"]',
        )
        db.add(org)
        db.flush()

        advisor = User(
            id=self.advisor_id, organization_id=self.org_id,
            email=demo_email("rachel.nunez", "example.com"),
            full_name="Rachel Nunez", role="advisor",
            password_hash=hash_password(DEMO_PASSWORD),
            must_change_password=False, is_active=True,
            booking_timezone=CHI,
            available_start_time="09:00", available_end_time="17:00",
            available_days="0,1,2,3,4",
            buffer_minutes=15, max_bookings_per_day=6,
        )
        db.add(advisor)

        manager = User(
            id=self.sid("manager"), organization_id=self.org_id,
            email=demo_email("dale.whitfield", "example.com"),
            full_name="Dale Whitfield", role="org_admin",
            password_hash=hash_password(DEMO_PASSWORD),
            must_change_password=False, is_active=True,
            booking_timezone=CHI,
        )
        db.add(manager)
        db.flush()

        # THE FAMILY. Cold, four years untouched - the record a funeral home
        # has thousands of and never calls.
        lead = Lead(
            id=self.lead_id, organization_id=self.org_id,
            assigned_to_id=self.advisor_id,
            first_name="Marguerite", last_name="Harrelson",
            phone=demo_phone(431), phone_raw="(555) 010-0431",
            email=demo_email("m.harrelson", "example.com"),
            tier="pre_need", contact_channel="sms", status="new",
            engagement_temperature=EngagementTemperature.COLD,
            source_year=2022,
            source_file="cedar-hollow-preneed-2022.csv",
            last_action_raw="Called: Left voicemail",
            last_contact_date=now - timedelta(days=1487),
            status_reason_raw="Attempting Contact",
        )
        db.add(lead)

        # Two more families so a queue is not a single row, and so the
        # dashboard numbers are computed from something plural.
        db.add(Lead(
            id=self.sid("lead", "okonkwo"), organization_id=self.org_id,
            assigned_to_id=self.advisor_id,
            first_name="Adaeze", last_name="Okonkwo",
            phone=demo_phone(512), email=demo_email("a.okonkwo", "example.com"),
            tier="pre_need", contact_channel="sms", status="new",
            engagement_temperature=EngagementTemperature.COLD,
            source_year=2021, source_file="cedar-hollow-preneed-2021.csv",
            last_contact_date=now - timedelta(days=1702)))
        db.add(Lead(
            id=self.sid("lead", "bianchi"), organization_id=self.org_id,
            assigned_to_id=self.advisor_id,
            first_name="Sal", last_name="Bianchi",
            phone=demo_phone(688), email=demo_email("s.bianchi", "example.com"),
            tier="at_need", contact_channel="sms", status="new",
            engagement_temperature=EngagementTemperature.WARM,
            source_year=2024, source_file="cedar-hollow-web-2024.csv",
            last_contact_date=now - timedelta(days=41)))
        db.commit()

        return {"organization": org.name, "advisor": advisor.full_name,
                "lead": "%s %s" % (lead.first_name, lead.last_name)}

    # ── the running order ───────────────────────────────────────────────────

    def steps(self):
        return [
            Step("enroll", "Enrol the family in a reactivation cadence",
                 "Open the lead. The cadence panel now shows an active "
                 "sequence with touch 1 due.",
                 self._enroll),
            Step("touch_1", "Send touch 1 (SMS)",
                 "The message appears in the conversation as sent. Nothing "
                 "left this machine - the firewall log will show you.",
                 self._touch_one, provider="sms"),
            Step("family_reply", "The family replies",
                 "An inbound message lands and the lead turns hot. Point at "
                 "the classification - the product read it, nobody tagged it.",
                 self._family_reply, provider="sms"),
            Step("ai_response", "AI answers the reply",
                 "The AI response is in the same thread, seconds after the "
                 "inbound. This is where a human would otherwise be typing.",
                 self._ai_response, provider="sms"),
            Step("email_touch", "Follow up by email",
                 "The conversation is multi-channel now. The timeline "
                 "interleaves both without anyone stitching them together.",
                 self._email_touch, provider="email"),
            Step("voice_call", "Taffiney calls the family",
                 "Open the call record for the transcript. This is what the "
                 "live Retell integration produces - simulated here.",
                 self._voice_call, provider="voice"),
            Step("book", "Book the family file review",
                 "The appointment is on Rachel's calendar and in her My Day. "
                 "Availability came from the real scheduling engine.",
                 self._book, provider="calendar"),
            Step("outcome", "Record the result",
                 "The family is booked, the cadence stopped itself, and the "
                 "whole history reads in order on one screen.",
                 self._outcome),
        ]

    # ── step handlers ───────────────────────────────────────────────────────

    def _lead(self, db):
        return db.query(Lead).filter(Lead.id == self.lead_id).first()

    def _enroll(self, db, now):
        db.add(CadenceState(
            id=self.sid("cadence"), lead_id=self.lead_id,
            status="active", current_touch_number=0,
            cadence_started_at=now,
            next_touch_due_at=now + timedelta(minutes=5)))
        lead = self._lead(db)
        lead.engagement_temperature = EngagementTemperature.WARM
        db.commit()
        return "Cadence started for Marguerite Harrelson."

    def _touch_one(self, db, now):
        db.add(Message(
            id=self.sid("msg", "touch1"), lead_id=self.lead_id,
            sender_id=self.advisor_id,
            body=("Hi Marguerite, this is Rachel at Cedar Hollow Memorial. "
                  "We're reviewing family files to make sure the details we "
                  "have on record are still current. Would you have a few "
                  "minutes this week?"),
            # A simulated provider id, shaped like a real one so the UI renders
            # identically, and clearly marked so nobody mistakes it for a
            # Twilio SID in a support ticket.
            twilio_sid="SIMULATED-DEMO-touch1",
            twilio_status="delivered", delivery_status="delivered",
            delivery_status_at=now, sent_at=now))
        state = db.query(CadenceState).filter(
            CadenceState.lead_id == self.lead_id).first()
        if state:
            state.current_touch_number = 1
            state.last_touch_sent_at = now
            state.next_touch_due_at = now + timedelta(days=3)
        lead = self._lead(db)
        lead.status = "sent"
        db.commit()
        return "Touch 1 delivered."

    def _family_reply(self, db, now):
        db.add(Reply(
            id=self.sid("reply", "1"), lead_id=self.lead_id,
            body=("It has probably been a few years since we looked at any of "
                  "it. My husband handled that side of things."),
            source="sms", twilio_sid="SIMULATED-DEMO-reply1",
            is_hot=True, hot_reason="Engaged and open to a conversation",
            classification=ReplyClassification.INTERESTED,
            classification_confidence="high",
            classification_reasoning=(
                "Acknowledges the file is out of date and volunteers context "
                "rather than declining. No opt-out language."),
            received_at=now))
        lead = self._lead(db)
        lead.status = "hot"
        lead.engagement_temperature = EngagementTemperature.HOT
        state = db.query(CadenceState).filter(
            CadenceState.lead_id == self.lead_id).first()
        if state:
            # The real product stops a cadence the moment somebody replies.
            state.status = "stopped_replied"
            state.completed_at = now
        db.commit()
        return "Inbound reply classified as INTERESTED. Cadence stopped."

    def _ai_response(self, db, now):
        db.add(Message(
            id=self.sid("msg", "ai1"), lead_id=self.lead_id,
            sender_id=self.advisor_id,
            body=("That's very common, and it's exactly why we reach out. The "
                  "simplest way to bring everything up to date is to sit down "
                  "together for about half an hour - no cost, and nothing you "
                  "need to prepare. Would a weekday morning or an afternoon "
                  "suit you better?"),
            twilio_sid="SIMULATED-DEMO-ai1",
            twilio_status="delivered", delivery_status="delivered",
            delivery_status_at=now, sent_at=now))
        db.commit()
        return "AI response sent in the same thread."

    def _email_touch(self, db, now):
        db.add(EmailMessage(
            id=self.sid("email", "1"), lead_id=self.lead_id,
            sender_id=self.advisor_id,
            subject="Bringing your family file up to date",
            body_html=(
                "<p>Hello Marguerite,</p>"
                "<p>Following on from our text - I've put aside some time this "
                "week to go through your family file with you and make sure "
                "everything on record is still accurate.</p>"
                "<p>The review takes about half an hour and there's nothing "
                "you need to bring. If it's easier to talk it through first, "
                "just reply here.</p>"
                "<p>Warm regards,<br>Rachel Nunez<br>Cedar Hollow Memorial</p>"),
            provider_message_id="SIMULATED-DEMO-email1",
            status="sent", sent_at=now))
        db.commit()
        return "Email touch recorded on the same lead."

    def _voice_call(self, db, now):
        # VoiceCall is the one path with a genuine seam - voice_router writes
        # this row and commits BEFORE calling out, and voice_service writes no
        # rows at all. So a seeded call record is exactly the shape a real one
        # takes. Note there is no `summary` column on this model; the narrative
        # lives in `transcript` and the result in `outcome`.
        db.add(VoiceCall(
            id=self.sid("voice", "1"), lead_id=self.lead_id,
            advisor_id=self.advisor_id, organization_id=self.org_id,
            call_sid="SIMULATED-DEMO-call1",
            to_phone=demo_phone(431), from_phone=demo_phone(200),
            call_number=1, status="completed", twilio_status="completed",
            outcome="booking_requested",
            duration_seconds=196,
            booking_url_sent=True,
            transcript=(
                "Taffiney: Good morning, is this Marguerite? This is Taffiney "
                "calling on behalf of Cedar Hollow Memorial.\n"
                "Marguerite: Yes, speaking.\n"
                "Taffiney: We're reviewing our family files to make sure "
                "everything is current and complete. I can see it's been a "
                "little while since yours was looked at.\n"
                "Marguerite: It has probably been a few years. My husband "
                "handled that side of things, and he passed in 2023.\n"
                "Taffiney: I'm very sorry to hear that. That's often when a "
                "file needs the most attention, and it's not something you "
                "should have to work through on your own.\n"
                "Marguerite: No, I wouldn't know where to start.\n"
                "Taffiney: The best way to make sure everything is accurate is "
                "to sit down together and review the file properly. It takes "
                "about half an hour and there's no cost. Rachel handles those "
                "reviews - would a morning or an afternoon suit you better?\n"
                "Marguerite: Mornings are better. I'm usually free before "
                "lunch.\n"
                "Taffiney: Let me look at what Rachel has. I have Tuesday at "
                "ten, or Thursday at eleven.\n"
                "Marguerite: Tuesday at ten would work.\n"
                "Taffiney: Wonderful. I'll book that in and you'll get a "
                "confirmation shortly. Thank you Marguerite."),
            # NOT backdated. An earlier version started this call four minutes
            # before `now`, which placed it before the SMS exchange that
            # preceded it in the story and made the lead's timeline read out of
            # order. A step's records belong at the moment the step ran.
            started_at=now, ended_at=now + timedelta(seconds=196),
            created_at=now))
        db.commit()
        return "Voice call recorded with transcript and booking intent."

    def _book(self, db, now):
        # Next weekday at 10:00 local, which is inside Rachel's seeded hours.
        target = (now + timedelta(days=1)).date()
        while target.weekday() >= 5:
            target = target + timedelta(days=1)
        booked_local = datetime(target.year, target.month, target.day, 10, 0)

        db.add(BookingLink(
            id=self.sid("booking", "1"), lead_id=self.lead_id,
            user_id=self.advisor_id,
            token=self.sid("booking", "token", "1"),
            status="booked",
            # Naive LOCAL wall time, matching every other tenant booking in
            # this table - see app/services/tenant_scheduling.py.
            booked_time=booked_local,
            calendar_event_id="SIMULATED-DEMO-calendar-event-1",
            confirmation_sent=True,
            created_at=now))
        lead = self._lead(db)
        lead.status = "booked"
        db.commit()
        return "Family File Review booked for %s." % booked_local.strftime(
            "%A %d %B at %I:%M %p").replace(" 0", " ")

    def _outcome(self, db, now):
        db.add(Message(
            id=self.sid("msg", "confirm"), lead_id=self.lead_id,
            sender_id=self.advisor_id,
            body=("You're all set, Marguerite. Rachel will see you at Cedar "
                  "Hollow Memorial, 4120 Cedar Hollow Rd. If anything "
                  "changes, just reply here."),
            twilio_sid="SIMULATED-DEMO-confirm",
            twilio_status="delivered", delivery_status="delivered",
            delivery_status_at=now, sent_at=now))
        db.commit()
        return ("Confirmation sent. One cold four-year-old record is now a "
                "booked appointment.")
