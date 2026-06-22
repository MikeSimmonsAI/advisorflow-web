# CLAUDE / CLYDE REVIEW HANDOFF — AdvisorFlow Final Enhancement Package

Prepared for Claude/Clyde review.

## What This ZIP Is

This ZIP contains the AdvisorFlow project after the full enhancement pass that was built across Tasks 1 through 12, the visual polish pass, and the final real-data Overview charts/widgets task.

The work was done against the actual AdvisorFlow project structure after inspecting the project files. It is not a generic FastAPI/React scaffold.

Backend structure used:
- `app/models/models.py`
- `app/deps.py`
- `app/routers/`
- `app/services/`

Frontend structure used:
- `frontend/src/api/client.js`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`
- `frontend/src/pages/`
- `frontend/src/index.css`
- `frontend/src/styles/shared.css`

Start review with this file, then also check:
- `CURSOR_AI_REVIEW_LOG.md`
- `CHARTS_NOTES.md`
- `VISUAL_POLISH_NOTES.md`
- `UI_AUDIT_NOTES.md`

---

# Biggest Things Claude/Clyde Should Review First

1. **Database migrations**
   - New model/table: `AuditLogEntry` / `audit_log_entries`
   - New model/table: `Campaign` / `campaigns`
   - If production uses Alembic, migrations are required before deploy.

2. **Org/user scoping**
   - New endpoints must never leak another advisor's or org's data.
   - Most new advisor endpoints use `get_current_user`.
   - Admin-only features use `require_admin`.

3. **Lead merge safety**
   - `POST /admin/leads/merge` permanently deletes merged lead rows after moving history.
   - It is transactional and tested, but this is the highest-risk code path.

4. **AI draft reply fallback**
   - `POST /sms/draft-reply/{lead_id}` should never fail outward if OpenAI is unavailable.
   - It should return a safe generic fallback.

5. **Overview charts**
   - The charts use real database-backed endpoints.
   - No fake metrics or mock chart values should be present.
   - Recharts increased bundle size; build passed with a Vite warning.

6. **Compliance/DNC interaction**
   - If this branch merges with separate compliance hardening work, recheck send-path suppression enforcement.

---

# Task-by-Task Breakdown

## Task 1 — Audit Log

### Purpose
Add a permanent audit log foundation so future actions can be tracked.

### Files
- `app/models/models.py`
- `app/routers/audit_log_router.py`
- `app/main.py`
- `tests/test_audit_log_router.py`
- `frontend/src/pages/AuditLog.jsx`
- `frontend/src/pages/AuditLog.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

### Backend Added
Model:
- `AuditLogEntry`

Fields:
- `id`
- `organization_id`
- `actor_user_id`
- `action`
- `target_type`
- `target_id`
- `details`
- `created_at`

Helper:
- `log_action(db, organization_id, actor_user_id, action, target_type, target_id, details=None)`

Endpoint:
- `GET /audit-log`

Behavior:
- admin-only
- org-scoped
- paginated
- filterable by action

### Frontend Added
- Audit Log page
- route
- nav entry

### Tests
- org isolation
- helper persists records

### Review Notes
- Add production DB migration.
- Helper exists but is not wired into every mutation yet.

---

## Task 2 — Advisor Daily Work Queue

### Purpose
Create a "Today’s Work" screen for advisors.

### Files
- `app/routers/workqueue_router.py`
- `app/main.py`
- `tests/test_workqueue_router.py`
- `frontend/src/pages/WorkQueue.jsx`
- `frontend/src/pages/WorkQueue.css`

### Endpoint
- `GET /workqueue/today`

Returns:
- `needs_text`
- `needs_reply`
- `cadence_due`
- `outcomes_needed`

### Logic
- current advisor scoped
- not org-wide
- uses existing data only
- no new DB fields

### Review Notes
- A later audit found this router was initially unwired; it is now wired in `app/main.py`.

---

## Task 3 — Reply Inbox 2.0

### Purpose
Add triage actions to existing replies.

### Files
- `app/routers/sms_router.py`
- `tests/test_reply_triage_actions.py`
- `frontend/src/pages/Replies.jsx`
- `frontend/src/pages/Replies.css`

### Endpoints
- `PATCH /sms/replies/{reply_id}/mark-reviewed`
- `PATCH /sms/replies/{reply_id}/reclassify`

