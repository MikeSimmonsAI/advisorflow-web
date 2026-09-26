"""Wholesale Real Estate — the platform-level acquisition and disposition engine.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
---------------------------------------------
This is a PLATFORM capability, gated by the `wholesale_real_estate` feature key
in app/services/entitlements.py, exactly like `campaigns` or `crm`. It is not an
EvoSys Pro feature, not a BookaBoost feature, and there is no brand name
anywhere in this module. A brand switches it on for an organization; the code is
shared; the data is not.

EVERY TABLE HERE CARRIES `organization_id`, NOT NULL, AND IS INDEXED BY IT.
That is the isolation guarantee, and it is enforced in the schema rather than in
a convention, for the same reason `Lead.organization_id` is NOT NULL: a
convention is one forgotten filter away from a cross-tenant read.

THE SELLER IS A `Lead`. THERE IS NO SECOND CONTACT DATABASE.
------------------------------------------------------------
Mike's instruction was explicit: "Reuse the existing contact/lead architecture if
practical. Do not unnecessarily create a second disconnected contact database."

It is practical, and it is the load-bearing decision in this module. A property
owner reached by SMS is a Lead, which means the wholesale module inherits, for
free and without a second implementation:

    DNC and opt-out          Lead.status == "dnc", SuppressionEntry, STOP handling
    channel permissions      allow_sms / allow_email / allow_voice (tri-state)
    consent of record        sms_consent + the exact wording agreed to
    test-record suppression  Lead.is_test, via app/services/test_records.py
    message history          Message / Reply / EmailMessage
    AI conversation          the existing conversation + cadence engine
    duplicate detection      ContactRegistry
    ownership and scope      lead_scope.authorized_lead_query

Re-implementing any one of those for sellers is how a wholesaler ends up texting
someone who said STOP. `WholesaleSellerProfile` is therefore an EXTENSION of a
Lead — a 1:1 side table of real-estate-specific facts — and never a replacement.

BUYERS ARE NOT LEADS, AND THAT IS ALSO DELIBERATE.
A cash buyer is a counterparty, not a prospect: they are not in the lead funnel,
they have no tier or cadence, and putting them in `leads` would put them in every
advisor's lead list and every campaign cohort. They get their own table, with
their own opt-out flag that the disposition path checks before every send.

NOTHING IN THIS MODULE INVENTS A NUMBER.
Every estimated figure carries a `*_source` column recording where it came from
— ESTIMATED, IMPORTED, MANUAL or VERIFIED — so a screen can say which is which
and an offer can never present a guess as a fact. See VALUE_SOURCES below.
"""

from datetime import datetime

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Text, Integer,
    Numeric, Index, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.models import Base, gen_uuid


# ── Provenance of a number ──────────────────────────────────────────────────
#
# THE WHOLE POINT OF THIS CONSTANT. An ARV of 285,000 means something completely
# different depending on whether a person typed it, a comp set produced it, or a
# county record confirmed it. Storing the number without the provenance is how a
# platform ends up presenting an estimate as data, which the mission forbids in
# as many words.
VALUE_ESTIMATED = "estimated"    # derived by this platform from other inputs
VALUE_IMPORTED = "imported"      # came from a file or a provider response
VALUE_MANUAL = "manual"          # a person typed it
VALUE_VERIFIED = "verified"      # confirmed against a primary source by a person
VALUE_SOURCES = (VALUE_ESTIMATED, VALUE_IMPORTED, VALUE_MANUAL, VALUE_VERIFIED)


# ── Who did it ──────────────────────────────────────────────────────────────
#
# `AuditLogEntry.actor_user_id` is NOT NULL, so the platform audit table cannot
# record an action taken by an automation or by the AI — there is no user to
# name. Material wholesale events therefore land in `wholesale_events` below,
# which has a nullable actor and an explicit actor TYPE. Actions taken by a
# signed-in person are ALSO written to the platform audit log, so the existing
# Audit Log screen keeps telling the truth. Two writes, one of them optional,
# rather than one write that cannot express half the actors.
ACTOR_USER = "user"
ACTOR_AI = "ai"
ACTOR_AUTOMATION = "automation"
ACTOR_API = "api"
ACTOR_SYSTEM = "system"
ACTOR_TYPES = (ACTOR_USER, ACTOR_AI, ACTOR_AUTOMATION, ACTOR_API, ACTOR_SYSTEM)

# ── Phase 3 vocabularies ────────────────────────────────────────────────────
# Stored as plain strings, like every other status in this module, for the
# reason the WholesaleDeal docstring gives: a database ENUM needs a migration
# to add a value, and these are the values most likely to grow.

# Where a buyer stands on THIS deal. Distinct from whether the email sent.
BUYER_RESPONSE_STATUSES = (
    "not_contacted", "sent", "delivered", "opened", "replied", "interested",
    "needs_info", "offer_submitted", "selected", "passed", "rejected",
    "no_response",
)

# `claimed` (Phase 6) is the buyer's own word and nothing more: they told us,
# on their own deal page, that proof of funds is already with us. It sits
# deliberately BEFORE `received` — nobody has looked at a document — and it
# exists so that claim can be recorded without being dressed up as evidence.
# Writing it into `requested` would lose the claim; writing it into `received`
# would invent a document.
POF_STATUSES = ("not_requested", "requested", "claimed", "received", "verified",
                "expired", "rejected")

TITLE_STATUSES = ("not_opened", "opened", "title_search", "issue_found",
                  "clear_to_close", "closing_scheduled", "closed", "cancelled")

# Why a deal died. Free text loses the ability to ask "how many did we lose on
# price", which is the question that changes what a wholesaler does next week.
LOST_REASONS = (
    "seller_changed_mind", "price_too_high", "unable_to_contact",
    "title_issue", "property_condition", "no_buyer", "contract_expired",
    "seller_sold_elsewhere", "duplicate", "bad_data", "other",
)

DOCUMENT_TYPES = (
    "purchase_contract", "assignment_agreement", "seller_disclosure",
    "addendum", "inspection", "repair_estimate", "proof_of_funds",
    "title_document", "closing_statement", "buyer_doc", "property_photo",
    "other",
)


