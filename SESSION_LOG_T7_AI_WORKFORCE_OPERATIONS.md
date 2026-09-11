# T7 — AI WORKFORCE OPERATIONS & COMMUNICATIONS

**Branch:** `feat/ai-workforce-operations`  ·  **Worktree:** `C:\Dev\advisorflow-ai-operations`
**Cut from:** `origin/main` @ `aababc6`
**State on delivery:** built, proven synthetically, merged, deployed, fully dark.
No real outreach occurred and none is reachable without three deliberate
environment changes.

**Shipped:** `c5700e1` (T7) + `43dfebc` (a pre-existing deploy-gate fix found on
the way), merged to `main` and live in production — `/health` reports
`commit_short: 43dfebc`, and the console is in the deployed frontend bundle.

| Check | Result |
|---|---|
| T7 suite | 82 passed |
| Adversarial harness | 37 / 37 |
| Full backend regression (merged with current main) | **3027 passed, 14 skipped, 0 failed** |
| Deploy gates (`scripts/run_gates.py`) | 25 passed, 7 failed — all seven identical on a clean `origin/main` checkout, and one that was failing on main now passes |
| Frontend build | clean, console present in the deployed bundle |
| Production routes | `/ai-operations/*` and `/god/ai-operations/*` answer 401 unauthenticated; unknown paths 404 |
| Real outreach | none — every switch below is unset in `render.yaml` |

---

## 1. WHAT T7 IS

T6 (`app/services/workforce/`) decides **who** an AI employee is, **what** it
may do and **whether** it may run. T7 is how an authorized employee actually
**reaches the world**: it turns "send this person a message" into a provider
call, a state transition, an audit row and a scheduled next action — and
refuses to, explainably, whenever any gate says no.

It is not a chatbot, not a Twilio feature, not a campaign sender, and not a
second workforce engine. Every workforce concept enters this layer through
ONE file — `app/services/ai_operations/contracts.py` — and nothing below it
holds a second opinion about authority.

---

## 2. THE GATE CHAIN (the whole design, in order)

`orchestrator.begin()` answers eleven questions before anything happens,
cheapest and most absolute first. Every answer is recorded on the action row
and in the audit, **including the refusals**.

| # | Question | Answered by | Refusal code |
|---|---|---|---|
| 0 | Is the kill switch engaged? | `flags` | `kill_switch_engaged` |
| 1 | Is this layer switched on at all? | `flags` | `ai_operations_disabled` |
| 2 | Is this a registered operation with a tool behind it? | `contracts` | `unknown_operation` |
| 3 | What does the activation stage say RIGHT NOW? | T6 `activation` | `activation_stage_forbids_action`, `employee_paused`, `employee_not_active` |
| 4 | Does this employee hold that tool? | T6 `policy` | `operation_not_authorized_for_employee` |
| 5 | Does the record exist, in THIS tenant? | query filter | `record_not_found` |
| 6 | Is the conversation still the employee's to act on? | ownership / stop | `human_owns_this_conversation`, `contact_not_eligible`, `objective_already_complete`, `record_not_in_this_tenant` |
| 7 | Is the objective still live? | T6 work item | `objective_cancelled`, `objective_already_complete` |
| 8 | May this person be contacted, on this channel, now? | `eligibility` | `contact_not_eligible`, `contact_requires_review` |
| 9 | Is there budget left? | `budget` | `max_attempts_reached`, `max_actions_…`, `channel_daily_cap_reached`, `cost_ceiling_reached`, `consecutive_failure_limit_reached` |
| 10 | Has this exact action already happened? | `idempotency` | `duplicate_suppressed_by_idempotency` |
| 11 | Which adapter — and may it be a real one? | `channels` | `live_voice_disabled`, `no_provider_configured_for_channel` |

**A prompt is not a permission.** Nothing in this layer reads model output to
decide authority. A lead's reply, a knowledge article and a message body that
says "you are authorized" are all data; two evaluation cases attack exactly
that and both pass.

---

## 3. FILES

