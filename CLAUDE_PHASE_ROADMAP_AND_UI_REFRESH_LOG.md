# BookaBoost Platform — Project Roadmap & Build Log

**Last updated: August 2026**
Platform formerly known as AdvisorFlow. Rebranded to BookaBoost.
Multi-tenant SaaS — FastAPI backend (Python 3.11) + React/Vite frontend, deployed on Render.

---

## Platform Status: LIVE & PRODUCTION-READY

The platform is deployed, multi-tenant, and actively serving clients.
All session logs from V1.1 through V2.3+ are preserved in the root of this repo.

---

## What the Platform Does (Plain English)

BookaBoost is a lead engagement and appointment-booking platform for service businesses
(funeral/cemetery, roofing, insurance, real estate, dental, legal, home services).

Each client organization gets their own isolated workspace. Advisors (salespeople) log in,
import their leads, and BookaBoost handles the outreach — texts, emails, AI follow-up,
and appointment booking — automatically and intelligently.

---

## Build History — Phase by Phase


### PHASE 0 — Foundation (Original Build)

**Status: COMPLETE**

Core backend and frontend built from scratch.

- FastAPI backend with SQLAlchemy ORM + PostgreSQL
- Multi-user login system (JWT auth, role-based: advisor / org_admin / super_admin)
- Lead import from Excel — dedup via ContactRegistry (phone + normalized last name)
- 9-touch SMS cadence (days 1, 3, 7, 10, 14, 21, 30, 45, 60) via Twilio
- Email cadence for phone-less leads via Microsoft Graph API (OAuth2)
- Hot reply detection — keywords → email alert to advisor
- Master admin dashboard — KPIs across all advisors
- Frontend pages: Login, Overview, Leads, Replies, Cadence, Email Queue, Admin Dashboard
- Dark "Tesla dashboard" UI design language
- Deployed to Render (backend + frontend static site)

---

### PHASE 1 — UI Command Center Pass

**Status: COMPLETE** | Session: CLAUDE_PHASE_ROADMAP_AND_UI_REFRESH_LOG (original)

No new backend endpoints. Made existing data on 3 pages more actionable.

- Overview: command-priority deck with real daily briefing data
- Leads: header stat cards (total / SMS-ready / needs review / blocked)
- Replies: classification filters, book-first priority lane, search input
- Added `.command-kicker` shared CSS utility

---

### PHASE 1.1 — Manual Lead Edit + Outcomes

**Status: COMPLETE** | Session: SESSION_LOG_V1.1

- PATCH /leads/{id} endpoint — edit contact fields inline
- LeadOutcome model — record what happened at the appointment
- LeadDetail.jsx edit panel — advisors can fix bad data without admin
- Outcome recording UI on LeadDetail


### PHASE 1.2 — Action Center, Roles, Tone Selector

**Status: COMPLETE** | Session: SESSION_LOG_V1.2

- Action Center toggles on lead cards (quick status changes)
- Role-based UI gating (advisor vs org_admin vs super_admin views)
- Reply tone selector (cold / warm / hot / urgent) on draft-reply panel
- Draft reply AI — generates suggested response based on conversation history

---

### PHASE 1.3 — Google Contacts + Referrals

**Status: COMPLETE** | Session: SESSION_LOG_V1.3

- Google Contacts import integration
- Referral tracking system — track who referred whom
- Lead source attribution improvements

---

### PHASE 1.4 — Certified Appointment Pipeline

**Status: COMPLETE** | Session: SESSION_LOG_V1.4

- PipelineConversation model — tracks AI conversation state per lead
- Full pipeline stages: outreach_sent → replied → ai_responding → booking_sent → booked
- AI generates first outreach message using lead type + tone context
- Confidence scoring (0–100) — determines auto-send vs. flag for human review
- Pipeline launch endpoint — bulk-launch for a list of leads

---

### PHASE 1.5 — Certification Wired In

