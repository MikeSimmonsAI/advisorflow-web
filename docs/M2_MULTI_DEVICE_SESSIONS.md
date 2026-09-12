# M2 — Multi-device sessions and the mobile foundation

## What this document is

M2 was briefed as a build: replace one-session-per-user with server-side
sessions, then stand up the Expo foundation. By the time the thread opened,
**both were already on `main`** — commit `23265c7`, "Mobile: a session that is a
row, and an app that renders what the server says", merged before this thread
started. Rebuilding it would have duplicated shipped work.

So M2 became an audit with teeth: trace what shipped, attack it, and close what
the attack found. It found two things, and they are the only new behaviour here.

---

## What was already there (verified, not written, by this thread)

| Piece | Where |
|---|---|
| `user_sessions` — one row per device, revoke reasons, `to_public_dict()` with no `jti` | `app/models/session_models.py` |
| start / rotate-in-place / revoke / revoke-all / revoke-by-id, all funnelled through one service | `app/services/session_service.py` |
| Row is authoritative; no-`jti` fails closed; revoked or expired refuses before any fallback | `app/deps.py` |
| `/auth/login`, `/auth/refresh` (session-scoped), `/auth/logout`, `/auth/logout-all`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}` | `app/routers/auth_router.py` |
| Force-logout, password change, deactivation and admin reset all revoke every row AND clear the legacy column | `app/routers/admin_router.py`, `auth_router.py` |
| Expo / expo-router app: SecureStore, API client with one 401 handler, `/auth/my-contexts` consumption, context resolver, session screen, device headers | `mobile/` |
| Push registration contract, with sending deliberately off | `app/services/push_service.py`, `app/routers/device_router.py` |
| Ephemeral media told the truth about rather than papered over | `app/services/mobile_storage.py` |

The Security Triad properties are intact and re-proved here: `purpose="access"`
required, setup tokens on their own signing key and unable to authenticate,
no-`jti`/no-session refused, OAuth identity from a server-side authorization
transaction, `SET LOCAL` statement timeout.

---

## What this thread found, and fixed

### 1. The legacy-column fallback was wider than the case it was built for

`deps.get_current_user` falls back to `users.session_token` when the presented
`jti` names no row, so a token minted before `user_sessions` existed survives
the deploy. Unqualified, that rule reads: *any* `jti` equal to the column
authenticates, row or no row. A `jti` can lose its row without being
pre-migration:

* **A refresh race.** Two rotations of one row interleave. The row keeps the
  second `jti`; the column keeps the first. The first token then names no row,
  matches the column, and authenticates — one session, two live credentials,
  and revoking the row reaches only one of them.
* **A deleted row.** `demo_environment` already deletes `user_sessions` rows on
  a demo reset, and the retention sweep below deletes long-dead ones. Deleting
  a *revoked* row must not be the thing that brings its token back.

**Fix.** `session_service.user_has_sessions()`. The column is honoured only for
a user with **no rows at all** — exactly the pre-migration case. Once somebody
has signed in since the table shipped, their sessions are rows and the column is
bookkeeping. A genuinely pre-migration token still works; there is a test that
says so, because tightening this into a forced sign-out for the whole customer
base would be its own outage.

### 2. `user_sessions` had no lifecycle

It is the first authentication table here that *grows*: a row per sign-in, and
every revoked and expired one kept. Nothing deleted from it.

**Fix.** `session_service.purge_dead_sessions()` plus `_session_cleanup_loop` in
`app/main.py`, daily, reporting through the existing `job_runs` ledger as
`JobName.SESSION_CLEANUP` — the same mechanism as the four loops already there,
not a fifth one. It will not delete a live row, will not delete a recently
revoked one (`revoked_reason` is the answer to "why did my phone sign itself
out", and the retention window is the support window — `SESSION_RETENTION_DAYS`,
default 30), and takes at most `SESSION_PURGE_BATCH` rows per pass so a sweep
nobody ran for a year is still a short statement.

The sweep and the fence are one change, not two: deleting rows would have been
unsafe without the fence, and the fence is what makes the sweep safe to run.

---

## Evidence

**Backend, targeted:** `tests/test_session_adversarial.py` — 70 new tests, all
passing. `tests/test_mobile_sessions.py` (23), `test_mobile_devices.py` (21),
`test_setup_token_cannot_authenticate.py` (14), `test_auth_service.py` (8),
`test_oauth_state_authorization.py` (33) — all passing.

**The adversarial matrix**, all thirty items, and where each is answered:

| # | Attack | Answered by |
|---|---|---|
| 1–2 | session id substitution / another user's session id | `revoking_someone_elses_session_is_a_404`, `a_jti_that_belongs_to_another_users_session_is_refused` |
| 3–5 | revoked / expired / deleted session | `a_revoked_session_is_refused_and_stays_refused`, `an_expired_row_is_refused…`, `a_deleted_session_row_is_refused` |
| 6 | inactive user | `a_deactivated_user_is_refused_while_the_row_is_still_live` |
| 7–8 | stale membership / stale role | `a_role_change_is_read_from_the_user_not_the_token` |
| 9–10 | wrong tenant / wrong brand | `an_org_override_…_changes_nothing`, `a_brand_override_…_changes_nothing` |
| 11 | malformed JWT | 10 parametrised shapes + wrong key + `alg=none` |
| 12 | wrong purpose | 7 parametrised purposes, all with a live row |
| 13–14 | setup token as access / access token as setup | `test_setup_token_cannot_authenticate.py` |
| 15 | token from session A paired with session B | `a_jti_that_belongs_to_another_users_session_is_refused` |
| 16 | refresh replay | `a_refresh_cannot_be_replayed`, `…does_not_create_a_second_row` |
| 17 | concurrent refresh | `concurrent_refresh_never_leaves_more_than_one_live_credential` (8 threads, 8 sessions, one row) |
| 18 | concurrent login | `two_logins_in_flight_leave_two_independent_sessions` |
| 19–20 | logout/refresh and revoke-all/refresh races | `refresh_after_logout_is_refused`, `refresh_after_logout_all_is_refused`, `a_logged_out_session_cannot_be_refreshed_into_a_new_row` |
| 21 | password-reset/session race | `refresh_after_a_password_change_is_refused` |
| 22–23 | duplicate / forged device metadata | `another_persons_device_id_does_not_revoke_their_session`, `forged_device_metadata_is_stored_as_a_label_and_nothing_more`, `absurd_metadata_is_clipped…` |
| 24–25 | session enumeration / endpoint authorization | `a_session_id_that_is_not_yours_is_always_the_same_404` (7 shapes), `the_session_endpoints_require_authentication` |
| 26 | raw secrets in API | `the_session_list_carries_no_credential_material` |
| 27–28 | cross-org / cross-brand context bleed | the two override tests above |
| 29 | God authority regression | `god_authority_is_not_reachable_by_holding_a_gods_jti`, `a_session_row_confers_nothing` |
| 30 | OAuth / setup regression | `test_oauth_state_authorization.py`, `test_setup_token_cannot_authenticate.py` |

**Mobile:** `tsc --noEmit` clean; 52 jest tests across 5 suites passing.

**Live, over real HTTP:** `scripts/run_m2_local_verify.bat` drives the full
A/B/C acceptance scenario — three sign-ins, refresh each, per-device logout,
revoke-by-id, sign-out-everywhere, sign in again and re-read
`/auth/my-contexts` — against a real uvicorn with a throwaway database and one
controlled test identity. 38 assertions, 0 failures.

**Live, against production:** `scripts/run_m2_live_verify.bat` with no identity
runs the unauthenticated half against `advisorflow-backend.onrender.com`: the
service answers, and all four session endpoints refuse anonymous callers. The
three-device half against production needs a controlled production test
identity, which this thread did not have and did not invent.

---

## Still open, and honestly labelled

* **Push delivery is NOT BUILT.** Registration contract and schema exist;
  sending is off until it is deliberately turned on. M3.
* **Persistent media storage is NOT BUILT.** `mobile_storage` refuses an upload
  into ephemeral storage rather than swallowing it, and reports the capability
  truthfully. Do not build mobile features that depend on `/tmp` persistence.
* **Brand Sales Workspace prospect messaging is still a GAP.** `sms_router` and
  `email_router` require tenant customer users. Nothing here loosened
  `require_tenant_user`, and nothing should until there is a proper
  platform/brand-sales communications authority model.
* **Mobile-ready revenue history** has no source yet. M3 dependency.
* **No signed App Store / Play Store release** was produced. The foundation
  type-checks, tests and builds; that is all that is claimed.

---

## The ship candidate, and what was run against it

One candidate was frozen and tested once, rather than restarting a 3,800-test
suite every time `main` advanced. That restarting is what cost this thread most
of its time: three runs died — a plain child of a dropped shell, a `start`ed
child of the same console tree, and a detached `cmd /c` whose quoted-and-
redirected command line Windows mangled into nothing. `-u` and `--junitxml` are
in `scripts/m2_regression_inner.bat` for the same reason: a buffered stdout only
reaches disk every 8 KB, so a run in progress looks stalled and a run that dies
loses everything since the last flush.

**Candidate:** `origin/main` at `8c4096b` + four modified files
(`app/deps.py`, `app/main.py`, `app/models/job_models.py`,
`app/services/session_service.py`) + `tests/test_session_adversarial.py`, this
document and the `scripts/m2_*` verification harness.

**Full regression:** 3792 passed, 3 failed, 15 skipped (38m42s).

All three failures are PRE-EXISTING and are not M2's. Proven, not assumed: a
detached worktree at exactly `8c4096b` — none of these changes present — fails
the same three, identically.

| Failure | Why it is not M2's |
|---|---|
| `test_commercial_agreements.py::test_completed_previously_mirrors_as_done_with_the_decision_date` | `date(2026,9,12) == date(2026,9,11)` — a UTC/local midnight rollover in the test, failing only across that boundary |
| `test_experience_severity.py::test_the_sales_workspace_palette_follows_the_appearance_axis` | asserts a `[data-appearance="dark"] .sw-scope{` rule in the Sales Workspace stylesheet |
| `test_experience_severity.py::test_the_dark_palette_is_designed_rather_than_inverted` | same stylesheet |

Nothing in this change can reach a commercial-agreement date or a stylesheet.

**Reconciliation.** `origin/main` then moved to `52d1929` (Launch Pad preview).
The incoming diff is seven `frontend/src/pages/launch/*` files plus
`tests/test_launch_experience.py` — no auth, no session model or service, no
`deps`, no startup or cleanup wiring, no mobile auth, no shared DB or session
infrastructure. So the completed regression was kept and a reconciliation gate
run instead of a fourth full suite:

**376 passed, 0 failed** on the reconciled tree — the M2 and security suites
(`test_session_adversarial`, `test_mobile_sessions`, `test_mobile_devices`,
`test_setup_token_cannot_authenticate`, `test_oauth_state_authorization`,
`test_auth_service`), the context and workspace boundary suites
(`test_context_boundaries`, `test_context_routing_gates`, `test_sales_workspace`,
`test_exec_workspace`), the incoming surface
(`test_launch_experience`, `test_customer_onboarding_delivery`,
`test_customer_onboarding_end_to_end`), and the schema gates
(`test_model_registry`, `test_auto_migrate`).

Mobile on the same tree: `tsc --noEmit` clean, 52 jest tests across 5 suites.
