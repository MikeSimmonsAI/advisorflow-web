# Calendar + Sales Workspace Scheduling — session log / pass-down

**Branch:** `feat/sales-calendar-scheduling`
**Worktree:** `C:\Dev\advisorflow-calendar-scheduling` (based on `origin/main` @ `b2ec346`)
**Date:** 11 September 2026
**Scope:** the two existing Sales Workspace routes `/sales/calendar` and
`/sales/team`, plus the scheduling engine behind them.

This is the BACK END / APP record. The customer-facing website is a separate
entity and is documented separately; nothing in this work touches it.

---

## 1. What this was, and what it turned out to be

The brief read as a redesign of two screens. It was mostly not that.

The scheduling engine underneath was already strong and had been for several
checkpoints: `app/services/availability.py` computes a genuine participant
INTERSECTION (not a union), resolves recurring rules per local date so DST
comes out right, and subtracts work hours, lunch, PTO, blocked time, buffers,
minimum notice and the booking horizon. `sales_appointments` was already the
single authoritative record with per-participant provider event ids, reschedule
already MOVED the row rather than cancel-and-recreate, and Postgres already
carried a `gist` exclusion constraint on participant overlap that settles the
concurrent double-book race at the database.

So the work split into four real gaps and two screens.

---

## 2. The four gaps that were closed

### 2.1 `refresh_many` was dead code

`app/services/external_busy.py` is deliberately split in two halves:
`refresh_external_busy` talks to a provider and may fail; `external_busy_intervals`
only reads the cache and cannot fail. The availability engine calls ONLY the
second one, which is what keeps a four-person search from becoming four vendor
round trips.

The cost of that split is that an unrefreshed cache does not make the engine
slow — it makes it WRONG, and wrong in the most expensive direction: it reports
somebody free during a meeting it has not heard about.

`refresh_many` existed for exactly this and **was never called from any sales
surface**. The only thing that populated the cache was a user clicking "test
connection". Team Availability and Find Team Time were answering from whatever
the cache happened to hold, which for most users was nothing.

Now: `_refresh_external()` in `sales_scheduling_router.py` is called by
`team_availability`, `find_team_time` and `calendar_view`, and
`create_appointment` / `reschedule_appointment` force-refresh and re-check at
commit. Never fatal — a provider outage degrades the answer and says so.

### 2.2 Appointment outcome did not exist

T9 Intelligence reported appointment completion as UNKNOWN, and it was right
to. `status` recorded what the calendar INTENDED; nothing recorded what
happened. The only way to turn that into "it happened" was to compare
`starts_at` to `now()`, which counts every meeting nobody attended.

New: `app/services/appointment_outcome.py`, nine outcomes, plus columns on
`sales_appointments` (`outcome`, `outcome_notes`, `outcome_recorded_at`,
`outcome_recorded_by`, `occurred`, `completed_at`, `followup_appointment_id`).

Design points that matter:

- **NULL is a real answer.** No default, no back-fill, no job that marks old
  meetings complete. Back-filling would be fabricating evidence: defaulting to
  `completed` invents meetings, defaulting to `no_show` accuses prospects who
  did attend.
- **`status` and `outcome` can never contradict each other** —
  `OUTCOME_TO_STATUS` settles both from one value.
- **A meeting that did not happen releases everybody's time** (`is_blocking`
  off), and `completed_at` stays NULL on a no-show.
- **A future meeting cannot be marked completed.** Only a cancellation or a
  reschedule can be recorded before it starts.
- **Stage moves are opt-in and separately authorised.** `advance_stage` must be
  asked for AND the caller must hold edit access to the deal. A stage that
  moves as a side effect is how every "Won" in a pipeline becomes suspect.
- **The pending-outcome queue is the mechanism.** An optional field gets filled
  in when somebody feels like it, and the resulting dataset is worse than none
  because its gaps are invisible. `GET /sales/appointments/pending-outcome`
  turns "unrecorded" into a number a manager can drive to zero.

`GET /sales/appointments/completion-facts` is the contract with T9. It reports
`unrecorded` as its own figure and returns `completion_rate: null` — not zero —
when nothing has been recorded, because zero is a claim and null is the truth.
It also states `authoritative: true` and `inferred_from_clock: false` so no
consumer can mistake it for an estimate.

### 2.3 External edits were invisible in one direction

The old behaviour was asymmetric in a way that quietly lost information:

- a provider event DELETED upstream was silently recreated on the next update
  (right outcome, no record);
- a provider event MOVED upstream was **never noticed at all** — Outlook said
  Thursday, EvoSys said Tuesday, both were confident.

