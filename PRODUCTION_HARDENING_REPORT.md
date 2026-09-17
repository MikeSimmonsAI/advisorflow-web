# PRODUCTION HARDENING / SERVICE-ROLE CLEANUP

**Repo:** `advisorflow-web` · **Branch:** `main` · **Base:** `034a25b` · **Head:** `16f56a4`
**Date:** 2026-09-17

> **REAL EMAILS SENT: 0**
> **REAL SMS SENT: 0**
> **REAL VOICE OUTREACH: 0**
> **CADENCE ENABLED: NO**
> **AUTOMATIC OUTREACH ENABLED: NO**
> **PUSHED: NO**
> **DEPLOYED: NO**

Nothing in this pass contacts a customer, enables an outreach path, adds a
provider credential, or touches production data. Everything below is code,
configuration in `render.yaml`, and tests. Production was read only.

---

## 1. FINAL SERVICE ROLE MAP

`SERVICE_ROLE` is now set on all five Render services and read by
`app/service_role.py`. It is the only thing that decides which background
schedulers a process starts.

| Render service | Type | `SERVICE_ROLE` | Starts schedulers | Runs |
|---|---|---|---|---|
| `advisorflow-backend` | Web | `backend` | **All five** | `app.main:app` (HTTP + loops) |
| `advisorflow-voice` | Web | `voice` | **None** | `app.main:app` (HTTP only) |
| `advisorflow-cadence-job` | Cron | `job` | **None** | cadence cron entrypoint |
| `advisorflow-email-poller` | Cron | `job` | **None** | email poller entrypoint |
| `advisorflow-ai-conversation` | Cron | `job` | **None** | AI conversation entrypoint |
| `advisorflow-frontend` | Static | — | n/a | Vite build |

**A process with no role starts nothing.** `normalize()` maps anything
unrecognised — unset, empty, misspelled — to `unknown`, and `owns()` returns
`False` for every job under `unknown`. That is deliberate and it is the single
most important property in the file: each of these loops can eventually text or
email a customer, and a process that cannot say what it is has no business
doing that. The failure mode of a typo is a service that does no background
work, which is visible and harmless; the alternative failure mode is a second
process quietly sending everything twice, which is what actually happened.

`UNASSIGNED` is currently empty — every loop in `LOOP_JOB_NAMES` has exactly one
declared owner, asserted by `test_service_role.py`. A new loop added without an
owner shows up there and fails the test rather than silently running nowhere.

`current_role()` reads the environment on every call rather than caching at
import, so a test can set the variable without reloading the module — and so a
process cannot end up acting on a role it held at import time.

---

## 2. BACKGROUND LOOP OWNERSHIP — BEFORE AND AFTER

`app/main.py`'s startup handler used to call `asyncio.create_task()` five times
unconditionally. `advisorflow-voice` runs the **same** `app.main:app`, so it ran
all five as well.

| Loop | Interval | Before | After |
|---|---|---|---|
| `ai_conversation_loop` | 120 s | backend **+ voice** | backend only |
| `cadence_loop` | 1 h | backend **+ voice** | backend only |
| `review_request_loop` | 30 min | backend **+ voice** | backend only |
| `support_intelligence_loop` | 6 h | backend **+ voice** | backend only |
| `session_cleanup_loop` | 24 h | backend **+ voice** | backend only |

The evidence that this was real, not theoretical: `job_runs` showed **1,436**
`ai_conversation_loop` runs in 24 hours against 720 configured, in pairs one to
two seconds apart.

Startup is now plan-driven — `service_role.startup_plan()` returns what to start
and what to skip, `log_startup_plan()` writes both to the log at boot, and
`main.py` iterates the plan. There are no ad-hoc booleans scattered through the
startup handler; a loop whose factory is missing from the map logs an error and
is skipped rather than failing startup silently.

---

## 3. EXPECTED JOB-RUN REDUCTION

