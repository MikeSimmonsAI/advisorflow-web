"""SERVICE OPERATIONS (Issue #452 / #506) - what happens AFTER a service
company wins a customer: its customers, their locations, jobs, recurring
service, the team that does the work, the record of the work, and invoices.

    EvoSys platform
      -> service company        (an existing Organization, never a new one)
          -> ITS customer       (ServiceCustomer - not an EvoSys customer)
              -> service locations
                  -> service jobs

TENANT RULE: every row carries a NOT NULL organization_id (the service
company) and every query in app/services/service_ops filters on it first.
Identity is the platform's own User; nobody gets a second login system.

New tables only - created by Base.metadata.create_all, no destructive
migration and no change to an existing table.

TIME: every instant is stored in UTC (naive UTC, the platform convention);
the IANA zone the work happens in is stored beside it.
"""
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.sql import func

from app.models.models import Base, gen_uuid


def _org():
    return Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)


def _fk(table: str, nullable: bool = True, ondelete: str = "SET NULL"):
    return Column(String, ForeignKey("%s.id" % table, ondelete=ondelete), nullable=nullable, index=True)


class ServiceOpsSettings(Base):
    """Per service company: what is switched on (entitlements) and its
    business details for estimates/invoices. No row = everything OFF."""
    __tablename__ = "service_ops_settings"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = Column(String, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
                             unique=True)
    # Entitlements (#19): switched on by EvoSys, never automatically. No prices here.
    ops_enabled = Column(Boolean, nullable=False, default=False)           # SERVICE OPERATIONS
    advanced_enabled = Column(Boolean, nullable=False, default=False)      # ADVANCED: invoice PDFs, reporting
    buyer_portal_enabled = Column(Boolean, nullable=False, default=False)  # optional buyer portal
    ai_assist_enabled = Column(Boolean, nullable=False, default=False)     # AI suggestions (gateway + budget)
    timezone = Column(String(64), nullable=True)
    business_name = Column(String(200), nullable=True)
    billing_address = Column(Text, nullable=True)
    billing_email = Column(String(200), nullable=True)
    billing_phone = Column(String(40), nullable=True)
    invoice_prefix = Column(String(12), nullable=False, default="INV")
    next_invoice_seq = Column(Integer, nullable=False, default=1001)
    estimate_prefix = Column(String(12), nullable=False, default="EST")
    next_estimate_seq = Column(Integer, nullable=False, default=1001)
    payment_terms_days = Column(Integer, nullable=False, default=30)
    payment_instructions = Column(Text, nullable=True)
    default_tax_rate = Column(Float, nullable=False, default=0.0)          # percent, e.g. 8.25
    updated_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ServiceCustomer(Base):
    """The service company's OWN customer (a property manager, a restaurant,
    a homeowner). Never an EvoSys master prospect."""
    __tablename__ = "service_customers"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    name = Column(String(200), nullable=False)
    kind = Column(String(20), nullable=False, default="business")          # business / individual
    contact_name = Column(String(160), nullable=True)
    contact_email = Column(String(200), nullable=True)
    contact_phone = Column(String(40), nullable=True)
    contact_preference = Column(String(20), nullable=True)                 # email / phone / text / any
    billing_name = Column(String(200), nullable=True)
    billing_address = Column(Text, nullable=True)
    billing_email = Column(String(200), nullable=True)
    notes = Column(Text, nullable=True)
    # Traceable link to the Acquisition record it came from (sales -> delivery).
    source_opportunity_id = _fk("acquisition_opportunities")
    status = Column(String(12), nullable=False, default="active")          # active / archived
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index("ix_service_customers_org_name", "organization_id", "name"),)