New: `app/services/appointment_reconcile.py`, plus `get_event()` on the
provider interface (base / microsoft / google; the .ics fallback correctly
reports `supports_read_back() == False` because it has no calendar to query).

The key design decision: **drift is measured against `pushed_starts_at`, not
against the appointment's live time.** Compared against the live time, an
in-flight reschedule whose push has not completed looks exactly like somebody
editing Outlook, and the reconciler raises conflicts against its own work.

Classification, and only the deterministic classes are healed:

| class | healed? | why |
|---|---|---|
| `provider_deleted` | yes | a deleted event carries no information an overwrite would destroy |
| `provider_changed` | yes | EvoSys's wording is authoritative, no commitment at stake |
| `provider_moved` | **no** | the likeliest reason somebody moved it is that they agreed the new time with the prospect; restoring ours puts the rep back in a meeting the customer has left |
| `provider_orphaned` | **no** | a stored event id is not proof of ownership; acting on an unprovable id is a cross-tenant calendar write waiting to happen |

An unreachable provider returns `unknown` and claims NOTHING. A reconciler that
reported a conflict every time Microsoft had a bad minute would train everyone
to ignore conflicts.

Adopting the provider's new time is deliberately NOT a one-click action — that
is a reschedule, which has to re-check every other participant, re-push every
calendar, reset the prospect's confirmation and write the deal timeline. The
conflict payload points at the reschedule flow instead.

### 2.4 The two screens were agenda lists

`TeamCalendar.jsx` was 310 lines of grouped list; `TeamAvailability.jsx` a
322-line static day grid that drew only free time and meetings.

Rebuilt to the approved targets. New `calendarTime.js` holds all the time and
geometry so the two screens cannot disagree about placement. New
`BookAppointment.jsx` and `OutcomeDialog.jsx`.

---

## 3. Three bugs found during verification

These are the ones worth remembering, because none of them had a symptom.

### 3.1 A dead calendar grant was reported as an empty calendar

**Three individually correct behaviours composing into a lie.**

1. `resolve_provider_key` returns `"microsoft"` whenever a live connection row
   exists.
2. `get_provider` is allowed to FALL BACK to the .ics provider when Microsoft
   reports itself unready — a revoked consent, a dead token.
3. `IcsEmailProvider.get_busy` correctly returns `([], None)` — "there is no
   external calendar to read", which is true for somebody who never connected
   one.

Composed, a dead Microsoft grant became a **successful read of an empty Outlook
calendar**. That deleted every cached busy block for the user, stamped the
connection healthy (`last_sync_at` bumped, `failure_count` reset,
`calendar_scope_ok` set) so the ten-minute freshness check then suppressed
retries, and reported `external_checked: true` to the availability grid.

Net effect: somebody whose Outlook token had died showed as **verified-free**,
and would have been booked over a meeting nobody could see.

Fix: `refresh_external_busy` now checks that the provider it got back is the
one it asked for, reports `provider_unavailable` with `needs_reauth`, and
**leaves the cache alone**. Regression test:
`test_a_dead_grant_is_never_reported_as_an_empty_calendar`.

### 3.2 A dead grant then read as "no external calendar connected"

Found in the visual audit, and a sibling of 3.1. Once `calendar_scope_ok` is
cleared, `_live_connections` filters the row out and the resolver returns .ics —
the same answer it gives someone who never connected a calendar. So a rep whose
grant had died was told they had no calendar rather than that they needed to
reconnect, which hides the one action that fixes it.

Fix: before concluding `no_external_calendar`, look for a connection row that
was once connected. If there is one, report `reauth_required` with the stored
error. The team roll-up now separates "2 need reconnecting" from "3 not
connected".

### 3.3 Multi-day PTO lost its middle days

Both grids asked "does this span START or END on this day". A Wednesday-to-
Friday absence therefore drew on Wednesday and Friday and left **Thursday
looking blank** — and blank space in an availability grid reads as bookable.

Fix: `spanTouchesDay()` (a real overlap test, with an end-at-midnight rule so a
block "until Friday 00:00" does not paint an empty Friday) plus `clampToDay()`
so a span that began yesterday starts at the top of the column.

Also fixed in the same pass: the header read `"Sep 6 – 2026 (day: 12)"`.
`{day:'numeric', year:'numeric'}` is a combination no locale has a pattern for,
and en-US answers it with literally that. Ask a locale only for combinations it
actually formats.

---

## 4. API surface added

