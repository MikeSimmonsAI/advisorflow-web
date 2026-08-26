"""Scenario 3 - EvoSys Pro selling its own software, discovery to Won.

THE OTHER TREE. Nothing in this file touches a Lead, an Organization or a
Message. It works in `BrandSalesOrg -> Opportunity -> Proposal -> PortalEvent`,
which is a separate tenancy domain with separate permissions. The two scenarios
tell adjacent stories and share no row.

DRIVEN THROUGH THE REAL SERVICES, NOT AROUND THEM
-------------------------------------------------
`proposal_service.create_proposal`, `apply_pricing`, `publish_proposal` and
`pricing_approvals.create_request` are called exactly as the product calls
them. That is the point: the resulting records passed the same validation a
rep's click would trigger, so the demo cannot show a state the product could
not actually produce. `scripts/demo_env.py` established this discipline and
this scenario keeps it.

The one thing that is NOT the real path is delivery. `send_proposal(...,
dry_run=True)` publishes the proposal and issues its access key without
claiming anybody received it - which is exactly right, because nobody did.
The "sent" status is then set here, in this file's own name, rather than by
leaning on a preview to fake it.

BUYER ACTIVITY IS REAL PORTAL EVENTS
------------------------------------
`PortalEvent` rows are what the live deal room writes when a buyer opens a
proposal. Seeding those means the buyer-activity timeline, the "first viewed"
stamp and the manager's engagement signals all compute from the same data the
real thing computes from - no hardcoded "viewed 3 times" anywhere.
"""

from datetime import datetime, timedelta

from app.models.models import (
    Platform, User, PortalEvent, ProposalBlock,
    PROP_SENT, PROP_VIEWED,
    PORTAL_OPENED, PORTAL_PROPOSAL_VIEWED, PORTAL_DEMO_OPENED,
)
from app.models.sales_models import (
    BrandSalesOrg, Membership, Opportunity, BrandPackage, DiscoveryRecord,
    SCOPE_BRAND_SALES_ORG, ROLE_SALES_MANAGER, ROLE_SALES_REP,
)
from app.models.scheduling_models import (
    MeetingType, SalesAppointment, AppointmentParticipant,
)
from app.services.auth_service import hash_password
from app.services.meeting_roles import ensure_meeting_types
from app.services import proposal_service as ps
from app.services.demo_scenarios.base import (
    Scenario, Step, DOMAIN_BRAND, demo_phone, demo_email,
)
from app.services.demo_scenarios.customer_reactivation import DEMO_PASSWORD

CHI = "America/Chicago"