class ServiceLocation(Base):
    __tablename__ = "service_locations"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    address = Column(String(300), nullable=True)
    city = Column(String(120), nullable=True)
    state = Column(String(40), nullable=True)
    postal_code = Column(String(20), nullable=True)
    service_category = Column(String(40), nullable=False, default="other")  # cleaning / window_washing / ...
    facility_type = Column(String(60), nullable=True)                      # office, storefront, home, warehouse ...
    contact_name = Column(String(160), nullable=True)
    contact_phone = Column(String(40), nullable=True)
    contact_email = Column(String(200), nullable=True)
    contact_preference = Column(String(20), nullable=True)
    # SENSITIVE: gate codes, alarm codes, key boxes. Managers and the people
    # assigned to this location / its jobs only - enforced in the service layer.
    access_instructions = Column(Text, nullable=True)
    special_requirements = Column(Text, nullable=True)
    assigned_member_ids = Column(Text, nullable=True)                      # JSON list of ServiceTeamMember ids
    standard_duration_min = Column(Integer, nullable=True)
    preferred_windows = Column(Text, nullable=True)                        # JSON [{"days":[0..6],"start":"08:00","end":"12:00"}]
    timezone = Column(String(64), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ServiceTeamMember(Base):
    """A person's role in the service company's OPERATIONS. Identity is the
    platform User; this row only says what they may do in Service Operations."""
    __tablename__ = "service_team_members"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ops_role = Column(String(20), nullable=False, default="field_worker")
    team_name = Column(String(80), nullable=True)
    active = Column(Boolean, nullable=False, default=True)
    availability = Column(Text, nullable=True)                             # JSON {"0":["08:00","17:00"], ...} weekday->hours
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_service_team_member"),)


class ServiceChecklistTemplate(Base):
    __tablename__ = "service_checklist_templates"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    service_category = Column(String(40), nullable=False)
    name = Column(String(120), nullable=False)
    items = Column(Text, nullable=False)                                   # JSON [{"key","label","required"}]
    active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ServiceRecurringSchedule(Base):
    """A recurring service agreement. Future jobs are created inside a bounded
    horizon, one per (schedule, local date) - see ServiceJob unique key."""
    __tablename__ = "service_recurring_schedules"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id = Column(String, ForeignKey("service_locations.id", ondelete="CASCADE"), nullable=False, index=True)
    service_type = Column(String(120), nullable=False)
    frequency = Column(String(16), nullable=False)                         # one_time/daily/weekly/biweekly/monthly/custom
    interval_days = Column(Integer, nullable=True)                         # custom
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=True)
    start_time = Column(String(5), nullable=False, default="09:00")        # local wall time
    duration_min = Column(Integer, nullable=False, default=60)
    timezone = Column(String(64), nullable=False, default="America/Chicago")
    assigned_member_ids = Column(Text, nullable=True)                      # JSON list
    checklist_template_id = _fk("service_checklist_templates")
    opportunity_id = _fk("acquisition_opportunities")                      # contract reference
    estimate_id = _fk("service_estimates")
    price_per_visit = Column(Float, nullable=True)
    expected_monthly_value = Column(Float, nullable=True)
    instructions = Column(Text, nullable=True)
    status = Column(String(12), nullable=False, default="active")          # active / paused / cancelled
    materialized_through = Column(Date, nullable=True)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ServiceJob(Base):
    """ACTUAL delivery work. Separate from an Acquisition appointment (a SALES
    opportunity) with a traceable link to it."""
    __tablename__ = "service_jobs"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id = Column(String, ForeignKey("service_locations.id", ondelete="CASCADE"), nullable=False, index=True)
    schedule_id = _fk("service_recurring_schedules")
    occurrence_date = Column(Date, nullable=True)                          # local date, recurring jobs only
    opportunity_id = _fk("acquisition_opportunities")
    estimate_id = _fk("service_estimates")
    # Idempotency for conversions and API retries; unique per company.
    idempotency_key = Column(String(160), nullable=True)
    service_type = Column(String(120), nullable=False)
    status = Column(String(24), nullable=False, default="draft")
    scheduled_start = Column(DateTime, nullable=True)                      # UTC
    timezone = Column(String(64), nullable=False, default="America/Chicago")
    duration_min = Column(Integer, nullable=False, default=60)
    instructions = Column(Text, nullable=True)                             # customer instructions
    internal_notes = Column(Text, nullable=True)
    supplies = Column(Text, nullable=True)
    checklist = Column(Text, nullable=True)                                # JSON snapshot [{key,label,required,done,done_by,done_at}]
    completion_notes = Column(Text, nullable=True)
    actual_start = Column(DateTime, nullable=True)
    actual_end = Column(DateTime, nullable=True)
    completed_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    attention_reason = Column(Text, nullable=True)
    cancel_reason = Column(Text, nullable=True)
    price = Column(Float, nullable=True)
    requires_customer_review = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (
        UniqueConstraint("schedule_id", "occurrence_date", name="uq_service_job_occurrence"),
        UniqueConstraint("organization_id", "idempotency_key", name="uq_service_job_idem"),
        Index("ix_service_jobs_org_start", "organization_id", "scheduled_start"),
        Index("ix_service_jobs_org_status", "organization_id", "status"),
    )


