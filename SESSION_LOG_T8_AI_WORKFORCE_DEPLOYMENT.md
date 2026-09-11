# T8 — AI WORKFORCE BUILDER, DEPLOYMENT & COMMERCIALIZATION

**Branch:** `feat/ai-workforce-deployment` · **Worktree:** `C:\Dev\advisorflow-ai-deploy`
**Cut from:** `origin/main` @ `9f197f6` (T2 + T6 + T7 already landed)
**Shipped:** `0db9370`, fast-forwarded onto `main` and live in production.
**State on delivery:** built, proven synthetically, attacked, merged, deployed, fully dark.
No real outreach occurred and none is reachable — T8 adds no environment switch of
its own, so the dark-launch assertions T6 and T7 shipped still describe the whole
system.

| Check | Result |
|---|---|
| T8 suite | **94 passed** (plus 4 tests policing the plan-limits exemption) |
| Synthetic lifecycles | **48 / 48 steps** across three employees |
| Adversarial harness | **43 / 43** across eight dimensions |
| Full backend regression | **3278 passed, 14 skipped, 0 failed** |
| Deploy gates (`scripts/run_gates.py`) | 25 passed, 7 failed — **all seven identical on a clean `origin/main`** |
| Frontend build | clean, 305 modules |
| Production health | **`/health` reports `commit_short: 0db9370`**; all ten T8 routes answer 401 unauthenticated, unknown paths 404 |
| Real outreach | **none** — no SMS, no email, no voice call, no appointment |

---

## 1. WHAT T8 IS

T6 built the workforce engine. T7 gave it controlled operational reach. T8 is the
PRODUCT wrapped around both: how a white-label brand OFFERS an AI employee, and
how a customer HIRES, CONFIGURES, PROVISIONS, ACTIVATES, PAUSES and REMOVES one.

It is not a second AI engine, not a second communications engine, not a prompt
builder and not a second billing system. Every one of those was a real
temptation and each is refused structurally rather than by convention:

* **Authority** is answered by T6. `activation.py` asks `workforce.service.activate`
  and T6's resolver still takes the minimum across four scopes afterwards.
* **Reach** is answered by T7. The synthetic proof drives a real send through
  `ai_operations.orchestrator`, and the adapter that answers is the simulated one.
* **Prompts** do not exist here. `constants.FORBIDDEN_CONFIG_FRAGMENTS` refuses
  the word — and eighteen others — on the way IN, because a field that is never
  rendered and is still accepted is a field somebody reaches with curl.
* **Money** is answered by T2. No T8 table has an amount, a currency, an
  interval or a Stripe column, and `tests/test_ai_deployment_dark_deploy.py`
  fails if one appears.

God Mode remains root. There is no platform superadmin, no "AI workforce owner",
and no route in this thread that is not either `require_god` or scoped to the
caller's own workspace by its signature.

---

## 2. THE TWO STATE MACHINES, AND WHY THEY ARE TWO

Section 3 of the brief asks for commercial and operational state to stay
distinct. They are two vocabularies with **no value in common**, which a test
asserts as a set operation:

```
OPERATIONAL  selected → configuring → validation_required → ready
             → controlled → active → paused → suspended → retired

COMMERCIAL   unknown · not_offered · available · included · pending
             · entitled · lapsed
```

Three properties fall out of the transition table rather than out of code that
has to remember them:

* **`retired` has no outgoing edge.** A retired deployment is replaced, never
  revived — re-using the row would attach a new commercial arrangement to an
  old one's history.
* **`suspended` never leads straight back to a live state.** Entitlement
  returning puts a deployment back at `ready`; a person still has to switch it
  on. Section 6: no existing customer becomes active because something changed
  underneath them.
* **`pending` is not in `COMMERCIALLY_LIVE`.** Opening a checkout is not paying
  for one.

`validation_required` and `review_required` are deliberately different things.
The first is on the LIFECYCLE and means the customer has work to do — a required
answer missing, a handoff owner who left. The second is on the READINESS VERDICT
and means the platform has work to do: everything functions and a person should
look before real people are contacted. Collapsing them would make one word mean
both, and a customer cannot act on the second one.

