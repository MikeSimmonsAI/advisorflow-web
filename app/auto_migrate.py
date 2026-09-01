"""
Auto-migration: runs automatically on every app startup (wired into
main.py's on_startup handler), so a new column or enum value added to
the SQLAlchemy models never again requires someone to manually SSH into
Render's Shell tab and run a migration command by hand.

WHY THIS EXISTS: this project relies on Base.metadata.create_all() at
startup, which only creates brand-new TABLES - it never adds a new
COLUMN to a table that already exists, and it never adds a new VALUE to
a Postgres enum TYPE that already exists. Three separate times this
session, a new column or enum value got added to the Python models but
never reached the live database until someone manually ran a one-off
script - and the app silently broke in the meantime (every request
touching the affected table/column would fail) until that manual step
happened. Mike was explicit: he does not want to do that by hand every
single deploy.

THE FIX: consolidate every column-add and enum-add into the lists below,
and call run_auto_migrations() from main.py's startup handler, right
after create_all(). Every statement here is written to be safe to run
on EVERY boot, forever:
  - Column adds use "ADD COLUMN IF NOT EXISTS" - a no-op if it already exists.
  - Enum adds use "ADD VALUE IF NOT EXISTS" - same, a no-op if already present.
Neither ever drops, renames, or alters existing data - this module only
ever ADDS things, which is what makes it safe to run unconditionally on
every single startup rather than needing a human to decide when to run it.

ADDING A NEW COLUMN OR ENUM VALUE IN A FUTURE SESSION: add it to
COLUMNS_TO_ADD or ENUM_VALUES_TO_ADD below. That's the only step needed
- no separate one-off script, no manual Shell command, no "don't forget
to run this" note. The next deploy picks it up automatically.

SQLite (local/test) is skipped entirely for the enum step, since SQLite
doesn't enforce enum types at the database level at all - this only
matters for Postgres (production). The column-add step DOES run against
SQLite too, using SQLite's own ALTER TABLE ADD COLUMN syntax, so local
dev/test environments stay consistent with production without needing
a different code path.
"""

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError

