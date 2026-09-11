# T9 — AI WORKFORCE SUPERVISOR, INTELLIGENCE & OPTIMIZATION

What shipped, what it refuses to do, and what a reader should check first.

---

## WHAT T9 IS

The management, quality, exception and optimization layer **above** the AI
Workforce. T6 built the engine, T7 built its reach, T8 built the product. T9 is
the layer a **person** uses to manage the result: what are my AI employees
doing, what are they producing, what is waiting, what is failing, where do
humans need to intervene, what is costing money, which employees are
performing, which need attention, what changed, why it matters, and what to do
next.

It consumes authoritative T6/T7/T8 records and produces authorized
intelligence. It creates no new truth, and it holds no authority of its own.

## THE THREE RULES THAT SHAPED EVERY MODULE

**1. Unknown is never zero.** A rate with no denominator, revenue attribution,
provider cost, appointment completion — each is reported as absent with a
sentence saying why. A zero on a management screen reads as failure and gets
acted on. `metrics.rate()` returns `None` below a minimum denominator;
`revenue` is present in every payload and is always unknown; the executive
contract guarantees it.

**2. T9 explains evidence and never creates it.** Findings separate FACT,
METRIC, INTERPRETATION and UNKNOWN into four database columns rather than one
blob, so a renderer cannot print an interpretation where a fact belongs. No
model runs anywhere in this package — every interpretation is assembled from
computed numbers, which is what makes findings deterministic and testable.

**3. Every consequential action is a delegation.** Pause goes through
`ai_deployment.lifecycle.pause`. Takeover goes through
`ai_operations.stop.take_over`. Handoff acceptance goes through
`workforce.handoff.accept`. Each `ai_management_actions` row records the module
that performed it, so "T9 delegates" is checkable from the data. A test greps
the whole package and fails if any T9 module constructs a T6/T7/T8 row or
assigns an activation stage, a commercial state, an entitlement key or a
readiness verdict.

---

## WHAT IS NEW ON DISK

`app/models/workforce_intelligence_models.py` — seven tables, all management
state, none of it a copy of engine truth:

| table | what it holds |
|---|---|
| `ai_intelligence_read_models` | materialised payloads, with `computed_at` AND `source_watermark` so a screen can tell "old and correct" from "not computed" |
| `ai_attention_items` | the Needs Attention queue's **lifecycle** — content is recomputed every pass; only first-seen/acknowledged/resolved persists |
| `ai_supervisor_findings` | findings, with fact / metric / interpretation / unknown in separate columns |
| `ai_review_decisions` | append-only reviewer decisions (the queue itself is not stored — it already exists authoritatively) |
| `ai_reconciliation_findings` | contradictions between systems, with both sides quoted and the owner of the fix named |
| `ai_management_actions` | the receipt for every action, including the system that performed it and any refusal code, verbatim |
| `ai_intelligence_runs` | whether T9's own passes ran — so silent failure cannot look like a quiet day |

`app/services/workforce_intelligence/` — nineteen modules. The ones worth
reading first are `scope.py` (the single place T9 decides what a caller may
see), `metrics.py` (unknown-is-not-zero), `attention.py` (root-cause
deduplication) and `executive.py` (the T10 contract).

`app/routers/workforce_intelligence_router.py` — 48 routes across three
routers: the customer surface (no organization parameter anywhere), the
white-label brand surface, and a God extension.

Frontend: `AIWorkforceCommand.jsx` (eight tabs, Needs Attention first) and
`AIWorkforceEmployee.jsx`.

---

## THE THINGS MOST LIKELY TO BE MISREAD

**`scope.for_system()` is read-only.** A background pass gets a scope that
cannot take any action touching another system. That is deliberate — a cron
must not be able to pause a customer's workforce — and it is asserted.

**Resolving an attention item does not fix it.** The next pass will put it back
if the underlying condition is still true. An item that could be dismissed
permanently while still true would be a way to hide a problem from the next
person on shift.

**A cancelled appointment is not a failure to book.** The ledger counted the
booking when it happened and that count is correct. What must not happen is
treating it as an outcome that still stands — `appointments_standing` is a
separate fact, and `appointments_completed` is unknown because attendance is
not recorded against a booking.

**Thresholds are detection settings, not SLAs.** Every sentence T9 produces
says what HAS happened ("waiting since 09:12"), never what should happen by
when. Section 6 asks for both configurability and no invented promises; those
are two different things and this is the line between them.

**There is no workforce quality score.** A receptionist and a reactivation
specialist do not share a scale. Benchmarks group by job role, publish nothing
below three employees in a group, and `benchmark()` takes no database session
at all — so it cannot reach another tenant even by mistake.

---

## PROOF

- **68** functional tests (`tests/test_ai_workforce_intelligence.py`)
- **43** adversarial and isolation tests
  (`tests/test_ai_workforce_intelligence_security.py`)
- **14** lifecycle, scale and dark-launch tests
  (`tests/test_ai_workforce_intelligence_proof.py`)

The three synthetic lifecycles — Reactivation Specialist, Full-Lifecycle Energy
Residential, Full-Lifecycle Energy B2B — run through T8's own deployment proof
and are then enriched **through the engine's own contracts** (`queue.enqueue`,
`queue.advance_to`, `queue.record_failure`, `handoff.create`,
`performance.bump`). A proof that wrote its own rows would keep passing against
a shape nothing produces any more.

Scale is asserted as a **query count**, not a clock: four employees and sixteen
must produce the same shape of page, and a timing assertion would pass on a
fast machine with an N+1 in it.

---

## THE DARK LAUNCH IS UNCHANGED

T9 adds no environment switch of its own and turns none of the existing ones
on. `AI_OPERATIONS_ENABLED`, `AI_OPERATIONS_LIVE_SEND` and
`AI_WORKFORCE_LIVE_VOICE` are read and rendered on the command centre — "why is
nothing sending" has a factual answer and this is it — and there is no endpoint
anywhere in T9 that can change any of them. Asserted before and after the full
proof run.

**Zero real outreach.** Counted across the whole database, not just the
synthetic tenants: non-simulated communications and non-simulated allowed tool
executions, both zero.

---

## WHAT T9 DELIBERATELY DOES NOT DO

- No second root. God Mode is the only root authority; `for_platform` refuses
  anybody who is not a `god_admin`, and a white-label owner is bounded to their
  own brand by a subquery.
- No competing ticket system. A platform-suspected exception escalates into T5
  Support's own `create_ticket`, which resolves entitlement, queue and clock
  itself.
- No silent repair. Contradictions between T6, T7 and T8 are surfaced with both
  sides quoted and the owning layer named. `REMEDIATION_OWNER` never says "t9".
- No T10 screens. `executive.snapshot()` is the contract T10 reads; it is
  versioned (`t9.executive.v1`) and its guarantees are published at
  `/ai-workforce-intelligence/contract`.