| | Before/day | After/day |
|---|---|---|
| `ai_conversation_loop` | 1,440 | 720 |
| `review_request_loop` | 96 | 48 |
| `cadence_loop` | 48 | 24 |
| `support_intelligence_loop` | 8 | 4 |
| `session_cleanup_loop` | 2 | 1 |
| **Loop subtotal** | **1,594** | **797** |
| `advisorflow-email-poller` | 1,440 | 288 |
| `advisorflow-ai-conversation` | 96 | 96 |
| `advisorflow-cadence-job` | 1 | 1 |
| **Cron subtotal** | **1,537** | **385** |
| **TOTAL SCHEDULED RUNS/DAY** | **3,131** | **1,182** |

**A 62% reduction**, against a platform with effectively no production users yet.

---

## 4. EMAIL POLLER — BEFORE AND AFTER

| | Before | After |
|---|---|---|
| Schedule | `* * * * *` (every minute) | `*/5 * * * *` |
| Runs/day | 1,440 | 288 |
| Lookback | 5 minutes | 15 minutes (`POLL_LOOKBACK_MINUTES`) |
| Page size | 50 | 200 (`POLL_PAGE_SIZE`) |
| `$orderby` | `receivedDateTime desc` | `receivedDateTime asc` |
| Second cron created | — | **No** |

**Why the window had to widen first.** There is no cursor — the poller asks
Microsoft Graph for recent mail and de-duplicates on `(Reply.lead_id,
Reply.body)` plus a Graph category tag. Changing only the schedule would have
left a 5-minute lookback running every 5 minutes: any scheduling jitter, any
slow run, any missed tick, and a customer reply falls into the gap and is never
seen. The window is now three times the interval, so two consecutive runs
overlap by 10 minutes and the overlap costs nothing because the de-duplication
already handles it.

**The `desc` → `asc` change is a separate real defect.** With `$top=50` and
`desc`, a burst of more than 50 messages truncated the **oldest** — the ones
closest to ageing out of the window and therefore the ones that would never be
seen again. `asc` truncates the newest, which the next run picks up anyway.

---

## 5. AI CRON SAFETY — FIXED BEFORE CONFIG, NOT AFTER

The `advisorflow-ai-conversation` cron exits 1. The diagnosis established it is
pure configuration: the service carries 7 environment variables and none of them
is `OPENAI_API_KEY`, `RESEND_API_KEY` or `EMAIL_FROM_ADDRESS`. One run reported
`processed: 25, errors: 25`; every other run reported `processed: 0`. The job
never crashed — `sys.exit` reflects the error count.

**Two things were fixed before anyone goes near the missing keys.**

**A generic fallback can no longer reach a customer.** `generate_touch_email`
returns a hardcoded generic message when AI generation fails. `_send_touch`
would have sent it. Twenty-five families were one working Resend key away from
receiving an automated message nobody wrote or approved. The fallback now
carries `generation_failed: True`, and `_send_touch` refuses before touching the
provider unless `APPROVED_FALLBACK_TEMPLATE` is set — which it is not, and
setting it is a business decision, not a config fix.

**A missing key fails once, not twenty-five times.** `preflight()` checks the
required configuration with no database access and no provider call.
`process_scheduled_touches` aborts before the query and returns
`{"processed": 0, "aborted": True, "missing_config": [...], "error": <reason>}`.
The `error` key is what the cron's exit code already reads, so the job still
exits non-zero and still shows red — but the log now names the missing variable
once instead of producing twenty-five identical failures that read like a
sending problem.

---

## 6. MEMORY — ROOT CAUSE, FROM EVIDENCE ONLY

The backend restarted out of memory on a 512 MB Starter instance. Two distinct
causes, neither of which is traffic.