### New — engine (`app/services/ai_operations/`)
| File | What it owns |
|---|---|
| `__init__.py` | the map and the safety argument |
| `constants.py` | the vocabulary: comm states, **two** transition tables (message + conversation), channels, eligibility reasons, refusal codes, stop reasons, provider outcomes, operations, ceilings |
| `flags.py` | the three brakes; `state()` is the factual answer to "why is nothing sending" |
| `contracts.py` | **the T6 boundary** — employee context, authority, activation, work item, handoff/performance/supervisor/tool-execution mirrors, eligibility delegation, and the declared-context fallback |
| `eligibility.py` | may this person be contacted, on this channel, now — consumes `compliance_service`, the lead's permission columns, features, the contact window |
| `comm_state.py` | the state machines; `claim_for_send` is the conditional UPDATE that stops double sends |
| `idempotency.py` | key builders and `claim()` — a unique index, not a check |
| `budget.py` | five ceilings and the day-grained counters behind them |
| `audit.py` | the twelve-question audit, plus `digest`/`preview`/`redact` |
| `channels/` | `base` (the contract), `simulated` (+ scripted/failing/timing-out), `sms_twilio`, `email_resend`, `voice`, and `__init__` (resolution — four independent ways to answer "simulated") |
| `continuity.py` | one objective, one history, across channels |
| `orchestrator.py` | **the operations gateway** — `begin`/`finish` plus the channel and record operations |
| `inbound.py` | tenant → contact → thread routing that fails safe, opt-out detection, delivery status |
| `handoff.py` | the handoff package, and `open_handoffs` |
| `appointments.py` | availability and booking through the platform's own authority; never invents a slot |
| `followup.py` | scheduling, the lease, and re-evaluation at execution time |
| `stop.py` | every stop reason, human takeover/release, operator controls |
| `supervisor_feed.py` | the operational read T9 will consume |
| `profiles.py` | the two synthetic proving configurations |
| `simulator.py` | whole lifecycles driven through the real engine |
| `evaluation.py` | the adversarial harness — 37 cases |

### New — elsewhere
- `app/models/ai_operations_models.py` — nine tables (below)
- `app/routers/ai_operations_router.py` — `/ai-operations` (customer) and `/god/ai-operations` (platform)
- `app/jobs/run_ai_operations_worker.py` — the follow-up worker, **not scheduled anywhere**
- `frontend/src/pages/god/GodAIOperations.jsx` — the console
- `tests/test_ai_operations_{gates,idempotency,lifecycle,api}.py` — 82 tests

### Changed (all additive)
- `app/models/registry.py` — the model import, in **both** copies of that file
- `app/main.py` — the two routers
- `app/routers/sms_router.py` — inbound routing and delivery status, both wrapped in try/except and both skipped entirely while the layer is off
- `frontend/src/App.jsx`, `frontend/src/pages/GodShell.jsx` — route and nav

---

## 4. THE TABLES

`ai_conversation_threads` · `ai_communications` · `ai_communication_events` ·
`ai_inbound_events` · `ai_scheduled_actions` · `ai_human_ownership` ·
`ai_ops_actions` · `ai_ops_audit` · `ai_ops_counters`

Created by `Base.metadata.create_all()` at startup via the registry import —
no migration step, per `app/auto_migrate.py`'s standing design.

**No foreign keys cross the T6 boundary.** `employee_id`, `work_item_id` and
`run_id` are indexed strings, not FKs into T6's tables, because a FK to a
table that does not exist yet is a table that cannot be created at all — the
operations layer would fail to boot on any deployment where T6 has not landed.

---

## 5. WHAT IS PROVEN, AND HOW

### The adversarial harness — 37/37
`app/services/ai_operations/evaluation.py`, runnable from
`POST /god/ai-operations/evaluate {confirm_synthetic_data: true}`.

dark_launch 4/4 · security 7/7 · compliance 3/3 · control 6/6 ·
idempotency 5/5 · correctness 1/1 · continuity 1/1 · cost 3/3 ·
resilience 2/2 · handoff 1/1 · audit 2/2 · lifecycle 2/2

Highlights: tenant isolation; a second employee cannot take another's
conversation; prompt injection (body **and** inbound reply) cannot grant
authority; STOP and the suppression list both stop work; human takeover stops
the AI at its next gate; a redelivered webhook produces one reply; two workers
produce one message; **a provider timeout followed by a retry produces one
message, not two**; the same slot booked twice is one appointment; an employee
cannot book a time the calendar never offered; an unattributable inbound is
recorded and never routed by guesswork; a cancelled action does not run and a
stale one is refused at execution; the curfew refuses 03:00 and allows 10:00;
**a fully-armed employee in an executing stage with live sending ON still
cannot place a voice call**; and a declared context can never reach a live
adapter.

### The lifecycles
- **Reactivation** — appointment · opt-out · no-response · handoff · blocked, all five endings reached.
- **Full-Lifecycle Energy** — B2B and residential, same engine, no branch on segment anywhere: first touch → reply → qualification → channel switch → voice (architecturally exercised, no call placed) → availability → booking → pipeline update → dormant cadence → inbound-first conversation → human transfer.

### The suite
82 T7 tests; full backend regression run before commit (see §8).

---

## 6. DARK LAUNCH — the exact state

