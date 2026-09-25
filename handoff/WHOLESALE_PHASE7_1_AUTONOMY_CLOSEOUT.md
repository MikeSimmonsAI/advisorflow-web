# PHASE 7.1 — EVOSENSE AUTONOMY CLOSEOUT

**SET THE STRATEGY. LET EVOSENSE HUNT. COME BACK WHEN SOMETHING NEEDS YOU.**

Repo `C:\Dev\advisorflow-web`, branch `main`, HEAD `184dc41` — uncommitted, same working
tree as Phase 6.1 and Phase 7. Nothing committed, pushed, merged or deployed.
`feature/universal-intake` not touched. No real data vendor connected. No redesign.

This pass closed exactly two gaps:

1. EvoSense hunts are now run by the **platform's own background-loop machinery**, not
   by a script or a button.
2. **Real inbound seller SMS replies flow into EvoSense automatically** through the
   platform's existing inbound webhook — persisted first, then routed, then read.

---

## 0. Working-tree safety

Before any change: `git status` (94 entries), branch `main`, HEAD `184dc41`, recorded to
`C:\Users\simmo\Downloads\p71_snapshot\` together with `tracked.diff` (every tracked
modification), `files.txt` (every modified and untracked file), a 112 MB tar of every one
of those files (`worktree_changed_files.tar`), and a copy of `advisorflow.db`.
No reset, clean, checkout, stash or merge was used.

Reconciliation: the current Windows tree was compared file-by-file with the Phase 7 copy.
Nothing had changed since Phase 7. The platform files this pass edits
(`app/main.py`, `app/models/job_models.py`, `app/service_role.py`,
`app/routers/sms_router.py`, `tests/test_backend_availability.py`) were verified
unmodified by any other workstream before writing (the last four are clean at HEAD;
`main.py` carries only the Phase 7 lines). Every file written was re-verified on Windows
by hash afterwards.

## 1. Scheduler architecture — reused, not rebuilt

The platform already had one: in-process asyncio loops in `app/main.py`, each owned by
exactly one service role in `app/service_role.py` (`SCHEDULER_OWNER`, fail-closed when
`SERVICE_ROLE` is unset), each pass recorded in the `job_runs` ledger by
`job_run_service.record_job_run`, each pass's blocking work run off the event loop by
`_off_loop`. EvoSense joined it the way every other loop did:

| Where | Change |
|---|---|
| `app/models/job_models.py` | `JobName.EVOSENSE_HUNT = "evosense_hunt_loop"`, added to `LOOP_JOB_NAMES` |
| `app/service_role.py` | `SCHEDULER_OWNER[EVOSENSE_HUNT] = ROLE_BACKEND` (one owner; voice/job/unknown never run it) |
| `app/main.py` | `_evosense_hunt_loop()` (same shape as `_sales_reminder_loop`: startup offset, `record_job_run`, `_off_loop`, never kills the process) + one line in the loop factory table |
| `tests/test_backend_availability.py` | the new loop is in the list whose bodies must not block the event loop |

Every 15 minutes the loop calls `evosense.scheduler.run_due(db)`. There is no second daemon.

## 2. Cadence

New table `evosense_hunt_schedules` (one row per strategy, created on activation or on the
first pass): `cadence` **manual | daily | interval** (hours), `next_due_at`,
`last_scheduled_at`, `last_run_id`, `last_status`, `last_error`, `consecutive_failures`,
lock columns. **Daily is the default.** A new strategy is due immediately; after a
successful hunt it is due again 24 hours later (or after its interval). Manual-only never
runs by itself. Set in the Strategy Builder, section 1 ("Automatic hunting").

## 3. Locking and idempotency

* **The schedule row is created once.** Two workers meeting a new strategy at once: one
  creates the row, the other uses it (savepoint + unique `strategy_id`).
* **One hunt per strategy at a time.** `scheduler.claim()` takes the lock with one
  conditional `UPDATE … SET lock_token, locked_until WHERE id = :id AND (locked_until IS NULL
  OR locked_until < now)`; zero rows = someone else is hunting → the run is recorded as
  SKIPPED ("Another hunt of this strategy is already running") and `hunt.skipped_lock` is
  logged. The lock expires on its own after 2 hours if a worker dies.
* **Manual and scheduled use the same service.** `hunt.run_strategy` is the only hunt; the
  Run hunt button, the scheduler and the ops script all call it, and it takes the lock.
* **Idempotent by construction** (Phase 7): observations are unique per source reference,
  enrichment only runs for undecided / budget-freed properties, the owner-level contact cap
  stops a second conversation, and the budget is the atomic reservation.

## 4. Failure behaviour

The run row is committed the moment the hunt starts. If anything raises, the transaction is
rolled back, the run is marked **FAILED** with the error, `hunt.failed` is logged, the lock
is released, `consecutive_failures` increments, and the strategy is due again **one hour
later**. Work committed before the failure is kept and re-seen, not re-created: the test
forces a crash mid-hunt and proves the retry creates no duplicate property and charges no
owner twice. The Strategies screen shows "failed: … — retrying".

## 5. Kill switches, checked before and inside every hunt

| Switch | Scheduled hunts | Inbound replies |
|---|---|---|
| Pause EvoSense | not started (`hunt.skipped_paused`, logged once per state) | **still saved; opt-out / wrong-party still applied immediately**; anything else is saved and HELD, then read automatically after resume |
| Pause discovery | not started | unaffected |
| Pause paid data | hunt runs; discovery, scoring, cached/manual data continue; **no paid lookup** | unaffected |
| Pause SMS | outreach refused by eligibility | accepted and stored |
| Pause AI replies | — | rules read the reply; no model call; unreadable → a person reviews it |

Budgets (daily/monthly, org and strategy), provider availability, tenant scope and
`max_properties` per run are enforced inside the same hunt as before.

## 6. Command Center — the truth about automation

"What happens next" no longer mentions a script. It shows an automation strip —
**Automatic hunting** (Active / Paused / Manual only), **Inbound replies** (Active),
**Last seller reply**, **AI review pending**, **Routing review** (and **Held while
paused** when there are any) — and one line per strategy: *Next automatic hunt: DFW
Distressed SFR (TEST) — tomorrow · 4:50 PM · daily*, *hunting now*, or *manual only*.
When EvoSense or discovery is paused: **AUTOMATIC HUNTING PAUSED** and no time is shown.
Each strategy card shows AUTOMATIC HUNTING · LAST HUNT · NEXT HUNT · STATUS.

## 7. Inbound SMS → EvoSense

**The route (one path, the platform's).** `POST /sms/webhook/inbound` →
`guard_inbound` (Twilio signature + the account owns the receiving number) → receiving
number → **organization** → `process_inbound_sms(...)` (the webhook body, split out
unchanged so there is exactly one inbound path). Three additions, marked PHASE 7.1 in the code:

1. **Duplicate delivery.** A `MessageSid` already stored for a Reply in this organization
   creates nothing new (no second Reply, classification, pipeline run, DNC write) — it is
   only re-offered to EvoSense, whose routing is idempotent per Reply.
2. **Ownership before automation.** If the sender's Lead is in an EvoSense conversation,
   the platform AI pipeline does **not** auto-reply to them. Hard-stop / DNC / suppression
   handling is unchanged and applies to every message.
3. **Persist first, then EvoSense.** After the platform's commit, the persisted `Reply` is
   handed to `evosense.inbound.route_reply`. Any EvoSense failure is caught and logged;
   the message is already saved and Twilio still gets its 200.

**Message persistence order:** platform `Reply` committed → `EvoSenseMessage` created pointing
at it (`platform_ref`) and committed → only then is it read.

**How EvoSense knows the conversation is its own.** Not by phone alone:
receiving number → organization → sender → **Lead in that organization** → the EvoSense
**engagement whose `lead_id` is that Lead** (the Lead EvoSense created when it started working
the owner). Only that organization's engagements are considered.

* no engagement → not EvoSense's; untouched (a normal lead reply behaves exactly as before)
* exactly one, on the same Lead the platform chose → routed
* anything else → **ROUTING REVIEW REQUIRED**: saved, attached to nothing, shown in NEEDS YOU
  with one button per candidate property and "None of these"
  (`POST /wholesale/evosense/routing-reviews/{id}`).

**Automatic processing of a routed reply** (`conversation.evaluate`): hard stops first
(deterministic, never sent to a model, never paused) → outcome (17) → facts with the seller's
words, message id and truth state SELLER STATED → Seller Intent → nurture / suppression /
stop / NEEDS YOU → audit events → Command Center and Inbox. No button.

**AI failure.** When the rules cannot place a real reply and the AI reader fails, the message
stays saved with no outcome and no facts, a NEEDS YOU hand-off says **AI review pending — the
seller's reply is saved but could not be read automatically**, the property page shows the
seller's actual words with **Retry reading**, and every scheduler pass retries it. When the
reading succeeds the pending reason is removed (and the hand-off closed if that was its only
reason). A message with an outcome is never read twice.

**SANDBOX replies use the same path.** "Simulate a seller reply" on a SANDBOX property now
calls `process_inbound_sms` with the caller's organization — the same persistence, hard-stop,
DNC/suppression and EvoSense routing a Twilio webhook gets, minus only the signature (there is
no Twilio) and the number lookup. The review seed delivers its three replies this way.

## 8. Audit events

`hunt.queued · hunt.started · hunt.succeeded / partial · hunt.failed · hunt.skipped_paused ·
hunt.skipped_lock · reply.received · inbound.routed · inbound.routing_review ·
inbound.routing_resolved · reply.read (outcome, facts, Seller Intent) · reply.ai_failed ·
reply.held · reply.after_close · handoff.opened · nurture.set` plus the platform's own
suppression entry and `job_runs` row per pass. Scheduler "not due" passes write nothing.

## 9. Tests

```
NEW  tests/test_evosense_autonomy.py            18 passed  (+1 with PostgreSQL)
     primary journey (scheduler → … → signed webhook → NEEDS YOU, no offer) · loop ownership ·
     cadence manual/interval · wrong person · not now · duplicate webhook · AI failure + retry ·
     AI paused · pause (skip, STOP honoured, hold + read after resume) · pause paid data ·
     routing review · normal lead untouched · tenant isolation · held lock · two concurrent
     workers (SQLite + PostgreSQL 16) · failed hunt retried without duplicates · manual Run hunt
     shares the lock · Command Center truth