**(a) A ~306 MB structural baseline.** The process was two-thirds full before
serving a request. The control proves it is import-time rather than load:
`advisorflow-voice` runs the same image on the same plan with nearly no traffic
and sat at **306 MB flat for 62 hours**, while the backend climbed from the same
figure to 372.7 MB over 15.5 hours.

Measured locally, library by library, importing `app.main`:

| Library | RSS at import | Who needs it |
|---|---|---|
| `fastapi` | +34.3 MB | every request |
| `sqlalchemy` | +10.7 MB | every request |
| `stripe` | **+45.9 MB** | `/billing` only |
| `pandas` | **+44.8 MB** | CSV/XLSX import only |
| `googleapiclient.discovery` | **+20.0 MB** | Google Calendar sync only |
| `openai` | +8.2 MB | AI paths only |

**(b) Allocator retention, not leaked objects.** `/proc/self/status` reported
`RSS 373 MB` against `VmData 826 MB`. That 453 MB gap is address space glibc
took from the OS and did not return.

Ruled out with evidence during the diagnosis, and unchanged here: traffic
volume, CPU, worker count, thread count, file descriptors, database connections,
and the background loops (which complete in 8–135 ms).

---

## 7. ALLOCATOR CHANGE

`MALLOC_ARENA_MAX=2`, set on all five services in `render.yaml`.

**Why.** glibc creates up to `8 × nproc` per-thread arenas. Render reports the
host's full core count to a container allowed half a CPU, so a Starter instance
gets dozens. Each holds its own free lists and its own top-of-heap; freed memory
in arena 11 cannot satisfy an allocation in arena 3, and an arena returns memory
to the OS only when its own top chunk is large and contiguous. A **sync**
FastAPI app is the worst case: every sync route runs in an anyio threadpool
thread, so request work is spread across threads and therefore across arenas,
and the heap ratchets instead of levelling off. That matches the observed curve
exactly — a slow steady climb with no plateau on ~2,700 requests/day.

**Expected impact.** A flatter curve, not a smaller baseline. It does not touch
the ~222 MB of imports; it attacks the ~4.3 MB/hour of growth that turned two
days of uptime into a restart. A modest throughput cost is possible under heavy
concurrency as threads contend for fewer arenas; at this traffic level that is
not a real trade.

**How we verify.** `/proc/self/status` in Render Shell at deploy and again 24 h
later. `VmData` should track much closer to `RSS`, and `RSS` should flatten
rather than climb. `advisorflow-voice` remains the control: if the gap closes on
both, the allocator was the cause; if it closes only on the busy one, the
remaining growth is request-driven and belongs in application code.

**Rollback.** Delete the two lines and redeploy. No code reads it.

---

## 8. TOP MEMORY OPTIMIZATIONS, RANKED BY EVIDENCE

Ranked by measured production call volume × allocation per call. The production
request mix came from the backend logs, not from guessing which endpoints look
expensive.

### #1 — Startup imports · **−84.2 MB, measured**

`import app.main`: **272.7 MB → 188.5 MB**, 2,684 modules → 1,728. `stripe` and
`pandas` now sit behind `app/lazy_module.py`; `googleapiclient.discovery` moved
to its single call site. Every process on the platform paid for all three,
including three cron jobs that will never read a spreadsheet.

`openai` was deliberately **not** deferred: 8.2 MB across nine importing modules,
and the AI conversation loop touches it on a schedule anyway.

The proxy exists because `stripe` has thirteen call sites across two modules,
including `except stripe.error.SignatureVerificationError` in the webhook
handler. Deleting the top-level import and missing one turns into a `NameError`
on a live billing request — the path least likely to be exercised by a test run
and most expensive to get wrong. The proxy leaves every call site as written, so
there is no site to miss. It forwards `__setattr__` too, because
`stripe.api_key = key` must land on the real module; that is the specific trap
in `importlib.util.LazyLoader`.

### #2 — `GET /notifications/` · ~1,440 calls/day, **53% of all HTTP traffic**