### Behavior
- reply must belong to a lead in the current user's org
- supports enum-based reclassification
- DNC reclassification does **not** change Lead status

### Tests
- mark reviewed
- reclassify
- org isolation
- invalid classification
- DNC classification does not update lead status

---

## Task 4 — System Health Monitor

### Purpose
Show an advisor what integrations are connected.

### Files
- `app/routers/health_router.py`
- `app/main.py`
- `tests/test_health_router.py`
- `frontend/src/pages/SystemHealth.jsx`
- `frontend/src/pages/SystemHealth.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

### Endpoint
- `GET /health/advisor-status`

Returns:
- `twilio_connected`
- `google_calendar_connected`
- `microsoft_365_connected`
- `last_cadence_run`

### Notes
- `last_cadence_run` returns null because no existing scheduler ledger/timestamp field was found.
- No fix buttons or actions were added.

---

## Task 5 — Manager Command Dashboard Metrics

### Purpose
Upgrade admin dashboard with quality metrics, not just volume counts.

### Files
- `app/routers/admin_router.py`
- `tests/test_admin_dashboard_metrics.py`
- `frontend/src/pages/Admin.jsx`
- `frontend/src/pages/Admin.css`

### Endpoints
- `GET /admin/dashboard/metrics`
- `GET /admin/dashboard/funnel`

### Metrics
Per-advisor and org-wide:
- leads owned
- messages sent
- replies
- hot replies
- booked leads
- DNC leads
- duplicate leads prevented
- reply rate
- hot-reply rate
- booking rate
- DNC rate

### Rate Formulas
- `reply_rate = replies / messages_sent`
- `hot_reply_rate = hot_replies / messages_sent`
- `booking_rate = booked_leads / leads_owned`
- `dnc_rate = dnc_leads / leads_owned`

All return zero when denominator is zero.

### Funnel
- total leads
- sent
- replied
- hot/interested
- booked
- sold

Sold uses:
- `LeadOutcome.resulted_in_sale == True`

### Frontend
- Added Metrics tab
- Per-advisor table
- Org-wide metric cards
- CSS funnel display

### Tests
- exact math against fixture data
- org isolation
- zero division

---

## Task 6 — Campaign Builder

### Purpose
Saved filters plus message track/cadence application for admins.

### Files
- `app/models/models.py`
- `app/routers/campaign_router.py`
- `app/main.py`
- `tests/test_campaign_router.py`
- `frontend/src/pages/Campaigns.jsx`
- `frontend/src/pages/Campaigns.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

### New Model
`Campaign`

Fields:
- `id`
- `organization_id`
- `name`
- `created_by_id`
- `filter_criteria`
- `message_track`
- `created_at`

### Endpoints
- `POST /campaigns`
- `GET /campaigns`
- `POST /campaigns/{id}/preview`
- `POST /campaigns/{id}/apply`

### Behavior
- admin-only
- org-isolated
- preview does not modify
- apply updates matching leads' `message_track`
- optional cadence start through existing cadence service
- DNC leads are skipped

### Tests
- combined tier/source_year filters
- preview does not modify
- apply updates correct leads only
- skips DNC
- org isolation

### Review Notes
- Add production DB migration for `campaigns`.

---

## Task 7 — Lead Merge & Cleanup Center

### Purpose
Resolve duplicate and messy lead data.

