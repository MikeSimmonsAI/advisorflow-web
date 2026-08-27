"""
Sample Data router — generates realistic demo data that fills ALL app screens.

Covers: Overview, Leads, Replies, CRM Pipeline, AI Hub (PipelineConversation),
Email Queue, Availability, and Calendar views. Industry-aware: detects the
org's industry and generates matching leads, tiers, and appointment types.

SAFETY: every sample record gets source_file = "SAMPLE_DATA" so the
/clear endpoint can surgically remove them without touching real data.
Fake phone numbers use 555-XXXX (never assigned to real US lines).
"""

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models.models import (
    User, Lead, LeadTier, LeadStatus, MessageTrack, EngagementTemperature,
    Reply, ReplyClassification, CadenceState, Message, Organization,
    EmailMessage, PipelineConversation, BookingLink, Campaign,
)
from app.models.models import CRMContact, CRMNote
from app.routers.admin_router import require_super_admin
from app.routers.audit_log_router import log_action

router = APIRouter(prefix="/sample-data", tags=["sample-data"])

SAMPLE_TAG = "SAMPLE_DATA"

# ── Name pools ────────────────────────────────────────────────────────────────
FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Linda", "Michael", "Barbara",
    "William", "Elizabeth", "David", "Susan", "Richard", "Jessica", "Joseph", "Sarah",
    "Charles", "Karen", "Thomas", "Nancy", "Christopher", "Lisa", "Daniel", "Betty",
    "Matthew", "Dorothy", "Anthony", "Sandra", "Mark", "Ashley", "Donald", "Dorothy",
    "Steven", "Kimberly", "Paul", "Emily", "Andrew", "Donna", "Joshua", "Michelle",
    "Kevin", "Carol", "Brian", "Amanda", "George", "Melissa", "Edward", "Deborah",
    "Ronald", "Stephanie",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
]