Answered every call with an unbounded `.all()` of fully-hydrated ORM rows, each
carrying a free-text message built from a lead's name, phone and the entire body
of their reply. Nothing expires a notification and nothing marks one read except
a human clicking it, so the unread set only grows. The badge rendered
`notifications.length` — the count was paid for by loading every row to measure
the list.

Now: `items` capped at 50, `unread_count` as a SQL count that stays exact past
the cap, `has_more`, and named columns instead of mapped objects. `limit=0` is
clamped to 1 rather than meaning "unbounded".

### #3 — Security headers middleware · 100% of requests

`BaseHTTPMiddleware` implements `call_next` with an anyio task group and a pair
of memory-object streams — a task, two streams and a wrapper response allocated
per request, to set five constant headers and delete one. Now plain ASGI,
editing the header list on `http.response.start`.

### #4 — `GET /outcomes/summary` · on every dashboard load

Loaded every `LeadOutcome` row in the organization as mapped objects to produce
two integers via `len()` and a `sum()` over a boolean. Both are SQL counts now.
`top_sale_items` still needs Python because the column is comma-separated free
text, but the rows are grouped in SQL first, so the loop runs over the distinct
strings people have typed — a few hundred at worst — weighted by occurrence,
instead of over every outcome ever recorded.

### Checked and deliberately left alone

This matters as much as the list above, because the instruction was not to
optimize blindly:

| Endpoint | Finding |
|---|---|
| `GET /leads/` | Already column-projected with an explicit `COLS` list, paginated, no relationship loading. Nothing to do. |
| `GET /leads/daily-briefing` | Five `func.count()` scalars. Already correct. |
| `GET /leads/status-funnel` | `GROUP BY` in SQL. Already correct. |
| `GET /admin/dashboard/metrics` | Bounded by advisor count, and already batched through `AdvisorCounts` with a prior fix for per-advisor org refetching. |
| `GET /sms/replies` | Bounded `.all()`. |
| `GET /activity/sent` | Two bounded `.all()`s. |
| `GET /settings/my-capabilities`, `/auth/my-contexts`, `/god/platform/context`, `/demo/environment`, `/launch/me`, `/pipeline/forecast` | Small, no unbounded materialization. |
| `GET /god/orgs` | Three unbounded `.all()`s over Organizations, Users and Platforms — but those tables hold tens of rows, it is god-admin only, and it is not on a customer's dashboard path. **Not changed**: the risk of touching a god-scoped query outweighs a few kilobytes. Noted, not fixed. (It also ignores the `?limit=200` the client sends — cosmetic, left alone.) |

There are 371 `.all()` calls across the routers. The four above are the ones the
production request mix says are actually being called at volume. The rest are
not evidence of anything yet.

---

## 9. FRONTEND REQUEST REDUCTIONS

**Evidence:** a single client IP, one four-second window in the production logs
— **22 GETs, each preceded by its own CORS preflight: 44 round trips to render
one screen.** Two of the GETs were `/settings/profile` and two were
`/settings/my-capabilities`, issued in the same instant.

| Change | Before | After |
|---|---|---|
| Notification poll interval | 30 s | 60 s |
| Notification polling in a hidden tab | continues forever | stops; immediate catch-up on return |
| Overlapping notification polls | stack up behind a slow response | in-flight guard |
| Duplicate concurrent GETs | one request per caller | coalesced in flight |
| `/notifications/` per client/day | ~1,440 | ~720 ceiling, ~0 while hidden |
| Requests per dashboard load | 22 GET + 22 OPTIONS | 20 GET + 20 OPTIONS |

**The dedupe coalesces; it does not cache.** The entry is dropped the moment the
promise settles — in a `finally`, so rejections are dropped too. There is no
TTL, no stored response, nothing to invalidate. A joined caller receives the
answer to a request that was **already in flight when they asked**, which is no
staler than the answer their own request issued in the same millisecond would
have carried.

