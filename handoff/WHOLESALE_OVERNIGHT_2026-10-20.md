# Wholesale overnight: owner report + engineering handoff (2026-10-20)

## OWNER REPORT (one page)

**Bottom line.** Nothing in production was changed overnight. Nothing was sent, charged or merged. One real
problem is waiting on you: **#245**, the Tarrant tax-roll misread. Everything else is ready for review, in order.

**What needs your GO, in this order**
1. **#245 — Tarrant tax-roll fix** (separate from #242). Since Oct 17 the county widened its file, and our reader
   misread it. Result: 1,017 properties carry 2,034 false tax signals, and 66 of them falsely score 45+.
   - Nothing was sent or charged because of it.
   - Fix: merge and deploy #245, then run the targeted repair (dry run first, then apply with the code it gives).
   - Expected result: 2,022 false signals removed, the 12 real delinquencies kept, 66 → 0 at 45+.
   - The repair has an exact undo.
   - If #245 can't be live before **8:34 AM CT** (the next hunt), switch off only the *Tarrant County tax roll*
     source (EvoSense › Controls › Sources). Every other source and search keeps running; New Check Out is untouched.
   - Steps: `handoff/TARRANT_ROLL_INCIDENT_2026-10-20.md`.
2. **#242 — Wholesale release** (website buyer form → EvoSys Buyers, better owner names, better seller-reply reading). Ready.
3. Then the three drafts below, each after #242.

**What got better (drafts, nothing live)**
- **#247 Act-first queue.** Every property now shows one plain next step: evidence → owner → contact →
  conversation → qualified → deal. There is a ranked "act first" list and a funnel.
  - Properties worth working that have no phone or email become a "find contact" work item (max 25 a day).
  - Title-flag items no longer flood the queue (it had 1,155).
  - Includes a safe repair for 513 cut-off owner names: 450 can be fixed automatically, the rest are left for a person.
- **#248 Seller-reply reading.** 11 test seller journeys (inherited house, repairs, deadline, buyer match / no match,
  not interested, STOP, wrong person, two prices, price above every buyer).
  - These found and fixed a real flaw: "owned it for 30 years" was read as a **$30,000 asking price**, and a phone
    number as $817,000.
  - No real data was affected (production has no seller replies yet).
- **#249 Wholesale website.** Buyer form tested on desktop and iPhone.
  - Fixed the consent checkbox, which showed as a big empty box.
  - Registrations made while the platform is down can now be re-sent safely.

**Not done / honest gaps**
- Paid owner-contact lookup: prepared, **not switched on** (see the engineering notes; it needs your budget).
- Collin/Denton: these two counties are about a quarter of everything found, but none of it can score 45+, because we have no
  distress source there yet. A proposal is in the engineering notes; nothing was changed.
- Appointments are not tracked as a funnel step yet; the funnel shows them as "unavailable".
- Website still needs your hosting/DNS steps before it is live.

**Decisions only you can make:** GO on #245 (and the optional pause), GO on #242, a paid-lookup budget if you
want contact lookups, and hosting access for wholesale.evosyspro.live.

---

## ENGINEERING HANDOFF

### PRs (all lane-pushed branches; none merged)
| PR | Branch | Base | Head | State |
|---|---|---|---|---|
| #242 | feature/wholesale-development | main | 4941839 (+ this doc) | ready; main aee2e14 merged in |
| #245 | feature/wholesale-tarrant-roll-layout | main | 7eb429e | ready; independent of #242 |
| #247 | feature/wholesale-opportunity-queue | #242 | b51bde6 | draft; contains #245 merged |
| #248 | feature/wholesale-journeys | #242 | b228e56 | draft |
| #249 | feature/wholesale-site-checks | #242 | f176ca2 | draft |

Merge order: #245 → main and #242 → main (independent), then rebase/retarget #247/#248/#249 onto main.
#247's diff shrinks once #245 is on main. A merge slip (revert route lost `commit/return`) was caught by tests and fixed in b51bde6.

