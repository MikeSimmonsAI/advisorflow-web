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

## Continuation run sci-dependency-free-continuation-20261006-2059 (static + stdlib only)

Newly proven (source inspection plus `python3 -I scripts/sci_readiness_harness.py`): 154 executed, 154 PASS, 0 FAIL (was 147). Seven scenarios added in groups `webhook-url`, `cross-org`, `no-auto-send`:

- Proxy URL reconstruction (`candidate_urls`): forwarded proto+host, host-only https, configured base, raw URL, de-duplication, first hop of comma-chained headers. A signature over an attacker host, tampered params or a wrong token does not verify behind an http-reporting proxy.
- `twilio_security` rejects with `Raise` on missing token or missing signature (AST check, no pass-through).
- Voice guard `assert_org_matches` (loaded with fastapi stubbed): tenant-signed request cannot act on another org; tenant account with no org fails closed; platform token is trusted platform-wide as designed.
- Guards import no SMS/email/AI/SMTP surface.

Defects found: none. No application code changed.

First-login checklist corrections (source: `frontend/src/App.jsx` lines 486, 675-680, 1214; `Layout.jsx:67`; `program_router.py:972-986`):
- `/sci` is the SCI front door, not the workspace itself. It selects the SCI workspace for a user whose context list contains it, then opens Program Center at `/program` (nav label Family Service Center). Step 3 should read: go to `/sci`; expect to be taken to `/program`.
- Launch Readiness tab reads `GET /program/readiness-test` (manager or observer). Pressing run is `POST /program/readiness-test/run`, manager only. Both match the frontend calls.
- Manager 403 list (`/admin/users`, `/org-settings/*`, `/god/*`) was not re-inspected here; still per `SCI_MANAGER_ROLE_RESULTS.md`.

Still UNVERIFIED: pytest/DB/FastAPI route tests, frontend install/build, deployed or authenticated staging, mailbox provider, Michael's login. Verdicts unchanged. Rollback: revert this run's commit; code baseline 088eb42.

## Persist finalizer (relay sci-persist-finalizer-20261006-2202)

Recreated the ten inbound-email / readiness-evidence scenarios stranded by the failed push of the earlier run. Harness: 164 executed, 164 PASS, 0 FAIL (was 154). Groups `inbound-email` and `controlled-readiness`. `inbound_mailbox_service` is loaded with httpx, sqlalchemy and the ORM models stubbed (restored afterwards), and `route()` runs against a fake session.

- STOP typed in an email still suppresses after `clean_body`; a quoted "Reply STOP" does not suppress a genuine reply.
- `clean_body` handles None/empty and caps at 4000.
- RE/FW/FWD/AW/SV subject normalization (stacked, numbered, any case).
- `recipients_of` skips null/blank recipients.
- Mailbox service has no send/Twilio/SMTP imports; `no_lead` and `ambiguous` paths return no lead.
- Empty/None reply is neither opt-out nor HOT.
- Controlled-readiness evidence: synthetic, nothing sent, no secret-shaped values, fixed result shape.
- `route()` fails closed (`no_lead`, `ambiguous`) with a fake session; shared-mailbox ambiguity fails closed and subject beats recency.

Defects found: none. No application code changed. Rollback: revert this commit; baseline d7c5f35.