That distinction is the whole safety argument, and it is exactly the constraint
about not staling security state. `workspaceAuthority.js` and `Layout.jsx` both
carry comments explaining that a capability list rendered from a stale answer
shows an org admin a door the server will refuse. A few seconds of TTL would
reintroduce that, and the difference between the safe and unsafe versions is one
line — so `tests/test_request_dedupe_gate.py` fails if `setTimeout`, `Date.now`,
`TTL`, `expires` or `maxAge` appears anywhere in the dedupe.

The key carries every scoping header (org override, brand, workspace,
observation mode) so two contexts asking the same path are never merged, and the
gate asserts the key against `request()` itself so it cannot drift behind a
newly added header. The map is cleared on login and logout. Writes are not
coalesced.

**One correction to an earlier figure.** `/ping` was ~720 calls/day in the logs,
which I initially attributed to the app. The app's keep-alive runs every 14
minutes (~103/day). The rest is external uptime monitoring hitting the same
endpoint from several regions, the same pattern already documented for
`/health`. That traffic is not ours to reduce and was left alone.

---

## 10. HEALTH CHECK

`GET /health` answered 200 and `HEAD /health` answered **405**, so roughly half
of every external uptime check was failing against a completely healthy service.
FastAPI does not add `HEAD` to a `GET` route the way plain Starlette does.

- Route is now `@app.api_route("/health", methods=["GET", "HEAD"])`.
- `healthCheckPath: /health` added to `advisorflow-backend`, so Render itself has
  a real health signal instead of inferring one from the port.

---

## 11. RECOMMENDED RENDER PLAN

**Recommendation: stay on 512 MB for `advisorflow-backend` and re-measure after
this deploys.**

Resizing before the fixes land would have hidden the bug. Resizing *after* them,
without measuring, would be guessing.

| Plan | Post-fix baseline | Headroom | Verdict |
|---|---|---|---|
| **512 MB** | ~222 MB (43%) | ~290 MB | **Recommended now.** Baseline drops from 60% to 43% of limit and headroom rises from ~206 MB to ~290 MB. At the previously observed 4.3 MB/h, time-to-ceiling moves from ~48 h to ~67 h — and `MALLOC_ARENA_MAX=2` targets that growth rate directly. If the curve flattens, 512 MB is correct and not merely survivable. |
| **1 GB** | ~222 MB (22%) | ~800 MB | **The upgrade to make if growth persists.** Operating target ~350–400 MB, i.e. **35–40% of limit**, leaving room for a real user load rather than for a leak. Choose this only if 24 h of post-deploy measurement still shows a climb with no plateau. |
| **2 GB** | ~222 MB (11%) | ~1.8 GB | **Not justified by anything measured.** Nothing in the evidence suggests a working set anywhere near this. Revisit only when concurrent user load, not baseline, is the constraint. |

**The operating target is not 85–95% of the limit.** Sustained operation above
~70% on a box whose baseline is fixed at import time leaves nothing for a
traffic spike, an import job, or a Stripe webhook that finally loads the 46 MB
library on demand. The three deferred imports are now *demand-loaded*, which is
the right trade — but it means a first billing request adds ~46 MB to that
process. At 43% baseline that is comfortable; at 85% it is a restart.

`advisorflow-voice` and the three cron jobs get the same baseline reduction for
free and need no resize.

---

## 12. DATABASE AND JOB LOAD ESTIMATE

**Scheduled work:** 3,131 → 1,182 runs/day (−62%), per §3.

**Database connections.** The duplicate schedulers meant `advisorflow-voice` held
a connection pool doing background work it should never have been doing — five
loops, each opening a session per tick, against a Starter Postgres. Voice now
opens sessions only for HTTP requests it actually serves.

**Query volume, roughly, per day, from the two ends that dominate:**