class WholesaleSettings(Base):
    """One row per organization. Every tunable in the module lives here.

    THERE IS NO PLATFORM-WIDE OFFER FORMULA AND NO PLATFORM-WIDE THRESHOLD.
    Mike: "Do not hard-code '70% rule' as universal truth." The investor
    percentage, the wholesale fee, the qualification bands and the pipeline
    stages are all columns on THIS table, defaulted to something sane and
    changeable by the customer without a deploy.

    A missing row is not an error. `wholesale_settings.resolve()` creates one
    with defaults on first read, so a customer who has never opened the settings
    screen still has a coherent configuration.
    """

    __tablename__ = "wholesale_settings"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False, unique=True)

    # ── Offer formula ───────────────────────────────────────────────────────
    # MAO = (ARV x investor_percentage) - repairs - transaction costs - fee
    # Stored as a percentage (70.0 = 70%), NOT a fraction, because that is how
    # every investor in this business says it out loud and a screen that asks
    # for 0.7 gets 70 typed into it.
    investor_percentage = Column(Numeric(6, 3), nullable=False, default=70.0)
    default_wholesale_fee = Column(Numeric(12, 2), nullable=False, default=10000)
    # Transaction assumptions, as a percentage of ARV and a flat amount. Both,
    # because closing costs are partly proportional and partly not.
    transaction_cost_percent = Column(Numeric(6, 3), nullable=False, default=3.0)
    transaction_cost_flat = Column(Numeric(12, 2), nullable=False, default=0)
    # A floor under the spread. An offer that leaves the buyer less than this is
    # flagged, not blocked — the person deciding is the one who knows why.
    min_buyer_margin = Column(Numeric(12, 2), nullable=True)

    # ── Qualification bands ─────────────────────────────────────────────────
    # Scores at or above the threshold land in that band; below `low_threshold`
    # is LOW. `review_below_completeness` routes an under-informed seller to
    # REVIEW instead of scoring them, because a high score computed from three
    # known facts is a confident wrong answer.
    high_threshold = Column(Integer, nullable=False, default=70)
    medium_threshold = Column(Integer, nullable=False, default=45)
    review_below_completeness = Column(Integer, nullable=False, default=40)

    # ── Targeting ───────────────────────────────────────────────────────────
    # JSON arrays of strings. Empty/absent means "no restriction", never "none".
    markets = Column(Text, nullable=True)          # ["DFW", "Houston"]
    target_states = Column(Text, nullable=True)    # ["TX", "OK"]
    target_counties = Column(Text, nullable=True)
    target_cities = Column(Text, nullable=True)
    target_zips = Column(Text, nullable=True)

    # ── Pipeline ────────────────────────────────────────────────────────────
    # JSON array of {"key","label","order","terminal":bool}. NULL means the
    # module's default stage list (app/services/wholesale_pipeline.py), which is
    # the shape a new customer gets; editing it here never renames a stage a
    # deal already sits in, because stage keys are stored on the deal.
    pipeline_stages = Column(Text, nullable=True)

    # ── Providers ───────────────────────────────────────────────────────────
    # `*_provider` is a key from the relevant registry ("manual" always exists).
    # Credentials are NOT stored here; they are read from environment variables
    # named by the provider adapter, so a key never lands in the database or in
    # an API response. See app/services/wholesale_enrichment.py.
    enrichment_provider = Column(String, nullable=False, default="manual")
    enrichment_auto = Column(Boolean, nullable=False, default=False)
    property_data_provider = Column(String, nullable=False, default="manual")
    comps_provider = Column(String, nullable=False, default="manual")
    esign_provider = Column(String, nullable=False, default="manual")
    # Phase 6. How the assistant should PHRASE WHAT IT REPORTS BACK — the
    # one-sentence summary it writes for the operator after reading an owner's
    # message. It steers nothing else, and it cannot steer anything else:
    # nothing in this module writes to an owner, and the deterministic rules
    # (stop requests, escalation to a person) are applied before a model is
    # consulted at all. See `wholesale_ai._SYSTEM_PROMPT`.
    ai_tone = Column(String, nullable=True)   # professional | conversational | direct

    # ── Public contact (Phase 7.3 closeout) ──────────────────────────────────
    # What the Investor Deal Room and the Seller Portal show as THIS
    # organization's public phone and email. Organization-owned on purpose:
    # there is NO fallback to the platform (brand) support line or to any other
    # organization, so a white-label customer with nothing configured shows no
    # public contact rather than somebody else's. NULL = not configured.
    public_contact_phone = Column(String, nullable=True)
    public_contact_email = Column(String, nullable=True)

    # ── Public seller inquiry + seller SMS program ──────────────────────────
    # `public_intake_key` is how a public seller-inquiry form reaches THIS
    # organization: the website's server posts to
    # /site-intake/wholesale/{key}/seller-inquiry, and the key is looked up
    # here. No organization id, no display name, nothing a browser chooses.
    # NULL = this organization accepts no public seller inquiries.
    public_intake_key = Column(String, nullable=True, unique=True)
    # Who a public seller inquiry is assigned to (a user of THIS organization).
    # NULL = unassigned; the workspace admins are notified instead.
    inquiry_assignee_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # Email the workspace about new / re-engaged / held seller inquiries (see
    # wholesale_notify). OFF by default. Recipients: comma-separated; blank =
    # the assignee, else the workspace admins.
    inquiry_email_enabled = Column(Boolean, nullable=False, default=False)
    inquiry_email_recipients = Column(Text, nullable=True)
    # The seller SMS program. OFF by default and fail-closed: nothing is texted
    # under this program until an admin turns it on AND a Messaging Service is
    # configured AND the recipient holds program consent. The sender number is
    # recorded for display/audit; sends go through the Messaging Service.
    sms_program_enabled = Column(Boolean, nullable=False, default=False)
    sms_sender_number = Column(String, nullable=True)
    sms_messaging_service_sid = Column(String, nullable=True)   # MG...
    sms_campaign_sid = Column(String, nullable=True)            # CM... (after approval)
    sms_brand_sid = Column(String, nullable=True)               # BN...

    # ── Cost control ────────────────────────────────────────────────────────
    # A cap of 0 means "no paid calls at all", which is the honest default for a
    # module built to run lean. NULL means unlimited and has to be typed in on
    # purpose. Every paid provider call passes through `cost_control.admit()`.
    enrichment_daily_cap = Column(Integer, nullable=True, default=0)
    enrichment_monthly_cap = Column(Integer, nullable=True, default=0)
    enrichment_max_records_per_run = Column(Integer, nullable=True, default=25)
    enrichment_requires_approval = Column(Boolean, nullable=False, default=False)

    # ── Automation ──────────────────────────────────────────────────────────
    # Each switch controls ONE transition. They are off by default: an
    # automation nobody asked for that moves a deal on its own is worse than no
    # automation at all.
    auto_enrich_on_import = Column(Boolean, nullable=False, default=False)
    auto_stage_on_enrichment = Column(Boolean, nullable=False, default=True)
    auto_qualify_on_reply = Column(Boolean, nullable=False, default=True)
    auto_analysis_on_qualified = Column(Boolean, nullable=False, default=True)
    auto_match_on_contract = Column(Boolean, nullable=False, default=True)

    # ── Approval gates ──────────────────────────────────────────────────────
    # NOTHING BINDS THE COMPANY WITHOUT A PERSON. These default to True and the
    # module refuses the corresponding transition while the gate is on and no
    # approval exists. They are columns rather than constants because a
    # one-person shop may legitimately want the offer gate off; they are ON by
    # default because the money-and-legal default has to be the cautious one.
    require_offer_approval = Column(Boolean, nullable=False, default=True)
    require_contract_approval = Column(Boolean, nullable=False, default=True)
    require_assignment_approval = Column(Boolean, nullable=False, default=True)

    # ── AI ──────────────────────────────────────────────────────────────────
    ai_qualification_enabled = Column(Boolean, nullable=False, default=True)
    ai_persona_name = Column(String, nullable=True)
    ai_direction = Column(Text, nullable=True)   # extra instruction for the model

    created_at = Column(DateTime, server_default=None, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wholesale_settings_org", "organization_id"),
    )


