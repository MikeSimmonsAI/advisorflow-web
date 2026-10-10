# SCI production readiness - Oct 10, 2026 (prepared overnight; NOT deployed)

## Candidate
- Branch `release/sci-lead-workflow` @ 4557eab3 = `sci-program` 1345e69f merged onto `main` 90485585.
- 15 commits ahead of main, 0 behind; main is an ancestor, so the merge is a fast-forward.
- 51 files, +5373 / -2690. No migration files, BUT one automatic schema change: `app/auto_migrate.py` adds the column `lead_outcomes.attendance` (VARCHAR, nullable) at startup. Additive only; existing rows stay empty. It runs on production automatically the first time the new code starts, so approving the merge also approves this column. (Correction 08:55: an earlier version of this page said "no schema changes".)

## Evidence
- Full suite on the candidate: 7769 passed, 28 skipped, 0 failed (05:31 check-in).
- Wholesale on the trial merge: 533/533; 8 proof scripts pass. AI Workforce: 266/266.
- Visual acceptance on staging (sci-staging-frontend.onrender.com), automated per width in same-origin frames:
  - 9 Family Service Center screens (Dashboard, Responses, Review, Locations, Campaigns, Assets & Flyers, Launch Readiness, Health, Settings)
    + Leads Directory, Lead Command Center, Replies, My Work, Activity & call history, Appointments.
  - Widths 1920 / 1600 / 1280 / 768 / 390: 75/75 combinations with 0 horizontal overflow, 0 error states, 0 stuck "Loading".
  - Lead Command Center on a test record: Call, Message, Resend link and Resume AI are all disabled with the reason "Internal test record - excluded from all outreach".
- Polish note (not a blocker): the disabled "Resend link" keeps a faded blue primary style; it could read as clickable.

## Approval gate (Mike)
1. Merge `release/sci-lead-workflow` into `main` (fast-forward). Production auto-deploys from `main`.
2. After deploy: open /program and one /leads/:id in production; confirm test records show no live send controls.

## Rollback
- Redeploy the previous production commit 90485585 in Render, or `git revert` the 15 commits on main. The added `lead_outcomes.attendance` column can stay after a rollback: the older code never reads it, so nothing breaks and nothing needs undoing.