| Source | Before | After |
|---|---|---|
| Email poller Graph+DB cycles | 1,440 | 288 |
| `ai_conversation_loop` ticks | 1,440 | 720 |
| `/notifications/` queries (per client) | 1,440 unbounded reads | ≤720 capped reads + 720 counts |
| `/outcomes/summary` | full `lead_outcomes` scan per dashboard load | 2 counts + 1 grouped read |

The single largest database-load change is not a count — it is the shape of the
`/notifications/` query. An unbounded read of a table that only grows, executed
once a minute per client forever, is the one item here whose cost scales with
account age rather than with usage.

---

## 13. TESTS

New in this pass:

| File | Tests | Covers |
|---|---|---|
| `tests/test_service_role.py` | 38 | Role normalization, unknown-starts-nothing, one owner per loop, `UNASSIGNED` empty, `render.yaml` sets the variable on every service |
| `tests/test_ai_touch_safety.py` | 13 | Fallback never sends, preflight fails once, abort shape carries `error` |
| `tests/test_email_poller_schedule.py` | 12 | Window ≥ 3× interval, `asc` ordering, page size, `render.yaml` schedule |
| `tests/test_health_probe.py` | 9 | `HEAD` is 200 with no body, `healthCheckPath` present |
| `tests/test_startup_memory.py` | 10 | Heavy libraries absent from a clean `import app.main`; the proxy's `__setattr__` lands on the real module |
| `tests/test_security_headers.py` | 26 | Every header by name on normal/404/401/HEAD paths; middleware is not a `BaseHTTPMiddleware` |
| `tests/test_notification_bell_payload.py` | 15 | Cap, exact count past the cap, newest-kept ordering, cross-advisor scoping, payload shape |
| `tests/test_request_dedupe_gate.py` | 8 | Coalescing not caching, no TTL primitives, key covers every scoping header, writes not merged |
| `tests/test_outcomes_summary_aggregation.py` | 10 | NULL handling, blank free text, occurrence weighting, cross-org scope, query shape |

Three of these assert **shape rather than result**, because the fix would
otherwise be invisible to the test suite: a restored `.all()` in
`/outcomes/summary` produces identical numbers, a reverted middleware produces
identical headers, and a TTL added to the dedupe produces identical data until
the moment somebody's permissions change.

Full-suite result: see §15.

---

## 14. COMMITS

Committed in logical clusters, named files only, nothing pushed.

| SHA | Cluster |
|---|---|
| `f8f9390` | Service roles: centralized gating, duplicate schedulers removed |
| `ad8e883` | AI cron safety, fixed before configuration |
| `9dad4f7` | Email poller `*/5` with a window wide enough to make it safe |
| `034a25b` | `/health` answers HEAD; Render gets a real health signal |
| `e0c35ed` | Memory: startup baseline, allocator, middleware |
| `4eebc50` | Notification bell: the cap and the poll |
| `a3759ab` | In-flight GET coalescing |
| `16f56a4` | `/outcomes/summary` counts in SQL |

`auto_migrate.py` was left untouched. No `git add .` or `git add -A` was used.
Four files whose working copies carry CRLF were written back with their original
line endings so the commits contain the change and not a whole-file rewrite.

---

## 15. FULL REGRESSION

Whole suite, all 232 test files, run in batches against the final tree:

```
4,742 passed
   14 skipped
    0 failed
    0 errors
```

**The 14 skips are both pre-existing environment gaps, not anything this pass
introduced:**

- 8 in `test_import_service.py` — "Real Restland test file not available in
  this environment". These need a customer CSV that is not in the repo.
- 6 in `test_request_statement_timeout.py` — `TEST_POSTGRES_URL` is not set,
  and the pooled-connection behaviour under test cannot be observed on SQLite.