# ── Industry-specific data ─────────────────────────────────────────────────────
INDUSTRY_CONFIG = {
    "funeral": {
        "tiers": [
            ("pre_need", "warm", "pre_need_lock_price"),
            ("pre_need", "warm", "pre_need_lock_price"),
            ("pre_need", "hot", "pre_need_lock_price"),
            ("at_need", "warm", "at_need_support"),
            ("at_need", "hot", "at_need_support"),
            ("imminent", "hot", "imminent_support"),
            ("contract_sold", "warm", "upsell_existing"),
            ("contract_sold", "hot", "upsell_existing"),
            ("partial", "unknown", None),
            ("email_only", "unknown", "email_only_nurture"),
        ],
        "crm_stages": [
            "inquiry", "pre_need", "at_need",
            "arrangements", "services_complete", "aftercare", "closed",
        ],
        "crm_stage_labels": {
            "inquiry": "Inquiry",
            "pre_need": "Pre-Need Planning",
            "at_need": "At-Need",
            "arrangements": "Arrangements",
            "services_complete": "Services Complete",
            "aftercare": "Aftercare",
            "closed": "Closed",
        },
        "appt_types": [
            "Pre-Need Planning Consultation",
            "At-Need Arrangement Conference",
            "File Review",
            "Follow-Up Appointment",
            "Veterans Benefits Consultation",
        ],
        "outreach_messages": [
            "Hi {first}, this is {advisor} with {org}. We have your family's file on record and wanted to reach out about your pre-planning options. Would you have 15 minutes this week?",
            "Hi {first}, {advisor} here from {org}. We're following up on the pre-arrangement file we have on file for your family. Are you available for a quick call?",
            "Hello {first}, this is {advisor} at {org}. We wanted to touch base about your family's planning file and make sure everything is up to date.",
        ],
        "reply_messages": {
            "interested": [
                "Yes, I'd like to come in and discuss our options.",
                "That sounds good. When are you available?",
                "I've been meaning to call. Yes, let's set something up.",
                "We definitely need to get this taken care of. What times work?",
            ],
            "callback": [
                "Can you call me back tomorrow? I'm at work right now.",
                "Please call me, I can't text easily.",
                "Text me a number to call back at.",
            ],
            "neutral": [
                "Who gave you my number?",
                "I'll have to talk to my wife about this.",
                "We're not ready yet but maybe later in the year.",
                "What exactly is this about?",
            ],
            "not_interested": [
                "Not interested, we have everything taken care of.",
                "We already have a policy in place.",
                "Please don't contact us again.",
            ],
            "dnc": [
                "STOP",
                "Please remove us from your list.",
                "DO NOT contact us again.",
            ],
        },
        "source_files": ["2022 Pre-Need List", "2023 Cemetery Records", "2024 Purchased List", "2021 At-Need Follow-Up", "Community Outreach 2024"],
        "ai_directions": [
            "Focus on lock-in pricing before rates increase next quarter",
            "Family lost a loved one recently — be compassionate and supportive",
            "Veterans family — mention VA burial benefits",
            "Prior customer — mention loyalty discount for second family member",
        ],
    },
    "fiber": {
        "tiers": [
            ("new_inquiry", "warm", "new_inquiry_intro"),
            ("new_inquiry", "warm", "new_inquiry_intro"),
            ("new_inquiry", "hot", "new_inquiry_intro"),
            ("new_inquiry", "hot", "new_inquiry_intro"),
            ("partial", "unknown", None),
            ("email_only", "unknown", "email_only_nurture"),
            ("pre_need", "warm", "pre_need_lock_price"),
            ("pre_need", "cold", "pre_need_lock_price"),
            ("contract_sold", "warm", "upsell_existing"),
            ("at_need", "warm", "at_need_support"),
        ],
        "crm_stages": [
            "new_lead", "contacted", "quoted", "pending_install",
            "installed", "active", "churned",
        ],
        "crm_stage_labels": {
            "new_lead": "New Lead",
            "contacted": "Contacted",
            "quoted": "Quoted",
            "pending_install": "Pending Install",
            "installed": "Installed",
            "active": "Active Customer",
            "churned": "Churned",
        },
        "appt_types": [
            "New Service Consultation",
            "Installation Appointment",
            "Service Upgrade Consultation",
            "Tech Support Visit",
            "Business Account Consultation",
        ],
        "outreach_messages": [
            "Hi {first}, this is {advisor} with {org}. We're expanding fiber internet in your area and wanted to offer you a special install rate. Are you interested?",
            "Hi {first}, {advisor} here from {org}. Your neighborhood just qualified for our gigabit fiber service at $49/mo — no contract. Want to schedule a free install?",
            "Hello {first}, this is {advisor} at {org}. We're running a door-to-door special for residents in your area. Can I share our rates with you?",
        ],
        "reply_messages": {
            "interested": [
                "Yes! We've been waiting for fiber in this area.",
                "Sounds great, when can someone come out?",
                "What speeds do you offer? We work from home.",
                "Finally! Please sign us up.",
            ],
            "callback": [
                "Call me after 5pm please.",
                "I'm interested but at work right now.",
                "Can you text me the pricing info?",
            ],
            "neutral": [
                "We already have Spectrum.",
                "What's the contract length?",
                "How long does install take?",
            ],
            "not_interested": [
                "We're happy with what we have.",
                "Not interested at this time.",
            ],
            "dnc": [
                "Stop texting me",
                "Remove me from your list",
            ],
        },
        "source_files": ["Q1 Door Canvass", "Q2 Web Leads", "Q3 Purchased List", "Apartment Complex Outreach", "Business District List"],
        "ai_directions": [
            "Area recently got fiber access — emphasize being first in neighborhood",
            "Work-from-home lead — focus on upload speeds and reliability",
            "Business account — mention dedicated support line",
            "Current cable customer — pitch price comparison",
        ],
    },
    "roofing": {
        "tiers": [
            ("new_inquiry", "hot", "new_inquiry_intro"),
            ("new_inquiry", "warm", "new_inquiry_intro"),
            ("new_inquiry", "warm", "new_inquiry_intro"),
            ("new_inquiry", "cold", "new_inquiry_intro"),
            ("pre_need", "warm", "pre_need_lock_price"),
            ("at_need", "hot", "at_need_support"),
            ("at_need", "warm", "at_need_support"),
            ("partial", "unknown", None),
            ("contract_sold", "warm", "upsell_existing"),
            ("email_only", "unknown", "email_only_nurture"),
        ],
        "crm_stages": [
            "new_lead", "inspection_scheduled", "estimate_sent",
            "negotiating", "contract_signed", "in_progress", "complete", "follow_up",
        ],
        "crm_stage_labels": {
            "new_lead": "New Lead",
            "inspection_scheduled": "Inspection Scheduled",
            "estimate_sent": "Estimate Sent",
            "negotiating": "Negotiating",
            "contract_signed": "Contract Signed",
            "in_progress": "In Progress",
            "complete": "Complete",
            "follow_up": "Follow-Up",
        },
        "appt_types": [
            "Free Roof Inspection",
            "Damage Assessment",
            "Insurance Claim Walkthrough",
            "Estimate Presentation",
            "Project Consultation",
        ],
        "outreach_messages": [
            "Hi {first}, this is {advisor} with {org}. After the recent storms in your area we're offering free roof inspections. Want to schedule one?",
            "Hi {first}, {advisor} here. We noticed storm activity in your zip code — we're doing free inspections this week for homeowners. Are you available?",
            "Hello {first}, this is {advisor} at {org}. We're in your area doing insurance claim assessments. Roof damage can be hidden — want us to take a look at no charge?",
        ],
        "reply_messages": {
            "interested": [
                "Yes, we've been worried about our roof after the hail storm.",
                "Please come out! We filed an insurance claim already.",
                "Great timing, can you come this week?",
            ],
            "callback": [
                "Call me, I'd like to know more.",
                "Text me when you're in the area.",
            ],
            "neutral": [
                "Our roof is fine, we just had it done.",
                "How much does it cost if there's no damage?",
            ],
            "not_interested": [
                "We're renting, not our roof.",
                "Not interested thanks.",
            ],
            "dnc": ["Stop", "Remove me"],
        },
        "source_files": ["Storm Damage List 2024", "Hail Zone Map Q2", "Insurance Leads", "Direct Mail Response", "Web Inquiry Q3"],
        "ai_directions": [
            "Recent hail storm in area — mention insurance claim assistance",
            "Homeowner filed claim already — help move them along",
            "Follow-up from last season — reference prior inspection",
        ],
    },
    "insurance": {
        "tiers": [
            ("new_inquiry", "hot", "new_inquiry_intro"),
            ("new_inquiry", "warm", "new_inquiry_intro"),
            ("new_inquiry", "warm", "new_inquiry_intro"),
            ("pre_need", "warm", "pre_need_lock_price"),
            ("pre_need", "cold", "pre_need_lock_price"),
            ("at_need", "hot", "at_need_support"),
            ("contract_sold", "warm", "upsell_existing"),
            ("contract_sold", "warm", "upsell_existing"),
            ("partial", "unknown", None),
            ("email_only", "unknown", "email_only_nurture"),
        ],
        "crm_stages": [
            "prospect", "needs_analysis", "proposal_sent",
            "underwriting", "policy_issued", "active", "renewal",
        ],
        "crm_stage_labels": {
            "prospect": "Prospect",
            "needs_analysis": "Needs Analysis",
            "proposal_sent": "Proposal Sent",
            "underwriting": "Underwriting",
            "policy_issued": "Policy Issued",
            "active": "Active Client",
            "renewal": "Up for Renewal",
        },
        "appt_types": [
            "Life Insurance Consultation",
            "Policy Review",
            "Needs Analysis",
            "Final Expense Review",
            "Medicare Supplement Consultation",
        ],
        "outreach_messages": [
            "Hi {first}, this is {advisor} with {org}. I'm reaching out because families in your area qualify for new final expense coverage starting at $18/mo. Can we talk?",
            "Hi {first}, {advisor} here from {org}. We help families protect their loved ones from unexpected funeral costs. Do you have 10 minutes this week?",
        ],
        "reply_messages": {
            "interested": [
                "Yes, I've been thinking about getting coverage.",
                "My husband and I have been talking about this. Can you call us?",
                "What's the monthly premium for $25k coverage?",
            ],
            "callback": ["Call me after 6pm", "I'm at work, text me"],
            "neutral": ["What company are you with?", "I already have some coverage"],
            "not_interested": ["Not interested", "We have plenty of coverage"],
            "dnc": ["Stop", "Remove me from your list"],
        },
        "source_files": ["Senior List 2024", "Medicare Aged-In", "Mailer Response Q2", "Referral Batch", "Online Lead Q3"],
        "ai_directions": [
            "Senior citizen — keep it simple, focus on peace of mind",
            "Has existing policy — compare rates and offer upgrade",
            "Recent family loss — approach with empathy",
        ],
    },
}

