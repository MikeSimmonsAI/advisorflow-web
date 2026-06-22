# Cursor AI Review Log — AdvisorFlow Enhancement Pass

Generated for Cursor review after the multi-task enhancement pass.

## Review Goal

Open this project in Cursor and verify the implemented features, file structure, tests, and integration points before merge/deploy.

This work was done against the actual AdvisorFlow project structure, not a generic FastAPI/React scaffold.

## Project Structure Confirmed

Backend:
- Models live in `app/models/models.py`.
- Dependencies/auth/session live in `app/deps.py`.
- Routers live in `app/routers/`.
- Services live in `app/services/`.

Frontend:
- React/Vite frontend lives under `frontend/`.
- API calls should use `frontend/src/api/client.js`.
- Main routes are in `frontend/src/App.jsx`.
- Layout/sidebar is in `frontend/src/components/Layout.jsx`.

## Major Features Added / Changed

### Task 1 — Audit Log
Files:
- `app/models/models.py`
- `app/routers/audit_log_router.py`
- `app/main.py`
- `tests/test_audit_log_router.py`
- `frontend/src/pages/AuditLog.jsx`
- `frontend/src/pages/AuditLog.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

Added:
- `AuditLogEntry` model.
- `GET /audit-log`.
- `log_action(...)` helper.
- Frontend audit log page.
- Admin nav/route wiring.

Important:
- Requires DB migration for `audit_log_entries` in production if using Alembic.

### Task 2 — Advisor Daily Work Queue
Files:
- `app/routers/workqueue_router.py`
- `app/main.py`
- `tests/test_workqueue_router.py`
- `frontend/src/pages/WorkQueue.jsx`
- `frontend/src/pages/WorkQueue.css`

Added:
- `GET /workqueue/today`.
- Lists: `needs_text`, `needs_reply`, `cadence_due`, `outcomes_needed`.
- Advisor-scoped, not admin-scoped.

Important:
- A later audit found this router was initially not wired in `main.py`; it has now been wired.

### Task 3 — Reply Inbox 2.0
Files:
- `app/routers/sms_router.py`
- `tests/test_reply_triage_actions.py`
- `frontend/src/pages/Replies.jsx`
- `frontend/src/pages/Replies.css`

Added:
- `PATCH /sms/replies/{reply_id}/mark-reviewed`
- `PATCH /sms/replies/{reply_id}/reclassify`
- One-click frontend reply triage controls.

Important:
- Reclassifying a reply to `dnc` intentionally does **not** change `Lead.status`.

### Task 4 — System Health Monitor
Files:
- `app/routers/health_router.py`
- `app/main.py`
- `tests/test_health_router.py`
- `frontend/src/pages/SystemHealth.jsx`
- `frontend/src/pages/SystemHealth.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

Added:
- `GET /health/advisor-status`.
- Read-only status cards for Twilio, Google Calendar, Microsoft 365, and cadence run status.

Important:
- `last_cadence_run` returns `null`; no scheduler ledger/table was invented.

### Task 5 — Manager Command Dashboard Metrics
Files:
- `app/routers/admin_router.py`
- `tests/test_admin_dashboard_metrics.py`
- `frontend/src/pages/Admin.jsx`
- `frontend/src/pages/Admin.css`

Added:
- `GET /admin/dashboard/metrics`
- `GET /admin/dashboard/funnel`
- Metrics tab in Admin dashboard.
- Per-advisor quality metrics and org-wide funnel.

Important:
- Existing admin endpoints were not removed/replaced.
- Rate math is tested with exact fixture counts.