class WholesaleProperty(Base):
    """A property. Almost every column is nullable, and that is the design.

    "Do not require every field. Allow incomplete properties to enter the
    pipeline and become enriched later." A property with nothing but a street
    address is a legitimate starting state — it is what a driving-for-dollars
    list looks like — and a schema that refuses it forces the user to invent
    data, which is the failure this module is most careful to avoid.

    The one required fact is the organization. Everything else can arrive later.
    """

    __tablename__ = "wholesale_properties"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True)

    street_address = Column(String, nullable=True)
    unit = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    county = Column(String, nullable=True)
    market = Column(String, nullable=True)
    parcel_apn = Column(String, nullable=True)

    property_type = Column(String, nullable=True)      # single_family, duplex, land, ...
    bedrooms = Column(Numeric(5, 1), nullable=True)
    bathrooms = Column(Numeric(5, 1), nullable=True)
    square_feet = Column(Integer, nullable=True)
    lot_size_sqft = Column(Integer, nullable=True)
    year_built = Column(Integer, nullable=True)

    estimated_value = Column(Numeric(14, 2), nullable=True)
    estimated_value_source = Column(String, nullable=True)   # VALUE_SOURCES
    # Mortgage information is recorded ONLY when it legitimately arrives — from
    # the owner, a title search or an import. Nothing in this module derives it.
    mortgage_balance = Column(Numeric(14, 2), nullable=True)
    mortgage_source = Column(String, nullable=True)
    liens_note = Column(Text, nullable=True)

    ownership_type = Column(String, nullable=True)     # individual, llc, trust, estate
    owner_name = Column(String, nullable=True)
    owner_mailing_street = Column(String, nullable=True)
    owner_mailing_city = Column(String, nullable=True)
    owner_mailing_state = Column(String, nullable=True)
    owner_mailing_zip = Column(String, nullable=True)
    occupancy_status = Column(String, nullable=True)   # owner_occupied, tenant, vacant, unknown

    acquisition_source = Column(String, nullable=True)  # "csv:2026-09-22 list", "manual", provider key
    source_detail = Column(String, nullable=True)
    tags = Column(Text, nullable=True)                  # JSON array of strings
    notes = Column(Text, nullable=True)

    # SANDBOX MARKING, same rule and same spelling as `Lead.is_test`. Test data
    # is excluded from every dashboard metric and from every outreach path, and
    # the flag is copied onto the deal and the seller lead so no path has to
    # join back here to find out. See app/services/wholesale_service.py.
    is_test = Column(Boolean, nullable=False, default=False)
    test_note = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsprop_org_created", "organization_id", "created_at"),
        Index("ix_wsprop_org_zip", "organization_id", "zip_code"),
        Index("ix_wsprop_org_state_city", "organization_id", "state", "city"),
        Index("ix_wsprop_org_assigned", "organization_id", "assigned_to_id"),
    )


class WholesaleSellerProfile(Base):
    """Real-estate facts about a seller. The PERSON is the Lead this points at.

    One row per (property, lead) pair: the same owner can be the seller of two
    properties and have a different asking price and timeline for each, and the
    same property can change hands. Contact details, consent, DNC and message
    history are NOT duplicated here — they live on the Lead, where every
    existing guard already reads them.

    THE STRUCTURED ANSWER COLUMNS ARE THE POINT. "Store structured answers
    instead of burying everything in conversation text." A qualification score
    computed by re-reading a transcript is a score nobody can check; these
    columns are what the AI extraction writes, what a human can correct, and
    what the qualification engine actually reads.
    """

    __tablename__ = "wholesale_seller_profiles"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    property_id = Column(String, ForeignKey("wholesale_properties.id", ondelete="CASCADE"),
                         nullable=False)

    owner_status = Column(String, nullable=True)        # owner_of_record, heir, agent, unknown
    relationship_note = Column(String, nullable=True)
    preferred_contact_method = Column(String, nullable=True)  # sms, email, phone

    # ── What the AI (or a person) actually established ───────────────────────
    is_available = Column(Boolean, nullable=True)       # tri-state: NULL = not asked
    considering_selling = Column(Boolean, nullable=True)
    asking_price = Column(Numeric(14, 2), nullable=True)
    asking_price_source = Column(String, nullable=True)
    timeline = Column(String, nullable=True)            # asap, 30_days, 90_days, 6_months, no_rush
    motivation = Column(Text, nullable=True)
    reason_for_selling = Column(String, nullable=True)
    property_condition = Column(String, nullable=True)  # excellent, good, fair, poor, distressed
    major_repairs = Column(Text, nullable=True)
    occupancy = Column(String, nullable=True)
    mortgage_note = Column(Text, nullable=True)         # only what they volunteered
    decision_makers = Column(String, nullable=True)
    best_callback_time = Column(String, nullable=True)
    # BOOKING SEMANTICS FOR A SELLER. A seller is never sent the platform's
    # self-service booking link; a person schedules the call or walkthrough.
    # appointment_status is one of wholesale_service.APPOINTMENT_STATUSES and
    # appointment_at is when it is (or was). "Booked" for a seller means
    # appointment_status == "scheduled" - it never flips the Lead to the
    # funeral-planning "booked" status and its post-booking concierge.
    appointment_status = Column(String, nullable=True)
    appointment_at = Column(DateTime, nullable=True)

    # ── Qualification, and the reason for it ────────────────────────────────
    qualification_band = Column(String, nullable=True)  # high / medium / low / review / excluded
    qualification_score = Column(Integer, nullable=True)
    qualification_reasons = Column(Text, nullable=True)  # JSON array of readable strings
    completeness = Column(Integer, nullable=True)        # 0-100, how much we actually know
    ai_summary = Column(Text, nullable=True)
    ai_intent = Column(String, nullable=True)            # see wholesale_ai.INTENTS
    ai_last_run_at = Column(DateTime, nullable=True)
    # Set when the AI could not run or could not be trusted. A deal in this
    # state is routed to a person rather than being scored optimistically.
    needs_human = Column(Boolean, nullable=False, default=False)
    needs_human_reason = Column(String, nullable=True)

    is_test = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("property_id", "lead_id", name="uq_wsseller_property_lead"),
        Index("ix_wsseller_org_lead", "organization_id", "lead_id"),
        Index("ix_wsseller_org_property", "organization_id", "property_id"),
    )