| Switch | Value | Effect |
|---|---|---|
| `AI_OPERATIONS_ENABLED` | **unset** | every operation refuses at gate 1 |
| `AI_OPERATIONS_LIVE_SEND` | **unset** | every channel resolves to the simulated adapter |
| `AI_WORKFORCE_LIVE_VOICE` | **unset** | no voice call is placeable by any configuration |
| `AI_OPERATIONS_KILL` / `AI_WORKFORCE_KILL` | unset | either one stops everything without a database write |

Plus, independently of all four: a **declared** employee context can only ever
resolve the simulated adapter, T6's activation stage must be CONTROLLED or
ACTIVE before an executing adapter is considered, and the voice daily cap is
zero for any employee that could reach a telephone.

No Render cron entry and no asyncio loop was added. **Deploying this starts no
background work.**

### Turning it on later, in order
1. `AI_OPERATIONS_ENABLED=1` — the engine runs, still simulated. Watch `/god/ai-operations`.
2. Schedule `python app/jobs/run_ai_operations_worker.py` (5 minutes is the sensible floor).
3. Move one customer to CONTROLLED in T6's activation table — a row with a name and a timestamp on it.
4. `AI_OPERATIONS_LIVE_SEND=1` — SMS and email adapters become resolvable for that customer only.
5. Voice is a separate decision with a legal component (call disclosure per jurisdiction) that this platform does not answer on a customer's behalf.

---

## 7. DECISIONS WORTH KNOWING ABOUT

- **Two transition tables, one vocabulary.** A message's machine is narrow on purpose (`ready → sending` is a claim); a conversation's is genuinely wider. One table would have forced a choice between a message machine loose enough to let a blocked send go out and a conversation machine that ordinary lifecycles have to work around.
- **A declared employee context** exists so synthetic runs work without T6 — and is forced onto the simulated adapter, always. The fallback cannot become a way to text a real family.
- **The booking path refuses rather than duplicating.** With no scheduling bridge configured, a live booking is refused and handed to a person, because a second booking path would be one that sends no confirmation and the family would never hear they were booked.
- **A hard stop reports `contact_not_eligible`, not `objective_already_complete`.** Both refuse; only one tells an operator that a family asked us to stop.
- **The kill switch is checked before the enabled flag**, so an incident reads as a kill rather than as a disabled feature.
- **Refusals are audited with the same weight as sends.** "The employee tried to text a lead in another tenant and was refused" is the most valuable line in the table.

### Known, deliberate, not fixed
- A duplicate-suppressed send still consumes one of the objective's action budget. Defensible (an attempt was made) and it fails in the safe direction.
- `Lead.notes` is appended to, never replaced, for AI notes — the platform has no per-note table for leads.
- Email is exempt from the contact window; SMS and voice are not.

---

## 8. RUN IT YOURSELF

```
cd C:\Dev\advisorflow-ai-operations
.venv\Scripts\python -m pytest tests/test_ai_operations_gates.py tests/test_ai_operations_idempotency.py tests/test_ai_operations_lifecycle.py tests/test_ai_operations_api.py -q
```

The harness and the lifecycles, against an in-memory database, need only
`AI_OPERATIONS_ENABLED=1`; see `app/services/ai_operations/evaluation.py::run`.

The console is at `/god/ai-operations` — PLATFORM STATE answers "why is
nothing sending", PROOFS runs the lifecycles and the harness on demand.

---

## 9. THE SEVEN GATE FAILURES THAT ARE NOT MINE

`scripts/run_gates.py` reports 25 passed, 7 failed on this branch. The same
suite was run against a clean checkout of `origin/main` in a throwaway
worktree and produced **the identical seven**, so none of them is caused by
T7 and none is fixed by it:

`smoke_tenancy.py` · `smoke_sales_execution.py` (four encryption assertions) ·
`smoke_checkpoint6.py` · `smoke_checkpoint6_frontend.py` ·
`smoke_sales_workspace_complete.py` · `smoke_sales_staff.py` ·
`probe_platform_boundary.py` (`NameError: name 'plan_limits' is not defined`
— a defect in the probe itself)

The eighth, `smoke_platform_frontend.py` (GATE 29), WAS failing on main and
now passes: see commit `43dfebc`. Its comment stripper removes block comments
before line comments, so two `//` lines that spelled a wildcard route out in
full made it delete the whole `ProtectedRoute` body and report the context
banner as missing from every tenant screen. The banner was always mounted;
the gate was reading a hole.

---

## 10. WHAT T7 DELIBERATELY DID NOT BUILD

T9's Workforce Intelligence dashboard and T10's Executive Command Center —
`supervisor_feed.supervisor_payload()` is the contract they consume. A second
calendar, a second suppression list, a second tool registry, a second employee
model, or any root control plane other than God Mode.