class BrandSalesCycle(Scenario):
    key = "brand_sales"
    name = "EvoSys Pro B2B Sales Cycle"
    domain = DOMAIN_BRAND
    industry = "software"
    summary = ("A prospect from discovery through demo, proposal, secure deal "
               "room, buyer activity, a change request and acceptance, to a "
               "closed-won deal.")

    @property
    def platform_id(self):
        return self.sid("platform")

    @property
    def brand_id(self):
        return self.sid("brand")

    @property
    def rep_id(self):
        return self.sid("rep")

    @property
    def manager_id(self):
        return self.sid("manager")

    @property
    def opp_id(self):
        return self.sid("opp", "brightwater")

    def seed(self, db, now: datetime) -> dict:
        db.add(Platform(id=self.platform_id, name="EvoSys Pro",
                        slug="demo-evosyspro"))
        db.flush()
        db.add(BrandSalesOrg(
            id=self.brand_id, platform_id=self.platform_id,
            name="EvoSys Pro Sales", slug="demo-evosyspro-sales",
            timezone=CHI))
        db.flush()

        for key, name, price, desc in (
                ("starter", "Starter", 1497, None),
                ("growth", "Growth", 2495, None),
                ("professional", "Professional", 4995,
                 "The full platform, configured for you.")):
            db.add(BrandPackage(
                id=self.sid("pkg", key), platform_id=self.platform_id,
                key=key, name=name, price=price, currency="USD",
                description=desc))
        db.flush()

        def staff(uid, email, name):
            # organization_id is NULL by design - brand-sales staff have no
            # customer tenant. See claude/SALES_WORKSPACE_ARCHITECTURE.md.
            u = User(id=uid, organization_id=None, email=email, full_name=name,
                     password_hash=hash_password(DEMO_PASSWORD), role="advisor",
                     must_change_password=False, is_active=True)
            db.add(u)
            return u

        staff(self.rep_id, demo_email("tobias.reyner", "example.com"),
              "Tobias Reyner")
        staff(self.manager_id, demo_email("priya.raman", "example.com"),
              "Priya Raman")
        staff(self.sid("rep2"), demo_email("nadia.okoye", "example.com"),
              "Nadia Okoye")
        db.flush()

        for uid, role in ((self.rep_id, ROLE_SALES_REP),
                          (self.sid("rep2"), ROLE_SALES_REP),
                          (self.manager_id, ROLE_SALES_MANAGER)):
            db.add(Membership(
                id=self.sid("mem", uid[-8:]), user_id=uid,
                scope_type=SCOPE_BRAND_SALES_ORG, scope_id=self.brand_id,
                role=role, is_active=True))
        db.commit()

        ensure_meeting_types(db, self.brand_id)
        db.commit()

        # THE HEADLINE DEAL - discovery already done, demo ready, no proposal
        # yet. That is the state a rep is actually in when they open the app.
        opp = Opportunity(
            id=self.opp_id, brand_sales_org_id=self.brand_id,
            owner_user_id=self.rep_id,
            company_name="Brightwater Funeral Group",
            contact_name="Eleanor Vance",
            email=demo_email("e.vance", "example.com"),
            phone=demo_phone(770),
            industry="Memorial services",
            stage="demo", status="open",
            selected_package_id=self.sid("pkg", "professional"),
            demo_url="https://demo.evosyspro.live/brightwater",
            demo_status="ready", demo_ready_at=now - timedelta(days=3),
            next_action="Walk them through the demo and send the proposal",
            next_action_due_at=now + timedelta(hours=2),
            discovery_completed_at=now - timedelta(days=9))
        db.add(opp)
        db.flush()

        db.add(DiscoveryRecord(
            id=self.sid("discovery"), opportunity_id=self.opp_id,
            business_description=("Four funeral homes across the Dallas-Fort "
                                  "Worth metro, family owned since 1978."),
            business_goals=("Convert more of the pre-need database without "
                            "adding headcount."),
            current_process=("A part-time coordinator works a spreadsheet and "
                             "calls people back when she can."),
            current_tools="Google Workspace, a shared inbox, and a wall diary.",
            bottlenecks=("Nobody answers after 5pm. Evening enquiries go to "
                         "voicemail and most never call back."),
            required_integrations="Google Calendar and their website form.",
            automation_opportunities=("After-hours capture, reactivation of the "
                                      "2019-2022 pre-need list, reminders."),
            desired_outcome="Twenty extra file reviews a month.",
            team_size="26 across four locations",
            completed_at=now - timedelta(days=9)))

        # Two more deals so the pipeline and the manager's queues are plural.
        db.add(Opportunity(
            id=self.sid("opp", "ridgeline"), brand_sales_org_id=self.brand_id,
            owner_user_id=self.sid("rep2"),
            company_name="Ridgeline Memorial Partners",
            contact_name="Marcus Hale", email=demo_email("m.hale", "example.com"),
            stage="discovery", status="open",
            selected_package_id=self.sid("pkg", "growth"),
            next_action="Book the discovery call"))
        db.add(Opportunity(
            id=self.sid("opp", "pinecrest"), brand_sales_org_id=self.brand_id,
            owner_user_id=self.rep_id,
            company_name="Pinecrest Family Services",
            contact_name="Ana Whitfield", email=demo_email("a.whitfield", "example.com"),
            stage="proposal", status="open",
            selected_package_id=self.sid("pkg", "starter"),
            next_action="Chase the proposal - sent nine days ago"))
        db.commit()

        return {"brand": "EvoSys Pro Sales", "rep": "Tobias Reyner",
                "manager": "Priya Raman", "deal": "Brightwater Funeral Group"}

    def steps(self):
        return [
            Step("meeting", "Schedule the demo meeting",
                 "The meeting is on Tobias's calendar and in his My Day. No "
                 "Zoom room was created - the provider is simulated.",
                 self._meeting, provider="meeting"),
            Step("proposal", "Build the proposal",
                 "A real proposal record, priced from the selected package. "
                 "Open it and show the deliverables and terms.",
                 self._proposal),
            Step("approval", "Rep requests a discount above their authority",
                 "Switch to Priya. The request is sitting in her approvals "
                 "queue with the rep's own reason attached.",
                 self._approval),
            Step("approve", "Manager approves the pricing",
                 "The proposal now carries the approved adjustment, and the "
                 "audit shows Priya as the actor, not Tobias.",
                 self._approve),
            Step("send", "Send the proposal to the deal room",
                 "The secure deal room link is live. Nothing was emailed - "
                 "the send is a dry run and the firewall blocks the rest.",
                 self._send, provider="portal"),
            Step("buyer_opens", "The buyer opens it",
                 "Buyer activity appears on the opportunity. Point at the "
                 "timestamps - this is engagement the rep did not have to ask "
                 "about.",
                 self._buyer_opens, provider="portal"),
            Step("change_request", "The buyer asks for a change",
                 "The deal needs a revision. This is the moment a rep would "
                 "normally lose two days to email.",
                 self._change_request, provider="portal"),
            Step("revision", "Send revision v2",
                 "A new version in the same deal room, with the prior version "
                 "still on record.",
                 self._revision, provider="portal"),
            Step("accept", "The buyer accepts",
                 "Acceptance is recorded against the version they actually "
                 "saw.",
                 self._accept, provider="portal"),
            Step("won", "Close the deal",
                 "Manager's closing pipeline updates. Show the whole timeline "
                 "from discovery to Won on one screen.",
                 self._won),
        ]

    # ── step handlers ───────────────────────────────────────────────────────

    def _opp(self, db):
        return db.query(Opportunity).filter(Opportunity.id == self.opp_id).first()

    def _rep(self, db):
        return db.query(User).filter(User.id == self.rep_id).first()

    def _manager(self, db):
        return db.query(User).filter(User.id == self.manager_id).first()

    def _meeting(self, db, now):
        mt = (db.query(MeetingType)
              .filter(MeetingType.brand_sales_org_id == self.brand_id,
                      MeetingType.key == "discovery_demo").first())
        starts = now + timedelta(hours=3)
        appt = SalesAppointment(
            id=self.sid("appt", "demo"), brand_sales_org_id=self.brand_id,
            opportunity_id=self.opp_id,
            meeting_type_id=mt.id if mt else None,
            title="Discovery + Demo - Brightwater Funeral Group",
            starts_at=starts, ends_at=starts + timedelta(minutes=60),
            timezone=CHI, status="scheduled", confirmation_status="confirmed",
            prospect_name="Eleanor Vance",
            prospect_company="Brightwater Funeral Group",
            prospect_email=demo_email("e.vance", "example.com"),
            meeting_provider="simulated",
            meeting_url="https://demo.evosyspro.live/meeting/brightwater",
            notes="INTERNAL: their operations lead is the real decision maker.")
        db.add(appt)
        db.flush()
        for uid in (self.rep_id, self.manager_id):
            db.add(AppointmentParticipant(
                id=self.sid("part", uid[-6:]),
                appointment_id=appt.id, user_id=uid, is_required=True,
                busy_start_at=appt.starts_at, busy_end_at=appt.ends_at,
                is_blocking=True))
        db.commit()
        return "Demo meeting scheduled for %s." % starts.strftime("%A %H:%M")

    def _proposal(self, db, now):
        opp = self._opp(db)
        prop = ps.create_proposal(db, opp, self._rep(db), now=now)
        prop.deliverables = (
            "Configured EvoSys Pro workspace across four locations\n"
            "After-hours enquiry capture with automatic follow-up\n"
            "Reactivation campaign over the 2019-2022 pre-need list\n"
            "Google Calendar two-way sync for every advisor\n"
            "Voice booking agent with live availability\n"
            "Team training and a 30-day review")
        prop.implementation_plan = (
            "Week 1 - workspace configured, data imported, calendars connected.\n"
            "Week 2 - automations live, voice agent tested, team trained.\n"
            "Week 3 - reactivation campaign begins.\n"
            "Week 4 - review against the twenty-reviews-a-month target.")
        prop.terms = ("Monthly, cancel any time with 30 days' notice.\n"
                      "Setup, training and the reactivation campaign included.")
        opp.stage = "proposal"
        db.commit()
        return "Proposal v%s created for %s." % (prop.version, opp.company_name)

    def _latest(self, db):
        """The live proposal for this opportunity, through the service's own
        accessor rather than a hand-rolled version sort."""
        return ps.current_proposal(db, self.opp_id)

    def _approval(self, db, now):
        from app.services import pricing_approvals as appr
        # create_request returns {"ok", "error", "request"} and never raises -
        # a refusal must surface here rather than leaving the demo in a state
        # the operator cannot explain.
        res = appr.create_request(
            db, self._latest(db), self._rep(db), -400,
            "Brightwater is comparing us against Vendor X at 4,600. I need to "
            "be within touching distance to keep this in play.",
            now=now)
        db.commit()
        if not res.get("ok"):
            raise RuntimeError("Approval request refused: %s" % res.get("error"))
        return "Approval request raised - waiting on Priya."

    def _approve(self, db, now):
        from app.services import pricing_approvals as appr
        prop = self._latest(db)
        req = appr.open_request_for(db, prop.id)
        if req is None:
            raise RuntimeError("No open approval request to decide - run the "
                               "previous step first.")
        appr.decide(db, req, self._manager(db), approve=True,
                    note="Approved - hold at this number, no further movement.",
                    now=now)
        db.commit()
        return "Pricing approved by the manager."

    def _send(self, db, now):
        prop = self._latest(db)
        opp = self._opp(db)
        ps.publish_proposal(db, prop, self._rep(db), now=now)
        db.add(ProposalBlock(
            id=self.sid("block", "demo"), proposal_id=prop.id,
            block_type="website_url", position=99,
            content="Your Brightwater demo site",
            file_url="https://demo.evosyspro.live/brightwater"))
        db.commit()

        # dry_run publishes and issues the access key WITHOUT claiming a send.
        res = ps.send_proposal(db, prop, self._rep(db), now=now, dry_run=True)
        # The "sent" story is asserted here, in this file's own name.
        prop.sales_status = PROP_SENT
        prop.sent_at = now
        opp.proposal_status = PROP_SENT
        opp.proposal_sent_at = now
        db.commit()
        return "Deal room live: %s" % (res.get("portal_url") or "(link issued)")

    def _token(self, db, prop):
        from app.models.models import ProposalToken
        return (db.query(ProposalToken)
                .filter(ProposalToken.proposal_id == prop.id).first())

    def _buyer_opens(self, db, now):
        prop = self._latest(db)
        tok = self._token(db, prop)
        for i, (offset, ev, label) in enumerate((
                (0, PORTAL_OPENED, None),
                (2, PORTAL_PROPOSAL_VIEWED, None),
                (7, PORTAL_DEMO_OPENED, "Your Brightwater demo site"),
                (54, PORTAL_OPENED, None))):
            db.add(PortalEvent(
                id=self.sid("portal", "v1", i),
                proposal_id=prop.id, opportunity_id=self.opp_id,
                token_id=tok.id if tok else None,
                event_type=ev, label=label, proposal_version=prop.version,
                recipient_email=demo_email("e.vance", "example.com"),
                user_agent_family="Chrome",
                occurred_at=now + timedelta(minutes=offset)))
        prop.first_viewed_at = now + timedelta(minutes=2)
        prop.last_viewed_at = now + timedelta(minutes=54)
        prop.sales_status = PROP_VIEWED
        db.commit()
        return "Buyer opened the deal room four times, including the demo site."

    def _change_request(self, db, now):
        opp = self._opp(db)
        opp.next_action = ("Brightwater asked to phase the fourth location - "
                           "send a revised proposal")
        opp.next_action_due_at = now + timedelta(hours=4)
        from app.models.sales_models import OpportunityEvent
        db.add(OpportunityEvent(
            id=self.sid("event", "change"),
            opportunity_id=self.opp_id, event_type="buyer_change_request",
            summary="Buyer requested a change",
            detail=("Eleanor asked whether the fourth location could start in "
                    "month two rather than day one, to spread the training "
                    "load."),
            actor_user_id=None))
        db.commit()
        return "Change request logged against the opportunity."

    def _revision(self, db, now):
        opp = self._opp(db)
        # create_VERSION, not create_proposal. `create_proposal` starts a fresh
        # v1; the product's revision path supersedes the previous version and
        # keeps it on record, which is exactly what a buyer change request
        # produces and what the deal room is meant to show.
        prop = ps.create_version(db, self._latest(db), self._rep(db), now=now)
        prop.deliverables = (
            "Configured EvoSys Pro workspace - three locations at launch\n"
            "Fourth location onboarded in month two\n"
            "After-hours enquiry capture with automatic follow-up\n"
            "Reactivation campaign over the 2019-2022 pre-need list\n"
            "Google Calendar two-way sync for every advisor\n"
            "Voice booking agent with live availability\n"
            "Team training and a 30-day review")
        prop.implementation_plan = (
            "Week 1 - three locations configured, calendars connected.\n"
            "Week 2 - automations live, team trained.\n"
            "Week 3 - reactivation campaign begins.\n"
            "Month 2 - fourth location onboarded and trained.")
        prop.terms = ("Monthly, cancel any time with 30 days' notice.\n"
                      "Fourth location billed from month two.")
        ps.publish_proposal(db, prop, self._rep(db), now=now)
        res = ps.send_proposal(db, prop, self._rep(db), now=now, dry_run=True)
        prop.sales_status = PROP_SENT
        prop.sent_at = now
        opp.proposal_status = PROP_SENT
        opp.proposal_sent_at = now
        db.commit()
        return "Revision v%s sent to the same deal room." % prop.version

    def _accept(self, db, now):
        prop = self._latest(db)
        tok = self._token(db, prop)
        for i, (offset, ev) in enumerate(((0, PORTAL_OPENED),
                                          (3, PORTAL_PROPOSAL_VIEWED))):
            db.add(PortalEvent(
                id=self.sid("portal", "v2", i),
                proposal_id=prop.id, opportunity_id=self.opp_id,
                token_id=tok.id if tok else None,
                event_type=ev, proposal_version=prop.version,
                recipient_email=demo_email("e.vance", "example.com"),
                user_agent_family="Safari",
                occurred_at=now + timedelta(minutes=offset)))
        prop.first_viewed_at = now
        prop.last_viewed_at = now + timedelta(minutes=3)

        from app.models.sales_models import OpportunityEvent
        db.add(OpportunityEvent(
            id=self.sid("event", "accept"),
            opportunity_id=self.opp_id, event_type="proposal_accepted",
            summary="Buyer accepted proposal v%s" % prop.version,
            detail="Eleanor accepted the phased plan in the deal room.",
            actor_user_id=None))
        opp = self._opp(db)
        opp.stage = "closing"
        opp.next_action = "Confirm start date and raise the paperwork"
        db.commit()
        return "Acceptance recorded against version %s." % prop.version

    def _won(self, db, now):
        opp = self._opp(db)
        opp.stage = "won"
        opp.status = "won"
        opp.next_action = None
        opp.next_action_due_at = None
        from app.models.sales_models import OpportunityEvent
        db.add(OpportunityEvent(
            id=self.sid("event", "won"),
            opportunity_id=self.opp_id, event_type="stage_changed",
            summary="Deal marked Won",
            detail="Brightwater Funeral Group - Professional, phased rollout.",
            actor_user_id=self.rep_id))
        db.commit()
        return "Brightwater Funeral Group is Won."