class WholesaleDeal(Base):
    """ONE deal = one property being worked. The deal room renders this row.

    "I should not have to jump through six unrelated areas to understand one
    deal." Everything with a lifecycle of its own — comps, offers, approvals,
    documents, buyer matches, outreach, events — points AT this row, so the deal
    room is one query plus its children rather than a tour of the schema.

    Stage is a STRING, not an enum type, because the stage list is configurable
    per organization (WholesaleSettings.pipeline_stages) and a Postgres enum
    cannot be extended by a customer. The same reasoning the platform already
    applies to `Lead.tier` and `Lead.status`, both of which are VARCHAR for
    exactly this reason — and the memory rule that goes with it: never call
    `.value` on these.
    """

    __tablename__ = "wholesale_deals"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("wholesale_properties.id", ondelete="CASCADE"),
                         nullable=False)
    seller_lead_id = Column(String, ForeignKey("leads.id"), nullable=True)
    seller_profile_id = Column(String, ForeignKey("wholesale_seller_profiles.id"), nullable=True)
    assigned_to_id = Column(String, ForeignKey("users.id"), nullable=True)

    stage = Column(String, nullable=False, default="new_property")
    stage_changed_at = Column(DateTime, default=datetime.utcnow)
    previous_stage = Column(String, nullable=True)
    lost_reason = Column(String, nullable=True)

    # ── Analysis workspace. Every assumption visible, every one editable. ────
    arv = Column(Numeric(14, 2), nullable=True)
    arv_source = Column(String, nullable=True)           # VALUE_SOURCES
    arv_method = Column(String, nullable=True)           # "comps:median", "manual", provider key
    repair_estimate = Column(Numeric(14, 2), nullable=True)
    repair_estimate_source = Column(String, nullable=True)
    repair_notes = Column(Text, nullable=True)
    # Snapshot of the formula inputs AT THE TIME the offer was calculated, so a
    # later settings change never silently rewrites the history of an offer.
    investor_percentage_used = Column(Numeric(6, 3), nullable=True)
    transaction_costs = Column(Numeric(14, 2), nullable=True)
    desired_wholesale_fee = Column(Numeric(12, 2), nullable=True)
    max_allowable_offer = Column(Numeric(14, 2), nullable=True)
    proposed_offer = Column(Numeric(14, 2), nullable=True)
    analysis_notes = Column(Text, nullable=True)
    analysis_updated_at = Column(DateTime, nullable=True)

    # ── Contract / acquisition ──────────────────────────────────────────────
    contract_price = Column(Numeric(14, 2), nullable=True)
    contract_status = Column(String, nullable=True)      # none, preparing, sent, signed, cancelled
    contract_signed_at = Column(DateTime, nullable=True)
    inspection_deadline = Column(Date, nullable=True)
    close_of_escrow_target = Column(Date, nullable=True)
    # Phase 3. The dates a contract actually turns on, kept apart because
    # "signed" is two events and an option period runs from the later one.
    contract_date = Column(Date, nullable=True)
    seller_signed_at = Column(Date, nullable=True)
    buyer_signed_at = Column(Date, nullable=True)
    effective_date = Column(Date, nullable=True)
    earnest_money = Column(Numeric(14, 2), nullable=True)
    earnest_money_due = Column(Date, nullable=True)
    earnest_money_received_at = Column(Date, nullable=True)
    option_fee = Column(Numeric(12, 2), nullable=True)
    closing_deadline = Column(Date, nullable=True)

    # ── Disposition / assignment ────────────────────────────────────────────
    assigned_buyer_id = Column(String, ForeignKey("wholesale_buyers.id"), nullable=True)
    buyer_price = Column(Numeric(14, 2), nullable=True)
    assignment_fee = Column(Numeric(14, 2), nullable=True)
    assignment_status = Column(String, nullable=True)    # none, pending, signed, cancelled
    assignment_signed_at = Column(DateTime, nullable=True)

    # ── Title and closing ───────────────────────────────────────────────────
    title_company = Column(String, nullable=True)
    title_contact = Column(String, nullable=True)
    title_status = Column(String, nullable=True)         # not_opened, opened, clear, issue
    title_opened_at = Column(DateTime, nullable=True)
    closing_date = Column(Date, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    # THE MONEY. Recorded once, at close, by a person. Nothing computes it.
    wholesale_fee_collected = Column(Numeric(14, 2), nullable=True)
    deal_result = Column(String, nullable=True)          # closed_won, closed_lost, cancelled

    # ── Phase 3: the rest of what a title company actually asks for ─────────
    title_escrow_officer = Column(String, nullable=True)
    title_phone = Column(String, nullable=True)
    title_email = Column(String, nullable=True)
    title_commitment_received_at = Column(Date, nullable=True)
    title_issues = Column(Text, nullable=True)
    closing_time = Column(String, nullable=True)
    closing_location = Column(String, nullable=True)
    closing_status = Column(String, nullable=True)
    other_costs = Column(Numeric(14, 2), nullable=True)
    lost_reason_detail = Column(Text, nullable=True)
    # Who chose the buyer, and when. A disposition decision with no name on it
    # is the one nobody can explain three months later.
    buyer_selected_at = Column(DateTime, nullable=True)
    buyer_selected_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    # Set at close. The economics stop being casually editable and a correction
    # has to be a deliberate, audited act rather than a typo.
    economics_locked = Column(Boolean, nullable=False, default=False)

    # ── Phase 5. THE PUBLICATION BOUNDARY. ──────────────────────────────────
    # Nothing on this deal reaches an outside reader unless a column below says
    # so. These are not display preferences: the buyer and seller serializers
    # are whitelists that read these, so a field nobody published cannot leak
    # through a forgotten `delete payload.x` in a browser.
    buyer_room_published = Column(Boolean, nullable=False, default=False)
    buyer_room_published_at = Column(DateTime, nullable=True)
    buyer_room_published_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    # Written FOR investors, by a person. Never the internal analysis notes and
    # never the seller conversation — those are a different audience's words.
    buyer_room_summary = Column(Text, nullable=True)
    buyer_room_condition = Column(Text, nullable=True)
    # The price shown to investors. Deliberately its own column rather than
    # `buyer_price`: what we are asking and what a chosen buyer agreed to are
    # two different facts, and publishing must never reveal the second.
    buyer_room_asking_price = Column(Numeric(14, 2), nullable=True)
    # Off unless switched on. ARV and repairs are our workings, not a listing.
    buyer_room_show_arv = Column(Boolean, nullable=False, default=False)
    buyer_room_show_repairs = Column(Boolean, nullable=False, default=False)
    buyer_room_show_comps = Column(Boolean, nullable=False, default=False)

    seller_room_published = Column(Boolean, nullable=False, default=False)
    seller_room_published_at = Column(DateTime, nullable=True)
    seller_room_published_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    # One message the seller sees on their page, written by a person.
    seller_room_message = Column(Text, nullable=True)
    # Who the seller should call. Falls back to nothing rather than to an
    # internal user's direct line that nobody meant to publish.
    seller_room_contact_name = Column(String, nullable=True)
    seller_room_contact_phone = Column(String, nullable=True)
    seller_room_contact_email = Column(String, nullable=True)

    # ── Phase 5. Getting paid, recorded as facts rather than inferred. ───────
    title_file_number = Column(String, nullable=True)
    # not_funded | funding_scheduled | funded. A closing date that has passed
    # is not funding, and this column is what stops the board assuming it was.
    funding_status = Column(String, nullable=True)
    funded_at = Column(DateTime, nullable=True)
    fee_collected_at = Column(DateTime, nullable=True)
    fee_payment_method = Column(String, nullable=True)
    fee_payment_reference = Column(String, nullable=True)
    fee_recorded_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    fee_variance_note = Column(Text, nullable=True)

    is_test = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsdeal_org_stage", "organization_id", "stage"),
        Index("ix_wsdeal_org_created", "organization_id", "created_at"),
        Index("ix_wsdeal_org_assigned", "organization_id", "assigned_to_id"),
        Index("ix_wsdeal_org_property", "organization_id", "property_id"),
    )