### Files
- `app/routers/admin_router.py`
- `tests/test_lead_cleanup_router.py`
- `frontend/src/pages/LeadCleanup.jsx`
- `frontend/src/pages/LeadCleanup.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

### Endpoints
- `GET /admin/leads/potential-duplicates`
- `POST /admin/leads/merge`
- `PATCH /admin/leads/{lead_id}/fix-contact-info`

### Duplicate Detection
- reuses existing `dedup_service.py` normalization
- groups by normalized phone or normalized last name
- same-org only

### Merge Behavior
Moves:
- `Message`
- `Reply`
- `CadenceState`
- `LeadOutcome`

Then deletes merged lead rows.

Safety:
- transactional
- rejects merge into self
- org-isolated
- rejects cadence conflict when both keep and merge leads have a `CadenceState`

### Tests
- preserves history
- message/reply counts sum correctly
- simulated failure rolls back
- org isolation
- merge into self rejected

### Claude Priority Review
This is one of the highest-risk features. Review transaction and rollback behavior carefully.

---

## Task 8 — Mobile Responsive Pass + Page Shell

### Purpose
UI consistency and mobile usability.

### Files
- `frontend/src/components/PageShell.jsx`
- `frontend/src/components/Layout.jsx`
- `frontend/src/components/Layout.css`
- `frontend/src/index.css`
- `frontend/src/styles/shared.css`
- multiple page CSS files
- `UI_AUDIT_NOTES.md`

### Added
- `PageShell.jsx`
- mobile hamburger sidebar overlay
- mobile stacking/card layout rules
- CSS token cleanup
- UI audit notes

### Important
Existing pages were not converted to `PageShell` yet. The component is staged for future use.

---

## Task 9 — Lead Detail Assignment

### Purpose
Allow admins to reassign a lead from Lead Detail.

### Files
- `frontend/src/pages/LeadDetail.jsx`
- `frontend/src/pages/LeadDetail.css`
- `tests/test_user_management.py`

### Backend
No duplicate backend logic added.

Uses existing:
- `POST /admin/leads/reassign`

### Frontend
- Added admin-only "Assigned to" selector
- visible for `org_admin` and `super_admin`

### Tests
- single-lead reassignment works
- plain advisor blocked with 403
- unauthorized attempt leaves lead untouched

---

## Task 10 — AI-Drafted One-Click Reply

### Purpose
Suggest a reply on Lead Detail using AI/fallback logic.

### Files
- `app/routers/sms_router.py`
- `app/services/draft_reply_service.py`
- `tests/test_draft_reply_router.py`
- `frontend/src/pages/LeadDetail.jsx`
- `frontend/src/pages/LeadDetail.css`

### Endpoint
- `POST /sms/draft-reply/{lead_id}`

### Behavior
- current user/org scoped
- uses recent reply and conversation history
- lazy OpenAI client pattern
- JSON-only prompt
- never raises if OpenAI is unavailable/fails
- generic fallback returned
- includes booking link
- reuses existing booking link if present
- creates booking link using existing helper only when needed

### Frontend
- Suggest Reply button
- fills existing textarea
- advisor still edits and manually clicks Send Now

### Tests
- no OpenAI key fallback
- booking link created when missing
- booking link reused when present

---

## Task 11 — Email Queue Search + Phone Visibility

### Purpose
Make Email Queue searchable and show phone when present.

### Files
- `app/routers/email_router.py`
- `tests/test_email_router.py`
- `frontend/src/pages/EmailQueue.jsx`
- `frontend/src/pages/EmailQueue.css`

### Backend
Email queue endpoint now supports:
- optional `search` query param

Search fields:
- first name
- last name
- email

Response:
- includes phone if present

### Frontend
- search input
- phone column
- `—` when missing

### Tests
- partial name search
- partial email search
- phone present/null handling

---

## Task 12 — Overview Daily Briefing

### Purpose
Make Overview more useful with real daily briefing lines.

### Files
- `app/routers/leads_router.py`
- `tests/test_daily_briefing_router.py`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

### Endpoint
- `GET /leads/daily-briefing`

Returns:
- replies needing attention
- cadence touches due today
- leads imported last 24 hours
- bookings last 7 days

### Scope
- current advisor only

### Frontend
- added "Today" section
- existing stat cards remain unchanged

---

## Visual Polish Pass

### Purpose
Make the UI feel closer to the reference mood board without inventing fake features.

### Files
- `frontend/src/index.css`
- `frontend/src/styles/shared.css`
- `frontend/src/components/StatCard.jsx`
- `frontend/src/components/StatCard.css`
- `frontend/src/components/Layout.css`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`
- `VISUAL_POLISH_NOTES.md`

### Added
- deeper glass panel treatment
- stronger glow/shadow
- better StatCard hierarchy
- sidebar active/glow polish
- avatar glow
- corner bracket refinement

### Explicitly Not Added
- fake charts
- fake gauges
- fake health score
- invented metrics

---

# Final Task — Real Charts and Widgets for Overview

## Purpose
Add actual charts/gauges backed by real database endpoints.