**One existing test needed updating, and it is the right kind of failure.**
`test_notification_service.py::test_get_unread_notifications_excludes_read_ones`
read `unread[0].message` off the returned rows. They are dicts now rather than
mapped `Notification` objects — which is precisely the change that stops this
endpoint hydrating full ORM rows, including whole reply bodies, once a minute
per client. The assertion was updated to `unread[0]["message"]` and the reason
recorded next to it. No assertion was weakened and nothing was skipped.

That was the **only** caller of `get_unread_notifications` outside the router,
confirmed by grep before the change and by the suite after it.

---

## 16. SAFE DEPLOYMENT ORDER

Each step is independently revertible. Do not batch them.

1. **`render.yaml` Blueprint sync — `SERVICE_ROLE` first, on its own.**
   This is the one change that alters *which processes do work*. Sync it, then
   confirm in the logs that `advisorflow-backend` prints a startup plan with
   five loops and `advisorflow-voice` prints one with none. If the plan is
   wrong, revert this before anything else ships.
2. **Backend code deploy** (memory, notifications, outcomes, middleware,
   `/health`). `MALLOC_ARENA_MAX` rides along in the same Blueprint sync as
   step 1 but takes effect on this restart. Record `/proc/self/status` for
   `advisorflow-backend` and `advisorflow-voice` **immediately before** this
   deploy — without the before-reading the verification in §7 cannot be done.
3. **Frontend deploy.** Order against step 2 does not matter: the bell reads
   both the old array and the new object.
4. **Email poller `*/5`.** Already in the Blueprint from step 1; confirm the
   first two runs overlap correctly and that reply ingestion continues
   unbroken before considering the change settled.
5. **Nothing for the AI cron.** It will keep exiting non-zero until someone
   decides about the missing keys. That is the correct state — it is now
   failing loudly and cheaply instead of almost sending twenty-five unapproved
   emails.

---

## 17. POST-DEPLOY VERIFICATION

**Within minutes:**

- [ ] `advisorflow-backend` logs a startup plan naming all five loops; `advisorflow-voice` logs one naming none.
- [ ] `job_runs` shows `ai_conversation_loop` ticking **once** per 120 s, no 1–2 s pairs.
- [ ] `HEAD /health` returns 200; Render's own health check reads green.
- [ ] `GET /notifications/` returns the object shape; the bell renders and the badge number matches.
- [ ] `/proc/self/status` on `advisorflow-backend`: **record `RSS` and `VmData`.** Expect `RSS` ≈ 220–230 MB against the previous ~306 MB.

**Within 24 hours:**

- [ ] `VmData` tracks close to `RSS` instead of exceeding it by ~450 MB.
- [ ] `RSS` has flattened rather than climbed at ~4.3 MB/h.
- [ ] `advisorflow-voice` (the control) sits flat at the new lower baseline.
- [ ] Email poller: 288 runs, no gap in reply ingestion across any boundary.
- [ ] Total scheduled runs ≈ 1,182, not ≈ 3,131.
- [ ] `/notifications/` is no longer the majority of request volume.

**If `RSS` still climbs after 24 h**, the allocator was not the whole story and
the remaining growth is request-driven. That is the point at which 1 GB is
justified — with a measurement behind it, not as a substitute for one.

---

## 18. WHAT WAS NOT DONE, AND WHY

- **No push, no deploy, no Render resize.** All of it is staged locally.
- **No provider credentials added.** The AI cron's missing keys are a decision,
  not a task.
- **`APPROVED_FALLBACK_TEMPLATE` left `None`.** Setting it authorises an
  automated message to go to a customer when AI generation fails. That is
  yours, and it should be a template somebody wrote and approved.
- **Cadence still off.** Unchanged.
- **`/god/orgs` left unoptimized.** Explained in §8.
- **Two orphan modules still present** — `app/crons/appointment_reminder_cron.py`
  and `app/jobs/run_ai_operations_worker.py`. No Render service runs them and
  nothing imports them. Deleting code is a decision about intent, not a
  hardening step; they are named here so the next pass can decide.