---

## 3. READINESS, AND THE SIGNATURE THAT GETS PAST IT

Twenty-two deterministic checks, asked in a fixed order, cheapest and most
absolute first. Nothing in `readiness.py` calls a model, reads model output, or
consults anything a model wrote — a harness case asserts that by reading the
module's own source.

Three verdicts, and **the middle one is not a softer no**:

| Verdict | What it means | What activation does |
|---|---|---|
| READY | every blocking check passed, nothing wants a person | may proceed |
| NOT READY | something is broken, and it is named with a fix | refuses |
| REVIEW REQUIRED | it works; somebody should look first | refuses until signed off |

The only thing that gets past REVIEW REQUIRED is an authorised person who has
seen what it is about, and the signature is scoped to **exactly which items they
saw**. `review_acknowledged_digest` is a hash of the review check KEYS; a new
review item appearing — the organization's sending address being removed, say —
changes the digest and the acknowledgement stops applying. A signature given for
one concern does not cover the next one, and a harness case proves it.

---

## 4. HOW COMMERCE AND THE WORKFORCE TALK

One direction, always. **Entitlement arriving never starts anything;
entitlement leaving always stops it.**

That asymmetry is what makes it safe to wire a Stripe webhook into this layer.
The worst a mistaken reconcile can do is stop an employee — an operator's click
to put right. The worst a MISSED one can do is leave a screen stale, because the
live gateway asks T2 on every single tool call regardless.

Three hooks, all wrapped so they can never fail their caller:

| Where | When |
|---|---|
| `billing_webhook._handle_subscription_deleted` | the subscription behind it ended |
| `catalog_purchase.remove_recurring_addon` | a rep or a customer took the add-on off |
| `catalog_purchase.withdraw_pending` | an unpaid checkout was withdrawn |

`ai_employee_deployments.commercial_state` is a MIRROR of T2's answer with a
`commercial_checked_at` beside it, so a stale mirror reads as stale rather than
as fact. Nothing reads that column to decide whether a tool may run.

---

## 5. WHAT A BRAND CONFIGURES, AND WHAT IT CANNOT

`ai_offering_terms` is the boundary between T6's brand layer and T2's catalogue,
made explicit. It names a `brand_catalog_items.key` inside the SAME brand — and
God Mode validates that the key resolves before storing it, because an item key
that resolves to nothing makes a job look configured and be unbuyable, and the
person who finds out is a customer clicking Hire.

**No price is entered on any T8 screen.** The amount lives on the catalogue
item, in Billing, where every other price on this platform lives.

The package rules have one deliberate asymmetry:

* For an ORDINARY job, an empty `eligible_plan_keys` means no restriction — a
  brand that did not need to restrict it.
* For a MANAGEMENT capability, an empty list means REFUSED. A brand that wants
  to sell oversight to a smaller package says so by naming that package, which
  is a decision with a name on it rather than a default. *Do not let Starter
  cheaply become Professional through add-ons* is section 9, and this is it.

What counts as a management capability is DERIVED from the platform template —
a job holding no outward-reaching tool and declaring no channel supervises
rather than works — so a twelfth job added tomorrow is classified the day it
lands rather than the day somebody remembers this file.

---

## 6. IDEMPOTENCY AND RACES

Two guarantees, both structural.

**Provisioning is a database constraint, not a check.**
`uq_ai_deployment_provisioning_key` means two clicks on Hire, a retried request
and a duplicated worker all reduce to one row — the second loses at the index
and `select` returns the winner's deployment, so the caller sees success either
way. A test inserts the duplicate row DIRECTLY, so dropping the index while
leaving the friendly lookup in place fails the suite.