**Status: COMPLETE** | Session: SESSION_LOG_V1.5

- Pipeline wired end-to-end: inbound reply → analyze → auto-send or flag
- FSA (advisor) notified via SMS when lead books or reply needs review
- Flagged replies surface a suggested response for the advisor to review/send
- Pipeline stats endpoint drives Overview page AI forecast


### PHASE 1.6 — Email Timeline Fix

**Status: COMPLETE** | Session: SESSION_LOG_V1.6

- Full conversation timeline on LeadDetail — SMS + email events in one chronological feed
- Email thread display with proper HTML rendering
- Timeline correctly interleaves outbound messages and inbound replies by timestamp

---

### PHASE 1.7 — Mixed Channel Cadence

**Status: COMPLETE** | Session: SESSION_LOG_V1.7

- Cadence engine extended to handle both SMS and email touches in one unified sequence
- Email cadence touches fire via Microsoft Graph (same OAuth2 token as email queue)
- CadenceState tracks channel per touch — no double-sending across channels
- Leads with both phone and email respect channel priority rules

---

### PHASE 1.8 — Email Queue Rebuild

**Status: COMPLETE** | Session: SESSION_LOG_V1.8

- Email queue completely rebuilt — was unreliable, now uses a dedicated EmailMessage model
- Email poller service (email_poller_service.py) runs every 2 minutes via Render cron
- Polls Microsoft Graph for inbound email replies and matches them back to leads
- Reply classification runs on email body same as SMS — INTERESTED / CALLBACK / DNC / NEUTRAL
- Hot reply email alerts fire correctly for email-sourced hot replies

---

### PHASE 1.9 — Email Queue Visual Redesign

**Status: COMPLETE** | Session: SESSION_LOG_V1.9

- Email Queue page rebuilt visually — cleaner card layout, status chips, priority sorting
- Composer panel redesigned — subject + body fields, send confirmation
- Email queue correctly separates unsent / sent / failed states


### PHASE 2.0 — Manager Command Dashboard Redesign

**Status: COMPLETE** | Session: SESSION_LOG_V2.0

Replaced the basic admin KPI table with a full manager intelligence center.

- Quality metrics per advisor: reply rate, hot reply rate, booking rate, DNC rate
- Org-wide funnel: total leads → sent → replied → hot → booked → sold
- Revenue analytics: sale counts by advisor, product mix (funeral arrangement / cemetery property / marker / memorial), monthly trend
- Per-advisor detail page — profile, metrics, and recent activity feed in one view
- Lead reassignment — drag leads to a different advisor from the admin dashboard
- Unassigned lead pool — leads with no advisor show in a dedicated queue

---

### PHASE 2.1 — Auto-Send Queue Phase 1

**Status: COMPLETE** | Session: SESSION_LOG_V2.1

Supervised AI auto-response for simple logistical questions — the first unsupervised send path.

Hard gate (4 conditions must ALL pass before any auto-send):
1. Reply classification must be "question" (not interested/callback/dnc/neutral)
2. A dedicated eligibility classifier independently confirms the question is simple/logistical
3. This must NOT be the lead's first-ever reply (prior context required)
4. Confidence must be HIGH — not medium or low

- AutoSendCandidate model — queued candidates wait for advisor approval or auto-fire after delay
- AutoSentLog model — permanent record of every auto-sent response
- Flagged-reply UI — surfaces suggested responses for advisor review before sending
- Auto-send phase setting per advisor — opt in/out of the feature

---

### PHASE 2.2 — Compliance Preflight Engine

**Status: COMPLETE** | Session: SESSION_LOG_V2.2

Single shared compliance gate all send paths must pass — replaces per-path ad-hoc checks.

Problems found before building:
- send_email_to_lead had ZERO compliance check — a STOP-texted lead could still receive emails
- The daily cadence job only checked Lead.status, never the suppression list
- SuppressionEntry was phone-only — couldn't represent an email-based opt-out

