# EvoSysPro — Overnight Full-Stack Master Build (2026-10-01, started 07:42 CT)

Mission spec: Max Life vertical + website, Atlantis operating CRM, Wholesale/EvoSense proof, Universal Intake,
communications/calling, mobile/PWA, security/reliability. Budget 8h. Prior handoff: EXPRESS_OVERHAUL_2026-09-29.md.

## Journal (CT)
- 07:42 start. Computer link was down at start (no commit/deploy path); build continued in the cloud copy.
- 07:45–08:20 wave 1 (5 parallel build streams): Max Life backend, Max Life website, Atlantis gaps, Wholesale e2e
  proof, Mobile/PWA. Computer link restored ~08:15.
- 08:20–08:45 wave 2: Max Life Command frontend; security/entitlement/universal-intake matrix.
- 08:45–09:15 wave 3: Max Life gaps (forms, landing, conversations, SQL paging, browser click-proof), Atlantis
  Sales Board + enrollment truth, human dialer/calling hardening.
- 09:15–09:40 wave 4: background-job reliability audit; cross-vertical UX QA.
- 09:40–10:05 wave 5: polish (voice status order, dead cron, overview counts, launch/me 200, energy overview grid),
  Ask EvoAI 19 intents + verified optional AI rephrase, web push server side (configured:false), God Max Life demo.
- 09:50 Windows full suite on the integrated tree: 6,871 passed, 0 failed, 27 skipped (71 min).
- 10:05 466 targeted tests on the final delta → commit a9ac4b1 (tag pre-overnight-20261001 = previous HEAD), deployed.
- 10:15 3154c9b: UTC-correct "ago" helpers, single clock, PWA headers.
- 10:15–11:00 platform UTC fix (install_utc_json hook + iso_utc in ~280 hand-built sites), perf (mailbox routing
  332→14 queries/poll, god mailbox page 671→20, contacts summary 1 pass, 4 indexes).
- 11:00 5920c74: UTC + perf deployed. Live checks: /version 5920c74, health ok, /push/config configured:false,
  /m + manifest + sw.js served, /agency + /god/demo 401 unauthenticated.
- 11:00–12:25 independent review of the day's work (fresh reviewer). No critical findings; 8 items, fixed below.
- 12:25–13:00 review fixes (commit after 5920c74):
  - Shared reply mailbox across tenants: a reply attaches only to a lead some workspace actually emailed; it
    follows the reply SUBJECT (normalized Re:/Fw:) rather than "most recently emailed"; still ambiguous across
    workspaces → not attached and logged (never guessed into the wrong tenant).
  - replies.source_message_id (RFC 5322 Message-ID) + partial UNIQUE index (lead_id, source_message_id): the same
    email seen by both readers or two overlapping poller runs is one Reply and one AI hand-off.
  - Advisor poller: an AI-handler exception no longer falls through to the pipeline (which could answer twice).
  - Manual email send: in-flight claim on (lead, subject) before the provider call → a double-click gets the
    existing "already sent / send again?" 409 instead of two emails. (Per-process; Render runs one web instance.)
  - Max Life offers: accept / decline / timeout-sweep settle an offer with one conditional UPDATE (offered → X);
    the loser gets 409 / is skipped. Previously accept + sweep could both win (accepted AND reassigned).
  - Noted only: a pydantic model nested inside a dict response skips the UTC "Z" marking (no live case found).
- Browser-pane login expired ~10:15 → live click-verification and Max Life demo creation wait on the owner signing in.