PHASE 7 EvoSense suites                         52 passed (53 with PostgreSQL) — unchanged
CROSS-TENANT                                    4 passed; attack list +2 routes
SMS / service role / backend availability       all passed
```

Full regression and browser results: §12.

## 10. Files changed

**New:** `app/services/evosense/scheduler.py`, `app/services/evosense/inbound.py`,
`tests/test_evosense_autonomy.py`, this report.

**EvoSense (Phase 7 files):** `app/models/evosense_models.py` (+ `EvoSenseHuntSchedule`),
`app/services/evosense/{hunt,conversation,handoff,views,sandbox_seed}.py`,
`app/routers/evosense_router.py` (cadence on create/edit, `retry-reading`, `routing-reviews/{id}`,
sandbox replies through the platform path), `frontend/src/pages/wholesale/evosense/{EvoCommand,
EvoProperty,EvoStrategies,EvoControls}.jsx` + `evosense.css` (surgical: automation strip,
routing review, pending banner, hunt status, cadence select, two switch descriptions),
`scripts/evosense_hunt.py` (now one scheduler pass by default), `scripts/seed_evosense_review.py`.

**Platform, additive:** `app/models/job_models.py`, `app/service_role.py`, `app/main.py`,
`app/routers/sms_router.py`, `tests/test_backend_availability.py`,
`tests/test_wholesale_cross_tenant.py`, `handoff/WHOLESALE_REAL_ESTATE_HANDOFF.md`.

## 11. Known gaps

1. **Local review runs no background loops.** The platform starts loops only in a process
   with `SERVICE_ROLE=backend` (fail-closed). The local `.env` has no `SERVICE_ROLE`, so the
   local server runs no loops — EvoSense's or anyone else's. In the review data the hunts
   were run by the scheduler pass itself (`run_due`, trigger `schedule`), and
   `python scripts\evosense_hunt.py` runs one pass on demand. On the deployed backend the loop
   runs every 15 minutes. See §13.
2. A paid provider call that succeeds and is then followed by a crash **before the commit**
   would be rolled back from the ledger (under-counted, never double-charged). With sandbox
   providers this cannot cost money; reserving in a separate committed transaction before the
   external call is P1 before a real vendor.
3. Duplicate protection for two copies of the same webhook arriving *at the same instant* relies
   on the MessageSid check (no unique index on `replies.twilio_sid`, a shared platform table).
4. Routing review lists come from the event log (no dedicated queue table).
5. Email/voice inbound are not wired (no EvoSense email/voice outreach exists).

## 12. Regression and browser results

```
FULL BACKEND REGRESSION (cloud, 6 workers, 55 min)   5,661 passed · 16 skipped · 3 failed
  - test_zoom_integration::test_requires_video_not_overwritten…   pre-existing (Phase 7 baseline)
  - test_wholesale_flow::test_a_real_property_runs_the_whole_way  xdist worker crash (process died);
                                                                 passes alone and in every slice run
  - test_evosense_autonomy::test_two_workers…[sqlite]             REAL BUG, FIXED — see below