### Tests (2 CPUs / 7 GB: never above `-n 2`)
`python -m pytest -p no:cacheprovider -o addopts="" -n 2 tests/test_evosense*.py tests/test_wholesale*.py`
- #248 branch: 812 passed.
- #247 branch: 372 focused passed.
- #245 is covered by tests/test_evosense_tarrant_roll_layout.py and tests/test_evosense_roll_layout_fix.py.

### Production state (read-only SELECTs, 01:25 CT)
- All live strategies: `daily_budget_cents = 0`, `pilot_allow_paid = false`.
- `evosense_cost_ledger` is empty: **$0 spent ever**.
- `paused_paid_data = false` and the org daily/monthly ceilings are NULL. The $0 strategy budgets are the only
  thing holding spend at zero.
- `evosense_facts`: 0 rows (no seller replies recorded yet).

### Paid owner-contact lookup pilot: prepared, NOT activated
The existing controls are enough; no code is needed. To run one pilot (each step is Mike's GO):
1. Set org ceilings first: `org_daily_budget_cents` and `org_monthly_budget_cents` (Controls → Budget). Today they are
   NULL, so a strategy budget change alone would be the only limit.
2. On one strategy:
   - `pilot_mode = true`, `pilot_max_properties` ≤ hard cap, `pilot_allow_paid = true`
   - `pilot_max_spend_cents` = the approved total
   - `max_cost_per_property_cents` set
3. Only properties that pass `readiness.worth_working` and have a `verify_contact` item (#247) should be looked up.
   Enrichment-request dedupe already prevents paying twice for one owner.
4. Kill switch: `paused_paid_data = true`, effective on the next action. Spend is visible in `evosense_cost_ledger`.

### Open follow-ups
- After the #245 repair, re-check the 147 open work items on the 1,017 affected properties; most will be stale.
- #248 reader: one reply with two prices keeps the later one and opens a PRICE_UNCLEAR hand-off. Revisit if real
  replies show the earlier figure is usually the real one.
- Appointments funnel step: needs a source of truth (wholesale_appointments) wired into readiness.funnel.

### P4 — Collin/Denton search focus (proposal only; nothing changed)
Production (read-only, 01:35 CT):
- Collin has **1,415** properties and Denton **972** (26% of 9,032). None is at 45+; the best scores are 33 and 37.
- Their only active signals are derived ones: LONG_OWNERSHIP, ABSENTEE_OWNER, OUT_OF_STATE_OWNER, plus 15 closed
  code-history items.
- We have no tax-delinquency, tax-sale or code-violation source for either county. LGBS lists both, but returned
  0 rows for them; it covers Dallas (392) and Tarrant (365).
- Behind on Taxes and Tired Landlord both include Collin and Denton. New Check Out has no county filter (all).

Options:
1. **Add a distress source** (recommended next build).
   - Denton: the county's Sheriff's Sale page lists the monthly tax-sale properties (first Tuesday)
     — <https://www.dentoncounty.gov/840/Sheriffs-Sale>, linked from <https://www.dentoncounty.gov/867/Delinquent-Tax-Sales>.
   - Collin: the official tax-sale publisher still needs confirming (the county tax office, 972-547-5020).
   - Format and terms of use need checking before writing an adapter.
2. **Until then, opt-in per strategy:** only keep Collin/Denton properties that carry a distress signal, so hunts
   stop filling the list with records that cannot qualify. This is Mike's choice per strategy. It must not edit
   New Check Out.

### Local scratch (not in git; contains production-derived data, never posted)
`/tmp/claude-0/-home-claude/e5e1c130-63b8-5ad7-bd8c-bfb704613d62/scratchpad/` contains:
- `prodcopy/`: the read-only production copy and the roll-fix dry-run report
- `pw/`: site-check run and screenshots

### Next command if resuming
`git checkout feature/wholesale-opportunity-queue && python -m pytest -p no:cacheprovider -o addopts="" -n 2 tests/test_wholesale_opportunity_queue.py`
— then the Collin/Denton proposal, or the #245 production steps once Mike says GO.