**Transitions are conditional updates, not read-then-write.** `transition`
moves a deployment only while it is still in the state the caller believed it
was in; two admins on two screens reduce to one UPDATE that matches and one
that matches nothing, and the one that matched nothing is told. `expected_state`
carries the browser's own view, so a tab left open through somebody else's
change is refused with `deployment_changed_since_this_screen_loaded` and a 409.

---

## 7. DEPROVISIONING — STOPPING WITHOUT ERASING

Retirement does four things and **none of them is a delete**:

1. the T6 employee is disabled and its stage set to `off`, so the next tool call
   is refused at EXECUTION time rather than at the next schedule;
2. its queued work items are paused, so the screens stop showing records as
   "working" while nothing is happening;
3. T7's pending follow-ups are marked cancelled, so nothing fires later against
   an employee nobody is paying for — an orphan schedule is the specific failure
   section 12 names;
4. the counts of everything it produced are written ONTO the retirement event,
   which is what makes "history was preserved" checkable afterwards rather than
   asserted in a docstring.

Retiring never touches T2. Removing an add-on is a commercial act with its own
authority on the Billing screen, because a customer tidying up their workforce
must not cancel a subscription by accident.

`deprovision.orphan_scan` looks for the three ways this can be violated by
something OUTSIDE T8: an `ai_employees` row no deployment entitles (T6's own
router can create one), a live deployment whose commercial standing is not live,
and a scheduled action pointing at a retired or suspended employee.
`repair_orphans` only ever moves things towards OFF.

---

## 8. WHAT WAS PROVEN, AND HOW

### The three synthetic lifecycles — 48 / 48

`app/services/ai_deployment/simulation.py`, runnable from
`POST /god/ai-workforce/proof/run {confirm_synthetic_data: true}`.

A Reactivation Specialist, and a full-lifecycle energy employee in each of its
two segments. Each runs the whole of section 15: catalogue availability →
entitlement → hire (twice, with one key) → configure → readiness → a customer's
request being recorded rather than granted → review acknowledged → controlled
activation by an operator → a T6 objective → a T7 operational action → handoff →
pause → resume → retire → cancellation safety → zero real outreach.

The two energy segments run the SAME steps with the SAME outcomes and differ
only in the answers they were given, which is the claim running both is there to
check: nothing in T6, T7 or T8 branches on segment.

### The adversarial harness — 43 / 43

`app/services/ai_deployment/evaluation.py`, runnable from
`POST /god/ai-workforce/proof/attack {confirm_synthetic_data: true}`.

isolation 8/8 · commerce 7/7 · package 3/3 · races 7/7 · injection 5/5 ·
readiness 6/6 · deprovision 4/4 · dark_launch 3/3

Highlights: another tenant's deployment is not loadable; a brand withdrawing a
job affects only its own customers; a catalogue item from another brand resolves
to nothing; a customer administrator cannot switch on live operation and cannot
clear their own review; a pending checkout is refused as unpaid; cancelling an
entitlement stops an employee that is working; **restoring it does not start one
again**; writing `active` onto a deployment does not make it entitled;
an add-on is not a route to a management capability; two clicks on Hire produce
one deployment and two provisioning workers produce one employee; a redelivered
webhook produces ONE suspension event; a stale browser tab is refused; a
configuration key naming an internal setting is refused rather than ignored;
prompt-shaped text inside a customer's own answer changes no tool grant; two AI
employees cannot be configured to hand work to each other; a signature on one
review item does not cover the next; ACTIVE is refused until CONTROLLED has
happened; a machine cannot switch an employee on; retiring deletes nothing and
cancels every queued follow-up; and the sweep finds an AI employee that nothing
entitles.

Every case runs in a savepoint that is rolled back, and a test asserts the
harness leaves no deployment behind.

---

## 9. DARK LAUNCH — THE EXACT STATE

**T8 adds no environment switch.** That is asserted twice — once in the harness
and once in `tests/test_ai_deployment_dark_deploy.py` — and it is the property
that keeps T6's and T7's own dark-deploy files describing the whole system
rather than two thirds of it.

Everything that was off stays off:

| Switch | Value | Effect |
|---|---|---|
| platform activation row | **off** | every scope resolves to off; a customer with no row is off, not "inherit" |
| `AI_OPERATIONS_ENABLED` | unset | every T7 operation refuses at gate 1 |
| `AI_OPERATIONS_LIVE_SEND` | unset | every channel resolves to the simulated adapter |
| `AI_WORKFORCE_LIVE_VOICE` | unset | no voice call is placeable by any configuration |

And independently of all four: no AI employee carries a price in any real
brand's catalogue, so no real customer resolves to entitled, so nothing T8 can
provision has a commercial arrangement behind it. Deploying this starts no
background work — no cron entry, no asyncio loop, no worker, asserted by a test.

### Turning it on later, in order

1. God Mode → AI Deployment → Brand terms: create the catalogue item in Billing
   with a real price, then name its key here and mark the job available.
2. The customer hires it and answers the business questions.
3. Readiness goes green; an operator acknowledges any review items.
4. An operator starts it in the **controlled** stage, with a reason.
5. `AI_OPERATIONS_ENABLED=1`, then schedule the T7 worker, then raise the T6
   activation scopes. Full capacity is a separate decision after controlled.

---

## 10. FILES

### New — the deployment layer (`app/services/ai_deployment/`)
| File | What it owns |
|---|---|
| `__init__.py` | the map and the safety argument |
| `constants.py` | two state machines, readiness verdicts, refusal codes, and what a customer may never configure |
| `catalog.py` | three views of T6's ONE job registry — platform, brand, customer |
| `commerce.py` | **the T2 boundary** — asks; never writes a commercial row |
| `capacity.py` | package and capacity guards; what buying does NOT buy |
| `configuration.py` | the guided business interview, and the loop detector |
| `readiness.py` | twenty-two deterministic checks; no model decides readiness |
| `lifecycle.py` | the state machine, idempotent provisioning, conditional updates |
| `activation.py` | asks T6 to switch one on; never opens a gate itself |
| `deprovision.py` | stopping without erasing, and the orphan sweep |
| `views.py` | the payloads both routers render |
| `simulation.py` | three synthetic lifecycles, end to end, reaching nobody |
| `evaluation.py` | the adversarial harness — 43 cases |

### New — elsewhere
- `app/models/ai_deployment_models.py` — three tables: `ai_offering_terms`,
  `ai_employee_deployments`, `ai_deployment_events`
- `app/routers/ai_deployment_router.py` — `/ai-workforce` (customer, no
  organization id on any route)
- `app/routers/god_ai_deployment_router.py` — `/god/ai-workforce` (every route
  `require_god`)
- `frontend/src/pages/AIWorkforce.jsx` — MY AI WORKFORCE
- `frontend/src/pages/god/GodAIWorkforceBuilder.jsx` — brand terms, deployments,
  proof
- `tests/test_ai_deployment_{proofs,lifecycle,api,dark_deploy}.py`

### Changed (all additive)
- `app/models/registry.py` — the model import, in **both** copies of that file
- `app/main.py` — the two routers
- `app/services/billing_webhook.py` — reconcile on subscription cancellation
- `app/services/catalog_purchase.py` — reconcile on add-on removal and withdrawal
- `app/services/ai_operations/contracts.py` — see §11
- `frontend/src/App.jsx`, `components/Layout.jsx`, `pages/GodShell.jsx` — routes,
  nav and one icon
- `tests/test_ai_workforce_neutrality.py` — T8's source joined the list it polices

---

## 11. A DEFECT FOUND IN THE T6/T7 BOUNDARY, AND FIXED

`ai_operations.contracts.request_work_item_state` called T6's queue as
`transition(db, work_item_id=..., organization_id=..., to_state=...)`. T6's
`transition` has never accepted those keywords — it takes the ROW. Every request
from the operations layer therefore raised `TypeError`, was swallowed by the
`except` beneath it, and returned `False`. **T7 could not move a T6 work item at
all**, silently, and the only symptom was an `info` log nobody reads.