What was built:
- compliance_preflight(db, lead, channel) — one function, all paths call it, always
- Cross-channel opt-out: a STOP on SMS blocks email too, and vice versa
- SuppressionEntry extended to support email-based suppression
- All send paths (SMS, email, cadence, batch) now call compliance_preflight first
- DNC reply from SMS automatically adds to suppression list (was two unconnected systems before)
- 689 tests passing after this session


### PHASE 2.3 — Industry-Agnostic Tier System

**Status: COMPLETE** | Session: SESSION_LOG_V2.3

Replaced hardcoded funeral-only tier/track enums with a real per-organization configuration system.

- TierDefinition model — per-org rows: tier_key, tier_label, track_key, track_label, ai_tone_context
- Lead.tier and Lead.message_track changed from Postgres ENUMs to plain validated strings
- Campaign.message_track and MessageTemplate.message_track same change
- tier_config_service.py — validate, list, seed, and get tone context per org
- Restland's existing 8 tiers seeded automatically on startup — behavior unchanged
- AI template generator now reads per-org tone context instead of a hardcoded dict
- Template editor shows THIS org's tracks only — not Restland's tracks in a roofing account
- 701 tests passing after this session

Supported industries: funeral, roofing, insurance, real_estate, dental, legal, home_services

**Still pending:** TierDefinition admin UI (backend complete, no screen yet) + Postgres ALTER TYPE migration for existing live data

---

### PHASE 2.4 — Multi-Tenant SaaS Infrastructure

**Status: COMPLETE** | Multiple sessions

Full white-label multi-tenant architecture built out.

- Organization model with branding: brand_name, brand_logo_url, brand_color_primary, brand_color_accent
- Provision Client endpoint (super_admin only) — one-shot org + supervisor account creation
- Org Settings page — industry, branding palette, social links, tier configuration
- Super admin org selector — switch between client orgs without logging out
- Layout.jsx applies org branding (colors, logo) dynamically per login
- PUT /admin/organizations/{id} — update any org's details, branding, active status
- GET /admin/organizations — list all orgs (super_admin only)
- Login domain detection — bookaboost.live vs evosyspro.live route correctly
- ProvisionClient.jsx — clickable org rows, edit modal, color palette picker


### PHASE 2.5 — Lead Data Enrichment + Address Fields

**Status: COMPLETE** | Task #30, #28, #35

- Address fields added to Lead model: street_address, city, state, zip_code
- auto_migrate.py updated to add columns on next deploy
- PATCH /leads/{id} extended to accept and save address fields
- LeadDetail.jsx edit panel shows and saves all address fields
- Lead Cleanup Center — admin view for finding and merging duplicate leads
  - Tier 1 match: same normalized phone (strongest signal)
  - Tier 3 match: same normalized last name + same source year
  - Safe merge: moves messages, replies, outcomes, cadence history to kept lead, deletes duplicates
  - Fix Contact Info: correct phone/email/name with ContactRegistry re-sync

---

### PHASE 2.6 — Post-Appointment Care System

**Status: COMPLETE** | Tasks #32, #33, #34

Automated follow-up after a lead books and completes their appointment.

- SurveyResponse model — stores submitted survey answers
- BookingFollowup model — tracks thank-you send status per lead
- post_appointment_service.py — sends thank-you SMS/email + survey link after appointment
- survey_router.py — serves the survey page, accepts and stores submissions
- GET /survey/results/{lead_id} — returns survey data for display on LeadDetail
- CRITICAL FIX: route ordering — /results/{lead_id} must be registered before /{token} wildcard
  (FastAPI matches in registration order; the results endpoint was permanently unreachable before this fix)

---

### PHASE 2.7 — CRM Integration + AI Hub + Voice

**Status: COMPLETE** | Tasks #12, #13, #14, #15, #18–#23