AFTER THE FIX
  wholesale / evosense / budget / sms / service-role / backend-availability slice (cloud)  423 passed
  race test ×15 on SQLite + PostgreSQL 16, thread exceptions = errors                   30/30 passed
  Windows: autonomy + budget concurrency                                                 21 passed
  Windows: autonomy + journeys + api + budget + cross-tenant + sms + service-role +
           backend-availability                                                          137 passed
  Windows: -k "wholesale or evosense"                                                    360 passed
FRONTEND  node --test: 12 pass · 2 fail (pre-existing God Mode token tests, unchanged)
          vite build: clean
BROWSER   Windows Chromium, logged in as the autonomy review org: Command Center, Inbox,
          Strategies, Providers & Controls, flagship property, Wholesale Settings (+2 more)
          at 1550 / 1280 / 820 / 390 → 32 page×width checks · 0 horizontal overflow ·
          0 clipped text · 0 undersized targets · 0 page errors
```

**The bug the full regression found.** A brand-new strategy has no schedule row yet. When two
workers reached it at the same instant, both inserted one; the unique `strategy_id` let only
one succeed and the other worker crashed. The lock was never breached (no double hunt), but a
worker must not crash. `scheduler.schedule_for` now creates the row inside a savepoint and, on
the unique-constraint conflict, uses the row the other worker created — the same pattern the
budget counters already use.

## 13. Needs Mike's decision

* **Run the loops locally?** Setting `SERVICE_ROLE=backend` for the local server would start
  EvoSense's loop — and every other platform loop (AI conversation, cadence, review requests,
  sales reminders) against the local database. I did not do that on your machine.
* Production: the backend on Render already declares `SERVICE_ROLE=backend`, so deploying this
  code starts automatic hunting there — but no organization has sandbox providers enabled and
  no real vendor exists, so a real organization's hunt discovers nothing until a data stack is chosen.

## 14. P1 (not built)

Real property / skip-trace vendors (after Phase 7 is frozen) · commit the budget reservation
before an external paid call · unique index on inbound provider message id · inbound email/voice
into EvoSense · everything already listed as P1 in the Phase 7 report.
