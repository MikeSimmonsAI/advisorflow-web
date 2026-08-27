# AdvisorFlow — Performance + Scale Hardening

**Status:** complete. Priorities 1–6 and 8 done, measured and proven byte-identical.
Priority 7 (pagination) deliberately not done — see §7.

**Dataset:** 25 customers / 75 advisors / 25 implementations / 125 leads /
75 opportunities / ~150 sales appointments / 225 opportunity events.
Reproduce with `python scripts/perf_bench.py --scale 25`.

## Headline

| route | queries | median | cut |
|---|---|---|---|
| `/admin/dashboard/metrics` | 604 → **12** | 492.2 → **27.9 ms** | −98.0% |
| `/god/ops/sales-operations` | 370 → **30** | 262.9 → **42.3 ms** | −91.9% |
| `/admin/dashboard` | 305 → **9** | 172.0 → **16.1 ms** | −97.0% |
| `/god/ops/implementations` | 227 → **10** | 167.9 → **17.2 ms** | −95.6% |
| `/sales/my-day` | 157 → **18** | 151.8 → **30.2 ms** | −88.5% |
| `/sales/implementations` | 133 → **8** | 126.7 → **19.4 ms** | −94.0% |
| `/god/ops/customer-organizations` | 127 → **7** | 105.9 → **12.0 ms** | −94.5% |
| `/sales/opportunities` | 108 → **6** | 99.7 → **35.7 ms** | −94.4% |
| `/god/customers` | 77 → **5** | 72.4 → **14.6 ms** | −93.5% |
| `/god/ops/won-queue` | 3 → 3 | — | unchanged |
| `/god/platform/overview` | 4 → 4 | — | unchanged |

**2,108 → 105 queries across the nine optimised routes. All eleven responses
byte-identical to the pre-mission code. Zero regressions. 30/30 gates pass.**

Equivalence was proven by restoring all nine touched files to `bf81f4f` (the
deploy before this mission), capturing a fresh baseline, restoring the work, and
diffing the **full normalised response body** of every route — not by digest
alone.

---

## 0. The measurement had to be fixed three times before any result counted

This section is longer than the fixes because a benchmark that lies is worse
than no benchmark. Three separate defects would each have invalidated the
mission.

### 0.1 The fixture created no appointments — the #1 priority measured nothing

`my_day`'s cost is almost entirely appointment-driven. The original fixture built
customers, implementations, leads and opportunities — and **zero
`SalesAppointment` rows and zero `OpportunityEvent` rows**. Every appointment
list came back empty, every serializer short-circuited, and the route reported a
healthy **28 queries / 33.7 ms**.

The true figure, once the fixture held a realistic calendar (6 meetings today, 30
over the next fortnight, 30 in the past month, plus a brand-wide set the rep is
deliberately *not* on) and a real activity timeline, was **157 queries**. A
**5.6× undercount** on the highest-priority route.

### 0.2 The equivalence check fired on identical code

Run twice with no code change, the harness reported `OUTPUT CHANGED` on six of
eleven routes.

| cause | fix |
|---|---|
| Model `default=datetime.utcnow` fires at INSERT, so fixture rows carried wall-clock stamps that several routes return | `_freeze_row_stamps()` pins every `created_at`/`updated_at` to a fixed anchor after the build |
| A fixed noon-UTC anchor put "today's" appointments on the route's *tomorrow* (at 03:00 UTC it is still yesterday in Chicago) | the local day comes from the real clock; only row stamps use the anchor |
| `generated_at` — a wall-clock field the route stamps on its own response | excluded, explicitly and by name |
| uuid4 ids on the three brand `Platform` rows the app seeds at import | masked, list sorting switched to a stable key. The fixture's own ids are readable strings (`opp-007`), so this cannot hide a fixture row |

Nothing else is excluded. A field that differs for any other reason must fail the
comparison — that is the entire point of having one.

### 0.3 The baseline expired at local midnight

Mid-mission, three routes reported `OUTPUT CHANGED` and the dates in the diff had
moved from Aug 26 to Aug 27. The fixture places "today's" appointments using the
real local day — it must, because `my_day` resolves its own local day the same
way — so a baseline captured before local midnight describes a different calendar
than a run captured after it.