class WholesaleComp(Base):
    """A comparable sale. Entered by a person or returned by a comps provider.

    NOTHING IN THIS MODULE FABRICATES A COMP. There is no generator, no
    "estimated comp", no synthetic fill when a provider is unavailable. With no
    provider connected the only way a row appears here is that somebody typed
    it, and the ARV derived from these rows says so in `WholesaleDeal.arv_method`.

    `included` is what makes the ARV auditable: the user picks which comps count,
    and the math runs over exactly the included set.
    """

    __tablename__ = "wholesale_comps"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)

    street_address = Column(String, nullable=True)
    city = Column(String, nullable=True)
    state = Column(String, nullable=True)
    zip_code = Column(String, nullable=True)
    sale_price = Column(Numeric(14, 2), nullable=True)
    sale_date = Column(Date, nullable=True)
    square_feet = Column(Integer, nullable=True)
    bedrooms = Column(Numeric(5, 1), nullable=True)
    bathrooms = Column(Numeric(5, 1), nullable=True)
    distance_miles = Column(Numeric(6, 2), nullable=True)
    property_type = Column(String, nullable=True)
    source = Column(String, nullable=False, default=VALUE_MANUAL)
    notes = Column(Text, nullable=True)
    # `included` is about the ARITHMETIC, not about existence. A comp excluded
    # from the ARV is still a comp somebody found and still on the screen —
    # which is why Phase 3 stopped calling the delete button "Remove".
    included = Column(Boolean, nullable=False, default=True)
    year_built = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wscomp_org_deal", "organization_id", "deal_id"),
    )


class WholesaleApproval(Base):
    """A decision a person made, with the inputs it was made on.

    THE LEGAL AND FINANCIAL GUARDRAIL. The AI recommends; this table is where a
    human either agrees or does not. `inputs` is a JSON snapshot of the numbers
    the recommendation was built from, captured at request time, because "what
    did we know when we approved this" is the entire question an approval record
    exists to answer — and a later edit to the deal must not be able to rewrite
    it.
    """

    __tablename__ = "wholesale_approvals"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)

    kind = Column(String, nullable=False)          # offer, contract, assignment, stage
    status = Column(String, nullable=False, default="pending")   # pending, approved, rejected
    amount = Column(Numeric(14, 2), nullable=True)
    recommendation = Column(Text, nullable=True)   # what was proposed, in words
    reasoning = Column(Text, nullable=True)        # why, in words
    inputs = Column(Text, nullable=True)           # JSON snapshot of the numbers
    requested_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    requested_by_actor = Column(String, nullable=False, default=ACTOR_USER)
    approver_id = Column(String, ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    comments = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsapproval_org_status", "organization_id", "status"),
        Index("ix_wsapproval_org_deal", "organization_id", "deal_id"),
    )


