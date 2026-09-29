# EvoSys Pro — Master Overnight Build (2026-09-28, 17:17 CT →)

Rollback: tag `pre-overnight-20260928` (= eb493fe, prod before this build).
Revert path: `git revert <build commits>` + push (Render auto-deploys); all schema changes are NEW tables only
(feature_overrides, feature_override_events, lead_notes, lead_tasks, reply_states, lead_intel_scrape_jobs,
lead_intel_prospects, lead_intel_routing_rules, wholesale_temperature_overrides, wholesale_seller_callbacks,
wholesale_deal_notes, wholesale_conversation_controls, wholesale_conversation_control_events,
wholesale_pilot_controls). No column/enum changes, no data migrations. Leaving the tables in place after a
revert is harmless.

## Journal
- 17:17 mission received (spec + 6 mockups). 17:18 kickoff; rollback tag pushed.
- 17:20 architecture audit (3 parallel explorers) → gap list (see "Audit findings").
- 17:25 shared model modules registered; 7 parallel build streams launched with disjoint file ownership.
- 18:24 all streams back green in isolation. Integration: routers, routes, EntitlementsPanel embed,
  ThemeToggle removed, manual-send tagging, god voice path gate, conftest Twilio guard hardened.
- 18:30 independent security review: 2 HIGH (suppression phone-format mismatch in routing + scraper),
  4 MEDIUM (feature_report resolved-list write-back, sandbox attach_seller crossover, unbounded pipeline
  summary, entitlement query count), 5 LOW. All fixed + tests.
- 18:37–19:16 container full suite: 6,538 passed; failures = environment-only (public-site/, deploy.bat,
  config/workspace-views) + pre-existing zoom date-boundary flake (fails on untouched baseline too).
- 19:14 Windows full suite (deploy gate) started on applied tree (ov1, 104 files).
- 19:15–19:40 headless visual QA vs mockups at 1440/390: 11 UI defects fixed (ov2, 16 files), incl. dark
  profile-checklist widget left over from dark theme, blank org wordmark, mojibake in admin text.
- 20:24 Windows full suite: 6,591 passed, 0 failed, 27 skipped (69 min). Deploy gate passed.
- 20:26 committed d76a044 (build) + 6c705ad (QA pass), pushed to main. 20:34 prod /version = 6c705ad, /health 200.
- 20:34 prod smoke (god token): 16 new/changed endpoints 200 (0.17–1.1 s); Atlantis contacts still 10,861, 0 leads;
  cross-tenant (ZZ A lead read from Atlantis) → 404 on rate-requests, thread, notes; compose-gate on the sandbox lead
  refuses (NO_SMS_CONSENT, QUIET_HOURS, NO_SENDER); no overrides exist in prod (matrix: 0 overrides). UI verified:
  /god/entitlements, /god/customers/<Atlantis>, /god/lead-intelligence, Atlantis /pipeline, /rate-requests, /replies;
  footer now "Atlantis Light & Power · Mike Simmons · Platform Owner".
- 20:35 second Windows full pass started on 6c705ad. Command Center Completed row 30 added.
- 21:46 second Windows full pass: 6,596 passed, 0 failed, 27 skipped (71 min).
- 21:48 polish: Rate Requests KPI cards show a dash (not "Not yet available") while the summary loads; this doc.

## What shipped (by workstream)
1. Hierarchical Feature Entitlements — resolver with provenance (platform → brand → org → workspace → role/user),
   states Enabled/Disabled/Inherited/Override/Requires Setup/Blocked by Dependency; explain + history + bulk;
   God page /god/entitlements; embedded in Org Control Center. Zero override rows ⇒ identical to legacy (tested
   across 9 org shapes). Brand-level disable closes the BookaBoost → wholesale URL/API bypass. 10-step
   portability proof: tests/test_entitlements_portability.py.
   Precedence: most specific explicit override wins across platform/brand/org; without an org override the org's
   legacy allow-list still applies; workspace/role/user overrides only narrow. Workspace overrides are stored and
   explained but not enforced per request (requests carry no location context).
2. Organization Control Center (/god/customers/:id) — Overview, Locations, People, Entitlements, Operations,
   Administration; readiness (Company, Primary Location, Users, Booking, SMS, Email, AI Automation) computed from
   real rows; blueprints core / energy / wholesale_real_estate (app/services/org_blueprints.py); e2e provisioning
   test for a wholesale and an energy org.
3. Lead Intelligence Control Center (/god/lead-intelligence) + God nav reorg (PLATFORM, SALES & REVENUE, LEAD
   INTELLIGENCE, AI WORKFORCE, SECURITY & PLATFORM, BRANDS). Scraper: industry, destination (recorded only),
   normalize → dedupe → suppression → qualification. Routing is explicit and lands in the destination org's
   import review queue (never Leads, never consent). Thresholds HIGH 45+/MEDIUM 22–44/LOW <22 read-only (no
   settings store).
4. Atlantis Sales Pipeline command center (/pipeline) — /pipeline/summary (SQL aggregates), appointments, paged
   conversations; Confirmed/Appointment Kept shown "Not yet available" (never written by the pipeline);
   projected bookings null (no forecast model).
5. Rate Requests work queue (/rate-requests) — server-side filters, energy fields, create (explicit human
   action; no consent/cadence/send), drawer, assign/status. Mapping New=new_inquiry, In Review=rate_review,
   Options Sent=proposal_sent, Completed=contract_signed, Booked=open request with booked appointment.
6. Communications / Replies command center (/replies) — no 200 cap, summary KPIs, filters, queue + workspace
   pane, notes, tasks, review/assign/callback; composer gated (DNC, STOP, consent, preflight, quiet hours,
   sender) and sends MANUAL through existing send_sms. Identity fix: footer shows workspace org + role in that
   workspace (the "EvoSys Wholesale" text was a user's full_name). Convert to Lead hidden (every reply already
   has a lead; unknown-number texts are not stored).
7. Wholesale pilot P0 — bulk voice through DNC/suppression/paused_voice/Pause AI gate (also the god call path);
   re-import reuses existing lead (DNC wins; test/real never crossed); HOT/WARM/COLD human override + audit;
   Callback Center (/wholesale/callbacks); deal notes; Pause AI / Take Over / Resume (SMS, voice, automated
   email); pilot cap 500 (250–500 recommended) at /wholesale/pilot; auto_match_on_contract default off for new
   rows; skip-trace cost from real ledgers (cheapest configured provider $0.10/hit — $0.01 not viable today);
   voicemail inbound not recorded → reported unavailable; dialer = tel: link only. Seed script
   scripts/seed_building_equity.py (no Derrick credentials; not run against prod).
8. Light-only theme — toggle removed, one documented token file (styles/appearance.css), rail/top-bar tokens in
   Layout.css.

## Owner follow-ups
- Derrick: create his user via the invite flow once his real email is known.
- email_router custom subject/body path writes EmailMessage directly (pre-existing; review later).
- /voice/inbound looks callers up across all orgs; TWILIO_FROM_NUMBER hard-coded in voice_router (pre-existing).
- DemoSitesPanel.jsx mojibake (pre-existing).