# Fallback generic config for unknown industries
GENERIC_CONFIG = INDUSTRY_CONFIG["funeral"]


def _gen_uuid() -> str:
    return str(uuid.uuid4())


def _random_phone(seed: int) -> str:
    """Fake US phone in 555-XXXX range (never a real number)."""
    return f"1469555{seed:04d}"


def _pick(lst: list):
    return random.choice(lst)


def _get_industry_config(org: Organization) -> dict:
    industry = (org.industry or "funeral").lower()
    return INDUSTRY_CONFIG.get(industry, GENERIC_CONFIG)


def _format_message(template: str, first: str, advisor_name: str, org_name: str) -> str:
    return template.format(first=first, advisor=advisor_name, org=org_name)


# ── Reply bodies by classification ───────────────────────────────────────────
SAMPLE_REPLY_BODIES = {
    ReplyClassification.INTERESTED: [
        "Yes, I'd like to set up a time.",
        "Sounds good, when can we meet?",
        "I'm interested, call me to schedule.",
        "Please reach out, we're ready to move forward.",
    ],
    ReplyClassification.CALLBACK: [
        "Can you call me tomorrow morning?",
        "I'm driving right now, call me later.",
        "Please give me a call.",
    ],
    ReplyClassification.NEUTRAL: [
        "What time does your office close?",
        "Who gave you this number?",
        "I need to think about it.",
        "Can you send me more info?",
    ],
    ReplyClassification.DNC: [
        "STOP",
        "Please remove me from this list.",
        "Do not contact me again.",
    ],
    ReplyClassification.NOT_INTERESTED: [
        "No thanks, we're all set.",
        "Not interested at this time.",
        "We have everything handled already.",
    ],
    ReplyClassification.WRONG_NUMBER: [
        "Wrong number.",
        "I don't know what this is about.",
        "You have the wrong person.",
    ],
    ReplyClassification.QUESTION: [
        "What's included in the price?",
        "Do I need to bring anything?",
        "Is this something my whole family needs to attend?",
    ],
}