class WholesaleDocument(Base):
    """A document slot on a deal, and the state of the thing in it.

    THIS MODULE SHIPS NO LEGAL FORMS. "Do NOT invent state-specific legal
    documents and represent them as approved legal forms." There is no purchase
    contract template in this codebase and none is generated. What exists is the
    WORKFLOW around a document somebody else produced: a named slot, an upload,
    parties, a signature state, timestamps and an audit trail.

    E-sign is pluggable and absent by default. `signature_status` moves to
    "signed" either because a provider said so or because a person uploaded a
    signed copy and marked it — and the manual path is fully supported, not a
    degraded one.
    """

    __tablename__ = "wholesale_documents"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)

    # purchase_contract, assignment_agreement, amendment, disclosure, title,
    # closing, proof_of_funds, buyer_doc, other
    doc_type = Column(String, nullable=False)
    title = Column(String, nullable=True)
    status = Column(String, nullable=False, default="needed")   # needed, uploaded, sent, executed, void
    signature_status = Column(String, nullable=False, default="none")  # none, out_for_signature, partially_signed, signed
    signature_provider = Column(String, nullable=True)
    external_ref = Column(String, nullable=True)     # provider envelope id, when there is one
    parties = Column(Text, nullable=True)            # JSON array of {name, role, email}
    # Phase 1/2 kept only what the operator TYPED about a file kept elsewhere.
    # Both are still here: a deal drawer that lists a document somebody has in
    # Dropbox is more useful than one that pretends it does not exist.
    file_name = Column(String, nullable=True)
    file_url = Column(String, nullable=True)
    # Phase 3: the actual stored object, when there is one.
    file_id = Column(String, ForeignKey("wholesale_files.id"), nullable=True)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id"), nullable=True)
    uploaded_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    uploaded_at = Column(DateTime, nullable=True)
    executed_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)

    # Phase 5 publication boundary. Both default FALSE: a document is internal
    # until somebody publishes it to a named audience. A purchase contract is
    # not a buyer document and an assignment agreement is not a seller
    # document, and neither mistake should be one forgotten filter away.
    buyer_visible = Column(Boolean, nullable=False, default=False)
    seller_visible = Column(Boolean, nullable=False, default=False)
    # Phase 5 lifecycle. `status` already existed; these two record WHEN the
    # two states nobody can prove without a provider actually happened, so they
    # stay NULL until something real sets them.
    viewed_at = Column(DateTime, nullable=True)
    superseded_by_id = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsdoc_org_deal", "organization_id", "deal_id"),
    )


class WholesaleBuyer(Base):
    """A cash buyer. A counterparty, not a lead — see this module's docstring.

    `do_not_contact` is this table's own opt-out and the disposition path checks
    it before every send. A buyer who asks to stop hearing about deals is not a
    DNC in the TCPA sense that `Lead.status` records, and conflating the two
    would put a business counterparty into a compliance suppression list — the
    same category error the Lead model's own comments warn about for duplicates.
    """

    __tablename__ = "wholesale_buyers"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    entity_type = Column(String, nullable=False, default="company")  # person, company
    company_name = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    preferred_channel = Column(String, nullable=False, default="email")  # email, sms, phone

    # ── Proof of funds and reliability ──────────────────────────────────────
    cash_verified = Column(Boolean, nullable=False, default=False)
    proof_of_funds_on_file = Column(Boolean, nullable=False, default=False)
    proof_of_funds_expires = Column(Date, nullable=True)
    typical_close_days = Column(Integer, nullable=True)
    past_deals_count = Column(Integer, nullable=False, default=0)
    # 1-5, typed by whoever works with them. Not computed, because a reliability
    # score the platform invented would be an opinion presented as a measurement.
    reliability_rating = Column(Integer, nullable=True)

    source = Column(String, nullable=True)      # referral, reia, import:<file>, provider key
    source_detail = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    do_not_contact = Column(Boolean, nullable=False, default=False)
    do_not_contact_reason = Column(String, nullable=True)
    is_test = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    buy_boxes = relationship("WholesaleBuyBox", back_populates="buyer",
                             cascade="all, delete-orphan", passive_deletes=True)

    __table_args__ = (
        Index("ix_wsbuyer_org_active", "organization_id", "is_active"),
        Index("ix_wsbuyer_org_email", "organization_id", "email"),
    )


class WholesaleBuyBox(Base):
    """What a buyer wants, as STRUCTURED DATA. Never only as a note.

    "Buy boxes must be STRUCTURED DATA. Do not store the entire buy box only as
    a note." A note cannot be matched against; a column can. A buyer may have
    several — an investor who buys rentals in one county and flips in another is
    two boxes, not one box with a paragraph in it — and the matching engine
    scores a deal against each and keeps the best.

    Geography and type fields are JSON arrays; an EMPTY OR ABSENT array means
    "no restriction on this dimension", never "matches nothing". That asymmetry
    is deliberate and is the one thing to get right when reading this table: a
    buyer who never told us their counties should not silently stop matching.
    """

    __tablename__ = "wholesale_buy_boxes"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)

    label = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    markets = Column(Text, nullable=True)          # JSON array
    states = Column(Text, nullable=True)
    counties = Column(Text, nullable=True)
    cities = Column(Text, nullable=True)
    zips = Column(Text, nullable=True)
    property_types = Column(Text, nullable=True)   # JSON array
    strategies = Column(Text, nullable=True)       # JSON array: flip, rental, brrrr, land, other

    min_price = Column(Numeric(14, 2), nullable=True)
    max_price = Column(Numeric(14, 2), nullable=True)
    min_beds = Column(Numeric(5, 1), nullable=True)
    max_beds = Column(Numeric(5, 1), nullable=True)
    min_baths = Column(Numeric(5, 1), nullable=True)
    min_sqft = Column(Integer, nullable=True)
    max_sqft = Column(Integer, nullable=True)
    max_year_built = Column(Integer, nullable=True)
    min_year_built = Column(Integer, nullable=True)
    # light, moderate, heavy, full_gut — how much work they will take on.
    rehab_tolerance = Column(String, nullable=True)
    min_spread = Column(Numeric(14, 2), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    buyer = relationship("WholesaleBuyer", back_populates="buy_boxes")

    __table_args__ = (
        Index("ix_wsbuybox_org_buyer", "organization_id", "buyer_id"),
    )


class WholesaleBuyerMatch(Base):
    """A scored match between a deal and a buyer, WITH ITS REASONS.

    "Show WHY the buyer matched. Do not make the score a black box." `factors`
    is a JSON array of {dimension, matched, weight, detail} — one entry per
    dimension the engine considered, including the ones that did not match, so
    the screen can show a 74% and explain the missing 26%.
    """

    __tablename__ = "wholesale_buyer_matches"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)
    buy_box_id = Column(String, ForeignKey("wholesale_buy_boxes.id", ondelete="SET NULL"),
                        nullable=True)

    score = Column(Integer, nullable=False, default=0)     # 0-100
    factors = Column(Text, nullable=True)                  # JSON array, see docstring
    disqualified = Column(Boolean, nullable=False, default=False)
    disqualified_reason = Column(String, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("deal_id", "buyer_id", name="uq_wsmatch_deal_buyer"),
        Index("ix_wsmatch_org_deal_score", "organization_id", "deal_id", "score"),
    )


