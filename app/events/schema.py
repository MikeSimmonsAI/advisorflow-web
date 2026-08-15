"""
AdvisorFlow Standardized Event Types
--------------------------------------
These are the canonical events every spinoff platform can emit.
AdvisorFlow Command Center consumes them to build its intelligence layer.

Adding a new event:
  1. Add a constant to EventType
  2. Document the expected data payload in the PAYLOAD_SCHEMA dict below
  3. Call emit() at the right point in the relevant router or service

Event naming convention: <domain>.<action>  (dot-separated, snake_case)
"""

import enum


class EventType(str, enum.Enum):
    # ── Lead lifecycle ────────────────────────────────────────────────────────
    LEAD_CREATED        = "lead.created"        # new lead enters the system
    LEAD_ENGAGED        = "lead.engaged"        # lead replied or clicked
    LEAD_SCORED         = "lead.scored"         # AI scored/classified a lead
    LEAD_HOT            = "lead.hot"            # lead upgraded to HOT status
    LEAD_DNC            = "lead.dnc"            # lead opted out / DNC flagged
    LEAD_DEAD           = "lead.dead"           # lead marked dead/unworkable

    # ── Appointment lifecycle ─────────────────────────────────────────────────
    APPOINTMENT_BOOKED      = "appointment.booked"      # appointment created
    APPOINTMENT_CONFIRMED   = "appointment.confirmed"   # lead confirmed attendance
    APPOINTMENT_COMPLETED   = "appointment.completed"   # appointment happened
    APPOINTMENT_NO_SHOW     = "appointment.no_show"     # lead didn't show

    # ── AI operations ─────────────────────────────────────────────────────────
    AI_ACTION_EXECUTED  = "ai.action.executed"  # any AI-driven send/score/route
    AI_ACTION_FAILED    = "ai.action.failed"    # AI action errored
    AI_DRAFT_APPROVED   = "ai.draft.approved"   # human approved AI draft
    AI_DRAFT_REJECTED   = "ai.draft.rejected"   # human rejected AI draft

    # ── Campaign / messaging ──────────────────────────────────────────────────
    CAMPAIGN_STARTED    = "campaign.started"    # bulk campaign launched
    CAMPAIGN_COMPLETED  = "campaign.completed"  # bulk campaign finished
    SMS_SENT            = "sms.sent"            # outbound SMS dispatched
    SMS_FAILED          = "sms.failed"          # outbound SMS failed
    EMAIL_SENT          = "email.sent"          # outbound email dispatched
    EMAIL_FAILED        = "email.failed"        # outbound email failed

    # ── Organization lifecycle ────────────────────────────────────────────────
    ORG_CREATED         = "org.created"         # new org provisioned
    ORG_DEACTIVATED     = "org.deactivated"     # org paused or cancelled

    # ── Subscription / billing ────────────────────────────────────────────────
    ORG_SUBSCRIPTION_STARTED    = "org.subscription.started"    # org started a paid plan
    ORG_SUBSCRIPTION_CANCELLED  = "org.subscription.cancelled"  # org cancelled
    PAYMENT_RECEIVED            = "payment.received"             # payment succeeded
    PAYMENT_FAILED              = "payment.failed"               # payment failed

    # ── Integration health ────────────────────────────────────────────────────
    INTEGRATION_CONNECTED   = "integration.connected"   # Twilio/Resend/Calendar linked
    INTEGRATION_FAILED      = "integration.failed"      # integration call failed

    # ── Website / marketing funnel (fed by public landing pages) ─────────────
    WEBSITE_VISIT       = "website.visit"       # visitor hit a brand website
    DEMO_REQUESTED      = "demo.requested"      # prospect submitted demo request form
    DEMO_SCHEDULED      = "demo.scheduled"      # demo booked on calendar
    DEMO_COMPLETED      = "demo.completed"      # demo call happened
    PROSPECT_SIGNED     = "prospect.signed"     # prospect became a paying org


# ── Expected data payload documentation (not enforced at runtime, but
#    documented here so all emitters stay consistent) ─────────────────────────
PAYLOAD_SCHEMA = {
    EventType.LEAD_CREATED: {
        "lead_id": "str",
        "source": "str — facebook|google|walk_in|referral|instagram|upload",
        "tier": "str — pre_need|at_need|imminent|etc.",
    },
    EventType.LEAD_ENGAGED: {
        "lead_id": "str",
        "channel": "str — sms|email",
        "sentiment": "str — interested|neutral|dnc|callback",
    },
    EventType.APPOINTMENT_BOOKED: {
        "lead_id": "str",
        "advisor_id": "str",
        "scheduled_at": "ISO8601 datetime string",
        "channel": "str — in_person|zoom|phone",
    },
    EventType.AI_ACTION_EXECUTED: {
        "action": "str — sms_send|email_send|lead_score|lead_route|review_response",
        "lead_id": "str|null",
        "tokens_used": "int|null",
        "autonomous": "bool — true if no human approval needed",
    },
    EventType.PAYMENT_RECEIVED: {
        "amount_cents": "int",
        "currency": "str — usd",
        "plan": "str — trial|standard|enterprise",
        "invoice_id": "str|null",
    },
    EventType.DEMO_REQUESTED: {
        "company_name": "str",
        "contact_name": "str",
        "contact_email": "str",
        "source": "str — google|facebook|direct|referral",
        "brand": "str — bookaboost|evosyspro|harmonyhustle",
    },
    EventType.PROSPECT_SIGNED: {
        "org_id": "str — the new org's ID",
        "demo_event_id": "str|null — links back to the original demo.requested event",
        "mrr_cents": "int — monthly recurring revenue from this org",
        "brand": "str",
    },
}