Proof it was the clock and not the code: the **untouched pre-mission code**, run
after the rollover, produced digest `ce419cfa6a042b0e` for `my_day` — the same
digest the *changed* code produced. Both differed from the previous day's
baseline. Identical output from both versions is a calendar, not a bug.

`--compare` now records the fixture's local day and refuses the equivalence
verdict across a day boundary, saying so loudly, instead of printing eleven
confident diffs that mean only that the clock moved.

---

## The one pattern behind almost every fix

Nine routes, one shape: **a serializer that costs N queries, called once per row,
sometimes over several overlapping lists.** The fix each time:

1. A prefetch class built **from the already-scoped result set** — so it can only
   contain rows the caller was already entitled to see, and widens nothing.
2. The serializer takes it as an *optional* argument. With it, no queries; without
   it, the original path is byte-for-byte untouched, so every other caller keeps
   working.
3. Where a serializer was called over overlapping lists, rows are memoised by id.
4. Where both a batched and a per-row path exist, they end at **one shared row
   builder** (`_advisor_row`, `_sales_projection_row`, `_completion_from`) so they
   cannot drift apart in shape.

---

## 1. `/sales/my-day` — 157 → 18

Four N+1 shapes compounding on one screen:

1. `_appt_brief` cost **three queries per call** (participants, meeting type, video
   row) and was called over **seven overlapping lists**. The same meeting paid for
   the same three rows up to four times. → 32 + 32 queries.
2. A local `kind(a)` helper re-read the meeting type, invoked three times per
   today's appointment on top of what `_appt_brief` already did. → 50 queries.
3. `recent_activity` resolved an actor name one query per row, 15 rows.
4. `_card` accepts a `names` map *specifically* so a board does not query per tile
   — and `my_day` was not passing one. Up to 36 cards, one query each.
5. `base.all()` re-executed the whole scoped opportunity query, loading every
   Opportunity a second time in full, to read one id off each.

Fixed with `ApptPrefetch` (3 queries for the whole set + memoised briefs),
`_name_map`, and `base.with_entities(Opportunity.id)`.

## 2. `_implementation_row` — `/god/ops/implementations` 227 → 10

Nine parent lookups plus two milestone counts per row. The won-queue then
re-serialised the same implementation across **six overlapping queues** — a
blocked, unowned, overdue one was built four times. `ImplPrefetch`: seven queries
for the whole list, rows memoised by id. This alone also took
`/god/ops/sales-operations` from 370 to 153.

## 3. God Sales Operations — 370 → 30

The remainder was `invite_state`, called once per implementation and costing a
query per admin inside — so a customer with three implementations was checked
three times. `invite_state_bulk` does every org in two queries. Ordered
oldest-first so the last write per user wins, which is the row `latest_for_user`
returned with its `DESC` + `first()`.

## 4. `_advisor_metrics` — `/admin/dashboard/metrics` 604 → 12

**Seven `COUNT(*)` per advisor.** At 75 advisors, 525 counts, plus one
`Organization` lookup per advisor on the god view — re-fetching the same org
three times for a 3-advisor customer.

Replaced with seven cohort-wide `GROUP BY` queries. **The group key is the pair
`(lead.organization_id, person)`, not the person alone** — the per-advisor version
scoped every count to that advisor's own organisation, and grouping on the person
would silently fold in another org's rows. That is the mistake this pattern
invites and it is guarded explicitly.

## 5. Indexes — the only change that helps Postgres specifically

Everything above cut *round trips*. Indexes cut the cost of each remaining one.
Each was verified missing against the model definitions, not guessed, and none
already existed under another name. They will **not** show up in a SQLite
benchmark at this scale and are not claimed to.

- **`users.organization_id` had no index at all** — the worst gap in the schema.
  Every tenant user list, advisor cohort, customer user count and invite check
  filters on it; on Postgres each was a sequential scan of the whole users table.
  Added, plus `(organization_id, role)` and `(organization_id, is_active)` since
  that filter rarely travels alone.