class WholesaleBuyerOutreach(Base):
    """One deal sent to one buyer, and everything that came back.

    The message body is stored because what we told a buyer about a property is
    a fact with consequences. Seller identity is NOT in it: `build_buyer_message`
    composes from the property and the numbers only, and this table is the
    evidence of that.
    """

    __tablename__ = "wholesale_buyer_outreach"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=False)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"), nullable=False)

    channel = Column(String, nullable=False, default="email")
    # queued, sent, delivered, failed, replied, interested, passed,
    # requested_info, offer_submitted, accepted
    status = Column(String, nullable=False, default="queued")
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=True)
    asking_price = Column(Numeric(14, 2), nullable=True)
    sent_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    sent_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)
    response_note = Column(Text, nullable=True)
    offer_amount = Column(Numeric(14, 2), nullable=True)
    # Set when a send was NOT attempted, and why. A skipped send that leaves no
    # trace is how somebody concludes the system is broken. Never silent.
    blocked_reason = Column(String, nullable=True)

    # ── What the provider actually said (Phase 2) ───────────────────────────
    #
    # Phase 1 recorded that a deal sheet had been PREPARED. These five columns
    # are the difference between that and knowing it was SENT.
    #
    # `attempts` and `last_attempt_at` are what make a retry safe: the send path
    # refuses to re-send a row that already succeeded unless a person explicitly
    # asks, and a failed row carries its own history rather than being
    # indistinguishable from one nobody has tried. A double-click on Send must
    # not mail a buyer twice.
    provider_message_id = Column(String, nullable=True)
    provider_error = Column(String, nullable=True)
    provider_result = Column(Text, nullable=True)     # JSON, as the provider returned it
    attempts = Column(Integer, nullable=False, default=0)
    last_attempt_at = Column(DateTime, nullable=True)

    # ── Phase 3: the disposition desk's own columns ─────────────────────────
    #
    # `status` above is the message's fate. These are the BUYER's, and they are
    # different questions: a deal sheet can be delivered to somebody who never
    # answers, and a buyer can submit an offer by phone on a sheet that failed
    # to send. Keeping them apart is what lets the board be honest about both.
    delivered_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    pof_status = Column(String, nullable=True)        # see POF_STATUSES
    pof_file_id = Column(String, ForeignKey("wholesale_files.id"), nullable=True)
    is_selected = Column(Boolean, nullable=False, default=False)
    target_close_date = Column(Date, nullable=True)
    # Phase 6. What the investor actually said when they answered from their
    # own link. The proposed closing date and the proof-of-funds status already
    # had columns above and are REUSED rather than duplicated; only these are
    # new. `respondent_*` is deliberately separate from the buyer's CRM record:
    # a public page must never silently rewrite the contact details somebody
    # keeps in their own buyer list.
    offer_financing = Column(String, nullable=True)   # cash, hard_money, ...
    respondent_name = Column(String, nullable=True)
    respondent_email = Column(String, nullable=True)
    respondent_phone = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsoutreach_org_deal", "organization_id", "deal_id"),
        Index("ix_wsoutreach_org_buyer", "organization_id", "buyer_id"),
        Index("ix_wsoutreach_org_status", "organization_id", "status"),
    )


class WholesaleEnrichmentRequest(Base):
    """One attempt to find contact information for an owner.

    A ROW EXISTS FOR EVERY ATTEMPT, INCLUDING THE FAILURES. "Skip trace fails →
    mark ENRICHMENT FAILED → allow retry/manual entry." A failure that leaves no
    record is indistinguishable from an owner nobody has tried yet, and the
    difference decides what a person does next.

    `result` holds the provider's normalized response — the shape defined by
    `EnrichmentResult` in app/services/wholesale_enrichment.py, never the raw
    vendor payload — so a second provider can be added without a reader change.
    """

    __tablename__ = "wholesale_enrichment_requests"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    property_id = Column(String, ForeignKey("wholesale_properties.id", ondelete="CASCADE"),
                         nullable=True)
    lead_id = Column(String, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)

    provider = Column(String, nullable=False, default="manual")
    status = Column(String, nullable=False, default="pending")
    # pending, succeeded, no_match, failed, not_configured, capped, manual
    requested_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    requested_by_actor = Column(String, nullable=False, default=ACTOR_USER)
    inputs = Column(Text, nullable=True)       # JSON: what we asked with
    result = Column(Text, nullable=True)       # JSON: normalized EnrichmentResult
    confidence = Column(Integer, nullable=True)
    error = Column(String, nullable=True)
    billable = Column(Boolean, nullable=False, default=False)
    cost_cents = Column(Integer, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsenrich_org_created", "organization_id", "created_at"),
        Index("ix_wsenrich_org_status", "organization_id", "status"),
        Index("ix_wsenrich_org_property", "organization_id", "property_id"),
    )


class WholesaleEvent(Base):
    """The module's own audit trail — the one that can name a non-human actor.

    See the ACTOR_* note at the top of this file for why this exists alongside
    `AuditLogEntry` rather than instead of it. A material action taken by a
    signed-in person is written to BOTH: here, and to the platform audit log
    that the existing Audit Log screen renders.
    """

    __tablename__ = "wholesale_events"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"), nullable=True)
    property_id = Column(String, ForeignKey("wholesale_properties.id", ondelete="CASCADE"),
                         nullable=True)

    action = Column(String, nullable=False)        # "deal.stage_changed", "enrichment.returned"
    actor_type = Column(String, nullable=False, default=ACTOR_SYSTEM)
    actor_user_id = Column(String, ForeignKey("users.id"), nullable=True)
    actor_label = Column(String, nullable=True)    # "AI qualification", "import automation"
    summary = Column(String, nullable=True)
    before_state = Column(Text, nullable=True)
    after_state = Column(Text, nullable=True)
    details = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsevent_org_created", "organization_id", "created_at"),
        Index("ix_wsevent_org_deal", "organization_id", "deal_id"),
        Index("ix_wsevent_org_action", "organization_id", "action"),
    )



# ── Phase 3: files, and the negotiation that was previously only a number ───

# What a property photo is OF. A wholesaler sends an investor a set of rooms and
# problems, not a pile of files, and "the third one is the roof" is not a caption
# anybody writes. The list is presentational: an unknown value is shown as itself
# rather than dropped, so an organization that adds its own is not punished.
PHOTO_CATEGORIES = (
    "exterior_front", "exterior_rear", "exterior_side", "kitchen",
    "living_room", "bedroom", "bathroom", "garage", "roof", "hvac",
    "electrical", "plumbing", "foundation", "damage", "repair_area",
    "yard", "neighborhood", "other",
)


