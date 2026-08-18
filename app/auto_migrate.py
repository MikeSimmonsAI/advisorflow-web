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
    # Post-appointment review request — track that we've sent the Google review SMS
    ("booking_links", "review_request_sent_at", "TIMESTAMP"),
    # Social media lead capture — org-level webhook credentials + token
    ("organizations", "social_webhook_token", "VARCHAR"),
    ("organizations", "meta_page_access_token", "VARCHAR"),
    ("organizations", "meta_webhook_verify_token", "VARCHAR"),
    ("organizations", "tiktok_webhook_secret", "VARCHAR"),
    ("organizations", "enabled_features", "TEXT"),
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
    # last_messaged_at on leads — denormalized for fast "sent today" badge in Leads list
    ("leads", "last_messaged_at", "TIMESTAMP"),
    # Org-level email sender — each brand sends from its own domain/address.
    # BookaBoost: support@bookaboost.live / EvoSys Pro: support@evosyspro.live.
    # When set, these override the global RESEND_API_KEY / EMAIL_FROM_ADDRESS env vars
    # so each org's outbound email genuinely comes from their own domain.
    ("organizations", "from_email", "VARCHAR"),
    ("organizations", "resend_api_key", "VARCHAR"),
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
    # AI lead quality note — Phase 2 field added to Lead model
    ("leads", "ai_lead_quality_note", "TEXT"),
    # Reply review tracking — when a reply was reviewed/actioned by an advisor
    ("replies", "reviewed_at", "TIMESTAMP"),
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
    "CREATE INDEX IF NOT EXISTS ix_messages_lead_id      ON messages(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_messages_sent_at      ON messages(sent_at)",
    "CREATE INDEX IF NOT EXISTS ix_messages_sender_id    ON messages(sender_id)",
    "CREATE INDEX IF NOT EXISTS ix_email_messages_lead_id   ON email_messages(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_email_messages_sent_at   ON email_messages(sent_at)",
    "CREATE INDEX IF NOT EXISTS ix_email_messages_sender_id ON email_messages(sender_id)",
    "CREATE INDEX IF NOT EXISTS ix_replies_lead_id       ON replies(lead_id)",
    "CREATE INDEX IF NOT EXISTS ix_replies_received_at   ON replies(received_at)",
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

    # ── ONE-TIME PASSWORD RESET (remove after first deploy) ─────────────────
    try:
        _PW_HASH = "$2b$12$Z.vk1S50eQYC0quZm77VAu/p1dfPmP/YyAl7y1Bk.lkenzIqNp3VO"
        with engine.connect() as conn:
            result = conn.execute(
                text("UPDATE users SET password_hash=:h, role='god_admin', is_active=true WHERE email='mike@simmonsstrong.com'"),
                {"h": _PW_HASH}
            )
            conn.commit()
            print(f"[auto_migrate] mike@simmonsstrong.com — role+password reset rowcount={result.rowcount}")
    except Exception as e:
        print(f"[auto_migrate] Password reset error: {e}")