It now loads the row inside the tenant and calls `advance_to`, which walks legal
edges and records every hop — the function T6's own header says to use, for the
reason recorded there: an action that already happened being reported as refused
is the worst possible pairing, because the employee believes it failed and tries
again.

This was pre-existing on `main` and is not T8's. It was found by writing a proof
that actually drives the boundary rather than describing it.

### And one waiver the existing suite correctly refused

`tests/test_plan_limits_coverage.py` refused the synthetic proof's world builder
the first time the full regression ran, exactly as its own docstring says it
should. It is now listed there as NOT_A_CUSTOMER_PATH with a stated reason —
and **four new tests police the claims that reason rests on**: that `_org` owns
every tenant it writes into and takes no organization argument, that every slug
carries the synthetic prefix, that every contact is a 555-01xx number at an
`.invalid` address, and that nothing else in `services/ai_deployment/`
constructs a User or a Lead. A waiver whose claims nothing checks is a
permission slip.

### The seven gate failures that are not T8's

`scripts/run_gates.py` reports 25 passed, 7 failed on this branch:
`smoke_tenancy.py` · `smoke_sales_execution.py` · `smoke_checkpoint6.py` ·
`smoke_checkpoint6_frontend.py` · `smoke_sales_workspace_complete.py` ·
`smoke_sales_staff.py` · `probe_platform_boundary.py`.

The same seven were run against a **clean `origin/main` in a throwaway
worktree** and produced the identical result — 0 passed, 7 failed — so none of
them is caused by T8 and none is fixed by it. This is the same set T6 and T7
each recorded. `smoke_platform_frontend.py` (GATE 29) passes, which is the one
that would have caught a malformed comment in the new frontend files.

---

## 12. WHAT T8 DELIBERATELY DID NOT BUILD

* **T9's analytics and intelligence.** The deployment screens report state and
  readiness; they compute no performance intelligence.
* **A second catalogue, checkout or entitlement engine.** Commerce is asked.
* **Any price.** No AI employee carries an amount anywhere in this thread, and
  the synthetic proofs use a QUOTED item precisely so the entitled path can be
  exercised without a figure being invented.
* **A second root control plane**, a second tool registry, a second activation
  resolver or a second employee model.
* **Prospecting as a deployable job.** The template exists in T6's library and
  T8 offers whatever a brand enables; nothing here loosens the policy around it.
* **Any background worker.** Deploying T8 starts nothing.

---

## 13. RUN IT YOURSELF

```
cd C:\Dev\advisorflow-ai-deploy
python -m pytest tests/test_ai_deployment_proofs.py tests/test_ai_deployment_lifecycle.py ^
                 tests/test_ai_deployment_api.py tests/test_ai_deployment_dark_deploy.py -q
```

The two proofs are also reachable from God Mode → **AI Deployment → Proof**,
which runs them inside a savepoint against the live database and rolls it back.

---

## 14. LIVE VERIFICATION, AFTER THE DEPLOY

`/health` reports `commit_short: 0db9370`. A successful deploy is itself
evidence the migration ran — `python -m app.migrate` is the backend's
preDeployCommand and a non-zero exit would have aborted the deploy with the
previous instance still serving — so the three new tables exist.

| Check | Result |
|---|---|
| `/ai-workforce/overview`, `/catalog`, `/deployments`, `/catalog/{job}/questions` | 401 unauthenticated |
| `/god/ai-workforce/overview`, `/templates`, `/deployments`, `/orphans` | 401 unauthenticated |
| `/ai-workforce/not-a-route`, `/god/ai-workforce/not-a-route` | 404 |
| T6's `/workforce/*` and `/god/workforce/*` | unchanged — 401 |
| T7's `/god/ai-operations/*` | unchanged — 401 |
| T2's `/billing/*` | unchanged — 401 |
| Served frontend bundle | carries both screens and both route prefixes |

Mounted, and closed. A route that 404s is a route nobody deployed; a route that
200s unauthenticated is a great deal worse.
