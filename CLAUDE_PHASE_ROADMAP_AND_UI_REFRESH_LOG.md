# Claude Phase Roadmap + UI Refresh Log

## What this package contains

This is the newest uploaded AdvisorFlow baseline with an added product execution roadmap and a focused UI usefulness pass on three pages:

- Overview
- Leads
- Replies

No backend logic was changed in this pass. The goal was to make the existing data already on these pages more useful and command-center-like without inventing metrics or adding fake visuals.

## New roadmap document

Added:

- `ADVISORFLOW_50_FEATURE_EXECUTION_PLAN_FOR_CLAUDE.md`

Purpose:

- Organizes the 50 dream enhancements into safe phases.
- Gives Claude/Cursor the build order, rules, acceptance criteria, and risk notes.
- Makes clear that the next practical build path should be:
  1. Full Lead Timeline / Case File
  2. Outcome Enforcement System
  3. Compliance Preflight Engine
  4. Revenue Rescue Center
  5. AI Next Best Action Engine

## UI usefulness pass

### Overview

Changed:

- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

Added:

- A command-priority deck below the existing stat cards.
- Priority tiles driven from real existing daily briefing data.
- A stronger “what should I pay attention to now?” section.

Real data used:

- replies needing attention
- cadence touches due today
- bookings last 7 days
- status funnel total

No fake chart data was added.

### Leads

Changed:

- `frontend/src/pages/Leads.jsx`
- `frontend/src/pages/Leads.css`

Added:

- Lead operations header treatment.
- Top command summary cards:
  - total leads
  - SMS ready
  - needs review
  - blocked/risky
- Sticky filter bar on desktop.
- More useful use of the space above the lead table.

Real data used:

- current lead list
- filtered lead count
- sendable leads
- needs review count
- DNC / duplicate / missing phone counts

No backend endpoint was added.

### Replies

Changed:

- `frontend/src/pages/Replies.jsx`
- `frontend/src/pages/Replies.css`

Added:

- Reply command summary cards:
  - needs attention
  - callbacks
  - reviewed
  - DNC / stop
- Search input for reply text.
- Classification filter.
- “Book-first priority lane” that surfaces interested/callback replies.
- Stronger card hover and visual hierarchy.

Real data used:

- loaded replies from the existing `/sms/replies` endpoint
- existing classification values
- reviewed state
- received_at timestamps

No backend endpoint was added.

## Shared CSS change

Changed:

- `frontend/src/styles/shared.css`

Added:

- `.command-kicker` utility used by the refreshed pages.

## Validation run

Frontend:

```bash
cd frontend
npm install --no-package-lock
npm run build
```

Result:

- build passed
- Vite still reports an existing bundle-size warning because Recharts is included
- npm still reports existing audit warnings: 1 moderate, 1 high

Backend:

```bash
python -m compileall -q app tests
```

Result:

- passed

## Important notes for Claude

- Do not interpret this UI pass as completion of the 50-feature roadmap.
- This only improves the usefulness of Overview, Leads, and Replies using existing data.
- The 50-feature plan is staged in `ADVISORFLOW_50_FEATURE_EXECUTION_PLAN_FOR_CLAUDE.md`.
- The next real backend feature should be planned first, then built in a separate tested pack.
- Do not build all 50 in one commit.