class WholesaleFile(Base):
    """ONE row per stored object, whatever it is a picture or a document of.

    Property photos, comp photos, purchase contracts, settlement statements and
    a buyer's proof of funds are all rows here. They differ by `kind` and by
    which column points at them, not by having their own upload code — "do not
    create separate upload implementations for every tab" is the instruction,
    and one table is how that stays true.

    `storage_key` NEVER LEAVES THE SERVER. There is no public URL column and
    that is deliberate: a signed purchase contract is not a public object, and
    an unguessable link is not authorization. Bytes are served by an endpoint
    that re-checks the organization every time.

    The uploader's own filename is kept in `original_filename` for display and
    is never part of the storage key — see `wholesale_files.py` for why.
    """

    __tablename__ = "wholesale_files"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)

    # Exactly one of these is normally set; the pairing is what a delete and an
    # authorization check both walk.
    property_id = Column(String, ForeignKey("wholesale_properties.id", ondelete="CASCADE"),
                         nullable=True)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"),
                     nullable=True)
    buyer_id = Column(String, ForeignKey("wholesale_buyers.id", ondelete="CASCADE"),
                      nullable=True)
    comp_id = Column(String, ForeignKey("wholesale_comps.id", ondelete="CASCADE"),
                     nullable=True)

    kind = Column(String, nullable=False)          # property_photo, comp_photo, document
    storage_backend = Column(String, nullable=False)   # s3 | local
    storage_key = Column(String, nullable=False)
    original_filename = Column(String, nullable=True)
    content_type = Column(String, nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    checksum = Column(String, nullable=True)       # sha256, so a re-upload is detectable

    caption = Column(String, nullable=True)
    is_primary = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)

    # Phase 5. What the picture is of, and who is allowed to see it.
    # `category` is a plain string against PHOTO_CATEGORIES rather than an enum
    # for the same reason `stage` is: the list is presentational and a customer
    # may want their own. An unrecognised value renders as itself.
    category = Column(String, nullable=True)
    # FALSE BY DEFAULT, AND THAT IS THE POINT. A photo of the inside of
    # somebody's house does not become an investor-facing marketing asset
    # merely because it was uploaded. A person decides, per file, and the buyer
    # serializer reads this column rather than trusting a filter in a browser.
    buyer_visible = Column(Boolean, nullable=False, default=False)
    # Phase 6. The owner's own transaction page may show a photo of their own
    # house. A SEPARATE decision from the investor one, not the same switch
    # under another name: a damage close-up is often exactly right for an
    # investor and exactly wrong for the person living there.
    seller_visible = Column(Boolean, nullable=False, default=False)

    uploaded_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsfile_org_property", "organization_id", "property_id"),
        Index("ix_wsfile_org_deal", "organization_id", "deal_id"),
        Index("ix_wsfile_org_buyer", "organization_id", "buyer_id"),
        Index("ix_wsfile_org_kind", "organization_id", "kind"),
    )


# Where an offer came from. A negotiation has two sides and the record has to
# say which one moved, or the history reads as a list of numbers.
OFFER_FROM_US = "us"
OFFER_FROM_SELLER = "seller"

OFFER_STATUSES = ("draft", "approval_pending", "approved", "presented",
                  "countered", "accepted", "rejected", "expired", "withdrawn")


class WholesaleOffer(Base):
    """One number, in one direction, at one moment — and what came of it.

    Phase 1 and 2 stored only the CURRENT proposed offer on the deal, which
    answers "what are we offering" and cannot answer "what has happened in this
    negotiation". A wholesaler needs the second one: an owner who countered
    twice and came down $18,000 is a different conversation from one who has
    not moved, and the deal row alone cannot tell them apart.

    This table does not replace `WholesaleApproval` and does not weaken it. An
    offer that needs a person's approval still gets an approval row with its own
    immutable snapshot of the inputs; this row points at it. The approval is the
    guardrail, the offer is the history.
    """

    __tablename__ = "wholesale_offers"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    deal_id = Column(String, ForeignKey("wholesale_deals.id", ondelete="CASCADE"),
                     nullable=False)
    approval_id = Column(String, ForeignKey("wholesale_approvals.id"), nullable=True)

    direction = Column(String, nullable=False, default=OFFER_FROM_US)
    amount = Column(Numeric(14, 2), nullable=True)
    status = Column(String, nullable=False, default="draft")
    # The MAO in force when this number was set down, so a later settings change
    # cannot make an old offer look reckless or prudent in hindsight.
    mao_at_time = Column(Numeric(14, 2), nullable=True)
    notes = Column(Text, nullable=True)

    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_by_actor = Column(String, nullable=False, default=ACTOR_USER)
    presented_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_wsoffer_org_deal", "organization_id", "deal_id"),
        Index("ix_wsoffer_org_created", "organization_id", "created_at"),
    )


class WholesaleContractTemplate(Base):
    """A contract form THIS ORGANIZATION supplied, stored so it can be reused.

    READ THIS BEFORE ADDING ANYTHING TO THIS TABLE.

    Nothing in this module drafts, generates, assembles or completes a legal
    contract. Not from a template, not from a clause library, not from a model.
    A wholesale assignment is a binding real-property contract whose sufficiency
    is a question of state law and of the facts of the deal, and software that
    produces one without a lawyer having written it is producing a liability
    with a confident font.

    So what this row holds is a FILE THE CUSTOMER GAVE US and the facts about
    where it came from:

        name            what they call it ("TREC 20-18 + our assignment rider")
        jurisdiction    free text, exactly as they typed it. Not validated,
                        because a validated state code would imply this module
                        knows the form is valid there, and it does not.
        source_note     who produced it. The screen asks for this and shows it
                        back, because "our attorney, March 2026" and "found it
                        online" are different documents and the person choosing
                        one should see which they are choosing.
        file_id         the stored bytes, in the same media store as everything
                        else. No text is extracted and no field is merged.
        guidance        the customer's own note to whoever uses it next.

    Using one is a two-step human act: pick the template, then get a FILL SHEET
    of this deal's known facts — address, parties, price, dates — to carry into
    their own document. The fill sheet is data this module already holds, laid
    out for copying. It is not a draft, and it contains no contract language.

    Archived rather than deleted: a deal that used a template must still be able
    to say which one, and a hard delete would break that with no warning.
    """

    __tablename__ = "wholesale_contract_templates"

    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id"), nullable=False)
    name = Column(String, nullable=False)
    doc_type = Column(String, nullable=True)        # purchase_agreement, assignment, ...
    jurisdiction = Column(String, nullable=True)    # free text, as typed
    source_note = Column(String, nullable=True)     # who produced this form
    guidance = Column(Text, nullable=True)          # the customer's own note
    file_id = Column(String, ForeignKey("wholesale_files.id"), nullable=True)
    file_name = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    archived_at = Column(DateTime, nullable=True)
    created_by_id = Column(String, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        Index("ix_ws_contract_templates_org", "organization_id", "is_active"),
    )