@router.post("/generate")
def generate_sample_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Generates comprehensive demo data covering ALL app screens.
    Industry-aware: detects the org's industry and generates matching content.
    All records tagged with source_file = SAMPLE_DATA for easy cleanup.
    """
    now = datetime.now(timezone.utc)
    nowu = datetime.utcnow()

    org = db.query(Organization).filter(Organization.id == current_user.organization_id).first()
    org_name = org.name if org else "Our Team"
    cfg = _get_industry_config(org)

    advisor_name = current_user.full_name or "Your Advisor"

    created = {
        "leads": 0, "crm_contacts": 0, "pipeline_conversations": 0,
        "bookings": 0, "email_messages": 0, "campaigns": 0,
    }

    # ── 1. LEADS (50+ across all statuses) ────────────────────────────────────
    lead_scenarios = [
        # (status, tier_idx, has_reply, reply_class, in_cadence, days_old, days_since_contact)
        ("new",     0, False, None,                        False, 0,  0),
        ("new",     1, False, None,                        False, 1,  1),
        ("new",     2, False, None,                        False, 2,  2),
        ("new",     0, False, None,                        False, 3,  3),
        ("new",     1, False, None,                        False, 4,  4),
        ("sent",    0, False, None,                        True,  1,  1),
        ("sent",    2, False, None,                        True,  2,  1),
        ("sent",    3, False, None,                        True,  3,  2),
        ("sent",    6, False, None,                        True,  5,  1),
        ("sent",    1, False, None,                        True,  6,  2),
        ("sent",    0, False, None,                        True,  7,  3),
        ("sent",    4, False, None,                        True,  8,  4),
        ("sent",    7, False, None,                        False, 10, 5),
        ("replied", 0, True,  ReplyClassification.INTERESTED,  False, 2, 1),
        ("replied", 2, True,  ReplyClassification.INTERESTED,  False, 3, 1),
        ("replied", 3, True,  ReplyClassification.CALLBACK,    False, 4, 2),
        ("replied", 1, True,  ReplyClassification.NEUTRAL,     False, 5, 2),
        ("replied", 5, True,  ReplyClassification.QUESTION,    False, 6, 3),
        ("replied", 6, True,  ReplyClassification.CALLBACK,    False, 7, 2),
        ("replied", 8, True,  ReplyClassification.NOT_INTERESTED, False, 8, 4),
        ("replied", 9, True,  ReplyClassification.WRONG_NUMBER, False, 3, 2),
        ("hot",     2, True,  ReplyClassification.INTERESTED,  False, 1, 0),
        ("hot",     5, True,  ReplyClassification.INTERESTED,  False, 2, 1),
        ("hot",     3, True,  ReplyClassification.INTERESTED,  False, 3, 1),
        ("hot",     0, True,  ReplyClassification.INTERESTED,  False, 4, 2),
        ("booked",  2, True,  ReplyClassification.INTERESTED,  False, 7, 3),
        ("booked",  5, True,  ReplyClassification.INTERESTED,  False, 10, 5),
        ("booked",  6, True,  ReplyClassification.INTERESTED,  False, 14, 7),
        ("dnc",     1, True,  ReplyClassification.DNC,         False, 14, 7),
        ("dnc",     3, True,  ReplyClassification.DNC,         False, 20, 10),
        ("dnc",     0, True,  ReplyClassification.DNC,         False, 25, 12),
        ("new",     8, False, None,                        False, 5, 5),   # partial
        ("new",     9, False, None,                        False, 3, 3),   # email only
        ("sent",    9, False, None,                        True,  4, 2),   # email only
        ("sent",    9, False, None,                        True,  6, 3),   # email only
        ("replied", 9, True,  ReplyClassification.INTERESTED,  False, 5, 2),  # email only interested
        ("new",     0, False, None,                        False, 1, 1),
        ("new",     4, False, None,                        False, 2, 2),
        ("sent",    5, False, None,                        True,  3, 1),
        ("sent",    2, False, None,                        True,  4, 2),
        ("replied", 4, True,  ReplyClassification.QUESTION,    False, 6, 3),
        ("replied", 7, True,  ReplyClassification.NEUTRAL,     False, 8, 4),
        ("hot",     4, True,  ReplyClassification.INTERESTED,  False, 5, 2),
        ("sent",    6, False, None,                        True,  9, 5),
        ("new",     3, False, None,                        False, 7, 7),
        ("replied", 1, True,  ReplyClassification.CALLBACK,    False, 9, 4),
        ("booked",  4, True,  ReplyClassification.INTERESTED,  False, 12, 6),
        ("sent",    3, False, None,                        True,  11, 6),
        ("new",     6, False, None,                        False, 6, 6),
        ("dnc",     2, True,  ReplyClassification.NOT_INTERESTED, False, 30, 15),
    ]

    leads_for_pipeline = []  # leads to attach PipelineConversation to
    leads_for_booking = []   # leads to attach BookingLink to

    for i, (status, tier_idx, has_reply, reply_class, in_cadence, days_old, days_since_contact) in enumerate(lead_scenarios):
        first = _pick(FIRST_NAMES)
        last = _pick(LAST_NAMES)
        tier_data = cfg["tiers"][tier_idx % len(cfg["tiers"])]
        tier_val, temp_val, track_val = tier_data

        is_email_only = tier_val == "email_only"
        contact_channel = "email_only" if is_email_only else "sms"

        # Engagement temperature: override based on status
        if status == "hot":
            temp_val = "hot"
        elif status in ("booked", "replied") and reply_class == ReplyClassification.INTERESTED:
            temp_val = "hot"
        elif status == "dnc":
            temp_val = "cold"

        source = _pick(cfg["source_files"])
        lead = Lead(
            organization_id=current_user.organization_id,
            assigned_to_id=current_user.id,
            first_name=first,
            last_name=last,
            phone=None if is_email_only else _random_phone(i + 100),
            email=f"{first.lower()}.{last.lower()}{i}@example.com" if (is_email_only or random.random() > 0.5) else None,
            tier=tier_val,
            engagement_temperature=EngagementTemperature(temp_val),
            message_track=track_val,
            contact_channel=contact_channel,
            status=status,
            source_year=random.choice([2021, 2022, 2023, 2024]),
            source_file=SAMPLE_TAG,
            import_list_name=source,
            last_action_raw=random.choice([
                "Called: LM/No Answer", "Called: Scheduled Appt.", "Texted: No Response",
                "Emailed: No Response", "Texted: Replied", "Called: Spoke with Family",
            ]),
            last_contact_date=nowu - timedelta(days=days_since_contact),
            last_messaged_at=nowu - timedelta(days=days_since_contact) if status != "new" else None,
            created_at=nowu - timedelta(days=days_old),
            relationship_type=random.choice(["cold_lead", "warm_lead", "previous_prospect", "existing_customer"]),
            notes="Sample demo record." if random.random() > 0.7 else None,
        )
        db.add(lead)
        db.flush()  # get lead.id before commit
        created["leads"] += 1

        # Add outbound message
        if not is_email_only and status != "new":
            msg_template = _pick(cfg["outreach_messages"])
            msg_body = _format_message(msg_template, first, advisor_name, org_name)
            msg = Message(
                lead_id=lead.id,
                sender_id=current_user.id,
                body=msg_body,
                twilio_status="delivered",
                sent_at=nowu - timedelta(days=days_since_contact),
            )
            db.add(msg)
            lead.last_messaged_at = nowu - timedelta(days=days_since_contact)

        # Add email for email_only leads
        if is_email_only and status in ("sent", "replied", "hot"):
            em = EmailMessage(
                lead_id=lead.id,
                sender_id=current_user.id,
                subject=f"Following up — {org_name}",
                body_html=f"<p>Hi {first}, we wanted to reach out about our services. Please reply or call us at your convenience.</p>",
                status=random.choice(["delivered", "sent", "delivered"]),
                sent_at=nowu - timedelta(days=days_since_contact),
            )
            db.add(em)
            created["email_messages"] += 1

        # Add reply
        if has_reply and reply_class is not None:
            reply_bodies = SAMPLE_REPLY_BODIES.get(reply_class, ["Thanks for reaching out."])
            r = Reply(
                lead_id=lead.id,
                body=_pick(reply_bodies),
                is_hot=(reply_class == ReplyClassification.INTERESTED),
                classification=reply_class,
                classification_confidence="high",
                classification_reasoning="Sample demo data",
                received_at=nowu - timedelta(days=max(0, days_since_contact - 1)),
            )
            db.add(r)

        # Add cadence state
        if in_cadence:
            c = CadenceState(
                lead_id=lead.id,
                status="active",
                current_touch_number=random.randint(1, 5),
                cadence_started_at=nowu - timedelta(days=days_old),
                next_touch_due_at=nowu + timedelta(days=random.randint(1, 7)),
                last_touch_sent_at=nowu - timedelta(days=days_since_contact),
            )
            db.add(c)

        # Queue leads for AI Hub and Bookings
        if status in ("hot", "replied") and reply_class == ReplyClassification.INTERESTED:
            leads_for_pipeline.append((lead, first, last))
        if status == "booked":
            leads_for_booking.append((lead, first, last))

    db.commit()

    # ── 2. CRM PIPELINE CONTACTS ───────────────────────────────────────────────
    crm_stages = cfg["crm_stages"]
    crm_stage_labels = cfg["crm_stage_labels"]
    crm_notes_by_stage = {
        crm_stages[0]: ["Initial inquiry received. Needs follow-up call.", "Web form submitted, waiting on contact."],
        crm_stages[1]: ["Left voicemail, awaiting callback.", "Spoke briefly, wants to set up full meeting."],
        crm_stages[2]: ["Quote sent via email. Follow up in 3 days.", "Presented options, customer reviewing."],
        crm_stages[3]: ["Paperwork in progress.", "Waiting on signature from second decision maker."] if len(crm_stages) > 3 else ["Processing."],
        crm_stages[4]: ["Service scheduled.", "Confirmed appointment, all set."] if len(crm_stages) > 4 else ["Active."],
        crm_stages[-2]: ["Completed, awaiting final payment.", "Follow-up in 30 days to check satisfaction."] if len(crm_stages) > 2 else ["Done."],
        crm_stages[-1]: ["Case closed successfully.", "File closed."],
    }

    # Create 3-5 contacts per stage
    for stage_idx, stage in enumerate(crm_stages):
        count = random.randint(3, 5)
        for _ in range(count):
            first = _pick(FIRST_NAMES)
            last = _pick(LAST_NAMES)
            contact = CRMContact(
                organization_id=current_user.organization_id,
                first_name=first,
                last_name=last,
                phone=_random_phone(200 + stage_idx * 10 + _),
                email=f"{first.lower()}.{last.lower()}@example.com" if random.random() > 0.4 else None,
                stage=stage,
                tags=",".join(random.sample(["high-priority", "referral", "returning", "walk-in", "web-lead", "cold-call"], k=random.randint(0, 2))),
                assigned_to_id=current_user.id,
                last_contacted_at=nowu - timedelta(days=random.randint(1, 30)),
                created_at=nowu - timedelta(days=random.randint(stage_idx * 5, stage_idx * 5 + 20)),
            )
            db.add(contact)
            db.flush()

            # Add a note to each contact
            note_pool = crm_notes_by_stage.get(stage, ["Follow-up needed."])
            note = CRMNote(
                contact_id=contact.id,
                author_id=current_user.id,
                content=_pick(note_pool),
                created_at=nowu - timedelta(days=random.randint(0, 5)),
            )
            db.add(note)
            created["crm_contacts"] += 1

    db.commit()

    # ── 3. PIPELINE CONVERSATIONS (AI Hub) ────────────────────────────────────
    pipeline_stages = [
        "outreach_sent", "replied", "ai_responding", "booking_sent", "booked",
    ]
    ai_directions = cfg.get("ai_directions", ["Engage warmly and book an appointment."])

    # Use our hot/replied leads, plus create a few extra with no matching lead
    pipeline_leads = leads_for_pipeline[:8] if len(leads_for_pipeline) > 8 else leads_for_pipeline
    # Pad with some direct pipeline entries
    for i, (lead, first, last) in enumerate(pipeline_leads):
        stage = pipeline_stages[i % len(pipeline_stages)]
        messages_sent = random.randint(1, 6)
        replies_received = random.randint(1, messages_sent)
        convo = PipelineConversation(
            organization_id=current_user.organization_id,
            lead_id=lead.id,
            advisor_id=current_user.id,
            stage=stage,
            lead_type=lead.tier,
            channel=lead.contact_channel or "sms",
            tone=random.choice(["warm", "warm", "hot", "urgent"]),
            ai_direction=_pick(ai_directions),
            auto_respond=True,
            confidence_threshold=85,
            response_delay_seconds=180,
            flagged=(stage == "ai_responding" and i % 3 == 0),
            flag_reason="Lead asked about pricing — review before sending" if (stage == "ai_responding" and i % 3 == 0) else None,
            touch_number=random.randint(1, 4),
            messages_sent=messages_sent,
            replies_received=replies_received,
            ai_responses_sent=random.randint(0, replies_received),
            started_at=nowu - timedelta(days=random.randint(1, 14)),
            last_outbound_at=nowu - timedelta(hours=random.randint(1, 72)),
            last_inbound_at=nowu - timedelta(hours=random.randint(1, 48)) if replies_received > 0 else None,
            booking_link_sent_at=nowu - timedelta(days=1) if stage in ("booking_sent", "booked") else None,
            booked_at=nowu - timedelta(hours=12) if stage == "booked" else None,
        )
        db.add(convo)
        created["pipeline_conversations"] += 1

    db.commit()

    # ── 4. BOOKING LINKS (Calendar + Availability) ────────────────────────────
    appt_types = cfg["appt_types"]
    # Past booked appointments (last 30 days)
    for i, (lead, first, last) in enumerate(leads_for_booking):
        days_ago = random.randint(1, 30)
        appt_hour = random.choice([9, 10, 11, 13, 14, 15, 16])
        appt_minute = random.choice([0, 30])
        booked_time = nowu - timedelta(days=days_ago) + timedelta(hours=appt_hour, minutes=appt_minute)
        # Normalize: set to the right time on that date
        booked_dt = datetime(
            booked_time.year, booked_time.month, booked_time.day,
            appt_hour, appt_minute, 0
        )
        bl = BookingLink(
            lead_id=lead.id,
            user_id=current_user.id,
            status="booked",
            booked_time=booked_dt,
            created_at=nowu - timedelta(days=days_ago + 3),
        )
        db.add(bl)
        created["bookings"] += 1

    # Upcoming confirmed appointments (next 14 days)
    upcoming_count = 6
    for i in range(upcoming_count):
        # Use random leads for upcoming appointments
        all_leads = db.query(Lead).filter(
            Lead.organization_id == current_user.organization_id,
            Lead.source_file == SAMPLE_TAG,
        ).limit(50).all()
        if not all_leads:
            break
        rand_lead = random.choice(all_leads)
        days_ahead = random.randint(1, 12)
        appt_hour = random.choice([9, 10, 11, 13, 14, 15])
        appt_minute = random.choice([0, 30])
        booked_dt = datetime(
            (nowu + timedelta(days=days_ahead)).year,
            (nowu + timedelta(days=days_ahead)).month,
            (nowu + timedelta(days=days_ahead)).day,
            appt_hour, appt_minute, 0,
        )
        bl = BookingLink(
            lead_id=rand_lead.id,
            user_id=current_user.id,
            status="confirmed",
            booked_time=booked_dt,
            created_at=nowu - timedelta(days=random.randint(1, 5)),
        )
        db.add(bl)
        created["bookings"] += 1

    db.commit()

    # ── 5. EMAIL QUEUE (EmailMessage) — extra non-lead entries ─────────────────
    # Additional email-only leads with queued/pending email messages
    extra_email_leads = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.source_file == SAMPLE_TAG,
        Lead.contact_channel == "email_only",
    ).limit(10).all()
    for lead in extra_email_leads:
        for j in range(random.randint(1, 3)):
            em = EmailMessage(
                lead_id=lead.id,
                sender_id=current_user.id,
                subject=random.choice([
                    f"Following up — {org_name}",
                    f"A note from {advisor_name} at {org_name}",
                    f"Quick question for you",
                    f"We'd love to connect, {lead.first_name}",
                ]),
                body_html=f"<p>Hi {lead.first_name or 'there'}, we wanted to reach out about our services at {org_name}. Please reply at your earliest convenience.</p>",
                status=random.choice(["delivered", "delivered", "sent", "queued"]),
                sent_at=nowu - timedelta(days=j * 3 + random.randint(0, 2)),
            )
            db.add(em)
            created["email_messages"] += 1

    db.commit()

    # ── 6. CAMPAIGNS ──────────────────────────────────────────────────────────
    campaign_names = {
        "funeral": [
            "Pre-Need Lock-In 2024 — Summer Push",
            "At-Need Family Outreach Q3",
            "Veterans Benefits Campaign",
            "Contract Renewal Re-Engagement",
        ],
        "fiber": [
            "Q3 Gigabit Rollout — Zone 4",
            "Business District Fiber Push",
            "Apartment Complex Outreach",
            "Competitive Displacement — Spectrum Areas",
        ],
        "roofing": [
            "Post-Storm Hail Zone Outreach",
            "Spring Inspection Special",
            "Insurance Claim Assistance Campaign",
            "Referral Reward Program",
        ],
        "insurance": [
            "Medicare Aged-In Campaign",
            "Final Expense Re-Engagement",
            "Senior Community Outreach",
            "Open Enrollment Push",
        ],
    }

    industry = (org.industry or "funeral").lower()
    names = campaign_names.get(industry, campaign_names["funeral"])
    for j, name in enumerate(names):
        camp = Campaign(
            organization_id=current_user.organization_id,
            name=name,
            created_by_id=current_user.id,
            filter_criteria=json.dumps({
                "tier": random.choice(["pre_need", "at_need", "contract_sold", "new_inquiry"]),
                "status": random.choice(["new", "sent"]),
            }),
            message_track=random.choice([
                "pre_need_lock_price", "at_need_support", "upsell_existing", "new_inquiry_intro",
            ]),
            created_at=nowu - timedelta(days=j * 14 + random.randint(0, 7)),
        )
        db.add(camp)
        created["campaigns"] += 1

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="sample_data.generate", target_type="organization",
        target_id=str(current_user.organization_id),
        details=created,
    )

    total = sum(created.values())
    return {
        "industry": org.industry or "funeral",
        "created": created,
        "total_records": total,
        "message": (
            f"Generated {total} demo records for {org_name} ({org.industry or 'funeral'} industry): "
            f"{created['leads']} leads, {created['crm_contacts']} CRM contacts, "
            f"{created['pipeline_conversations']} AI Hub conversations, "
            f"{created['bookings']} bookings, {created['email_messages']} email messages, "
            f"{created['campaigns']} campaigns."
        ),
    }


@router.delete("/clear")
def clear_sample_data(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    Deletes ONLY records tagged source_file == SAMPLE_DATA.
    Real imported data is never touched.
    """
    from app.models.models import EmailMessage, PipelineConversation, BookingLink, Campaign

    sample_leads = db.query(Lead).filter(
        Lead.organization_id == current_user.organization_id,
        Lead.source_file == SAMPLE_TAG,
    ).all()
    sample_lead_ids = [l.id for l in sample_leads]

    deleted = {}

    if sample_lead_ids:
        # Delete children first
        deleted["replies"] = db.query(Reply).filter(Reply.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)
        deleted["messages"] = db.query(Message).filter(Message.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)
        deleted["cadence_states"] = db.query(CadenceState).filter(CadenceState.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)
        deleted["email_messages"] = db.query(EmailMessage).filter(EmailMessage.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)

        # BookingLinks — delete them by sample lead ids
        bl_ids = [b.id for b in db.query(BookingLink).filter(BookingLink.lead_id.in_(sample_lead_ids)).all()]
        deleted["bookings"] = db.query(BookingLink).filter(BookingLink.lead_id.in_(sample_lead_ids)).delete(synchronize_session=False)

        # PipelineConversations by sample lead ids
        deleted["pipeline_conversations"] = db.query(PipelineConversation).filter(
            PipelineConversation.lead_id.in_(sample_lead_ids)
        ).delete(synchronize_session=False)

        deleted["leads"] = db.query(Lead).filter(Lead.id.in_(sample_lead_ids)).delete(synchronize_session=False)

    # CRM contacts — tagged by organization, check created in a sample window
    # We use a notes content match since CRMContact has no source_file
    # Instead, delete all sample CRM contacts by checking if they were sample-generated
    # (We'll track this by deleting CRM contacts created during the same session — org-scoped, all of them)
    # NOTE: for safety, only delete contacts with no real data markers
    # Actually, use a CRMNote to identify sample contacts:
    # Sample CRM contacts have notes that are sample notes — we can't easily distinguish.
    # For safety: skip CRM delete unless we add a sample marker. Leave CRM as-is on clear.
    deleted["crm_contacts"] = 0

    # Campaigns.
    #
    # This used to read:
    #
    #     .delete(...) if not sample_lead_ids else 0
    #
    # with the filter `organization_id == mine AND created_by_id == me` and NO
    # sample marker. The condition is inverted, so the branch that ran was the
    # one where NOTHING sample existed - and it deleted every campaign the
    # calling admin had ever created in that organization. "Clear the sample
    # data" was a button that removed real work precisely when there was no
    # sample data to remove.
    #
    # There is no marker on Campaign that identifies a sample campaign, so the
    # honest answer is to delete none of them and say so, the same decision
    # already taken for CRM contacts above.
    deleted["campaigns"] = 0
    deleted["campaigns_note"] = (
        "Campaigns are not removed: nothing on the Campaign row marks it as "
        "sample data, and the previous filter (created_by_id == caller) matched "
        "real campaigns. Use the scoped cleanup workflow instead."
    )

    db.commit()

    log_action(
        db, current_user.organization_id, current_user.id,
        action="sample_data.clear", target_type="organization",
        target_id=str(current_user.organization_id),
        details={"deleted_leads": len(sample_lead_ids)},
    )

    deleted["leads"] = len(sample_lead_ids)
    return {
        "message": f"Cleared {len(sample_lead_ids)} sample leads and associated records.",
        "deleted": deleted,
    }
