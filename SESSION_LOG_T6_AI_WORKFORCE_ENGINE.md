# T6 — AI WORKFORCE ENGINE

**Branch:** `feat/ai-workforce-engine` · **Worktree:** `C:\Dev\advisorflow-ai-workforce`
**Cut from:** `origin/main` @ `aababc6`
**State on delivery:** built, proven synthetically, reconciled with T7, merged, deployed, fully dark.
No real outreach occurred and none is reachable without deliberate, separate changes at four
independent layers.

**Shipped:** `0c6f442` (T6 + the reconciliation with T7) and `1462c3b` (deployment
assertions, tests only). Production serves `0c6f442` — `1462c3b` touches only `tests/`,
which every Render build filter ignores by design, so no service rebuilt for it.

| Check | Result |
|---|---|
| T6 suite | **141 passed** |
| Simulator | **85 / 85 scenarios** across 22 dimensions |
| T6 + T7 suites together | **226 passed** |
| Full backend regression (reconciled tree) | **3177 passed, 14 skipped, 0 failed** |
| Live verification against production | **33 / 33** |
| Frontend build | clean, 303 modules, both routes present in the served bundle |
| Synthetic scale run | 5,000 leads · 4 employees · 1,600 runs · 1,280 simulated sends · **0 real sends** |
| Real outreach | **none** — no SMS, no email, no voice call, no appointment, no customer pipeline mutation |

---

## 1. WHAT T6 IS

One engine at the AdvisorFlow platform layer that runs every AI employee. Eleven reusable
jobs, not eleven bots. Configured per brand and per customer; built once and used
everywhere.

It decides **who** an AI employee is, **what** it may do, and **whether** it may run. It is
not a chatbot, not a campaign tool, and not a second control plane: God Mode remains root,
and `/god/workforce` is `require_god` like the rest of the control plane rather than a new
kind of administrator.

## 2. WHAT THE MODEL CAN AND CANNOT DO

The model has no database, no shell, no HTTP and no SQL. Every action available to any AI
employee is one of **28 registered tools**, and every call passes thirteen ordered gates in
`app/services/workforce/tools.py` before anything happens: tenant scope, employee identity,
authority, entitlement, brand and channel policy, eligibility, argument schema, record
existence and ownership, business rules, rate limits, idempotency, audit.

A tool the model asks for is not a tool it may use. An instruction inside a lead's reply
saying otherwise is data, not authority — `memory.py` fences untrusted text and the
adversarial scenarios attack exactly that seam.

## 3. WHY NOTHING CAN REACH A PERSON

Four independent locks. Any one of them is sufficient.

1. **Activation** resolves to the MINIMUM across platform → brand → customer → employee, and
   a scope with no row is `off` rather than inherited. Only `controlled` and `active` may
   execute. The platform row seeds `off`.
2. **Entitlement.** Reaching outward additionally requires a live entitlement from the T2
   catalogue. No AI employee is purchasable, so no customer holds one. No prices were
   invented.
3. **Adapters.** The real SMS, email and voice adapters are the default and are never
   bypassed; the voice adapter raises unconditionally, because live AI voice is out of scope
   for this launch.
4. **Deployment.** `render.yaml` declares none of the engine's environment switches —
   asserted by `tests/test_ai_workforce_dark_deploy.py` so that turning one on has to delete
   a test that says it was meant to stay off.

The kill switch is separate from the stage, is checked at **execution** time rather than at
scheduling time, and exists at all four scopes plus an environment variable that can stop
everything without a database write.

## 4. DETERMINISM WHERE IT MATTERS

Contact eligibility is decided by code, not by the model: ALLOW / DENY / REQUIRES_REVIEW with
structured reasons, delegating to the platform's existing `qualification.qualify_one` rather
than inventing a second answer. The model cannot overturn a DENY. REQUIRES_REVIEW routes to a
person; it never behaves like ALLOW.

Work moves through an explicit 15-state machine with audited transitions and a
conditional-UPDATE claim, so two runners cannot take the same item, an employee cannot contact
the same record twice, and a run cannot loop unbounded. Appointments are read from the
existing calendar authority and booked through it; a slot the calendar did not offer is
refused.

## 5. RECONCILIATION WITH T7

T7 (`app/services/ai_operations/`) landed on main at 02:23 the same day, while T6 was
building. They are not rivals: T7 is how an authorized employee **reaches the world**, and it
names T6 as its authority. Every workforce concept crosses into T7 through `contracts.py`,
whose imports were written as `_try(...)` with a DECLARED fallback because T6 was not on main
when T7 shipped — and a declared context can never resolve a live adapter, which is what made
shipping it early safe.

T6 is present now, so that boundary loads the real thing. `tests/test_ai_workforce_t7_boundary.py`
proves it rather than asserting it: every module in `contracts.availability()` is loaded,
`T6_PRESENT` is true, every entry in T7's operation-to-tool map resolves against T6's real tool
registry, and the declared fallback still forces the simulated adapter. Without that test, a
rename of `app/services/workforce/` would silently revert T7 to its fallback and nothing would
fail.

## 6. WHAT THE EXISTING SUITE CAUGHT, AND WHAT WAS DONE ABOUT IT

* **`test_plan_limits_coverage.py`** correctly refused the two synthetic data builders. They
  are now listed there as NOT_A_CUSTOMER_PATH with stated reasons — the mechanism that file's
  own docstring prescribes — and three new tests enforce the claims those exemptions rest on,
  so the waiver is policed rather than granted.
* **`scripts/smoke_platform_frontend.py` (GATE 29)** failed because a T6 line comment spelled
  out the God wildcard, and that gate strips block comments with a regex before line comments —
  so the two characters that open a block comment swallowed `<ContextBanner />`. Reworded;
  32 of 32 now. Main hit the same trap hours earlier, and the fix points at the explanation it
  left.
* **A named brand in `workforce_models.py`'s docstring.** Removed, and
  `tests/test_ai_workforce_neutrality.py` now fails on any named brand, customer or person in
  engine source — comments included, because a comment is where the first hard-coded brand
  gets written.

No existing test was weakened. The pre-existing `probe_render_build_filters.py` failures were
checked against a clean `origin/main` and are identical there; they are not T6's.

## 7. WHAT WAS DELIBERATELY NOT DONE

* No live SMS, email, voice call, appointment or customer pipeline mutation.
* No real customer data read, copied or referenced. Every synthetic phone is in the 555-01xx
  fiction block, every address is at `.invalid`, every tenant is flagged `is_demo`.
* No prices, no revenue attribution, no invented ROI. The performance ledger reports
  `revenue: None` with a note rather than a number it cannot source.
* No second root role, no controlled activation switched on, no credential placed in source.

## 8. KNOWN CHARACTERISTICS

* Enqueue throughput is roughly 730 rows/sec because each row takes its own savepoint. That is
  the cost of the duplicate-assignment guard being a constraint rather than a check, and it is
  the right trade at this scale.
* The main JS bundle is over the 500 kB warning threshold. Pre-existing; T6 added ~5 kB.