# (table, column, full column definition) - every column ever added to
# an EXISTING table that create_all() would never retroactively add to
# a live database. Append here, never remove (removing an entry doesn't
# undo it on databases that already have the column, and a stale no-op
# entry costs nothing to leave in place).
COLUMNS_TO_ADD = [
    # ── Two-rate package pricing ───────────────────────────────────────────
    # brand_packages.price stays the MONTH-TO-MONTH rate. contract_price is the
    # lower rate earned by signing a term agreement, and is NULL for any package
    # that has no term option - which is refused rather than defaulted.
    # brand_packages.price keeps holding the ONE-TIME implementation charge.
    # These are the NEW recurring platform rates layered on top of it.
    # ── Demo mockups: one live link PER SLOT ───────────────────────────────
    # Existing rows predate slots and are all website concepts, which is what
    # the backfilled default says. New rows default to "platform" in the model,
    # so the product walkthrough does not retire the website concept.
    # ── Provider-neutral voice lifecycle (Retell integration, 2026-08-28) ────
    # All additive and nullable. `create_all()` never adds a column to an
    # existing table, so voice_calls — which has been in production since the
    # Twilio voice work — needs these here or they would only appear on a
    # freshly created database and fail on the first real webhook.
    ("voice_calls", "provider", "VARCHAR"),
    ("voice_calls", "provider_call_id", "VARCHAR"),
    ("voice_calls", "direction", "VARCHAR"),
    ("voice_calls", "agent_id", "VARCHAR"),
    ("voice_calls", "campaign_id", "VARCHAR"),
    ("voice_calls", "answered_at", "TIMESTAMP"),
    ("voice_calls", "disconnect_reason", "VARCHAR"),
    ("voice_calls", "summary", "TEXT"),
    ("voice_calls", "analysis_json", "TEXT"),
    ("voice_calls", "transfer_requested", "BOOLEAN DEFAULT FALSE"),
    ("voice_calls", "transfer_destination", "VARCHAR"),
    ("voice_calls", "transfer_status", "VARCHAR"),
    ("voice_calls", "callback_at", "TIMESTAMP"),
    ("voice_calls", "booking_link_id", "VARCHAR"),

    # Which published agent version an organization is approved to run.
    # Deliberately NOT defaulted: a backfilled version would be this file
    # guessing which conversation a customer's families should hear. NULL means
    # "not pinned yet", and the provider refuses to place the call rather than
    # letting the vendor pick whatever is newest.
    ("voice_agent_configs", "agent_version", "INTEGER"),

    # ── Customer-ready closeout, 2026-08-29 ────────────────────────────────
    #
    # The spoken callback number. A family who gives a different number on the
    # phone is still the same family; this holds what they said without
    # rewriting `leads.phone`, which every prior message, suppression check and
    # attempt count is reconciled against.
    ("leads", "callback_phone", "VARCHAR"),
    ("leads", "callback_phone_source", "VARCHAR"),
    ("leads", "callback_phone_at", "TIMESTAMP"),

    # Attempt policy, configurable instead of a constant in the orchestrator.
    # All NULL by default, which defers to the next level down and finally to
    # the system default of 3 - so this migration changes no live behaviour.
    ("organizations", "max_call_attempts", "INTEGER"),
    ("organizations", "max_dial_attempts", "INTEGER"),
    ("organizations", "redial_cooldown_minutes", "INTEGER"),
    ("voice_agent_configs", "max_call_attempts", "INTEGER"),
    ("voice_agent_configs", "max_dial_attempts", "INTEGER"),
    ("voice_call_campaigns", "max_call_attempts", "INTEGER"),
    ("voice_call_campaigns", "max_dial_attempts", "INTEGER"),

    # Was there a person on the line. NULL on every historical row, which the
    # attempt counter treats as a live conversation - the conservative reading,
    # because those rows predate the distinction and counting them keeps the
    # existing cap exactly as strict as it was.
    ("voice_calls", "answered_by", "VARCHAR"),
    ("voice_calls", "is_live_conversation", "BOOLEAN"),

    ("demo_sites", "slot", "VARCHAR(32) DEFAULT 'website' NOT NULL"),

    # ── Per-deal custom recurring rate ─────────────────────────────────────
    # The recurring counterpart to opportunities.implementation_fee. A "Custom"
    # package carries no catalogue rate on purpose; these let the DEAL state
    # one, priced per unit so the basis ("$250 per active paying customer,
    # 15 minimum") survives into the customer's document rather than being
    # flattened to a total nobody can check.
    #
    # Mirrored onto proposals as a SNAPSHOT: what was quoted must not move when
    # the deal is renegotiated.
    ("opportunities", "custom_unit_price", "NUMERIC(12,2)"),
    ("opportunities", "custom_unit_label", "VARCHAR"),
    ("opportunities", "custom_min_units", "INTEGER"),
    ("opportunities", "custom_term_months", "INTEGER"),
    # A proposal that deliberately quotes no price yet. Defaults false, so every
    # existing proposal keeps showing exactly what it showed yesterday.
    ("proposals", "withhold_pricing", "BOOLEAN DEFAULT FALSE NOT NULL"),
    ("proposals", "custom_unit_price", "NUMERIC(12,2)"),
    ("proposals", "custom_unit_label", "VARCHAR"),
    ("proposals", "custom_min_units", "INTEGER"),
    ("proposals", "custom_term_months", "INTEGER"),
    ("brand_packages", "monthly_price", "NUMERIC(12,2)"),
    ("brand_packages", "contract_monthly_price", "NUMERIC(12,2)"),
    ("brand_packages", "contract_term_months", "INTEGER"),
    ("opportunities", "billing_option", "VARCHAR"),
    ("opportunities", "contract_term_months", "INTEGER"),
    ("opportunities", "implementation_fee", "NUMERIC(12,2)"),
    ("proposals", "billing_option", "VARCHAR"),
    ("proposals", "contract_term_months", "INTEGER"),
    ("implementations", "billing_option", "VARCHAR"),
    ("implementations", "contract_term_months", "INTEGER"),
    ("users", "microsoft_oauth_refresh_token_encrypted", "VARCHAR"),
    ("users", "microsoft_email_address", "VARCHAR"),
    ("users", "microsoft_365_connected", "BOOLEAN DEFAULT FALSE"),
    ("users", "notification_phone", "VARCHAR"),
    ("users", "notify_via_sms", "BOOLEAN DEFAULT FALSE"),
    ("users", "can_import_leads", "BOOLEAN DEFAULT FALSE"),
    ("users", "feature_flags", "TEXT"),
    ("users", "auto_send_phase", "VARCHAR DEFAULT 'off'"),
    ("leads", "engagement_temperature", "VARCHAR"),
    ("leads", "google_contact_resource_name", "VARCHAR"),
    ("booking_links", "confirmed_at", "TIMESTAMP"),
    ("email_messages", "opened_at", "TIMESTAMP"),
    ("email_messages", "click_count", "INTEGER DEFAULT 0"),
    ("email_messages", "last_clicked_at", "TIMESTAMP"),
    ("replies", "classification", "VARCHAR"),
    ("replies", "classification_confidence", "VARCHAR"),
    ("replies", "classification_reasoning", "TEXT"),
    ("notifications", "send_failure_reason", "TEXT"),
    ("lead_outcomes", "has_preneed_planning", "BOOLEAN"),
    ("lead_outcomes", "has_insurance_funding", "BOOLEAN"),
    ("lead_outcomes", "is_veteran", "BOOLEAN"),
    ("lead_outcomes", "next_step", "TEXT"),
    ("users", "booking_page_url", "VARCHAR"),
    ("organizations", "org_address", "VARCHAR"),
    ("organizations", "org_phone", "VARCHAR"),
    # Org social links
    ("organizations", "facebook_url", "VARCHAR"),
    ("organizations", "google_review_url", "VARCHAR"),
    ("organizations", "instagram_url", "VARCHAR"),
    ("organizations", "linkedin_url", "VARCHAR"),
    # Lead physical address
    ("leads", "street_address", "VARCHAR"),
    ("leads", "city", "VARCHAR"),
    ("leads", "state", "VARCHAR"),
    ("leads", "zip_code", "VARCHAR"),
    # Advisor social links
    ("users", "facebook_url", "VARCHAR"),
    ("users", "google_review_url", "VARCHAR"),
    ("users", "instagram_url", "VARCHAR"),
    ("users", "linkedin_url", "VARCHAR"),
    # Campaign stats — added for Campaign Builder overhaul so history shows results
    ("campaigns", "purpose", "VARCHAR"),
    ("campaigns", "tone", "VARCHAR"),
    ("campaigns", "sent_count", "INTEGER DEFAULT 0"),
    ("campaigns", "skipped_count", "INTEGER DEFAULT 0"),
    ("campaigns", "error_count", "INTEGER DEFAULT 0"),
    ("campaigns", "status", "VARCHAR DEFAULT 'draft'"),
    ("campaigns", "ai_direction", "TEXT"),
    ("campaigns", "sent_at", "TIMESTAMP"),
    # Google Calendar — advisor OAuth + calendar integration
    ("users", "google_oauth_refresh_token_encrypted", "VARCHAR"),
    ("users", "google_calendar_id", "VARCHAR"),
    ("users", "google_calendar_connected", "BOOLEAN DEFAULT FALSE"),
    # Twilio A2P 10DLC registration state per organization
    ("organizations", "twilio_messaging_service_sid", "VARCHAR"),
    ("organizations", "twilio_a2p_brand_sid", "VARCHAR"),
    ("organizations", "twilio_a2p_brand_status", "VARCHAR"),
    ("organizations", "twilio_a2p_campaign_sid", "VARCHAR"),
    ("organizations", "twilio_a2p_campaign_status", "VARCHAR"),
    ("organizations", "twilio_a2p_campaign_use_case", "VARCHAR"),
    ("organizations", "twilio_a2p_registered_at", "TIMESTAMP"),
    # Post-appointment case file — lead-level case status
    ("leads", "case_status", "VARCHAR DEFAULT 'open'"),
    # Duplicate traceability + resolution. A flag used to say only "duplicate"
    # with no parent and no reason, and the only way off the list was deletion.
    ("leads", "duplicate_reason", "VARCHAR"),
    ("leads", "duplicate_match_field", "VARCHAR"),
    ("leads", "duplicate_match_value", "VARCHAR"),
    ("leads", "duplicate_resolved_at", "TIMESTAMP"),
    ("leads", "duplicate_resolved_by", "VARCHAR"),
    # Post-appointment review request — track that we've sent the Google review SMS
    ("booking_links", "review_request_sent_at", "TIMESTAMP"),
    # Appointment details moved OUT of the booking token and onto the row it
    # keys. The old self-contained token was 379 characters, which made a normal
    # SMS 4 segments and got it filtered by carriers (Twilio 30007).
    ("booking_links", "appt_label", "VARCHAR"),
    ("booking_links", "appt_duration", "INTEGER"),
    # Social media lead capture — org-level webhook credentials + token
    ("organizations", "social_webhook_token", "VARCHAR"),
    ("organizations", "meta_page_access_token", "VARCHAR"),
    ("organizations", "meta_webhook_verify_token", "VARCHAR"),
    ("organizations", "tiktok_webhook_secret", "VARCHAR"),
    ("organizations", "enabled_features", "TEXT"),
    # ── GATE 1 of administrative delegation (2026-09-01) ───────────────────
    # Separate from enabled_features on purpose: that column says the customer
    # may USE a service, this one says the ORGANIZATION may ADMINISTER the
    # infrastructure behind it. Nullable with no default, and NULL reads as
    # "nothing delegated", so every existing customer arrives at this gate
    # closed - which is the intended state for all of them.
    ("organizations", "delegated_capabilities", "TEXT"),
    # Fiber + generic intake form — service address and JSON blob for industry-specific fields
    ("leads", "service_address", "VARCHAR"),
    ("leads", "extra_data", "TEXT"),
    # Profile headshot — base64 data URL stored in DB (no external storage needed)
    ("users", "profile_photo_url", "TEXT"),
    # Dynamic member role label — what this org calls their staff (Agent, Rep, Advisor, etc.)
    ("organizations", "member_label", "VARCHAR(100)"),
    ("organizations", "members_label", "VARCHAR(100)"),
    # CRM contacts — pipeline stage for contacts table
    ("crm_contacts", "pipeline_stage", "VARCHAR DEFAULT 'new'"),
    ("crm_contacts", "company", "VARCHAR"),
    ("crm_contacts", "last_contact_at", "TIMESTAMP"),
    # Brute-force / credential-stuffing protection — added alongside auth_router lockout logic.
    # failed_login_attempts: resets to 0 on success, incremented on each bad password.
    # lockout_until: non-null while the account is temporarily locked; NULL = not locked.
    ("users", "failed_login_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "lockout_until", "TIMESTAMP"),
    # SMS vs email-only queue routing — drives contact_channel logic in outreach scheduler
    ("leads", "contact_channel", "VARCHAR DEFAULT 'sms'"),
    # Lead relationship context — tells AI who this person is relative to the business.
    # This is the PRIMARY AI guardrail: cold_lead, warm_lead, previous_prospect,
    # existing_customer, past_customer, re_engagement. Set at import or manually.
    ("leads", "relationship_type", "VARCHAR DEFAULT 'cold_lead'"),
    # Extra import metadata — JSON blob of any extra CSV columns not in the standard
    # field map. Stored raw so AI and UI can access them without schema changes.
    ("leads", "custom_fields", "TEXT"),
    # Import list name — user-supplied label for the import batch (e.g. "2024 Purchased List")
    ("leads", "import_list_name", "VARCHAR"),
    # Source category — user-defined grouping (purchased, organic, referral, database, etc.)
    ("leads", "source_category", "VARCHAR"),
    # Manual flag — advisor-set flag when auto-detection misses a bad contact
    # Values: null (clean), "bad_email", "remove_all"
    ("leads", "manual_flag", "VARCHAR"),
    ("leads", "manual_flag_reason", "VARCHAR"),
    # Meta app secret for Meta webhook HMAC verification
    ("organizations", "meta_app_secret", "VARCHAR"),
    # Twilio delivery receipts — updated by /sms/status-callback webhook
    # Values: pending, sent, delivered, failed, undelivered
    ("messages", "delivery_status", "VARCHAR DEFAULT 'pending'"),
    ("messages", "delivery_status_at", "TIMESTAMP"),
    # Explicit send-state vocabulary + provider error detail.
    # blocked | queued | sent | delivered | failed — app/services/message_state.py
    # Brand presentation on the platform row — see app/services/brand_config.py.
    # All nullable; the resolver falls back to frozen literals field by field.
    ("platforms", "short_name", "VARCHAR"),
    ("platforms", "logo_initial", "VARCHAR"),
    ("platforms", "logo_url", "VARCHAR"),
    ("platforms", "favicon_url", "VARCHAR"),
    ("platforms", "tagline", "VARCHAR"),
    ("platforms", "theme_slug", "VARCHAR"),
    ("platforms", "accent_color", "VARCHAR"),
    ("platforms", "accent_color_2", "VARCHAR"),
    ("platforms", "green_color", "VARCHAR"),
    ("platforms", "bg_color", "VARCHAR"),
    ("platforms", "invite_accent_color", "VARCHAR"),
    ("platforms", "support_phone", "VARCHAR"),
    ("platforms", "website_url", "VARCHAR"),
    ("platforms", "app_base_url", "VARCHAR"),
    ("messages", "send_state", "VARCHAR"),
    ("messages", "error_code", "VARCHAR"),
    ("messages", "error_message", "VARCHAR"),
    # last_messaged_at on leads — denormalized for fast "sent today" badge in Leads list
    ("leads", "last_messaged_at", "TIMESTAMP"),
    # Org-level email sender — each brand sends from its own domain/address.
    # BookaBoost: support@bookaboost.live / EvoSys Pro: support@evosyspro.live.
    # When set, these override the global RESEND_API_KEY / EMAIL_FROM_ADDRESS env vars
    # so each org's outbound email genuinely comes from their own domain.
    ("organizations", "from_email", "VARCHAR"),
    ("organizations", "resend_api_key", "VARCHAR"),
    # Reply-To is not the From address: the From must sit on a domain verified
    # with the sending provider, a Reply-To can be any mailbox a human reads.
    ("organizations", "reply_to_email", "VARCHAR"),
    # Optional second recipient. Deliberately no default - see the model.
    ("organizations", "cc_email", "VARCHAR"),
    # The calendar system of record, so it stops being decided by tuple order.
    ("organizations", "calendar_provider", "VARCHAR"),
    ("users", "calendar_provider", "VARCHAR"),
    # SMS consent of record — A2P 10DLC / TCPA. Verified missing from the
    # production database on Aug 25 2026: there was no queryable consent record
    # anywhere, despite the docs listing these as mandatory. Adding the columns
    # is the storage half; the capture-and-check logic still has to be written
    # at every opt-in point and before every send.
    # Internal test records — staff and QA fixtures living in production lead
    # tables. Excluded from every outreach path; see Lead.is_test.
    ("leads", "is_test", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("leads", "test_note", "VARCHAR"),
    ("leads", "sms_consent", "BOOLEAN DEFAULT FALSE"),
    ("leads", "sms_consent_timestamp", "TIMESTAMP"),
    ("leads", "sms_consent_ip", "VARCHAR"),
    ("leads", "sms_consent_text", "TEXT"),
    ("leads", "sms_consent_source", "VARCHAR"),

    # ── Sales Workspace, Checkpoint 1 (Aug 25 2026) ────────────────────────
    # `opportunities` and `discovery_records` were created by create_all() in
    # Phase 1, so they now EXIST in production and create_all() will never add
    # a column to them. Every field below therefore has to come through here.
    #
    # Demo build state — so a rep can see whether their demo is being built
    # without calling the owner to ask.
    ("opportunities", "demo_owner_user_id", "VARCHAR"),
    ("opportunities", "demo_requested_at",  "TIMESTAMP"),
    ("opportunities", "demo_due_at",        "TIMESTAMP"),
    ("opportunities", "demo_ready_at",      "TIMESTAMP"),
    ("opportunities", "demo_requirements",  "TEXT"),
    ("opportunities", "demo_url",           "VARCHAR"),
    ("opportunities", "demo_notes",         "TEXT"),
    # Discovery questions that were missing from the Phase 1 shape. All
    # nullable — an existing discovery record stays valid with them empty.
    ("discovery_records", "business_description",     "TEXT"),
    ("discovery_records", "automation_opportunities", "TEXT"),
    ("discovery_records", "desired_outcome",          "TEXT"),
    ("discovery_records", "demo_requirements",        "TEXT"),

    # ── External calendar sync, Checkpoint 3 (Aug 25 2026) ─────────────────
    # sales_appointments and sales_appointment_participants were created by
    # create_all() in Checkpoint 2, so they EXIST in production now and
    # create_all() will never add a column to them.
    ("sales_appointment_participants", "external_calendar_provider", "VARCHAR"),
    ("sales_appointment_participants", "external_event_id", "VARCHAR"),
    ("sales_appointment_participants", "external_synced_at", "TIMESTAMP"),
    ("sales_appointment_participants", "sync_status",
     "VARCHAR NOT NULL DEFAULT 'not_connected'"),
    ("sales_appointment_participants", "sync_attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("sales_appointment_participants", "sync_last_attempt", "TIMESTAMP"),
    ("sales_appointment_participants", "sync_error", "TEXT"),
    ("sales_appointment_participants", "ics_sent_at", "TIMESTAMP"),
    ("sales_appointments", "prospect_invite_sent_at", "TIMESTAMP"),
    ("sales_appointments", "prospect_invite_error", "TEXT"),
    ("sales_appointments", "rescheduled_count", "INTEGER NOT NULL DEFAULT 0"),
    ("sales_appointments", "rescheduled_at", "TIMESTAMP"),
    ("sales_appointments", "previous_starts_at", "TIMESTAMP"),
    ("sales_appointments", "reschedule_reason", "TEXT"),
    # calendar_connections is a NEW table, so create_all() builds it complete on
    # first deploy and these three are redundant there. They are listed anyway
    # because they were added after the table was first written, and a database
    # created between those two moments has the table WITHOUT them — which
    # create_all() will never repair.
    ("calendar_connections", "busy_window_start", "TIMESTAMP"),
    ("calendar_connections", "busy_window_end", "TIMESTAMP"),
    ("calendar_connections", "busy_fetched_at", "TIMESTAMP"),

    # ── Sales execution, Checkpoint 4 (Aug 26 2026) ────────────────────────
    # `proposals` and `sales_meeting_types` BOTH already exist in production, so
    # create_all() will never add a column to either. Every one of these is
    # required for the proposal engine and the Zoom integration to function.
    ("sales_meeting_types", "requires_video", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("sales_meeting_types", "video_provider", "VARCHAR"),

    # Sales linkage — the columns that make a customer-portal proposal usable
    # as an Opportunity proposal without forking the table.
    ("proposals", "brand_sales_org_id", "VARCHAR"),
    ("proposals", "opportunity_id", "VARCHAR"),
    ("proposals", "proposal_number", "VARCHAR"),
    ("proposals", "version", "INTEGER NOT NULL DEFAULT 1"),
    ("proposals", "supersedes_id", "VARCHAR"),
    ("proposals", "sales_status", "VARCHAR"),
    # Money. NUMERIC(12,2), never FLOAT.
    ("proposals", "package_id", "VARCHAR"),
    ("proposals", "base_amount", "NUMERIC(12,2)"),
    ("proposals", "adjustment", "NUMERIC(12,2)"),
    ("proposals", "final_amount", "NUMERIC(12,2)"),
    ("proposals", "currency", "VARCHAR DEFAULT 'USD'"),
    ("proposals", "price_override_by", "VARCHAR"),
    ("proposals", "price_override_at", "TIMESTAMP"),
    ("proposals", "price_override_reason", "TEXT"),
    # Structured content — real columns so versions can be diffed and reported.
    ("proposals", "executive_summary", "TEXT"),
    ("proposals", "business_need", "TEXT"),
    ("proposals", "objectives", "TEXT"),
    ("proposals", "recommended_solution", "TEXT"),
    ("proposals", "scope", "TEXT"),
    ("proposals", "deliverables", "TEXT"),
    ("proposals", "implementation_plan", "TEXT"),
    ("proposals", "terms", "TEXT"),
    # Lifecycle stamps.
    ("proposals", "sent_at", "TIMESTAMP"),
    ("proposals", "first_viewed_at", "TIMESTAMP"),
    ("proposals", "last_viewed_at", "TIMESTAMP"),
    ("proposals", "accepted_at", "TIMESTAMP"),
    ("proposals", "declined_at", "TIMESTAMP"),
    ("proposals", "change_requested_at", "TIMESTAMP"),
    ("proposals", "superseded_at", "TIMESTAMP"),
    ("proposals", "customer_response_note", "TEXT"),
    ("proposals", "responded_by_email", "VARCHAR"),
    # E-signature attachment point. Nothing reads these yet — they exist so
    # acceptance does not need re-modelling when a provider is added.
    ("proposals", "signature_provider", "VARCHAR"),
    ("proposals", "signature_envelope_id", "VARCHAR"),
    ("proposals", "signature_status", "VARCHAR"),
    ("proposals", "signed_at", "TIMESTAMP"),
    # Platform isolation — added when Platform model was introduced.
    # platform_id on organizations and users links each org/user to their brand platform.
    ("organizations", "platform_id", "VARCHAR"),
    ("users", "platform_id", "VARCHAR"),
    # sender_id on email_messages — was in the SQLAlchemy model from the start but was
    # never added to this list, so any DB created before create_all() included it is
    # missing the column entirely. Every query on EmailMessage (especially the activity
    # feed join + the non-manager sender filter) crashes until this column exists.
    ("email_messages", "sender_id", "VARCHAR"),
    # provider_message_id + status on email_messages — both referenced in timeline_router
    # and email_router queries; missing from production DB on old installs.
    ("email_messages", "provider_message_id", "VARCHAR"),
    ("email_messages", "status", "VARCHAR DEFAULT 'queued'"),
    # User notification columns — email-based hot-reply alerts; the SMS equivalents
    # (notification_phone, notify_via_sms) are covered above but these email variants were missed.
    ("users", "notification_email", "VARCHAR"),
    ("users", "notify_on_hot_reply", "BOOLEAN DEFAULT TRUE"),
    # Login tracking — last_login_at added for security audit, Twilio caller ID for SMS display name
    ("users", "last_login_at", "TIMESTAMP"),
    ("users", "twilio_caller_id_name", "VARCHAR"),
    # White-label / multi-platform branding columns on organizations —
    # required by fetchAndStoreBranding() in client.js and by org-settings endpoints.
    ("organizations", "brand_name", "VARCHAR"),
    ("organizations", "brand_logo_url", "VARCHAR"),
    ("organizations", "brand_color_primary", "VARCHAR"),
    ("organizations", "brand_color_accent", "VARCHAR"),
    ("organizations", "industry", "VARCHAR DEFAULT 'funeral'"),
    # Per-org tier config JSON — used by TierDefinition feature
    ("organizations", "tier_config", "TEXT"),
    # Per-org appointment type options — JSON array of strings for the Appt type dropdown
    ("organizations", "appointment_types", "TEXT"),
    # AI lead quality note — Phase 2 field added to Lead model
    ("leads", "ai_lead_quality_note", "TEXT"),
    # Reply review tracking — when a reply was reviewed/actioned by an advisor
    ("replies", "reviewed_at", "TIMESTAMP"),
    # CRM contacts — new master-record schema columns (first_name/last_name split,
    # address fields, funeral-appropriate stages, lead link, etc.)
    ("crm_contacts", "first_name", "VARCHAR"),
    ("crm_contacts", "last_name", "VARCHAR"),
    ("crm_contacts", "address_street", "VARCHAR"),
    ("crm_contacts", "address_city", "VARCHAR"),
    ("crm_contacts", "address_state", "VARCHAR"),
    ("crm_contacts", "address_zip", "VARCHAR"),
    ("crm_contacts", "stage", "VARCHAR DEFAULT 'inquiry'"),
    ("crm_contacts", "notes", "TEXT"),
    ("crm_contacts", "tags", "VARCHAR"),
    ("crm_contacts", "lead_id", "VARCHAR"),
    ("crm_contacts", "assigned_to_id", "VARCHAR"),
    ("crm_contacts", "updated_at", "TIMESTAMP DEFAULT NOW()"),
    ("crm_contacts", "last_contacted_at", "TIMESTAMP"),
    ("crm_contacts", "is_archived", "BOOLEAN DEFAULT FALSE"),
    # Custom CRM stages per org — JSON array of {key, label, color}
    ("organizations", "crm_stages", "TEXT"),
    # Custom field schema per org — JSON array of {key, label, type, options?}
    ("organizations", "crm_custom_fields", "TEXT"),
    # Custom field values per contact — JSON object {field_key: value}
    ("crm_contacts", "custom_data", "TEXT"),
    # Who uploaded each lead batch (full name of the user who ran the import)
    ("leads", "imported_by_name", "TEXT"),
    # Tenant-side Retell bridge. `integration_credentials` and
    # `integration_request_logs` were created whole by create_all() on the
    # deploy before this one, so they now EXIST and create_all() will never
    # retroactively add a column to them. Every column below is therefore a
    # migration, not a model change that happens to work on a fresh database.
    ("integration_credentials", "organization_id", "VARCHAR"),
    ("integration_request_logs", "organization_id", "VARCHAR"),
    ("integration_request_logs", "booking_link_id", "VARCHAR"),
    ("integration_request_logs", "lead_id", "VARCHAR"),
    # Checkpoint 6 — the audit engine learns about the control plane. Same
    # table, same helper; these columns are what let a god_admin or a
    # brand-sales actor be audited at all. See models.AuditLogEntry.
    ("audit_log_entries", "platform_id", "VARCHAR"),
    ("audit_log_entries", "brand_sales_org_id", "VARCHAR"),
    ("audit_log_entries", "before_state", "TEXT"),
    ("audit_log_entries", "after_state", "TEXT"),
    ("audit_log_entries", "note", "TEXT"),

    # Brand-sales staff management. Who a person reports to inside one brand.
    # `create_all()` creates TABLES only, never a column on a table that already
    # exists - `memberships` has existed since Checkpoint 1, so the column has to
    # be added here or it silently never appears in production.
    #
    # It is an org-chart fact and NOT an authorization input: no guard anywhere
    # reads it, and `is_sales_manager` still resolves from `role` alone.
    ("memberships", "reports_to_user_id", "VARCHAR"),
]

# New whole tables to create — uses CREATE TABLE IF NOT EXISTS so safe on every boot.
TABLES_TO_CREATE = [
    """
    CREATE TABLE IF NOT EXISTS crm_contacts (
        id VARCHAR PRIMARY KEY,
        organization_id VARCHAR NOT NULL REFERENCES organizations(id),
        created_by_id VARCHAR REFERENCES users(id),
        full_name VARCHAR,
        phone VARCHAR,
        email VARCHAR,
        company VARCHAR,
        pipeline_stage VARCHAR DEFAULT 'new',
        last_contact_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crm_contact_notes (
        id VARCHAR PRIMARY KEY,
        contact_id VARCHAR NOT NULL REFERENCES crm_contacts(id) ON DELETE CASCADE,
        created_by_id VARCHAR REFERENCES users(id),
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    )
    """,
]

# (postgres enum type name, value to add) - SQLAlchemy's SAEnum writes
# the Python enum MEMBER NAME (e.g. "ADVISOR_FLAGGED"), not member.value
# (e.g. "advisor_flagged") - see the casing-bug history in
# migrate_add_advisor_flagged_enum_value.py for exactly how this went
# wrong once already. Always use the uppercase member NAME here.
ENUM_VALUES_TO_ADD = [
    ("suppressionsource", "ADVISOR_FLAGGED"),
    # A voice opt-out lands in the SAME suppression table as an SMS STOP; only
    # the provenance differs. Postgres needs the enum value to exist before a
    # row can carry it, so this must be here rather than implied by the model.
    ("suppressionsource", "VOICE_OPT_OUT"),
    ("notificationtype", "REPLY_RECEIVED"),
]


# Real, one-time column-type-change migrations - columns that changed
# from a hard database enum to a plain string when the per-organization
# tier/track configuration system (TierDefinition) replaced the old
# hardcoded LeadTier/MessageTrack Python enums. Each entry is
# (table, column, postgres_enum_type_name).
#
# CRITICAL, documented standing rule for this codebase: SQLAlchemy's
# SAEnum writes the Python enum member NAME (uppercase, e.g.
# "PRE_NEED") into Postgres, not .value (lowercase "pre_need"). A naive
# `ALTER COLUMN ... TYPE VARCHAR USING column::text` cast would
# therefore convert every existing lead's tier to its UPPERCASE name -
# which would then silently fail to match any TierDefinition.tier_key
# (all lowercase, e.g. "pre_need"), since nothing would ever look
# correct again despite the migration "succeeding." The LOWER() call
# below is what actually prevents that corruption.
ENUM_COLUMNS_TO_CONVERT_TO_STRING = [
    ("leads", "tier", "leadtier"),
    ("leads", "message_track", "messagetrack"),
    ("campaigns", "message_track", "messagetrack"),
    ("message_templates", "message_track", "messagetrack"),
]


# Indexes to create — each is a full CREATE INDEX IF NOT EXISTS statement.
# Safe to run on every boot; Postgres and SQLite both support IF NOT EXISTS.
# These cover the most expensive missing indexes: the activity feed does full
# table scans on messages and email_messages (potentially millions of rows)
# because neither table had any indexes on lead_id, sent_at, or sender_id.
INDEXES_TO_CREATE = [
    # The webhook lookup key. Every Retell event resolves through this column,
    # several times per call, so it is indexed rather than scanned.
    "CREATE INDEX IF NOT EXISTS ix_voice_calls_provider_call_id ON voice_calls(provider_call_id)",
    "CREATE INDEX IF NOT EXISTS ix_voice_calls_campaign_id      ON voice_calls(campaign_id)",
    "CREATE INDEX IF NOT EXISTS ix_voice_calls_booking_link_id  ON voice_calls(booking_link_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_lead_id      ON messages(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_sent_at      ON messages(sent_at)",
    "CREATE INDEX IF NOT EXISTS ix_messages_sender_id    ON messages(sender_id)",
    "CREATE INDEX IF NOT EXISTS ix_email_messages_lead_id   ON email_messages(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_email_messages_sent_at   ON email_messages(sent_at)",
    "CREATE INDEX IF NOT EXISTS ix_email_messages_sender_id ON email_messages(sender_id)",
    "CREATE INDEX IF NOT EXISTS ix_replies_lead_id       ON replies(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_replies_received_at   ON replies(received_at)",
    # booking_links — looked up by lead, by org, and by slug
    "CREATE INDEX IF NOT EXISTS ix_booking_links_lead_id       ON booking_links(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_booking_links_organization_id ON booking_links(organization_id)",
    "CREATE INDEX IF NOT EXISTS ix_booking_links_slug          ON booking_links(slug)",
    # audit_log_entries — heavily filtered by org, action, actor, and time
    "CREATE INDEX IF NOT EXISTS ix_audit_log_org_created ON audit_log_entries(organization_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_actor       ON audit_log_entries(actor_user_id)",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_action      ON audit_log_entries(action)",
    # suppression_entries — looked up by org and by phone for every outbound send
    "CREATE INDEX IF NOT EXISTS ix_suppression_org_phone ON suppression_entries(organization_id, phone)",

    # ── Performance hardening: verified missing, not guessed ────────────────
    # Each of these was checked against the model definitions first; none of
    # them already existed under another name.
    #
    # users.organization_id had NO index at all, which is the single worst gap
    # in the schema. Every tenant user list, every advisor cohort, every
    # customer user count and every invite check filters on it, and on Postgres
    # each one was a sequential scan of the whole users table.
    # Rolling reports up by import list is how an operator keeps several
    # client businesses apart inside one tenant; without this the group-by
    # scans every lead in the org.
    "CREATE INDEX IF NOT EXISTS ix_leads_org_import_list ON leads(organization_id, import_list_name)",
    "CREATE INDEX IF NOT EXISTS ix_users_organization_id ON users(organization_id)",
    # ...and the same filter almost always carries a role or is_active
    # predicate alongside it.
    "CREATE INDEX IF NOT EXISTS ix_users_org_role   ON users(organization_id, role)",
    "CREATE INDEX IF NOT EXISTS ix_users_org_active ON users(organization_id, is_active)",

    # leads already has ix_leads_org_advisor(organization_id, assigned_to_id),
    # but a composite index cannot serve a query that does not filter on its
    # LEADING column - and the master dashboard counts leads by advisor with no
    # organisation predicate at all. That query could not use the composite one.
    "CREATE INDEX IF NOT EXISTS ix_leads_assigned_to_id ON leads(assigned_to_id)",

    # implementations.owner_user_id is indexed; sold_by_user_id is not, and the
    # rep's own /sales/implementations view filters on exactly that column.
    "CREATE INDEX IF NOT EXISTS ix_impl_sold_by_user_id ON implementations(sold_by_user_id)",
]


# Columns whose NOT NULL constraint must be RELAXED.
#
# COLUMNS_TO_ADD can only add; it cannot change an existing column. Dropping a
# NOT NULL is backward-safe in exactly one direction: every existing row already
# satisfies the looser rule, and code that always supplied a value keeps working.
# It is NOT reversible without first proving no NULLs exist, so only relax a
# constraint the data model genuinely no longer requires.
#
# Postgres only. SQLite cannot drop NOT NULL without rebuilding the table, and
# does not need to — local/test databases are built fresh from the models by
# create_all(), which already reflects nullable=True.
NULLABILITY_TO_RELAX = [
    # Brand-sales staff and some global/god users have no customer tenant at all.
    # See User.organization_id and claude/SALES_WORKSPACE_ARCHITECTURE.md.
    ("users", "organization_id"),
    # Checkpoint 4. A SALES proposal belongs to a brand_sales_org and an
    # opportunity, not to a customer tenant — the customer organization does not
    # exist until the deal is Won. Existing customer-portal proposals keep their
    # organization_id and are unaffected; this only permits the NULL that a
    # pre-sale proposal requires.
    ("proposals", "organization_id"),
    # Same reason, one table down: a file on a sales proposal has no customer
    # tenant either. Missing this relax means every upload fails on production
    # Postgres with a NOT NULL violation while passing on a fresh SQLite.
    ("proposal_files", "organization_id"),
    # Tenant-side Retell bridge. This column shipped NOT NULL, when a
    # credential could only ever be brand-scoped. A tenant key sets
    # `organization_id` instead and leaves this NULL; without the relax, every
    # tenant key insert fails on production Postgres with a NOT NULL violation
    # while passing on a fresh SQLite. `scope_kind()` is what keeps "both NULL"
    # from becoming a legal state.
    ("integration_credentials", "brand_sales_org_id"),
    # Checkpoint 6. This column shipped NOT NULL when every audited action
    # happened inside a customer tenant. A god_admin and a brand-sales user
    # both have users.organization_id = NULL by design, so provisioning a
    # customer, reassigning a brand manager or marking an implementation Live
    # could not be audited at all — the INSERT would violate NOT NULL. Every
    # existing caller still passes a tenant id; this only permits the rows that
    # previously could not exist.
    ("audit_log_entries", "organization_id"),
]


# Postgres-only DDL that cannot be expressed portably in the models.
#
# Each entry is (label, sql). Run in order, each in its own transaction, each
# failure logged and skipped — exactly like every other list in this file. All
# of these must be idempotent on their own terms.
#
# THE DOUBLE-BOOKING CONSTRAINT
# -----------------------------
# Two people must not be able to book Michael for overlapping meetings. The
# service layer checks for a conflict inside the booking transaction, which
# catches every ordinary case and works on SQLite too — but two concurrent
# requests can both pass that check before either commits. Only the database
# can settle that race.
#
# `busy_start_at`/`busy_end_at` on a participant row are the meeting window
# already expanded by that person's buffers, so the range here is exactly the
# time they are unavailable. `is_blocking` is false for cancelled meetings, so
# cancelling frees the slot without deleting history.
#
# Requires btree_gist (for the `user_id WITH =` half). If the extension cannot
# be created — a managed Postgres that forbids it — the constraint is skipped
# and the in-transaction check remains the protection. That is a real
# degradation, so it is logged loudly rather than passing silently.
POSTGRES_ONLY_DDL = [
    ("btree_gist extension",
     "CREATE EXTENSION IF NOT EXISTS btree_gist"),
    ("no-overlap constraint on sales_appointment_participants", """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'sales_participant_no_overlap'
            ) THEN
                ALTER TABLE sales_appointment_participants
                    ADD CONSTRAINT sales_participant_no_overlap
                    EXCLUDE USING gist (
                        user_id WITH =,
                        tsrange(busy_start_at, busy_end_at, '[)') WITH &&
                    ) WHERE (is_blocking);
            END IF;
        END $$;
    """),
]


def run_auto_migrations(engine) -> None:
    """
    Called once from main.py's startup handler, right after
    Base.metadata.create_all(). Safe to call on every single boot -
    every statement is a no-op if already applied.
    """
    is_sqlite = str(engine.url).startswith("sqlite")

    with engine.connect() as conn:
        # Create any new whole tables first (before column adds, since COLUMNS_TO_ADD
        # may reference columns in these new tables).
        for create_sql in TABLES_TO_CREATE:
            try:
                conn.execute(text(create_sql))
                conn.commit()
            except (OperationalError, ProgrammingError) as e:
                conn.rollback()
                print(f"[auto_migrate] Table create skipped: {e}")

        for table, column, definition in COLUMNS_TO_ADD:
            try:
                if is_sqlite:
                    # SQLite doesn't support "IF NOT EXISTS" on ADD COLUMN -
                    # check first, then add only if genuinely missing.
                    existing_cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
                    if column not in existing_cols:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
                else:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition};"))
            except (OperationalError, ProgrammingError) as e:
                # Logged, not raised - a single failed column-add (e.g.
                # the table itself doesn't exist yet on a brand-new
                # database, where create_all() just created it fresh
                # with this column already included) should never crash
                # the whole app on startup. Each statement is independent.
                print(f"[auto_migrate] Skipped {table}.{column}: {e}")
        conn.commit()

        if not is_sqlite:
            # Relax NOT NULL where the data model no longer requires it.
            # Idempotent: information_schema is consulted first, so this is a
            # genuine no-op once applied. SQLite is skipped - it cannot drop a
            # NOT NULL without rebuilding the table, and local/test databases
            # are created fresh from the models, which already say nullable.
            for table, column in NULLABILITY_TO_RELAX:
                try:
                    nullable = conn.execute(text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = :column"
                    ), {"table": table, "column": column}).scalar()

                    if nullable == "NO":
                        conn.execute(text(
                            f"ALTER TABLE {table} ALTER COLUMN {column} DROP NOT NULL;"
                        ))
                        conn.commit()
                        print(f"[auto_migrate] Relaxed NOT NULL on {table}.{column}.")
                    # else: already nullable, or the column/table does not exist yet.
                except (OperationalError, ProgrammingError) as e:
                    conn.rollback()
                    print(f"[auto_migrate] Skipped NOT NULL relax for {table}.{column}: {e}")

            # Postgres-only DDL: btree_gist plus the participant no-overlap
            # exclusion constraint that makes a double-booking race impossible.
            # See POSTGRES_ONLY_DDL for why this cannot live in the models.
            # SQLite skips it and relies on the in-transaction check alone.
            for _label, _sql in POSTGRES_ONLY_DDL:
                try:
                    conn.execute(text(_sql))
                    conn.commit()
                    print(f"[auto_migrate] OK: {_label}")
                except (OperationalError, ProgrammingError) as e:
                    conn.rollback()
                    print(f"[auto_migrate] !! DEGRADED - could not apply {_label}: {e}")
                    print("[auto_migrate] !! Double-booking now relies on the in-transaction "
                          "check alone; a concurrent race could slip through.")

        if not is_sqlite:
            # Real, one-time column-type conversions - enum columns that
            # became plain strings when TierDefinition replaced the old
            # hardcoded LeadTier/MessageTrack enums. Idempotent: checks
            # the column's CURRENT data type via information_schema
            # first, so this is a safe no-op on every later boot once
            # the conversion has already happened once. SQLite is
            # skipped entirely here since SQLite has no real column-type
            # enforcement to begin with - a SQLite column declared as
            # SAEnum already stores whatever Python handed it (which,
            # confirmed earlier in this project, is the lowercase
            # .value via SQLite's loose typing, not the uppercase NAME
            # quirk that's Postgres-specific), so there's nothing to
            # convert there.
            for table, column, enum_type in ENUM_COLUMNS_TO_CONVERT_TO_STRING:
                try:
                    current_type = conn.execute(text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name = :table AND column_name = :column"
                    ), {"table": table, "column": column}).scalar()

                    if current_type == "USER-DEFINED":
                        # Still the old enum type - convert now. LOWER()
                        # is the critical piece: Postgres holds the
                        # uppercase enum NAME (e.g. "PRE_NEED"), and
                        # every TierDefinition.tier_key/track_key is
                        # lowercase - a cast without LOWER() would
                        # silently corrupt every existing row to a value
                        # that matches nothing.
                        conn.execute(text(
                            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR USING LOWER({column}::text);"
                        ))
                        conn.commit()
                        print(f"[auto_migrate] Converted {table}.{column} from enum to string (lowercased).")
                    # else: already a plain string (varchar/text) - genuinely nothing to do, not even a log line needed every single boot.
                except (OperationalError, ProgrammingError) as e:
                    conn.rollback()
                    print(f"[auto_migrate] Skipped enum-to-string conversion for {table}.{column}: {e}")

        if not is_sqlite:
            # Postgres enum ADD VALUE has historically had restrictions
            # running inside an explicit transaction in some versions -
            # run these in their own autocommit-style execution, separate
            # from the column-add transaction above.
            for enum_type, value in ENUM_VALUES_TO_ADD:
                try:
                    conn.execute(text("COMMIT"))  # ensure no open transaction before ALTER TYPE
                    conn.execute(text(f"ALTER TYPE {enum_type} ADD VALUE IF NOT EXISTS '{value}';"))
                    conn.execute(text("COMMIT"))
                except (OperationalError, ProgrammingError) as e:
                    print(f"[auto_migrate] Skipped enum {enum_type}.{value}: {e}")

        # Indexes — CREATE INDEX IF NOT EXISTS is idempotent on both Postgres
        # and SQLite. Run these after all columns are confirmed to exist so the
        # index can always find its target column. Each index is independent;
        # one failure doesn't block the rest.
        for idx_sql in INDEXES_TO_CREATE:
            try:
                conn.execute(text(idx_sql))
                conn.commit()
            except (OperationalError, ProgrammingError) as e:
                conn.rollback()
                print(f"[auto_migrate] Index skipped: {e}")

    print(f"[auto_migrate] Startup migration check complete ({len(COLUMNS_TO_ADD)} columns, {len(ENUM_VALUES_TO_ADD)} enum values, {len(INDEXES_TO_CREATE)} indexes checked).")

    # ── Platform seed ─────────────────────────────────────────────────────────
    # Idempotent: ON CONFLICT (slug) DO NOTHING — safe to run on every boot.
    # These three rows are the canonical AdvisorFlow platforms. god_admin's
    # Command Center returns empty platform cards until these exist.
    PLATFORM_SEEDS = [
        {
            "slug":          "bookaboost",
            "name":          "BookaBoost",
            "domain":        "app.bookaboost.live",
            "support_email": "support@bookaboost.live",
        },
        {
            "slug":          "evosyspro",
            "name":          "EvoSys Pro",
            "domain":        "app.evosyspro.live",
            "support_email": "support@evosyspro.live",
        },
        {
            "slug":          "harmonyhustle",
            "name":          "Harmony Hustle",
            "domain":        "app.harmonyhustle.com",
            "support_email": "support@harmonyhustle.com",
        },
    ]
    with engine.connect() as conn:
        is_sqlite = str(engine.url).startswith("sqlite")
        for p in PLATFORM_SEEDS:
            try:
                if is_sqlite:
                    conn.execute(text("""
                        INSERT OR IGNORE INTO platforms (id, name, slug, domain, support_email, is_active)
                        VALUES (:id, :name, :slug, :domain, :support_email, 1)
                    """), {"id": __import__("uuid").uuid4().hex, **p})
                else:
                    conn.execute(text("""
                        INSERT INTO platforms (id, name, slug, domain, support_email, is_active)
                        VALUES (gen_random_uuid(), :name, :slug, :domain, :support_email, TRUE)
                        ON CONFLICT (slug) DO NOTHING
                    """), p)
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"[auto_migrate] Platform seed skipped ({p['slug']}): {e}")
    print("[auto_migrate] Platform seed check complete.")

    # ── Brand presentation backfill ───────────────────────────────────────────
    # Copies the values that were live in the four hardcoded registries onto the
    # platform rows, so consolidation changes NOTHING visually on the way in.
    #
    # COALESCE means only NULL columns are written. Run it a hundred times and
    # it does nothing after the first; edit a brand's colour in the product and
    # this will never overwrite that choice. That is what makes the database the
    # source of truth rather than a cache of these literals.
    try:
        from app.services.brand_config import FROZEN_BRAND_DEFAULTS as _FROZEN
    except Exception as _e:                                        # noqa: BLE001
        _FROZEN = {}
        print(f"[auto_migrate] brand backfill skipped (import): {_e}")

    if _FROZEN:
        _FIELDS = ("short_name", "logo_initial", "tagline", "theme_slug",
                   "accent_color", "accent_color_2", "green_color", "bg_color",
                   "invite_accent_color", "support_phone", "website_url",
                   "app_base_url")
        with engine.connect() as conn:
            for _slug, _cfg in _FROZEN.items():
                try:
                    _sets = ", ".join(
                        "%s = COALESCE(%s, :%s)" % (f, f, f) for f in _FIELDS)
                    _params = {f: _cfg.get(f) for f in _FIELDS}
                    _params["slug"] = _slug
                    conn.execute(text(
                        "UPDATE platforms SET " + _sets + " WHERE slug = :slug"),
                        _params)
                    conn.commit()
                except Exception as _e:                            # noqa: BLE001
                    conn.rollback()
                    print(f"[auto_migrate] brand backfill skipped ({_slug}): {_e}")
        print("[auto_migrate] Brand presentation backfill complete.")

    # ── CRM connections table ─────────────────────────────────────────────────
    # Not managed via SQLAlchemy models — created here so it's always present
    # without requiring a manual migration step.
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS crm_connections (
                    id                  VARCHAR PRIMARY KEY,
                    organization_id     VARCHAR NOT NULL,
                    name                VARCHAR NOT NULL,
                    crm_type            VARCHAR NOT NULL DEFAULT 'webhook',
                    webhook_url         VARCHAR,
                    webhook_secret      VARCHAR,
                    api_key_encrypted   VARCHAR,
                    api_base_url        VARCHAR,
                    sync_mode           VARCHAR DEFAULT 'push_only',
                    push_events         TEXT    DEFAULT '["booking","status_change"]',
                    annotation_tag      VARCHAR DEFAULT 'BookaBoost',
                    field_mapping       TEXT,
                    active              BOOLEAN DEFAULT TRUE,
                    last_push_at        TIMESTAMP,
                    last_pull_at        TIMESTAMP,
                    total_pushed        INTEGER DEFAULT 0,
                    total_pulled        INTEGER DEFAULT 0,
                    created_at          TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
            print("[auto_migrate] crm_connections table ensured.")
        except (OperationalError, ProgrammingError) as e:
            conn.rollback()
            print(f"[auto_migrate] crm_connections table note: {e}")


    # ── booking_followups table ───────────────────────────────────────────────
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS booking_followups (
                    id                  VARCHAR PRIMARY KEY,
                    booking_link_id     VARCHAR NOT NULL,
                    lead_id             VARCHAR NOT NULL,
                    advisor_id          VARCHAR NOT NULL,
                    channel             VARCHAR DEFAULT 'sms',
                    sent_at             TIMESTAMP DEFAULT NOW(),
                    survey_token        VARCHAR UNIQUE,
                    thank_you_sent      BOOLEAN DEFAULT FALSE,
                    survey_link_sent    BOOLEAN DEFAULT FALSE,
                    error               VARCHAR,
                    created_at          TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
            print("[auto_migrate] booking_followups table ensured.")
        except (OperationalError, ProgrammingError) as e:
            conn.rollback()
            print(f"[auto_migrate] booking_followups table note: {e}")

    # ── survey_responses table ────────────────────────────────────────────────
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS survey_responses (
                    id                      VARCHAR PRIMARY KEY,
                    booking_followup_id     VARCHAR NOT NULL,
                    lead_id                 VARCHAR NOT NULL,
                    advisor_id              VARCHAR NOT NULL,
                    rating                  INTEGER,
                    feedback                TEXT,
                    facebook_handle         VARCHAR,
                    instagram_handle        VARCHAR,
                    submitted_at            TIMESTAMP DEFAULT NOW(),
                    created_at              TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
            print("[auto_migrate] survey_responses table ensured.")
        except (OperationalError, ProgrammingError) as e:
            conn.rollback()
            print(f"[auto_migrate] survey_responses table note: {e}")

    # ── appointment_case_files table ──────────────────────────────────────────
    with engine.connect() as conn:
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS appointment_case_files (
                    id                          VARCHAR PRIMARY KEY,
                    lead_id                     VARCHAR NOT NULL,
                    organization_id             VARCHAR NOT NULL,
                    recorded_by_id              VARCHAR NOT NULL,
                    booking_link_id             VARCHAR,
                    appointment_date            TIMESTAMP,
                    appointment_type            VARCHAR,
                    outcome_type                VARCHAR,
                    products_discussed          TEXT,
                    products_sold               TEXT,
                    policy_carrier              VARCHAR,
                    policy_number               VARCHAR,
                    coverage_amount             VARCHAR,
                    premium_monthly             VARCHAR,
                    premium_annual              VARCHAR,
                    application_date            TIMESTAMP,
                    issue_date                  TIMESTAMP,
                    chk_id_verified             BOOLEAN DEFAULT FALSE,
                    chk_beneficiary_named       BOOLEAN DEFAULT FALSE,
                    chk_app_signed              BOOLEAN DEFAULT FALSE,
                    chk_payment_collected       BOOLEAN DEFAULT FALSE,
                    chk_illustrations_reviewed  BOOLEAN DEFAULT FALSE,
                    chk_medical_history         BOOLEAN DEFAULT FALSE,
                    chk_hipaa_signed            BOOLEAN DEFAULT FALSE,
                    chk_replacement_form        BOOLEAN DEFAULT FALSE,
                    chk_beneficiary_reviewed    BOOLEAN DEFAULT FALSE,
                    chk_riders_explained        BOOLEAN DEFAULT FALSE,
                    advisor_notes               TEXT,
                    objections_raised           TEXT,
                    client_concerns             TEXT,
                    referral_potential          BOOLEAN DEFAULT FALSE,
                    referral_notes              TEXT,
                    case_status                 VARCHAR DEFAULT 'open',
                    next_action                 VARCHAR,
                    next_action_date            TIMESTAMP,
                    next_action_notes           TEXT,
                    crm_synced_at               TIMESTAMP,
                    crm_sync_status             VARCHAR,
                    crm_external_id             VARCHAR,
                    crm_error                   TEXT,
                    created_at                  TIMESTAMP DEFAULT NOW(),
                    updated_at                  TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
            print("[auto_migrate] appointment_case_files table ensured.")
        except (OperationalError, ProgrammingError) as e:
            conn.rollback()
            print(f"[auto_migrate] appointment_case_files table note: {e}")

    # ── proposal_tokens.protect_content column ────────────────────────────────
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE proposal_tokens ADD COLUMN IF NOT EXISTS protect_content BOOLEAN NOT NULL DEFAULT FALSE"
            ))
            conn.commit()
            print("[auto_migrate] proposal_tokens.protect_content ensured.")
    except (OperationalError, ProgrammingError) as e:
        conn.rollback()
        print(f"[auto_migrate] proposal_tokens.protect_content note: {e}")

    # ── proposal_files table ──────────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS proposal_files (
                    id              VARCHAR PRIMARY KEY,
                    organization_id VARCHAR NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
                    proposal_id     VARCHAR NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
                    filename        VARCHAR NOT NULL,
                    content_type    VARCHAR NOT NULL,
                    file_size       INTEGER NOT NULL,
                    file_data       BYTEA   NOT NULL,
                    created_at      TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
            print("[auto_migrate] proposal_files table ensured.")
    except (OperationalError, ProgrammingError) as e:
        conn.rollback()
        print(f"[auto_migrate] proposal_files table note: {e}")

    # ONE-TIME ORG RENAME: Restland → Greenland (idempotent — no-op if already done)
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE organizations SET name = 'Greenland Cemetery and Funeral Home' "
                "WHERE name = 'Restland Cemetery & Funeral Home'"
            ))
            conn.commit()
    except Exception as e:
        # ASCII arrow deliberately. This line runs inside an exception handler
        # on every boot; on a console whose codepage is not UTF-8 (any plain
        # Windows shell) a U+2192 here raises UnicodeEncodeError *while
        # reporting an error*, turning a skipped no-op migration into a hard
        # crash. Render is UTF-8 so production never saw it — a local run did.
        print(f"[auto_migrate] Restland->Greenland rename note: {e}")

    # ONE-TIME PASSWORD RESET BLOCK REMOVED — security fix 2026-08-20
    # Reason: this block committed a bcrypt hash to source control and ran on
    # every deploy, overwriting any password change made through the app.
    # God-admin role enforcement is handled in main.py on_startup via
    # GOD_ADMIN_EMAIL env var (never hardcoded). Password is set once through
    # the app and is not touched by migrations.

    # Org-level shared Twilio credentials (toll-free / 10DLC fallback)
    _org_twilio_cols = {
        "org_twilio_account_sid":          "VARCHAR",
        "org_twilio_auth_token_encrypted": "VARCHAR",
        "org_twilio_phone_number":         "VARCHAR",
        "org_twilio_caller_id_name":       "VARCHAR",
        "org_twilio_number_type":          "VARCHAR DEFAULT 'toll_free'",
    }
    for col, col_type in _org_twilio_cols.items():
        try:
            with engine.connect() as conn:
                conn.execute(text(
                    f"ALTER TABLE organizations ADD COLUMN IF NOT EXISTS {col} {col_type}"
                ))
                conn.commit()
        except (OperationalError, ProgrammingError) as e:
            print(f"[auto_migrate] organizations.{col} note: {e}")
    print("[auto_migrate] org-level Twilio columns ensured.")