## What shipped (by area)
### Max Life Command (feature `insurance_agency`, prefix /agency)
- 15 new `agency_*` tables (app/models/agency_models.py), services app/services/agency/*, router agency_router.py.
- Prospects (a prospect IS a Lead; profile = stated facts only), provenance/consent events, explainable distribution
  (reasons only from stored data; `unavailable_factors` for missing data, e.g. conversion history), assign → offer →
  accept/decline → timeout sweep → reassign/escalate (configurable thresholds, audited), appointments (own table — the
  booking table triggers real reminders), applications with server-enforced state machine + stalled flag +
  requirements + document metadata → issue → policy + client, policy review tasks, recruiting stages + licensing
  milestones (entered, no government integration), agents workload/capacity, attention list (every item links to a
  record), summary counts that equal their lists, Ask EvoAI (7 supported intents; unsupported answered honestly),
  opportunity brief FACT/INFERENCE/INSUFFICIENT (rules; generated_by=rules), copilot (objection detection incl.
  employer coverage; education + discovery reply; human takeover on health topics; Simulate Send records an event,
  never a provider call), /agency/conversations.
- Frontend frontend/src/pages/agency/* (black/gold), agencyVertical.js nav (selected by explicit `insurance_agency`
  in enabled_features), agency workspaces land on /agency.
- scripts/seed_maxlife_demo.py: DEMO org/agents/prospects/apps/policies/recruits; refuses production; memberships;
  forced password change unless MAXLIFE_DEMO_PASSWORD; 3 prospects left unassigned for the distribution demo.

### Max Life public website (deploy-ready, not deployed)
- public-site/maxlife/ (static). BUILD. PROTECT. TRANSFER.; families vs builders journeys; Find Your Path; two forms →
  POST /site-intake/{platform_slug}/inquiry (new thin adapter app/routers/brand_site_inquiry_router.py over the
  existing public capture). SMS consent unchecked by default; consent=false stored explicitly.
- Go-live needs (owner): Platform slug `maxlife` + public intake destination (PUT /god/platform/public-intake/{id}),
  site origin in ALLOWED_ORIGINS or a proxy, hosting/domain, legal review of DRAFT legal pages, official logo.

### Atlantis
- /energy/follow-up (due/overdue/upcoming/escalation/no-response/customers), /energy/renewals (only real contract end
  dates; missing counted separately), /energy/move-concierge (energy_move_requests: checklist, services, status,
  tasks, activity; vendors NOT CONFIGURED). Rate request → deliberate enroll (records enrolled_at). Overview cards drill
  into real lists; Enrollments This Month real.
- Sales Board (/pipeline default tab): cards link to the lead, owner, age-in-stage (stage_entered_at; "stage date not
  recorded" before), next task, appointment, rate request, customer state, loss reason (required on mark-lost), reopen.
- New Lead columns (additive): stage_entered_at, pipeline_lost_at, pipeline_lost_reason, enrolled_at.

### Wholesale / EvoSense
- Full 17-hop walk through the real API (tests/test_wholesale_e2e_workflow.py). Fixed: manual seller reply recorded on
  a blocked/pending conversation (was 409 dead end); promotion carries seller-stated facts from a manually entered
  contact. Low-score wording no longer says "Nothing" while offering promotion.

### Communications / calling
- communication_identity(): public contact number vs outbound calling number vs SMS sender kept separate; AI call
  "call us back" uses the public contact number; hard-coded "Mike Simmons"/"Greenland Cemetery…" prompt defaults
  removed; reminder cron uses the shared sender resolver. Human dialer panel on lead detail (identity, state, history,
  notes + disposition + follow-up task, voicemail, next-call queue honouring DNC/suppression; tel: fallback when not
  configured; manual call log). Inbound webhook replay idempotent.

### Mobile / PWA
- /m shell (home/needs-attention, inbox + gated quick reply, tasks, calendar, contact, notifications, workspace
  switcher), brand-aware looks, manifest + app-shell-only service worker, push client stub (server not built).

### Security / reliability (found + fixed today)
- Agency: advisors could assign apps/appointments/tasks to colleagues → 403; detail views loaded the whole book.
- Energy move detail exposed tasks of leads the caller can't read; energy queue counts weren't advisor-scoped.
- record-sent stored unescaped markup.
- Email replies with the same text days apart were dropped as duplicates (both readers) → 5-minute window.
- Advisor poller handed replies to the AI before saving → possible double AI reply; now save first.
- Shared mailbox cursor could be pinned forever by one poison message → 24h retry window.
- Cadence cron reported success when an org failed; one org's DB error broke later orgs.
- Import commit claim was not atomic across instances → conditional UPDATE claim (409 for the loser).

## Owner decisions / Mike-only
- Same phone + different name: currently a NEW contact flagged "shares_phone_with_other_record" (household /
  switchboard rule); spec 43 asks for duplicate review. Changing it would push every shared household/office line in
  imports into review. Left as is pending your call.
- Max Life site hosting/domain + platform config + legal pages + logo.
- Twilio voice number / webhooks, Tracerfy (paid) — unchanged.

## Not done / known
- Ask EvoAI is a supported-intent parser (no free-form RAG). Brief/copilot are rules-based (no LLM enhancement).
- Push notification server side; in-browser calling.
- Dead appointment_reminder_cron.py (unused, broken) — recommend deletion. Voice /status sets ended_at on every callback.