- crm_router.py — CRM connection management API (list, create, test, delete connections)
- CRMIntegration.jsx — frontend page for connecting external CRMs
- AI Hub / Pipeline conversation UI — visual pipeline board, flagged reply review, AI forecast
- Voice Calls UI — call log, call initiation panel (Twilio Voice ready)
- Reports page — engagement metrics, booking funnel, activity by day charts
- Industry config in new org onboarding flow


### PHASE 2.8 — Org-Level Social Links

**Status: COMPLETE** | Commits: b84bd4f

Social links moved from advisor-level to organization-level by design.

- Social link fields added to Organization model: facebook_url, google_review_url, instagram_url, linkedin_url
- PATCH /org-settings/social-links — saves to the org, not the user
- OrgSettings.jsx — "📣 Social & Review Links" section added (Facebook, Google Review, Instagram, LinkedIn)
- Settings.jsx — advisor-level social links section removed (they were the wrong level)
- Survey pages and outreach now pull org-level social links — consistent brand links for all advisors
- PUT /settings/social-links (advisor-level) marked deprecated in code — kept for backwards compat

Design rationale: advisors should push the company's social links, not personal ones.

---

### PHASE 2.9 — Bulletproof Post-Booking AI Concierge

**Status: COMPLETE** | Commits: 7a41697

Complete rewrite of the post-booking AI communication system.

Problems found in the original:
- The post-booking path (conv.stage == "booked") was dead code — nothing ever set that stage
- No intent classification — AI would try to re-engage leads who are already booked
- Wrong model — gpt-4o-mini used for high-value, already-committed leads
- No conversation history passed to AI
- Email concierge path was never actually firing

What was rebuilt (ai_conversation_service.py):
- POST_BOOKING_SYSTEM_PROMPT — structured JSON response with intent classification:
  "reschedule" | "cancel" | "question" | "confirm" | "emotional" | "other"
- If intent is reschedule or cancel → escalate to advisor immediately, no auto-reply
- Hard keyword escalation runs first before AI (catches urgent cases fast)
- Upgraded to gpt-4o (full model) for post-booking conversations
- Full conversation history included in every AI call
- handle_inbound_reply() now uses lead.status == "booked" as authoritative check
  (conv.stage was lagging behind; lead.status is the source of truth)
- conv.stage synced as a side-effect of the lead.status check


### PHASE 3.0 — Full Codebase Security & Quality Audit

**Status: COMPLETE** | Commit: 1e73676, 7fce1b3 | August 2026

Top-to-bottom audit of every backend file. Every bug found was fixed before moving on.

#### Critical bugs fixed:

**survey_router.py — GET /results/{lead_id} permanently unreachable**
FastAPI matches routes in registration order. The `/{token}` wildcard was registered first,
so `/results/some-id` matched with token="results" and returned a 404 or wrong page.
Fix: moved `/results/{lead_id}` above `/{token}`. Also removed a duplicate function definition at the bottom.

**email_poller_service.py — hot reply email alert never firing**
The `reply` Python object was created with `is_hot=False`. The pipeline/AI handler
set `is_hot=True` in the database, but the in-memory object was never refreshed.
Fix: added `db.refresh(reply)` before the `if reply.is_hot:` check.

**main.py — password reset on every deploy**
The startup handler included `SET password_hash='$2b$12$...'` — this overwrote any
password change every time the app deployed. Any advisor who changed their password
had it silently reset on the next push.
Fix: removed password_hash from the UPDATE entirely (only enforces role now).

**main.py — two startup handlers**
Two `@app.on_event("startup")` functions existed. Only one runs; the other's logic
was silently skipped on every boot.
Fix: merged into one clean `on_startup()` with numbered steps.

**email_poller_service.py — hardcoded notification email**
Hot reply alerts always went to `michael.simmons@nsmg.com` regardless of the advisor's
configured `notification_email`.
Fix: checks `advisor.notification_email` first, falls back to default.

**email_poller_service.py — dead import**
`import httpx` called but `_httpx` alias used throughout. Dead import removed.