- **`leads.assigned_to_id`** — `ix_leads_org_advisor(organization_id,
  assigned_to_id)` exists, but a composite index cannot serve a query that does
  not filter on its **leading** column, and the master dashboard counts leads by
  advisor with no organisation predicate. That query could never use it.
- **`implementations.sold_by_user_id`** — `owner_user_id` is indexed, this is not,
  and a rep's own `/sales/implementations` filters on exactly this column.

## 6. Sales membership memoization

`sales_memberships()` was re-read independently by `sales_org_ids`,
`is_sales_manager` and `require_sales_member` — four reads per request of rows
that cannot change while the request is being assembled. Memoised on the
request's own `User` instance, which `get_current_user` builds fresh per request,
so the cache lives exactly as long as the request and cannot leak between users
or outlast another request's membership change. `invalidate_sales_memberships()`
exists and is called where a membership is granted, because a cache with no way
to clear it is a bug waiting for the one caller that needs it.

## 7. Pagination — deliberately NOT done

`/sales/opportunities` was queued for pagination because it was slow. It is now
**6 queries and 35.7 ms**. Pagination changes the response shape and needs a
frontend pass, so it is now a scale decision for 10,000+ opportunities, not a
performance fix. Recommended as its own change, with the frontend, when a brand's
pipeline actually approaches that size.

## 8. Remaining routes

`/sales/implementations` (six lookups per row), `/god/ops/customer-organizations`
(five) and `/god/customers` (three, one of which loaded every user row of every
customer to count four things) all took the same prefetch treatment.

One care point: `sales_projection`'s milestone rows feed `required_open` and
`blocked`, which are **ordered lists** in the response. The batched fetch orders
by `(position, created_at)` — exactly what the per-row `milestones()` did — then
groups. Getting that wrong produces no error, just a differently-ordered list
nobody notices for a month.

---

## Boundaries — unchanged

No tenant, brand, role or permission check was relaxed for speed. Every prefetch
is built from an **already-scoped** result set, so none can widen what a caller
sees. Full gate suite **30/30**, including Gates 23–30: platform boundary,
brand-owner boundary, platform owner neutrality, provisioning, cleanup, cleanup
receipt, tenant isolation.

## Scale outlook and Render thresholds

At 25 customers the app now issues **105 queries** where it issued 2,108. The
remaining counts are flat or near-flat in dataset size rather than linear, which
is the change that matters: the old figures grew with every customer added.

Recommended review points:

- **~150 customers / 450 advisors** — re-run `perf_bench --scale 150`. The routes
  above should stay in the tens of queries; anything that has grown linearly again
  is a new N+1 and should be found before it is felt.
- **Render instance upgrade** — the bottleneck is now round-trip latency and
  Postgres connection count, not query count. Upgrade when sustained p95 on
  `/admin/dashboard/metrics` exceeds ~400 ms on production data, or when
  connection-pool saturation appears in Render metrics — not on a query-count
  trigger, which no longer predicts anything.
- **Postgres**: after deploying, confirm the four new indexes exist
  (`\di ix_users_organization_id` and friends). `CREATE INDEX IF NOT EXISTS` is
  idempotent but silent, so verify rather than assume.

## Known issues found but not fixed here

- **Participant order is unspecified.** Neither the old nor the new code orders
  the participant read. It happens to be insertion order on SQLite and is not
  guaranteed on Postgres. Adding an `ORDER BY` reorders every `participants` list
  in the response — a real behaviour change — so it belongs in its own change with
  the frontend checked. *(This was caught during the mission: an `ORDER BY` added
  for tidiness changed the output, was detected by the byte diff, and was backed
  out.)*
- **`auto_migrate` emits ~16 SQL syntax errors on SQLite at every boot.** They are
  Postgres-only DDL (`ADD COLUMN IF NOT EXISTS`, `BYTEA`, `NOW()`) caught and
  logged. Harmless in production, but they make every local run and gate log noisy
  enough to hide a real error.
- **The gate suite is serial** — 31 gates, ~6 minutes, each paying ~14s to import
  the app before its first assertion. A process pool would take it to roughly one
  minute. Not in scope here, but it is the single biggest drag on iteration speed.
