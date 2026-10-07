# SCI desktop verification handoff

Branch `sci-program`, base SHA `8aa5578dae2c7b1f722b9ea04032e7787abbdaee` plus the
commit that adds this file (see `git log -1`).

## Status, honestly
- Only the dependency-free 164-check harness has ever run. It is not end-to-end readiness.
- On the relay runner (2026-10-07), `pip install` and `npm ci` were blocked by approval
  policy, so **no pytest, DB, route, frontend build or browser test has run**.
- `tests/test_sci_desktop_synthetic_flows.py` is new and has **never executed**; expect
  possible small fixes on first run (assumed login error codes 400/401, response shapes).

## Prerequisites (Windows)
Python 3.11+ (3.12 used by the runner), Node 20+ (22 used by the runner), Git.

## Setup and run
```
git fetch origin
git checkout sci-program
git pull --ff-only
python -m venv .venv
.venv\Scripts\activate
powershell -ExecutionPolicy Bypass -File scripts\sci_desktop_verify.ps1
```
Add `-SkipFrontend` to skip npm. Exit code is the number of failed steps (0 = pass).
Evidence is written to `handoff\SCI_DESKTOP_EVIDENCE.md`. Steps: stdlib harness,
`pip install -r requirements-dev.txt`, synthetic flows, SCI suites,
intake/inbound-email/reply/appointment suites, full pytest, `npm ci`, `npm run build`.

## Isolated test DB
pytest uses a fresh in-memory SQLite database per test (`tests/conftest.py`), and the
script sets `DATABASE_URL=sqlite:///:memory:`. Do not point any variable at a real
database. Twilio calls are blocked by the autouse `no_real_twilio_calls` fixture, and
the script clears provider keys from its process environment.

## Environment variable names (no values)
`DATABASE_URL`, `JWT_SECRET`, `ENCRYPTION_KEY`, `BOOKING_BASE_URL`, `FRONTEND_URL`,
`OPENAI_API_KEY`, `SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `MICROSOFT_CLIENT_ID`,
`MICROSOFT_CLIENT_SECRET`, `MICROSOFT_REDIRECT_URI` (from `.env.example`). For local
dev leave the provider ones unset. The script sets only `JWT_SECRET` and `DATABASE_URL`.

## Local app (manual, after tests pass)
`start-backend.bat` (uvicorn `app.main:app`, port 8000) and `start-frontend.bat`
(Vite dev server; port is Vite's default unless `vite.config.js` overrides it).
Use a local SQLite or throwaway DB, never a hosted one.

## Outstanding checklist
- [ ] Script steps 2-8 all pass; fix failures on `sci-program`
- [ ] Synthetic flows file passes (login, workspace isolation, suppression)
- [ ] Add synthetic tests: intake/qualification to a lead, email reply routing to the right workspace, appointment/handoff (existing suites listed in step 5 cover parts)
- [ ] Browser walk of the Launch Readiness tab
- [ ] Staging and provider evidence (owner-gated: Twilio/A2P, carrier, spend, real contacts)

## Rollback
All changes are additive (a test file, a script, docs). Roll back with
`git revert <commit>` or `git checkout 8aa5578 -- .`. No migrations or data changes.

## Preserving commits before leaving the GitHub relay
```
git fetch origin
git branch backup/sci-program-YYYYMMDD origin/sci-program
git branch backup/wholesale-nightly-YYYYMMDD origin/wholesale-nightly
git branch backup/platform-dev-YYYYMMDD origin/platform-dev
git bundle create sci-backup.bundle --branches --tags
```
Keep the bundle outside the repo. Do not merge these branches into each other; platform
closure `da29339` lives on `platform-dev`.