### Task 6 — Campaign Builder
Files:
- `app/models/models.py`
- `app/routers/campaign_router.py`
- `app/main.py`
- `tests/test_campaign_router.py`
- `frontend/src/pages/Campaigns.jsx`
- `frontend/src/pages/Campaigns.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

Added:
- `Campaign` model.
- `POST /campaigns`
- `GET /campaigns`
- `POST /campaigns/{id}/preview`
- `POST /campaigns/{id}/apply`

Important:
- Requires DB migration for `campaigns` table in production if using Alembic.
- Apply skips DNC leads.
- Optional cadence start uses existing cadence service.

### Task 7 — Lead Merge & Cleanup Center
Files:
- `app/routers/admin_router.py`
- `tests/test_lead_cleanup_router.py`
- `frontend/src/pages/LeadCleanup.jsx`
- `frontend/src/pages/LeadCleanup.css`
- `frontend/src/App.jsx`
- `frontend/src/components/Layout.jsx`

Added:
- `GET /admin/leads/potential-duplicates`
- `POST /admin/leads/merge`
- `PATCH /admin/leads/{lead_id}/fix-contact-info`
- Lead Cleanup frontend page.

Important:
- Data-integrity sensitive.
- Merge is transactional.
- Moves `Message`, `Reply`, `CadenceState`, and `LeadOutcome`.
- Rejects merge-into-self.
- Rejects cadence conflicts when both keep and merge leads have `CadenceState`.
- Uses existing dedup normalization utilities.

### Task 8 — Mobile Responsive + Page Shell
Files:
- `frontend/src/components/PageShell.jsx`
- `frontend/src/components/Layout.jsx`
- `frontend/src/components/Layout.css`
- `frontend/src/index.css`
- `frontend/src/styles/shared.css`
- multiple `frontend/src/pages/*.css`
- `UI_AUDIT_NOTES.md`

Added:
- Shared `PageShell` component.
- Mobile hamburger overlay sidebar.
- Mobile stacking/card treatment.
- CSS variable cleanup and audit notes.

Important:
- Existing pages were not refactored to use `PageShell`; component is staged for future use.

### Task 9 — Lead Detail Assignment
Files:
- `frontend/src/pages/LeadDetail.jsx`
- `frontend/src/pages/LeadDetail.css`
- `tests/test_user_management.py`

Added:
- Admin-only assigned-advisor selector on Lead Detail.
- Reuses existing `POST /admin/leads/reassign`.

Important:
- No duplicate reassignment backend logic was added.
- Plain advisors are blocked by the existing admin endpoint.

### Task 10 — AI-Drafted One-Click Reply
Files:
- `app/routers/sms_router.py`
- `app/services/draft_reply_service.py`
- `tests/test_draft_reply_router.py`
- `frontend/src/pages/LeadDetail.jsx`
- `frontend/src/pages/LeadDetail.css`

Added:
- `POST /sms/draft-reply/{lead_id}`.
- Suggest Reply button on Lead Detail.
- Fallback suggestion if OpenAI is unavailable.
- Booking link reuse/creation using existing booking link helper.

Important:
- Does not touch batch review/import message flow.
- Advisor still manually reviews and clicks Send now.

### Task 11 — Email Queue Search + Phone Visibility
Files:
- `app/routers/email_router.py`
- `tests/test_email_router.py`
- `frontend/src/pages/EmailQueue.jsx`
- `frontend/src/pages/EmailQueue.css`

Added:
- Optional `search` query param on email queue endpoint.
- Search by partial first name, last name, or email.
- Phone column on Email Queue UI.

Important:
- Email sending/Microsoft logic was not changed.

### Task 12 — Overview Daily Briefing
Files:
- `app/routers/leads_router.py`
- `tests/test_daily_briefing_router.py`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

Added:
- `GET /leads/daily-briefing`.
- Today briefing section on Overview.

Counts:
- replies needing attention
- cadence touches due today
- leads imported last 24h
- bookings last 7 days

Important:
- Existing stat cards remain.
- Counts are advisor-scoped.

### Visual Polish Pass
Files:
- `frontend/src/index.css`
- `frontend/src/styles/shared.css`
- `frontend/src/components/StatCard.jsx`
- `frontend/src/components/StatCard.css`
- `frontend/src/components/Layout.css`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`
- `VISUAL_POLISH_NOTES.md`

Added:
- Deeper glass treatment.
- Stronger glow/shadow.
- Better StatCard hierarchy.
- Sidebar active/glow polish.
- Visual notes.

Important:
- No fake charts, gauges, health scores, or invented metrics were added.

## Validation Already Run

Backend syntax:
```bash
python -m compileall -q app tests
```

Frontend build:
```bash
cd frontend
npm run build
```

Targeted backend test groups were run throughout the work. Key groups included:
- `tests/test_audit_log_router.py`
- `tests/test_workqueue_router.py`
- `tests/test_reply_triage_actions.py`
- `tests/test_health_router.py`
- `tests/test_admin_dashboard_metrics.py`
- `tests/test_campaign_router.py`
- `tests/test_lead_cleanup_router.py`
- `tests/test_draft_reply_router.py`
- `tests/test_email_router.py`
- `tests/test_daily_briefing_router.py`
- `tests/test_compliance_router.py`
- `tests/test_admin_router.py`
- `tests/test_user_management.py`
- `tests/test_sms_router.py`
- `tests/test_needs_attention_filter.py`

A full one-shot pytest run collected the full suite but timed out in the sandbox. The suite was then checked in smaller groups. The last whole-project audit showed file-by-file testing at:
- `249 passed`
- `8 skipped`
- `0 failed`

## Known Review Items for Cursor / Claude

1. **Database migrations needed**
   New tables:
   - `audit_log_entries`
   - `campaigns`

   Add Alembic migrations if this project uses migrations in production.

2. **Full CI should run outside the sandbox**
   The sandbox timed out on the full single-command pytest run. Run:
   ```bash
   python -m pytest
   ```
   in a normal local environment or CI.

3. **NPM audit warnings**
   Existing warnings were reported:
   - 1 moderate
   - 1 high

   `npm audit fix --force` was not run because it could introduce breaking dependency changes.

4. **Deprecation warnings**
   Existing warning areas observed:
   - Pydantic class `Config` deprecation
   - FastAPI `on_event` deprecation
   - JWT secret length warning
   - Some datetime deprecation warnings

5. **Review high-risk merge logic**
   Carefully inspect:
   - `POST /admin/leads/merge`
   - `tests/test_lead_cleanup_router.py`

   This permanently deletes merged lead rows after moving related history.

6. **Review AI draft endpoint**
   Carefully inspect:
   - `app/services/draft_reply_service.py`
   - `POST /sms/draft-reply/{lead_id}`

   It should never raise if OpenAI is missing/failing and should use booking link helper logic.

7. **Verify frontend routes/nav**
   New pages are wired:
   - `/audit-log`
   - `/workqueue`
   - `/system-health`
   - `/campaigns`
   - `/lead-cleanup`

8. **Verify role gating**
   Especially:
   - Lead Detail assignment selector
   - Admin-only pages
   - Advisor-only work queue/health/overview behavior



### Task 13 — Real Overview Charts / Widgets
Files:
- `app/routers/sms_router.py`
- `app/routers/leads_router.py`
- `app/routers/cadence_router.py`
- `tests/test_overview_charts_router.py`
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`
- `CHARTS_NOTES.md`

Added real-data endpoints:
- `GET /sms/replies/activity-by-day?days=14`
- `GET /leads/engagement-breakdown`
- `GET /cadence/health-summary`
- `GET /leads/status-funnel`

Added Overview widgets backed by those endpoints:
- Reply activity line chart.
- Engagement temperature donut chart.
- Cadence health radial gauge.
- Status funnel bars.

Important:
- Existing Overview stat cards were not removed.
- No fake charts, gauges, or invented metrics were added.
- Cadence health formula is documented in `CHARTS_NOTES.md` and in the backend endpoint docstring.
- Charts are scoped to `get_current_user` and the logged-in advisor's own leads only.
- Tests in `tests/test_overview_charts_router.py` verify exact counts and advisor/org isolation.

## Suggested Cursor Review Order

1. Run backend compile and full pytest.
2. Run frontend build.
3. Review migrations needed for new models.
4. Review high-risk lead merge transaction.
5. Review compliance/DNC interactions with send paths.
6. Review AI draft reply fallback and booking-link behavior.
7. Click through frontend pages in dev server.
8. Check mobile sidebar behavior at widths under 768px.

## Commands for Cursor

```bash
# backend
python -m compileall -q app tests
python -m pytest

# frontend
cd frontend
npm install
npm run build
npm run dev
```

## Bottom Line

This package contains a broad enhancement pass. The most important review areas are data integrity, role/org isolation, migrations, and the AI draft endpoint fallback behavior.


---

# Additional Final Note

A Claude-specific review handoff has been added:

- `CLAUDE_REVIEW_HANDOFF.md`

Claude/Clyde should read that file first for the extensive feature-by-feature breakdown, known risks, commands, and review order.
