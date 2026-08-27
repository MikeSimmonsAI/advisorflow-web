# AdvisorFlow — Performance + Scale Hardening

**Status:** priorities 1 and 6 complete, measured, deployed. Priorities 2–5, 7, 8 queued.
**Dataset for every number below:** 25 customers / 75 advisors / 25 implementations /
125 leads / 75 opportunities / ~150 sales appointments / 225 opportunity events.
Reproduce with `python scripts/perf_bench.py --scale 25`.

---

## 0. The measurement had to be fixed before any optimisation was allowed to count

Two defects in the benchmark would each have invalidated the entire mission.

### 0.1 The fixture created no appointments, so the #1 priority measured nothing

`my_day`'s cost is almost entirely appointment-driven. The original fixture built
customers, implementations, leads and opportunities — and **zero `SalesAppointment`
rows and zero `OpportunityEvent` rows**. Every appointment list in the response came
back empty, every serializer short-circuited, and the route reported a healthy
**28 queries / 33.7 ms**.

The true figure, once the fixture contained a realistic calendar (6 meetings today,
30 over the next fortnight, 30 in the past month, plus a brand-wide set the rep is
deliberately not on) and a real activity timeline, was **157 queries / 123.4 ms** — a
**5.6× undercount**. Optimising against the old number would have produced a
confident report about a route nobody had measured.

### 0.2 The equivalence check fired on identical code

The harness compares a digest of each response so a change in behaviour cannot hide
behind a change in speed. Run twice with **no code change at all**, it reported
`OUTPUT CHANGED` on six of eleven routes. Causes, in order of discovery:

| cause | fix |
|---|---|
| Model `default=datetime.utcnow` fires at INSERT, so fixture rows carried wall-clock stamps that several routes return | `_freeze_row_stamps()` pins every `created_at` / `updated_at` to a fixed anchor after the build |
| A fixed noon-UTC anchor put "today's" appointments on the route's *tomorrow* (at 03:00 UTC it is still yesterday in Chicago) | the local day comes from the real clock; only the row stamps use the anchor |
| `generated_at` — a wall-clock field the route stamps on its own response | excluded, explicitly and by name |
| uuid4 ids on the three brand `Platform` rows the app seeds at import | masked, and list sorting switched to a stable key. The fixture's own ids are readable strings (`opp-007`), so this cannot hide a fixture row |

After the fix a no-op run reports **11/11 SAME OUTPUT, 0 regressions**. That
verification is now the precondition for trusting any before/after in this document.

Every result below is additionally confirmed by a **byte-for-byte diff** of the full
normalised response body (`--dump`), not by digest alone.

---

## 1. `/sales/my-day` — 157 → 18 queries, 123.4 → 27.4 ms

**Bottleneck.** Four separate N+1 shapes compounding on one screen.

**Root cause.**

1. `_appt_brief` cost **three queries per call** (participants join, meeting type,
   video row) and was called over **seven overlapping lists** — today's, next,
   unconfirmed, discoveries, demos, upcoming, closing. The same meeting paid for the
   same three rows up to four times. → 32 participant + 32 video queries.
2. A local `kind(a)` helper re-read the appointment's `MeetingType` and was invoked
   three times per today's appointment, on top of the read `_appt_brief` already did.
   → 50 meeting-type queries.
3. `recent_activity` resolved an actor name **one query per row**, 15 rows.
4. `_card` accepts a `names` map *specifically* so a board does not query per tile —
   and `my_day` was not passing one. Up to 36 cards, one query each.
5. `base.all()` re-executed the whole scoped opportunity query, loading every
   Opportunity a second time in full, to read one id column off each.

**Fix.**

- `ApptPrefetch` — resolves participants, meeting types and video rows for the entire
  appointment set in **three queries**, and memoises each appointment's brief so a
  meeting appearing in four lists is serialised once.
- `_appt_brief(db, a, pre=None)` — with a prefetch it issues **no queries**; without
  one the original three-query path is untouched, so every other caller is unchanged.
- `_name_map(db, ids)` — batch name resolution; used for both the activity feed and
  the card owners.
- `base.with_entities(Opportunity.id)` instead of `base.all()`.

**Deliberately not done.** No `ORDER BY` was added to the participant read. Adding
one — even a sensible one — reordered every `participants` list in the response. That
is a behaviour change and this refactor is not the place for it. *Participant order
being unspecified at all is a real latent issue in both the old and new code; it is
flagged in §9 rather than smuggled in here.*

**Measured.**

| | before | after | change |
|---|---|---|---|
| queries | 157 | **18** | −88.5% |
| median | 123.4 ms | **27.4 ms** | −77.8% |
| top repeat | 50× meeting-type | 3× appointment | — |
| response body | — | — | **byte-identical** |

Per-shape elimination: meeting-type 50→0, participants 32→0, video rows 32→0,
user lookups 28→2.

---

## 6. Sales membership memoization — free, and it moved two other routes

`sales_memberships()` was re-read by `sales_org_ids`, `is_sales_manager` and
`require_sales_member` independently. A single request asked four times for a handful
of rows that cannot change while it is being assembled.

Memoised on the request's own `User` instance — `get_current_user` builds a fresh one
per request, so the cache lives exactly as long as the request and cannot leak between
users or outlast another request's membership change. `invalidate_sales_memberships()`
exists and is called where a membership is granted, because a cache with no way to
clear it is a bug waiting for the one caller that needs it.

| route | before | after |
|---|---|---|
| `/sales/opportunities` | 108 | 105 |
| `/sales/implementations` | 133 | 129 |

Both byte-identical.

---

## Boundaries — unchanged

No tenant, brand, role or permission check was relaxed for speed. `ApptPrefetch` is
built **from the already-scoped result** of `_visible_sales_appointments`, so it can
only ever contain appointments the caller was already entitled to see; it widens
nothing. Full gate suite: **30/30 pass**, including Gates 23–30 (platform boundary,
brand-owner boundary, platform owner, provisioning, cleanup, cleanup receipt, tenant
isolation).

---

## Still open

| # | target | current | note |
|---|---|---|---|
| 2 | `_implementation_row` | 227 q | 1+9N — nine per-row lookups plus two milestone counts. Same prefetch pattern applies. |
| 3 | God Sales Operations | 370 q | Loads full datasets to produce counts; duplicate implementation serialization. |
| 4 | `_advisor_metrics` | 604 q | `/admin/dashboard/metrics`, the worst route in the system. Needs cohort-wide grouped queries. |
| 5 | users/index audit | — | Verify actual indexes first; requires a migration. |
| 7 | `/sales/opportunities` pagination | 105 q | Changes the API shape — needs a frontend pass with it. |
| 8 | remaining N+1 sweep | — | `/admin/dashboard` 305, `/god/ops/customer-organizations` 127, `/god/customers` 77. |

## 9. Known issues found but not fixed here

- **Participant order is unspecified.** Neither version orders the participant read.
  It happens to be insertion order on SQLite and is not guaranteed on Postgres. Fixing
  it is a deliberate behaviour change (required-first is the sensible order) and
  belongs in its own change with the frontend checked.
- **`auto_migrate` emits ~16 SQL syntax errors on SQLite at every boot.** They are
  Postgres-only DDL (`ADD COLUMN IF NOT EXISTS`, `BYTEA`, `NOW()`) caught and logged.
  Harmless in production, but they make every local run and gate log noisy enough to
  hide a real error.