**main.py — mid-file duplicate imports**
`from sqlalchemy import text as _text` and `from app.routers.pipeline_router import router`
appeared twice (once at the top, once mid-file). Duplicates removed.

**sms_router.py — multi-tenant SMS security gap (CRITICAL)**
The inbound webhook did `db.query(Lead).filter(Lead.phone == lead_phone)` with no org filter.
In a multi-tenant environment, an inbound SMS from a lead in Org A could match a lead
with the same phone number in Org B — routing their reply to the wrong advisor entirely.
Fix: look up the advisor by the `To` Twilio number first, then scope lead lookup to
`Lead.organization_id == advisor.organization_id`.

**sms_router.py — post-booking AI never triggered via SMS**
The inbound webhook called `process_inbound_reply()` (pre-booking pipeline) for ALL leads,
including booked ones. A booked lead who texted back would get the pipeline trying to
re-engage them toward booking — the post-booking concierge was unreachable from SMS.
Fix: inbound webhook now checks `lead.status == "booked"` and routes to
`handle_inbound_reply()` (post-booking AI) vs `process_inbound_reply()` (pipeline).

**leads_router.py — discarded normalize_last_name return value**
`normalize_last_name(payload.last_name or "")` was called on manual lead creation
but its return value was thrown away — a dead call that looked like it was doing dedup
but wasn't.
Fix: result now assigned to `last_name_normalized`.

**settings_router.py — advisor-level social-links endpoint confusion**
`PUT /settings/social-links` saved social links to the User model, but social links
moved to the Organization model in Phase 2.8. Two endpoints, different targets, same name.
Fix: endpoint marked deprecated with a clear comment pointing to the org-level endpoint.

#### Files audited (clean — no issues):
- app/deps.py — clean, proper session management
- app/routers/auth_router.py — clean, password change flow correct
- app/routers/admin_router.py — clean, all queries properly org-scoped
- app/services/pipeline_service.py — clean, dual-channel (SMS + email) fallback correct

---

## Current Platform Capabilities (Full Feature List)

### Lead Management
- Import from Excel (dedup by phone + normalized last name)
- Manual lead creation with dedup check
- Address fields (street, city, state, zip)
- Lead tier and message track (per-org configurable)
- Lead status tracking: new → sent → replied → hot → booked → dnc → dead
- Lead reassignment between advisors (admin)
- Lead merge — safely consolidate duplicates with full history transfer
- Fix contact info — corrects phone/email/name with ContactRegistry re-sync
- Unassigned lead pool — queue for manual routing

### Outreach & Cadence
- 9-touch SMS cadence (Twilio, per-advisor credentials)
- Email cadence for phone-less leads (Microsoft Graph OAuth2)
- Mixed channel cadence — SMS + email in one unified sequence
- Compliance preflight on every send path — DNC / suppression check
- Cross-channel opt-out — STOP on SMS blocks email too
- MMS support — send image/flyer with a text message
- Batch SMS send with org-level compliance gating

### AI & Automation
- Pipeline AI conversation — gpt-4o-mini, drives leads toward booking
  - Confidence scoring (0–100) — auto-send vs. flag for human review
  - Intent detection: interested / objection / callback / question / not_interested / dnc / booked
  - Dual-channel fallback: SMS first, email if SMS unavailable
- Post-booking AI concierge — gpt-4o, handles booked lead communications
  - Intent classification: reschedule / cancel / question / confirm / emotional / other
  - Auto-escalates to advisor on reschedule or cancel
  - Hard keyword escalation runs before AI
- Auto-send queue — supervised auto-response for simple logistical questions
- AI draft reply — generates suggested response for advisor to review/send
- AI template generator — creates SMS/email templates per org industry and track
- Reply classification — INTERESTED / CALLBACK / DNC / WRONG_NUMBER / QUESTION / NEUTRAL
- Hot reply detection — fires notification to advisor immediately