```
GET  /sales/calendar/view                       day/week/month/agenda, one request
GET  /sales/calendar/sync-status                provider health with a reason and an action
GET  /sales/calendar/conflicts                  drift awaiting review (manager)
GET  /sales/appointments/pending-outcome        the queue that makes the data authoritative
GET  /sales/appointments/completion-facts       the T9 contract (manager)
GET  /sales/appointments/{id}/outcome-options   what applies to THIS meeting, and why not
POST /sales/appointments/{id}/outcome           record what happened
POST /sales/appointments/{id}/reconcile         check every participant's calendar copy
POST /sales/appointments/{id}/resolve-conflict  apply a human's decision (manager)
```

**Route order is load-bearing.** `/appointments/pending-outcome` and
`/appointments/completion-facts` are declared BEFORE `/appointments/{appt_id}`.
Declared after, they resolve as `appt_id="pending-outcome"` and 404. Guarded by
`test_pending_outcome_route_is_not_swallowed_by_the_id_route`.

---

## 5. Privacy rules, and where they live

- A meeting shows its title only to somebody on it. The SERVER sends the
  literal string `"Busy"` rather than a title the viewer may not read, so the
  rule cannot be undone in the browser. Even the meeting's TYPE is withheld —
  the block reads `kind: "blocked"`, because telling a rep which of a
  colleague's meetings are customer-facing lets them infer a deal.
- External busy carries the interval and NOTHING else. The cache has no subject
  column, so there is nothing to leak even by accident. The only available
  wording is `"Busy — external calendar"`, set server-side.
- `ExternalEventState` (the drift-detection type) deliberately has no attendee
  list and no body. A reconciliation pass runs over every participant's
  calendar, so anything that type could hold is something the reconciler would
  log about people who never consented.
- Conflict review is manager-only: a conflict names a specific person's
  calendar and how it disagrees with ours.

---

## 6. Tests

115 new tests in three files:

- `tests/test_calendar_scheduling_smoke.py` (13) — the new routes are reachable
  and return the shape the screens read, including on an EMPTY brand, which is
  the state every new brand starts in.
- `tests/test_calendar_scheduling.py` (60) — intersection, work hours, lunch,
  PTO, buffers, notice, external busy, the outcome lifecycle, reconciliation,
  concurrency, DST (spring forward AND fall back), cross-zone participants,
  privacy, tenancy, and the mobile contract.
- `tests/test_calendar_scheduling_attack.py` (42) — suspended and removed
  users, replayed confirmations, revoked and expired tokens, reschedule-after-
  cancel, double cancellation, outcome corrections, all-day events, unknown
  timezones, URL guessing, a parameterised sweep proving EVERY route taking an
  appointment id refuses another brand's, and a realistic-week scale test.

Provider behaviour is exercised through a fake registered in the provider
registry's own `register_provider` seam — no production code carries an
`if testing:` branch, and nothing in the suite can reach Microsoft or Google.

---

## 7. Deliberate deviation from the approved design — NEEDS MIKE'S CALL

Image 2 shows a **light left rail with a top app bar** (logo, global search,
LIVE pill, notifications, user menu). The Sales Workspace currently has a **dark
navy rail and no top bar**, and that rail is the approved prototype's own design
(`SalesStyles.jsx`: "the rail is already dark in light mode, where it is the one
dark element on a pale page"), shared by all ~15 sales screens through
`SalesShell.jsx`.

It was left alone, because changing it is a workspace-wide redesign and the
brief said not to redesign unrelated pages. Every capability in the brief's
required list for Screen 1 is present.

If the light rail and top bar are wanted, that is its own piece of work.

---

## 8. Notes for whoever picks this up

- **`build_frontend.bat` hardcodes `C:\Dev\advisorflow-web\frontend`.** Run
  from a linked worktree it builds the WRONG tree. Use
  `cd frontend && npm run build` inside the worktree instead.
- **`npm install` in a fresh worktree needs `--include=dev`** on this machine —
  a plain `npm install` skipped vite and the build failed with
  "Cannot find module .../vite/bin/vite.js".
- **`deploy.ps1` cannot run from a linked worktree.** Step 2 does
  `git checkout main`, which fails because main is checked out in
  `C:\Dev\advisorflow-web`. Merge the branch in the main worktree, then deploy
  from there.
- **The deployed frontend is the COMMITTED `frontend/dist`**, built and
  force-staged by `deploy.ps1` step 4/5 — not built on Render, despite what
  `render.yaml`'s frontend service block implies. Do not hand-commit `dist`.
- **The root `dist/` directory is legacy** and is not the deploy path.
- **SalesStyles.jsx is one JS template literal.** A backtick anywhere in a CSS
  comment ends the string and the build fails several hundred lines later with a
  syntax error pointing at prose. Cost one build to learn.
- `datetime.utcnow()` is used throughout, matching the existing codebase
  convention. The deprecation warnings are pre-existing and were not "fixed"
  here — changing the time convention mid-feature is its own change.
