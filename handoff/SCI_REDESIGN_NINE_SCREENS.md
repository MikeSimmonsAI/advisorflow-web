# SCI Family Service Center — nine-screen redesign (staging)

Written 2026-10-09 CT.

- **Branch:** `sci-program`. Commits f3a1ccc8, 4be6b376, 1893827a, c5a162f7.
- **Deployed to:** staging only. Production and `main` are untouched.
- **Staging URL:** https://sci-staging-frontend.onrender.com/program. `/sci` opens the same workspace.

## What changed

**The shell.** `/program` now renders its own full-page SCI shell:
- a navy sidebar with the nine screens;
- a sticky top bar with:
  - contact search, which opens Review across all staged records;
  - the location filter;
  - the HOT bell, which opens Responses;
  - the signed-in user.

Permissions are unchanged. The route still goes through `ProtectedRoute`; the new `bare` prop only leaves out the tenant Layout chrome, and the context banner ("Return to God Mode") stays.

**Deep links are unchanged:** `?tab=responses|review|locations|campaigns|assets|launch|health|settings`, plus `?location=`, `?queue=`, and the new `?q=`.

**One design system.** `frontend/src/pages/program/ProgramCenter.css`, with the tokens from the handoff package. Shared components are in `sci/ui.jsx`. Each screen is its own file:

| Screen | File | Data (all server-derived) | Actions verified on staging |
|---|---|---|---|
| Dashboard | `sci/Dashboard.jsx` | `/program/dashboard`; 7-day chart from `/program/responses` | KPI cards open filtered views; action center links |
| Responses | `sci/Responses.jsx` | `/program/responses`, `/leads/{id}/timeline`, `/voicemails`, `/program/unmatched-replies` | Thread loads with calls in time order; mark opened / responded / active / close; reply box (server gates decide; not test-sent to real phones) |
| Review | `sci/Review.jsx` | `/program/records` and attention counts | Queue chips, search, expand; hold → release round trip; assign location |
| Locations | `sci/Locations.jsx` | `/program/locations` (+ `verification_held`) | Search; Oaklawn shows HELD; edit → save |
| Campaigns | `sci/Campaigns.jsx` | `/program/campaigns`, `/preview`, `/program/health` (sms) | Real per-location preview; ON/OFF/BLOCKED; switch is unchanged (typed confirmation) |
| Assets & Flyers | `sci/Assets.jsx` | `/program/assets` | Empty state (staging has 0 files); filters; upload form. No sample covers. |
| Launch Readiness | `sci/Launch.jsx` | `/program/readiness-test` (+ `stages`) | Controlled test run: 10/10 PASS, webhook proof 13 PASS, 0 sent |
| Health | `sci/Health.jsx` | `/program/health` | Tiles, texting fact by fact, technical details, placement |
| Settings | `sci/Settings.jsx` | program, readiness, `/program/aliases`, `/program/alerts` | Six categories; inline validation; save round trip (SLA 15→16→15) |

## Backend corrections (needed so the screens tell the truth)

- **Launch checklist** (`readiness_check.py`):
  - Rewritten to today's facts and grouped by stage: A code QA, B inbound live test, C consent records, D carrier-approved purpose, E admin assignment, F production, G first-contact GO.
  - The six-number purchase and A2P items are removed. There is one line: 844-917-2171.
  - `why_no_go` is now derived from the open items.
- **Settings readiness** (`program_router._sms_items`): for SCI, the texting number is the toll-free line on the platform account, not an organization number. Reply routing checks the 844 row.
- **Health texting** (`health._sms_block`): reports configured, approved, sending on and last inbound text separately. "Working" requires proven approval; unknown approval is a warning.
- **Locations:** each location carries `verification_held`, using the same rule as the send gate (`sms_programs.location_name_unverified`). Today that is Oaklawn.

## Tests

- Frontend production build: passes.
- Backend: 310 related tests pass. They cover outreach program, consent center, every SCI suite and the lead-capacity guard.
- Manual QA on staging:
  - all nine screens at 1920 and 1600 desktop and 1280 laptop, and at 390 phone (in a fixed-width frame, because the Chrome window is maximized);
  - no sideways scroll on any screen at 390 after the fixes;
  - phone menu opens, navigates and closes.

## Not done / blocking

- No real outbound text was sent from the new reply box during QA, because it would text a real phone. The box uses the same `/sms/send` endpoint as the lead page, and the server's SCI gate decides.
- These are not yet on production. Shipping them takes the usual pull request from `sci-program` to `main`, with Mike's GO.