class ServiceJobAssignment(Base):
    """Who is on a job. Never deleted: removal sets removed_at, so the
    assignment history stays."""
    __tablename__ = "service_job_assignments"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    job_id = Column(String, ForeignKey("service_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    member_id = Column(String, ForeignKey("service_team_members.id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assigned_at = Column(DateTime, server_default=func.now())
    removed_at = Column(DateTime, nullable=True)
    removed_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class ServiceJobEvent(Base):
    """Append-only job history. Each row carries a hash of its content and
    the previous row's hash, so an edited or deleted row is detectable."""
    __tablename__ = "service_job_events"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    job_id = Column(String, ForeignKey("service_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    seq = Column(Integer, nullable=False)
    kind = Column(String(40), nullable=False)
    from_status = Column(String(24), nullable=True)
    to_status = Column(String(24), nullable=True)
    actor_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    at = Column(DateTime, nullable=False)
    data = Column(Text, nullable=True)
    prev_hash = Column(String(64), nullable=True)
    hash = Column(String(64), nullable=False)
    __table_args__ = (UniqueConstraint("job_id", "seq", name="uq_service_job_event_seq"),)


class ServiceJobAttachment(Base):
    """A private job photo/file. Bytes live in the platform's private file
    store; nothing here is ever a public URL."""
    __tablename__ = "service_job_attachments"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    job_id = Column(String, ForeignKey("service_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    storage_backend = Column(String(16), nullable=False)
    storage_key = Column(String(300), nullable=False)
    content_type = Column(String(80), nullable=False)
    byte_size = Column(Integer, nullable=False)
    checksum = Column(String(64), nullable=False)
    filename = Column(String(180), nullable=True)
    caption = Column(String(300), nullable=True)
    uploaded_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    removed_at = Column(DateTime, nullable=True)


class ServiceEstimate(Base):
    __tablename__ = "service_estimates"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    number = Column(String(40), nullable=False)
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id = _fk("service_locations")
    opportunity_id = _fk("acquisition_opportunities")
    lines = Column(Text, nullable=False)                                   # JSON [{description, quantity, rate}]
    discount = Column(Float, nullable=False, default=0.0)
    tax_rate = Column(Float, nullable=False, default=0.0)
    subtotal = Column(Float, nullable=False, default=0.0)
    tax = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)
    status = Column(String(12), nullable=False, default="draft")           # draft/sent/accepted/declined/expired
    expires_on = Column(Date, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_service_estimate_number"),)


class ServiceInvoice(Base):
    __tablename__ = "service_invoices"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    number = Column(String(40), nullable=False)
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id = _fk("service_locations")
    job_id = _fk("service_jobs")
    schedule_id = _fk("service_recurring_schedules")
    opportunity_id = _fk("acquisition_opportunities")
    # Repeated completion events / retries produce ONE invoice for the same source.
    generation_key = Column(String(160), nullable=True)
    billing_name = Column(String(200), nullable=True)
    billing_address = Column(Text, nullable=True)
    lines = Column(Text, nullable=False)
    discount = Column(Float, nullable=False, default=0.0)
    tax_rate = Column(Float, nullable=False, default=0.0)
    subtotal = Column(Float, nullable=False, default=0.0)
    tax = Column(Float, nullable=False, default=0.0)
    total = Column(Float, nullable=False, default=0.0)
    amount_confirmed = Column(Float, nullable=False, default=0.0)
    amount_reported = Column(Float, nullable=False, default=0.0)
    due_date = Column(Date, nullable=True)
    payment_instructions = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default="draft")           # draft/issued/partially_paid/paid/void
    issued_at = Column(DateTime, nullable=True)
    voided_at = Column(DateTime, nullable=True)
    void_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("organization_id", "number", name="uq_service_invoice_number"),
                      UniqueConstraint("organization_id", "generation_key", name="uq_service_invoice_genkey"))


class ServicePayment(Base):
    """A payment against an invoice. REPORTED (someone says it was paid) is
    kept apart from CONFIRMED (the company confirmed the money arrived).
    Only confirmed payments move an invoice to Paid."""
    __tablename__ = "service_payments"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    invoice_id = Column(String, ForeignKey("service_invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    method = Column(String(30), nullable=True)                             # check / ach / card / cash / other
    reference = Column(String(120), nullable=True)
    status = Column(String(12), nullable=False, default="reported")        # reported / confirmed / rejected
    reported_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    confirmed_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reported_at = Column(DateTime, server_default=func.now())
    confirmed_at = Column(DateTime, nullable=True)
    note = Column(Text, nullable=True)


class ServiceBuyerAccess(Base):
    """A login for the service company's OWN customer (optional buyer portal).
    Scoped to one ServiceCustomer, optionally to some of its locations."""
    __tablename__ = "service_buyer_access"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    location_ids = Column(Text, nullable=True)                             # JSON list; NULL = all of the customer's
    can_approve_estimates = Column(Boolean, nullable=False, default=False)
    status = Column(String(12), nullable=False, default="invited")         # invited / active / revoked
    invited_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    invited_at = Column(DateTime, server_default=func.now())
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_service_buyer_user"),)


class ServiceRequest(Base):
    """A request from a buyer (or logged by staff for one): more service, a
    schedule change, an issue, an estimate decision."""
    __tablename__ = "service_requests"
    id = Column(String, primary_key=True, default=gen_uuid)
    organization_id = _org()
    customer_id = Column(String, ForeignKey("service_customers.id", ondelete="CASCADE"), nullable=False, index=True)
    location_id = _fk("service_locations")
    job_id = _fk("service_jobs")
    kind = Column(String(24), nullable=False)                              # new_service/schedule_change/issue/help
    service_category = Column(String(40), nullable=True)
    body = Column(Text, nullable=False)
    status = Column(String(14), nullable=False, default="open")            # open / acknowledged / closed
    requested_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    handled_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    response = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class ServiceBuyerNetworkRequest(Base):
    """MULTI-SERVICE BUYER NETWORK foundation (#17). A buyer organization asks
    EvoSys for services; EvoSys matches eligible service companies one at a
    time; a company accepts. Platform-level (no service company owns it until
    accepted) and never copies one company's records to another."""
    __tablename__ = "service_buyer_network_requests"
    id = Column(String, primary_key=True, default=gen_uuid)
    buyer_name = Column(String(200), nullable=False)
    contact_name = Column(String(160), nullable=True)
    contact_email = Column(String(200), nullable=True)
    contact_phone = Column(String(40), nullable=True)
    city = Column(String(120), nullable=True)
    state = Column(String(40), nullable=True)
    service_category = Column(String(40), nullable=False)
    details = Column(Text, nullable=True)
    buyer_authorized = Column(Boolean, nullable=False, default=False)      # buyer agreed to be introduced
    status = Column(String(14), nullable=False, default="new")             # new/offered/accepted/declined/closed
    offered_org_id = Column(String, ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True)
    offered_at = Column(DateTime, nullable=True)
    accepted_at = Column(DateTime, nullable=True)
    service_customer_id = Column(String, ForeignKey("service_customers.id", ondelete="SET NULL"), nullable=True)
    created_by_id = Column(String, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
