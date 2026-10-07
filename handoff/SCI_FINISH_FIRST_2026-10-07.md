# SCI finish-first run: sci-finish-first-20261006-2058

Branch `sci-program`, baseline `f70046b`. Written 2026-10-06 CT. Staging only. Nothing sent, bought or changed anywhere.

## What this runner could and could not do

| Item | Result |
|---|---|
| Stdlib readiness harness (`python3 -I scripts/sci_readiness_harness.py`) | Executed: 147 run, 147 PASS, 0 FAIL. |
| Python deps (`pip install -r requirements-dev.txt` into a venv) | NOT RUN: the runner's permission layer refused package installs. `fastapi`/`sqlalchemy`/`pytest` are absent. |
| SCI pytest / DB / FastAPI route tests (A) | NOT RUN. Collected 0, executed 0, passed 0, failed 0, skipped 0. |
| Frontend `npm ci` / test / build (B) | NOT RUN: same refusal. `frontend/node_modules` absent. |
| Deployed staging verification (C) | NOT RUN: outbound network (`curl` to the staging host) refused. No deployed proof level is claimed; DEPLOYED AUTHENTICATED is NOT claimed. |
| Mailbox / inbound email proof (E) | NOT RUN live. Last evidence is the HOT proof in `SCI_OVERNIGHT_2026-10-06.md` section 6. |

This is an environment limitation, not an SCI code failure. No claim is made beyond the harness.

## One-page first login checklist (Michael, staging)

Source: `SCI_MANAGER_ROLE_RESULTS.md`, `SCI_GO_NO_GO_CHECKLIST.md` (A9, B2). Not re-verified tonight.

1. Open the SCI staging frontend: `https://sci-staging-frontend.onrender.com` (from `render-staging.yaml`; confirm it is the right host before sending Michael there).
2. Sign in with Michael's staging manager account. First interactive login/activation is EXTERNAL USER ACTION, not engineering.
3. Go to `/sci`. Expected: lands in the SCI workspace, Family Service Center / Program Center nav visible.
4. Expected scope: manager sees the whole SCI workspace (leads, conversations, reports, program). Expected denial (403): `/admin/users`, `/org-settings/*`, `/god/*`.
5. Open Program Center, tab "Launch Readiness". Expected: ten synthetic checks, signed webhook proof, verdict banner showing the gates still blocked. Press run: nothing is sent.
6. Expected: no other organization's data anywhere.
Report anything different to Mike; do not click anything labeled send/launch.

## Verdicts (from current evidence; unchanged from the 2026-10-06 checklist)

| Level | Verdict | Why |
|---|---|---|
| Staging / onboarding readiness | CONDITIONAL: unverified tonight | Code + harness green. Authenticated staging check and Michael's login outstanding. |
| Controlled internal test | CONDITIONAL GO, staging only, approved test recipients | Per `SCI_GO_NO_GO_CHECKLIST.md` section T. Re-run pytest/DB suite first (B1). |
| Real-number SMS/voice | NO-GO | Six numbers not bought (spend approval), A2P/carrier path unresolved. |
| First live SCI contact | NO-GO | Needs Mike's explicit GO, Kerry identity/copy, footer addresses, Oaklawn resolution. |
| Full production | NO-GO | Production promotion not authorized. |

## Lists

Mike/external only: approve six-number spend; A2P/carrier decision and attestation; Michael's first login; Kerry identity, signature and copy; Oaklawn facts; footer addresses; Outlook/Yahoo/iCloud placement; production promotion; first-live-contact GO.

Engineering remainder (needs a runner that allows dependency install and network): run `pip install -r requirements-dev.txt` then `pytest tests/test_sci_*.py` plus the DB/route suites; `npm ci && npm test && npm run build` in `frontend/`; confirm 844 and Oaklawn queue names in the real route test; deployed staging health and authenticated Launch Readiness contract check; staging mailbox status via `GET /god/staging/sci/status`.

Rollback: this run changed docs only. Revert this file's commit; code baseline is `f70046b`.

## What Mike can do next with SCI

Nothing new is proven tonight beyond the 147/147 harness. Mike can have Michael do the first staging login using the checklist above, and can approve or decline the spend and attestation gates. To unblock engineering, either allow dependency install and network on the relay runner or run the listed commands on a prepared machine, then re-issue this directive.