## Files
- `app/routers/sms_router.py`
- `app/routers/leads_router.py`
- `app/routers/cadence_router.py`
- `tests/test_overview_charts_router.py`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`
- `frontend/src/index.css`
- `CHARTS_NOTES.md`

## Endpoint 1
`GET /sms/replies/activity-by-day?days=14`

Returns:
- daily reply counts for current advisor's leads

## Endpoint 2
`GET /leads/engagement-breakdown`

Returns:
- counts of HOT/WARM/COLD/UNKNOWN for current advisor's owned leads

## Endpoint 3
`GET /cadence/health-summary`

Returns:
- cadence status counts
- active count
- healthy active count
- overdue active count
- health score

### Cadence Health Formula
```text
health_score = healthy_active_cadences / active_cadences * 100
```

Definitions:
- active cadence = `CadenceState.status == CadenceStatus.ACTIVE`
- healthy active cadence = active cadence where `next_touch_due_at is null OR next_touch_due_at >= now`
- overdue active cadence = active cadence where `next_touch_due_at < now`
- if active cadence count is zero, score returns zero

## Endpoint 4
`GET /leads/status-funnel`

Returns counts for:
- new
- sent
- replied
- hot
- booked

## Frontend
Overview now includes:
- Recharts reply activity line chart
- Recharts engagement donut
- Recharts cadence health radial gauge
- real status funnel bars

### Important
- Existing stat cards remain.
- No fake metrics copied from the visual reference.
- The reference was used only for visual polish.

## Tests
`tests/test_overview_charts_router.py`

Covers:
- exact counts
- advisor scoping
- cadence health formula
- funnel values

---

# Validation Summary

## Backend Compile
```bash
python -m compileall -q app tests
```
Passed.

## Frontend Build
```bash
cd frontend
npm run build
```
Passed.

Known build warning:
- Vite warns about bundle size after adding Recharts. This is not a functional failure.

## Test Groups Run During Work
Targeted and related test groups were run throughout:
- audit log
- workqueue
- reply triage
- health
- admin metrics
- campaign
- lead cleanup
- draft reply
- email queue
- daily briefing
- overview charts
- compliance
- admin router
- user management
- SMS router
- leads router
- cadence router/service/job
- needs-attention filter

Earlier file-by-file audit result:
- 249 passed
- 8 skipped
- 0 failed

Full one-shot pytest timed out in sandbox. Run it locally/CI:
```bash
python -m pytest
```

---

# Known Production To-Dos

1. Add DB migrations for:
   - `audit_log_entries`
   - `campaigns`

2. Run full backend test suite outside the sandbox.

3. Run frontend build locally:
```bash
cd frontend
npm install
npm run build
```

4. Review NPM audit warnings:
   - 1 moderate
   - 1 high

5. Consider code-splitting Overview charts later because Recharts increased bundle size.

6. Review high-risk lead merge logic.

7. Review AI draft reply fallback/booking behavior.

8. Review compliance send-path enforcement if merging with a compliance branch.

---

# Suggested Review Order for Claude/Clyde

1. Read this file.
2. Run backend compile.
3. Run full pytest.
4. Run frontend install/build.
5. Check migrations.
6. Review lead merge.
7. Review campaign apply.
8. Review AI draft reply.
9. Review Overview charts and formulas.
10. Click through frontend routes.
11. Test mobile under 768px.
12. Confirm no fake chart data exists.

---

# Useful Commands

Backend:
```bash
python -m compileall -q app tests
python -m pytest
```

Frontend:
```bash
cd frontend
npm install
npm run build
npm run dev
```

Focused tests:
```bash
python -m pytest tests/test_overview_charts_router.py -q
python -m pytest tests/test_lead_cleanup_router.py tests/test_campaign_router.py tests/test_draft_reply_router.py -q
```

---

# Bottom Line

This branch now contains a full AdvisorFlow enhancement pass:

- Audit Log
- Work Queue
- Reply triage
- System Health
- Manager Metrics
- Campaign Builder
- Lead Cleanup/Merge
- Mobile UI shell
- Lead Detail assignment
- AI reply drafting
- Email Queue search/phone visibility
- Overview daily briefing
- Real data-backed Overview charts
- Visual polish

Review focus should be:
- data integrity
- DB migrations
- org/user scoping
- destructive merge safety
- AI fallback behavior
- real chart data correctness
- frontend build/route integrity