### Post-Appointment Care
- Thank-you SMS/email sent automatically after appointment
- Survey link sent with thank-you message
- Survey page served at /survey/{token}
- Survey responses stored and visible on LeadDetail

### Notifications & Alerts
- Hot reply SMS alert to advisor's notification phone
- Hot reply email alert to advisor's notification email
- Pipeline flagged-reply notification (needs human review)
- Post-booking escalation alert (reschedule / cancel requests)
- Per-advisor notification preferences (email, phone)

### Admin & Multi-Tenant
- Multi-tenant isolation — all data scoped by organization_id
- Provision Client (super_admin) — one-shot org + supervisor creation
- Per-org branding: name, logo, primary color, accent color
- Per-org industry and tier configuration
- Per-org social links: Facebook, Google Review, Instagram, LinkedIn
- User management: create, deactivate, reactivate, edit, reset password
- Role hierarchy: super_admin → org_admin → advisor
- Audit log — every admin action recorded
- Demo data seed — generate realistic test data for any org

### Reports & Analytics
- Advisor quality metrics: reply rate, hot reply rate, booking rate, DNC rate
- Org-wide lead funnel: leads → sent → replied → hot → booked → sold
- Revenue analytics: sale counts by advisor, product mix, monthly trend
- Reply activity by day (chart data)
- Pipeline stats: by stage, flagged count, AI auto-sent vs. flagged
- AI forecast: projected bookings, active conversations, opportunities

### Compliance
- Suppression list (phone + email) — org-wide DNC registry
- Reply-based STOP → auto-adds to suppression list
- Compliance preflight gate on every send path
- Compliance Center UI — view, add, remove suppressed contacts
- A2P 10DLC path documented (correct Twilio registration for personalized advisor→lead SMS)

---

## What's Still Ahead

### Immediate / High Priority
- **TierDefinition admin UI** — backend complete (Phase 2.3), no screen to create/edit tiers yet
- **Postgres ALTER TYPE migration** — convert tier/message_track columns from ENUM to VARCHAR on live DB
- **Auto-Send Queue Phase 2** — higher autonomy, broader question types (deliberately not started)

### Medium Priority
- Campaign Builder overhaul — richer audience targeting, scheduling
- Full Conversation Timeline UI — SMS + email + notes in one scrollable thread
- AI Objection Library — common objections + suggested responses by industry
- Google Calendar integration — wire booking confirmation to calendar event creation
- Twilio A2P 10DLC registration — required for production SMS at scale

### Longer Term
- Caller ID name registration (Restland / client name instead of raw number)
- Forgot password / self-service password reset flow
- Mobile-optimized advisor UI
- Webhook integrations (Salesforce, HubSpot, etc.) via CRM router
- TierDefinition import/export per org
- Automated nightly engagement re-scoring

---

## Important Rules for Future Development

1. **Route ordering in FastAPI** — more specific routes MUST be registered before wildcard routes.
   `/results/{lead_id}` must come before `/{token}`. Always check this when adding new routes.

2. **SQLAlchemy object refresh** — after any DB mutation by a called function, call `db.refresh(obj)`
   before reading updated fields from the in-memory object. The Python object does not auto-update.

3. **Multi-tenant scoping** — every query that touches lead/message/reply data must filter by
   `organization_id`. Never trust a phone number or ID alone across orgs.

4. **Startup handler** — there is ONE `@app.on_event("startup")` in main.py. Do not add a second one.
   It silently wins over the first and causes half the startup logic to be skipped.

5. **Social links live at org level** — `Organization.facebook_url` etc. are what get pushed in surveys
   and outreach. `User.facebook_url` fields exist but are deprecated.

6. **Post-booking AI is gpt-4o** — do not downgrade it to gpt-4o-mini. These are committed leads
   and response quality matters more than cost here.

7. **Compliance preflight** — every new send path must call `compliance_preflight(db, lead, channel)`.
   Do not implement per-path DNC checks. One gate, all paths.
