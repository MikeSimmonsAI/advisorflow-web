# Wholesale Real Estate Engine — build handoff

**Built:** overnight, 22–23 September 2026
**Repo:** `C:\Dev\advisorflow-web` (branch `main`, uncommitted)
**Scope:** platform-level module, shared code, per-organization data and configuration

> **Repo note, first:** `C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web`
> is marked retired by a file in its own root (`!!!_DO_NOT_USE_THIS_REPO_!!!.txt`,
> cutover commit `68efa36`) and is ~35 routers behind. Everything below is in
> `C:\Dev\advisorflow-web`. Nothing was written to the OneDrive copy.

---

## 1. What this is

A wholesale real-estate acquisition and disposition engine built **into** the
existing AdvisorFlow platform — not beside it and not as a fork. It is gated by
one entitlement key, `wholesale_real_estate`, registered in
`app/services/entitlements.py` alongside `campaigns`, `crm` and the rest. A
white-label brand (EvoSys Pro today, BookaBoost or anything later) switches it on
per customer organization. There is no brand name anywhere in the module.

The architectural decision the whole thing rests on:

**The seller is a `Lead`.** A property owner reached by SMS is a lead in this
platform, which means the module inherits, without a second implementation of
any of it:

| Inherited | From |
|---|---|
| DNC / opt-out | `Lead.status == "dnc"`, `SuppressionEntry`, STOP handling |
| Channel permissions (tri-state) | `allow_sms` / `allow_email` / `allow_voice` |
| Consent of record | `sms_consent` + the exact wording agreed to |
| Test-record suppression | `Lead.is_test`, `app/services/test_records.py` |
| Message history | `Message` / `Reply` / `EmailMessage` |
| Duplicate detection | `ContactRegistry` |
| Scope and ownership | `lead_scope.active_workspace_org_id` |
| Master contact retention | `app/services/master_contacts.py` |

`WholesaleSellerProfile` is an **extension** of a Lead — a 1:1 side table of
real-estate facts — never a replacement. Re-implementing any of the above for
sellers is how a wholesaler eventually texts somebody who said STOP.

**Buyers are not leads**, and that is also deliberate. A cash buyer is a
counterparty, not a prospect: no tier, no cadence, and putting them in `leads`
would put them in every advisor's lead list and every campaign cohort. They get
their own table with their own `do_not_contact` flag, checked before every send.

---

## 2. Files

### New — backend (13 files, ~5,800 lines)

| File | Lines | What it is |
|---|---:|---|
| `app/models/wholesale_models.py` | 809 | 13 tables, every one `organization_id NOT NULL` + indexed |
| `app/services/wholesale_pipeline.py` | 175 | Stage list (configurable), the three gated transitions |
| `app/services/wholesale_analysis.py` | 474 | ARV from comps, MAO formula, deal summary, qualification. **Pure** |
| `app/services/wholesale_matching.py` | 328 | Buy-box scoring with per-dimension reasons. **Pure** |
| `app/services/wholesale_enrichment.py` | 266 | `EnrichmentProvider` interface, `ManualProvider`, cost controls |
| `app/services/wholesale_ai.py` | 394 | Structured extraction via `ai_gateway` + deterministic fallback |
| `app/services/wholesale_service.py` | 972 | Orchestration, automation, audit, dashboard |
| `app/routers/wholesale_router.py` | 1,805 | Acquisition side — 28 endpoints |
| `app/routers/wholesale_buyers_router.py` | 711 | Disposition side — 12 endpoints |
| `tests/test_wholesale_analysis.py` | 276 | 26 tests, no database |
| `tests/test_wholesale_matching.py` | 176 | 13 tests, no database |
| `tests/test_wholesale_flow.py` | 334 | 4 tests — the full deal, over HTTP |
| `tests/test_wholesale_guards.py` | 515 | 26 tests — isolation, entitlement, opt-out, failures |
| `tests/frontend/wholesaleNav.test.mjs` | 186 | 11 checks, plain `node`, no bundler |

### New — frontend (7 files, ~2,650 lines)

`frontend/src/pages/wholesale/` — `WholesaleCommand.jsx`,
`WholesaleProperties.jsx`, `WholesaleDeal.jsx`, `WholesaleBuyers.jsx`,
`WholesaleSettings.jsx`, plus `wsShared.jsx` (shared components) and
`wholesale.css` (everything scoped under `.ws-page`).

### Edited — 6 files, 112 added lines, 1 deleted

| File | Added | Change |
|---|---:|---|
| `app/main.py` | 21 | Two router imports + two `include_router` calls. **Nothing else** — no CORS, no middleware, no startup hook, no migration code |
| `app/models/registry.py` | 27 | Model module import, added to **both** duplicated blocks |
| `app/services/entitlements.py` | 10 | `FEATURES["wholesale_real_estate"]` + `REQUIRES[...] = ("leads",)` |
| `app/services/ai_gateway.py` | 7 | `CAPABILITY_MODELS["wholesale_seller_qualify"] = "gpt-4o-mini"` |
| `frontend/src/App.jsx` | 20 | Five imports, five routes, all `feature="wholesale_real_estate"` |
| `frontend/src/components/Layout.jsx` | 27 | "Wholesale" nav group + a `home` icon |

No existing behaviour was changed. No file was refactored. No production data
was touched.

---

## 3. Database

**13 new tables.** No migration script is needed and none was written: this
platform creates new tables through `Base.metadata.create_all()` in
`app/main.py`'s startup hook, and `app/auto_migrate.py` exists only for columns
added to *existing* tables — there are none here. The model module is registered
in `app/models/registry.py` (both blocks), which is what makes
`create_all()` see it.

```
wholesale_settings              one row per org — every tunable in the module
wholesale_properties            address optional-heavy; almost every column nullable
wholesale_seller_profiles       extension of a Lead; structured answer columns
wholesale_deals                 the deal room root; stage, analysis, contract, assignment, title, fee
wholesale_comps                 comparable sales, each with an `included` flag
wholesale_approvals             kind, status, amount, and a JSON snapshot of the inputs
wholesale_documents             slots and signature state — no legal forms are generated
wholesale_buyers                cash buyers, with their own do_not_contact
wholesale_buy_boxes             STRUCTURED — geography, price, size, strategy, rehab, spread
wholesale_buyer_matches         score + the per-dimension reasons behind it
wholesale_buyer_outreach        one deal to one buyer, and everything that came back
wholesale_enrichment_requests   every attempt, including the failures
wholesale_events                the module's own audit trail, with a NON-HUMAN actor type
```

**Why `wholesale_events` exists alongside the platform audit log:**
`AuditLogEntry.actor_user_id` is `NOT NULL`, so the platform table physically
cannot record an action taken by the AI or by an automation. Material actions are
written **here always**, and **additionally** to `audit_log_entries` whenever
there is a real signed-in user to name — so the existing Audit Log screen keeps
telling the truth and nothing automated is lost.

**Destructive migrations:** none. Nothing drops, renames or deletes.

---

## 4. Environment variables

**Added:** none are required. The module runs with zero new configuration.

Optional, and only if a paid skip-trace provider is added later — the adapter
declares its own variable names in `required_env` and the settings screen reports
which are missing:

```
# example only — no such provider is implemented
WHOLESALE_<PROVIDER>_API_KEY
```

The existing `AI_MANUAL_ACTIONS_ENABLED` / `AI_BACKGROUND_AUTOMATION_ENABLED` and
the `AI_BG_MAX_CALLS_*` caps already govern the one AI capability this module
adds. No new AI switch was invented.

---

## 5. The approval gates — read this one

Three transitions refuse without a human approval, and they are the three with
legal or financial weight:

| Moving to | Needs | Switch |
|---|---|---|
| `offer_sent` | approved `offer` | `require_offer_approval` (default **on**) |
| `under_contract` | approved `contract` | `require_contract_approval` (default **on**) |
| `assignment_pending` | approved `assignment` | `require_assignment_approval` (default **on**) |

A refused move returns **409** with a sentence saying what to do, not a code.
The gates are per-organization columns rather than constants because a one-person
shop may legitimately want the offer gate off — but they default on, because the
cautious default is the right one for money and contracts.

**No automation in this module can reach those transitions.** Automation calls
`_set_stage_unchecked`, which is internal and only ever used for transitions with
no weight (owner identified, seller engaged, dead). There is no code path from an
automation to a gated stage.

An approval stores a **JSON snapshot of the numbers it was requested on**. A later
edit to the deal cannot rewrite what was approved — which is the entire point of
having an approval record.

---

## 6. Providers, and what happens with none connected

| Provider slot | Shipped | With none connected |
|---|---|---|
| Enrichment / skip trace | interface + `ManualProvider` | `status="manual"`, a plain explanation, **no invented phone number**. Manual entry and CSV import write the identical record with `provider="manual"` |
| Property data | slot only | manual entry and CSV import |
| Comps | slot only | manual comp entry; ARV derived transparently from the comps the user ticked |
| E-sign | slot only | record a signed upload — a first-class path, not a degraded one |

`GET /wholesale/enrichment/providers` and the settings screen report
`configured: true/false` computed **from the environment**, not from a stored
flag that could go stale, plus the names of the missing variables.

**Cost control** applies only to *billable* providers. Manual entry is never
capped. The default caps are `0` — no paid calls at all — which is the honest
default for a module built to run lean.

**Adding a real provider later** is one class and one line in `PROVIDERS`.
Nothing above the adapter changes: the router, the models and every screen read
`EnrichmentResult`, never a vendor payload. There is a worked example in the
comments of `wholesale_enrichment.py`.

---

## 7. AI

One capability: `wholesale_seller_qualify` → `gpt-4o-mini`, registered in
`ai_gateway.CAPABILITY_MODELS`. Every call goes through `ai_gateway.chat_completion`,
so approved-models, the manual/background switches, the spend caps and the
circuit breaker all apply. This module never constructs an OpenAI client.

Its one job is **extraction**: read what the owner said into structured fields
(intent, asking price, timeline, condition, occupancy, repairs, motivation,
decision makers, callback time). It writes nothing, sends nothing and signs
nothing.

**The fallback is real, not a stub.** When the gateway refuses — switch off, cap
reached, no key, provider down — `_deterministic_read` runs: a pattern pass that
recognises STOP and opt-out language, "not interested", "already sold", "wrong
number", a stated price, a timeline, occupancy and condition. Results are marked
`source="rules"`, and anything ambiguous sets `needs_human`, which routes the
seller to REVIEW rather than scoring them optimistically.

**A deterministic opt-out never reaches the model at all.** Recognising STOP is a
compliance act and must not depend on a provider being up, a switch being on, or
a spend cap having room. And where the model *did* run but missed an opt-out the
pattern reader caught, the pattern reader wins — the one direction it overrides
the model, because it is the one with a legal consequence.

Values outside the vocabulary are **dropped, not stored**. A model returning
`"timeline": "sometime next decade"` produces `None`, because an unrecognised
string in that column would be read by the qualification engine as a timeline
nobody scores — a silent wrong answer.

---

## 8. What was tested, and how

### Automated: 80 new tests, all passing

```
tests/test_wholesale_analysis.py     26 passed   pure math, no database
tests/test_wholesale_matching.py     13 passed   pure scoring, no database
tests/test_wholesale_flow.py          4 passed   full deal end to end over HTTP
tests/test_wholesale_guards.py       26 passed   isolation, entitlement, opt-out, failures
tests/frontend/wholesaleNav.test.mjs 11 passed   nav / route / server-key agreement
```

The frontend one reads the nav group out of `Layout.jsx`, the routes out of
`App.jsx` and the feature registry out of `entitlements.py`, and asserts they
agree — so a `featureKey` the server has never heard of fails a test instead of
silently hiding the module from the customers who paid for it.

The flow test runs the whole mission flow through the real ASGI app: property →
owner → manual contact → seller reply read → comps → analysis → offer → **gate
refuses (409)** → approval → offer sent → **gate refuses** → contract approval →
under contract → three buyers with different buy boxes → ranked matching →
deal-sheet preview → disposition → buyer offer → assignment → **gate refuses** →
approval → documents → title → close + fee → dashboard reflects all of it →
audit trail names every actor including the AI and the automations.

The guard tests cover: cross-tenant 404 on deals, buyer lists not crossing
tenants, per-tenant settings, a 402 from the server when the module is not
entitled, the `REQUIRES` dependency, STOP handling with no AI, "not interested"
killing the deal, an opted-out buyer blocked **visibly** in disposition and
excluded entirely from matching, enrichment inventing nothing, a dead AI still
qualifying, an unreadable message going to a person, the gates being switchable
only by the customer, unknown stages refused with the real list, double-decided
approvals refused, impossible thresholds and buy boxes refused, a buyer with no
contact method refused, and a custom pipeline being stored and enforced.

### Live server smoke test

`uvicorn app.main:app` booted against a **fresh** SQLite file — so the startup
hook (`create_all` + `run_auto_migrations`) ran for the first time, which is
exactly what the first Render deploy will do.

```
13/13 wholesale tables created by the startup hook
GET  /wholesale/settings                           200   investor_percentage = 70.0
GET  /wholesale/dashboard                          200
POST /wholesale/properties                         200
POST .../seller                                    200
POST .../seller-reply                              200   rules reader: interested, $110,000, asap
POST .../comps  x3                                 200   ARV 300,000 (estimated)
PATCH .../analysis                                 200   MAO 146,000
POST .../stage offer_sent                          409   gate refused, as designed
POST .../approvals + decide, then stage            200
POST /wholesale/buyers, buy-box, match-buyers      200   scored 60% with reasons
GET  /wholesale/deals/{id}                         200   13 sections in one response
GET  /wholesale/dashboard (unauthenticated)        401
```

MAO check by hand: 300,000 × 70% = 210,000 − 45,000 repairs − 9,000 costs
(3% of ARV) − 10,000 fee = **146,000**. ✓

### The full existing suite — and what it caught

`5,355 passed, 14 skipped, 3 failed` in 47:30. **Two of the three were mine, and
they were a real defect, not a test technicality:**

```
FAILED tests/test_lead_capacity_matrix.py::test_every_lead_creating_path_consults_the_plan
        these create a Lead without consulting plan capacity:
        [('services/wholesale_service.py', 'attach_seller')]
FAILED tests/test_plan_limits_coverage.py::test_every_user_or_lead_creation_file_reaches_the_plan_limit_guard
```

**A seller is a Lead, so a seller costs a lead.** The inherited-guards argument
this whole module rests on cuts both ways: reusing `Lead` buys DNC, consent and
suppression for free, and it also means a wholesale seller occupies a seat in
the customer's package. `attach_seller` was creating them outside `plan_limits`,
so an import of five thousand owners would have walked straight through a plan
that includes two thousand five hundred — silently, and only visible later as a
billing argument.

Fixed properly rather than exempted:

- a single attach calls `plan_limits.require_capacity_for_org_id(...)` → **402**
  with the plan's own wording when the allowance is full;
- the CSV import holds ONE `plan_limits.CapacityCounter` for the whole file, so
  a ten-thousand-row spreadsheet counts the lead table once rather than ten
  thousand times — the shape that module's docstring prescribes;
- **running out of room does not throw the import away.** The properties are
  still created (they are not leads and cost nothing), the owners that did not
  fit are returned by row number in `capacity_blocked` with a `capacity_note`,
  and those properties sit in ENRICHMENT NEEDED. The limit stops the next
  addition; it does not roll back what fit, which is the rule `plan_limits`
  states for itself.

Two new tests cover both behaviours. All 102 tests across the wholesale suite
and those two platform guards now pass.

**The third failure is pre-existing and not mine.**
`tests/test_zoom_integration.py::test_requires_video_not_overwritten_when_user_edited_row`
fails identically with every wholesale change stashed — verified by
`git stash push --include-untracked -- app frontend tests`, re-running it, and
popping the stash back (your own uncommitted work on
`app/models/master_contact_models.py` and `app/routers/god_master_router.py` was
restored intact). It is unrelated to this module and is left for you to decide
on.

### Pre-commit checklist

1. Python import check — all 9 new modules + `registry` + `entitlements` + `ai_gateway` ✓
2. Attribute existence — caught one real bug: `Message.created_at` does not exist
   (it is `Message.sent_at`; `Reply` is `received_at`). Fixed and retested ✓
3. Endpoint smoke test — live uvicorn + HTTP, above ✓
4. Middleware — none changed; `app/main.py` diff is imports + two `include_router` ✓
5. Cross-cutting — scanned the `main.py` diff for CORS, middleware, startup hook
   and migration code: **zero hits** ✓
6. Secrets scan — 0 findings across all 20 new files and all 112 added lines ✓
7. Frontend dist — `npm run build` clean, 345 modules, rebuilt ✓
8. Diff review — `git diff --stat` on the 6 edited files: 112 insertions, 1 deletion ✓

---

## 9. Known issues and honest gaps

1. **`frontend/dist` is rebuilt but nothing is committed or pushed.** That is
   deliberate — the standing deploy pipeline is `.\deploy.ps1` from the repo root
   and it is Mike's call when to run it. Nothing has gone to GitHub or Render.
2. **BUYER outreach is queued, not sent.** `POST /wholesale/deals/{id}/disposition`
   composes the deal sheet, blocks the buyers it may not contact, and records a
   row per buyer — it does **not** hand the message to the email service yet.
   Seller outreach IS wired (see below); the buyer side is the same small piece
   of work and is improvement #1.
3. **Seller outreach is wired and real.**
   `POST /wholesale/deals/{id}/outreach` sends through `sms_service.send_sms`,
   which is the platform's own send path — so credential resolution, the
   suppression check, the consent record, the message row and the delivery
   receipt are the existing ones, not a second copy. It refuses a sandbox
   record, refuses a DNC contact, and reports a Twilio failure verbatim without
   ever pretending a send happened. Starting a multi-touch *cadence* still goes
   through the existing cadence engine on the lead; this is the first touch.
4. **Document files are referenced, not stored.** `file_name` / `file_url` are
   recorded; there is no upload endpoint for the file bytes yet. The platform has
   no generic tenant file store this module could reuse, and inventing one
   tonight was out of scope.
5. **No skip-trace, property-data or comps vendor is integrated.** Interfaces and
   manual paths only — see §6.
6. **Buy-box geography matching is exact-string, lowercased.** "Dallas" matches
   "dallas" but not "Dallas County". Fuzzy geography is a real improvement and a
   real source of wrong answers; it was left explicit.
7. **`_deterministic_read` price parsing** handles `$120,000`, `120000` and
   `120k`. It does not handle "a hundred and twenty thousand". The AI does.
8. **One pre-existing test failure, unrelated to this work.**
   `tests/test_zoom_integration.py::test_requires_video_not_overwritten_when_user_edited_row`
   fails on a clean tree too — see §8 for how that was verified. Yours to
   decide on.
9. **Memory has a stale repo path.** The saved note points at the retired
   OneDrive copy. Worth correcting so a future session does not build in the
   wrong place, as this one nearly did.

---

## 10. How to run a deal tomorrow

1. **Switch the module on.** God Mode → the customer's Features → enable
   **Wholesale real estate acquisition and disposition**. It requires `leads`,
   which every plan already includes. (An org whose `enabled_features` is `NULL` —
   the legacy "everything" state — already has it.)
2. **Wholesale → Command Center** appears in the left rail.
3. **Wholesale Settings** — set the investor percentage, the default fee and the
   transaction assumptions. Nothing here is a platform default you have to accept.
4. **Properties → Add property** (or Import a list). Address alone is enough.
5. **Open the deal → Seller tab → attach the owner.** Or Properties → tick →
   Enrich (which will tell you honestly that no provider is connected) → open the
   property → enter the phone.
6. **Seller tab → Start outreach.** Types a message and sends it as SMS through
   this workspace's own Twilio setup. A sandbox record refuses, on purpose —
   rehearse everything else on it and do the real send on a real property.
7. **Seller tab → Read a reply.** Paste what they said. It fills the structured
   answers and re-qualifies.
7. **Analysis tab → add comps → set repairs.** The ARV and the MAO appear with the
   arithmetic shown line by line.
8. **Offer tab → Request offer approval → Approve.** Then move the stage.
9. **Buyers screen → add buyers and buy boxes.** Or import a list with the buy-box
   columns in it.
10. **Deal → Buyer matching → Run matching.** Ranked, with every reason.
11. **Preview the deal sheet** (confirm for yourself that no seller detail is in
    it), then send to the buyers you pick.
12. **Record their responses → Assignment → Title → Close and record the fee.**
13. **Command Center** now shows all of it.

Use the **Sandbox** tick on a property and on a buyer to rehearse the whole thing
without touching a single real number on the dashboard.

---

## 11. Next five improvements, in the order I would do them

1. **Wire disposition into the real send paths.** `WholesaleBuyerOutreach` →
   `email_service` for the email channel and `sms_service` for SMS, keeping the
   `blocked_reason` behaviour exactly as it is. This is the single biggest gap
   between "records the outreach" and "does the outreach".
2. **Start the seller cadence from the deal room.** A button that enrols the
   seller lead in a wholesale cadence template through the existing engine, so
   READY FOR OUTREACH becomes one click rather than a trip to the Leads screen.
3. **Document upload.** A real file endpoint for the contract and assignment
   PDFs, then an e-sign adapter behind the existing `esign_provider` slot.
4. **One skip-trace adapter.** Pick a vendor, write the class, add the registry
   line. The interface, the cost caps, the status indicator, the failure handling
   and the manual fallback are already there and tested.
5. **A saved-search / buy-box alert.** When a new deal matches a buyer above a
   threshold they set, tell them. The matching engine and the reasons already
   exist; this is a query and a notification.

---

## 12. Design decisions worth not undoing

- **Nothing invents a number.** `arv_from_comps([])` is `None`, not a guess.
  `money("garbage")` is `None`, not `0`. Missing repairs are treated as zero
  **and warned about loudly**, because an unknown repair number is the commonest
  way a wholesale offer goes wrong.
- **Every figure carries its provenance** — ESTIMATED / IMPORTED / MANUAL /
  VERIFIED — in the schema and on every screen that shows it.
- **A manual or verified ARV is never overwritten by a comp set.** A person who
  typed a number knows more than the median of four comps.
- **A buy-box dimension the buyer never specified is not counted against them.**
  Otherwise a sparse buy box scores badly for being sparse, which teaches people
  to invent constraints to make the number go up.
- **A skipped send leaves a row.** An opted-out buyer gets a `blocked_reason`, not
  silence. Silence is how somebody concludes the system is broken.
- **404, not 403, for another tenant's record** — so an id cannot be probed for
  existence from outside the organization that owns it. Same choice `lead_scope`
  already makes.
- **The fee collected at close is the one number nothing computes.** It is a fact
  about money that moved; a platform that derived it would be reporting its own
  arithmetic as revenue.

---

# PHASE 2 — product review, operational completion, deployment readiness

**Done:** 23 September 2026. Surgical pass over the Phase 1 module. Nothing was
rebuilt, no second wholesale implementation exists, and the platform was not
redesigned.

## 13. What Phase 2 changed

### 13.1 Buyer outreach actually sends

`app/services/wholesale_disposition.py` (new) is the only place a deal sheet
leaves the building. It reuses the platform's own machinery rather than adding
a second sender:

| Concern | Reused |
|---|---|
| Email | `outbound_email_gate` + `email_service.send_email` |
| SMS | `sms_service._resolve_twilio_creds` |
| Sandbox / DNC | `test_records.is_outreach_eligible` |
| Demonstration tenants | `demo_guard.block_if_demo` |

`preflight()` refuses in a fixed order and each refusal is recorded, never
swallowed: **sandbox → opted out or inactive → no address on file → already sent
→ demonstration tenant → deployment switch off.**

Two new env switches, **both default OFF**, both named on the Settings screen so
an operator can see what to set:

```
OUTBOUND_EMAIL_WHOLESALE_BUYER_DISPOSITION
OUTBOUND_SMS_WHOLESALE_BUYER_DISPOSITION
```

A send is never automatic. The screen composes and previews; a person ticks the
buyers and presses Send; the button names the count it will send to. Each
attempt stores `provider_message_id`, `provider_error`, `provider_result`
(verbatim), `attempts` and `last_attempt_at` — five columns appended to
`wholesale_buyer_outreach` through `app/auto_migrate.py`'s `COLUMNS_TO_ADD`,
which is additive and non-destructive. Resend is idempotent against an already
sent row.

`SELLER_FIELDS_NEVER_SENT` plus `seller_leak_check()` is the mechanical guard on
the buyer deal sheet: seller phone, seller email, seller notes, motivation notes,
internal negotiation notes and the seller conversation never appear in it, and a
test asserts it rather than a reviewer remembering it.

### 13.2 Geography is normalized, and refuses to guess

`app/services/wholesale_geo.py` (new, 374 lines). Handles case, whitespace,
punctuation, ZIP+4, `Texas`/`TX`, `Ft.`/`Fort`, `St.`/`Saint`, and the `County` /
`Co.` / `Parish` / `Borough` suffixes. Every verdict carries a sentence a person
can read.

What it deliberately does **not** do: `resolve_containment()` returns `None` and
will keep returning `None` until a real geographic dataset is connected. The
module does not assert that a city is inside a county, because nothing available
to it knows that. The consequence is visible and intended — a county criterion is
**unknown**, not satisfied, when only a city is on file.

Disqualification requires the property to be positively **outside every
constrained field with none unknown**. A blank county column can never exclude a
buyer from a list.

> `Dallas Co.` is Dallas County, not Dallas, Colorado. `co` is both, and the
> county-word check runs before the state peel. This was a real bug, caught by
> `tests/test_wholesale_geo.py`.

### 13.3 Seller cadence is a control surface, not a second engine

The 9-touch sequence is the platform's existing `cadence_service`. Phase 2 adds
`cadence_status`, `_cadence_blockers`, `control_cadence` and
`stop_cadence_quietly` to `wholesale_service.py`, plus
`GET|POST /wholesale/deals/{id}/cadence`.

Start / pause / resume / stop, with the state, the next touch and the blockers
shown. It **stops on its own** for: opt-out or DNC, invalid or suppressed
channel, deal closed, deal dead or cancelled, manual stop — and the automatic
stop is audited as `automation`, not as the person who happened to be looking.

A sandbox deal **cannot be enrolled at all**. `CADENCE_SMS_SENDING` defaults off
and the screen says so rather than looking like it is working.

> `set_stage` — the path a person takes when they mark a deal dead from the UI —
> did not stop the cadence; only the internal `_set_stage_unchecked` did. Caught
> by `tests/test_wholesale_cadence.py`.

### 13.4 The screens were looked at, not just built

All five were rendered in a browser at 1512px and 820px, screenshotted and read.
Defects found and fixed:

| Defect | Cause |
|---|---|
| Every metric label and note drawn as its own bordered, elevated card | Phase 1's mechanical restyle put `stat-card` on the grid **and** on the three text rows inside each tile |
| Pipeline board clipped mid-word at 19 stages | flex row + `overflow-x` with no affordance; now a wrapping grid |
| Footnote overlapping the tiles it explained | `.ws-panel-note` carried a negative top margin meant for use under a title |
| Panel headers wrapping into two ragged lines | the action group sized itself to its widest child instead of taking the width the heading left |
| Wide tables running off the panel edge and being clipped | no scroll container above the 768px breakpoint; now every `.ws-table` sits in `.ws-scroll` |
| `single_family`, `qualified_opportunity`, `offer_submitted` shown to users | stored keys printed raw; now `fmtLabel` / `fmtLabels` |
| `true` shown eight times on the seller tab | `String(true)`; now `fmtBool`, which keeps `null` as **not stated** — not the same as *no* |
| A buyer column headed **State** that held `reliability_rating` | mislabelled; now **Rating**, and **Typical close** carries its unit |
| Import panel permanently open above the list on two screens | now behind a toggle, like Add |
| Three header buttons duplicating the left rail | removed from the Command Center |
| The last unstyled native control in the module | the deal-room stage `<select>`, now `.ws-input` and labelled |
| `Property type single_family in the buy box.` | missing verb **and** a raw key, in a backend reason string |

New: **"What is waiting"** on the deal room Overview. One line per unfinished
thing on the deal, each with the tab that answers it — pending approvals, an
opted-out owner, no contact method, outreach not started, a conversation flagged
for a person, no ARV, matched buyers nobody has contacted. Every line is a fact
already in the deal room payload. It predicts nothing and recommends nothing;
when there is nothing outstanding it says so rather than inventing an errand.

### 13.5 Cross-tenant attack test

`tests/test_wholesale_cross_tenant.py` (new). Tenant A creates one of every
object the module has an endpoint for. Tenant B — a real, signed-in user of a
different organization, with a valid token — then calls **every id-bearing
endpoint** with A's identifiers. Not one may return 2xx.

A fourth test in the file compares the attack list against `app.routes` at
runtime, so an endpoint added later without a line in the attack list fails this
file rather than going quietly untested.

> **It found one.** `GET /wholesale/properties/{id}/enrichment` answered **200**
> for any id at all, including another tenant's. No data leaked — the query was
> already org-scoped — but the endpoint never checked the property existed in the
> caller's organization, which made it an existence oracle and the one
> id-bearing endpoint in the module that did not 404 on somebody else's record.
> Fixed by resolving the property in-org first.

## 14. Files Phase 2 added or changed

### New

| File | What it is |
|---|---|
| `app/services/wholesale_geo.py` | Geography normalization and the containment seam |
| `app/services/wholesale_disposition.py` | The only path a deal sheet takes out |
| `tests/test_wholesale_geo.py` | 22 tests, no database |
| `tests/test_wholesale_disposition.py` | 21 tests |
| `tests/test_wholesale_cadence.py` | 18 tests — nothing here sends |
| `tests/test_wholesale_cross_tenant.py` | 4 tests — the attack |

### Changed (all additive)

| File | Change |
|---|---|
| `app/auto_migrate.py` | 5 columns appended to `wholesale_buyer_outreach` |
| `app/services/outbound_email_gate.py` | One new source + its env var, defaulting OFF |
| `app/services/wholesale_matching.py` | Geography delegated to `wholesale_geo`; `_label()` for reason sentences |
| `app/services/wholesale_service.py` | Cadence control surface; plan capacity on seller attach |
| `app/models/wholesale_models.py` | The 5 outreach provider-result columns |
| `frontend/src/pages/wholesale/*` | The UI fixes above; `wholesale.css` rebuilt on the platform's tokens |

## 15. Test state

```
162 passed
  test_wholesale_analysis.py      25
  test_wholesale_matching.py      14
  test_wholesale_flow.py           4
  test_wholesale_guards.py        28
  test_wholesale_geo.py           22
  test_wholesale_disposition.py   21
  test_wholesale_cadence.py       18
  test_wholesale_cross_tenant.py   4
  test_lead_capacity_matrix.py    12   (the platform's own, re-run)
  test_plan_limits_coverage.py    14   (the platform's own, re-run)

frontend: npm run build             ✓ 345 modules, 5.20s
          wholesaleNav.test.mjs     11 passed
```

**Nothing in this suite sends anything.** Every outbound switch is unset in the
test environment, and the tests that exercise a send path assert the refusal.

## 16. Known gaps, stated plainly

1. **City-to-county containment is not resolved.** `resolve_containment()`
   returns `None` by design. Until a geographic dataset is connected, a buy box
   constrained by county will read **unknown** for a property that has only a
   city on file, and that buyer stays in the list rather than being excluded.
2. **Documents track the slot, not the file — and now say why.** A document row
   records type, title, parties, signature state and the file name as the
   operator stored it. Phase 2 checked whether the platform already had a file
   capability before deciding this was a gap, and **it does**:
   `app/services/mobile_storage.py`, whose `store_upload()` accepts PDFs and
   refuses with a plain reason until `MEDIA_STORAGE_BACKEND=s3` plus
   `MEDIA_S3_BUCKET` and AWS credentials are set. The deal room now reports that
   capability (`document_storage`) and the Documents panel says, in this
   deployment's own words, that the file name is a reference rather than a held
   file. **No second storage layer was built.** Wiring an actual upload through
   `mobile_storage.store_upload()` is a small, well-defined follow-up once that
   storage is turned on — it is deliberately not done here because it is new
   product surface, not a review fix. E-sign remains a provider slot with a
   manual adapter.
3. **No paid data provider is connected.** Every provider slot ships with a
   manual adapter. The cost caps default to 0, which means no paid calls.
4. **The scroll affordance on wide tables is the browser's scrollbar.** Verified
   programmatically (`scrollWidth > clientWidth`) and styled explicitly on
   `.ws-scroll`; the headless browser used for the visual review does not draw
   scrollbars, so that one pixel-level detail is unconfirmed by screenshot.
5. **Approve is the primary button on an approval row.** Defensible — it is the
   expected action — but it is a money decision, and a reviewer may reasonably
   want both buttons neutral. Left as-is deliberately rather than changed
   without being asked.


---

# PHASE 3 — the production-usability build

**Done:** 23 September 2026. The module was made workable, not rebuilt. There is
still one wholesale implementation, and no working backend logic was replaced.

## 17. What Phase 3 changed

### 17.1 Files became real

`app/services/wholesale_files.py` (new) is the ONE storage path for property
photos, comp photos, deal documents and buyers' proof of funds. It delegates to
the platform's own `mobile_storage._put_object` for S3 and adds a `local`
backend, because `mobile_storage` refuses every upload unless S3 is configured —
which made local development impossible.

**`storage_key` never leaves the server and there is no URL column.** Retrieval
is `GET /wholesale/files/{id}`, which re-resolves the caller's organization every
time. Content type is sniffed from magic numbers rather than believed; object
keys are `uuid4` plus a suffix derived from the sniffed type, never the uploaded
filename; 25 MB cap; jpeg/png/webp/heic/pdf/doc/docx only; `_local_path()`
refuses anything escaping the root.

`capability()` reports backend, whether uploads work, whether storage is durable,
and the env var to set. **An upload control is not rendered when the deployment
cannot store the file** — a button that looks live and 503s is how somebody
concludes the product is broken rather than unconfigured.

Because an `<img>` cannot send an Authorization header, `AuthImage` fetches the
bytes with the session and renders from an object URL. The alternative — making
the objects public — is the thing the brief forbids.

### 17.2 Save, Edit and Delete stopped being ambiguous

The property is editable in place. Comps have **INCLUDE IN ARV / EXCLUDE FROM
ARV** as one control and **DELETE COMP** as a different one, in different words
and different colours, because "Remove" could have meant either and they are not
the same act. Excluding dims the row; it never hides it. Deleting asks by name
and says it cannot be undone.

Every editor closes **only on a successful save**. `act()` now returns whether it
worked, because an editor that closes on failure throws away what the person
typed and leaves an error about a form they can no longer see.

### 17.3 The numbers were written out

- **Comps**: median AND average $/sqft side by side, the range, and each comp's
  distance from the median, so an outlier is a fact rather than an argument.
  `analysis.comp_statistics()` lives in the service and is tested with plain
  objects.
- **The negotiation**: every offer and counter in order, each carrying **the MAO
  as it stood at the time**, so a later change to the repair estimate cannot
  retroactively make a bad offer look disciplined.
- **The assignment**: buyer price − contract price − costs = expected fee, with
  the fee actually collected on its own line and the difference between them on
  another. Nothing computes the collected fee.

### 17.4 Closing locks the money

`POST /deals/{id}/close` sets `economics_locked`. `PATCH /deals/{id}/contract`
now **refuses a price change on a closed deal** with a 409 naming the correction
route. `POST /deals/{id}/economics-correction` needs a sentence and writes its
own before-and-after event. The lock is on the money only — a typo in a closing
location is not a revenue restatement.

`POST /deals/{id}/lost` takes a reason from `LOST_REASONS`. Free text loses the
ability to ask "how many did we lose on price".

### 17.5 The disposition desk

`GET /deals/{id}/buyer-board` puts every buyer on the deal in one comparison:
what they offered, what we make on it, whether they can pay, how fast they close.
**Nothing ranks or recommends** — no best-buyer badge, no sort by offer. The
highest offer from somebody with no proof of funds is not the best buyer.

`POST /outreach/{id}/response` takes what a person heard, including the buyer's
offer amount, which previously could not be entered anywhere. It works whatever
the delivery status says, because a buyer who phoned in an offer after a failed
send is the case it exists for.

`POST /deals/{id}/select-buyer` records a decision a person made and stamps who
made it. The assignment still needs its own approval.

`verified` proof of funds is labelled **"Verified by a person"**. Nothing here
reads a bank letter.

### 17.6 Endpoints Phase 3 added or widened

New: `/files/capability`, `/files/{id}` (GET/PATCH/DELETE),
`/properties/{id}/photos` (GET/POST), `/deals/{id}/documents/upload`,
`/comps/{id}/photo`, `/outreach/{id}/proof-of-funds`, `/outreach/{id}/pof-status`,
`/outreach/{id}/response`, `/deals/{id}/offers` (GET/POST), `/offers/{id}`,
`/deals/{id}/lost`, `/deals/{id}/economics-correction`, `/documents/{id}`
(DELETE), `/operating-board`, `/deals/{id}/buyer-board`,
`/deals/{id}/select-buyer`, `/buyers/{id}` (DELETE).

Widened: `ContractIn` and `TitleIn` (17 Phase 3 columns existed on the model with
no way to write them); `CompIn` (`year_built`); `/properties` and `/deals` lists
(cover photo, seller, last activity, optional `next_action`, stage/band filters).

**70 wholesale routes.** No environment variable names were invented.

### 17.7 Deleting a buyer

`DELETE /wholesale/buyers/{id}` deletes a buyer **who has no history**, with
their buy boxes. A buyer attached to outreach, matches or an assigned deal is
**refused with a 409** that says how much history and what to do instead (mark
them inactive). Somebody will ask who we sold 1418 Cedar to, and the name has to
still be there.

## 17.8 Two things the end-to-end walk found that no screen showed

1. **A property field could not be cleared.** `_apply_property_fields` skipped
   nulls — correct when creating, wrong when editing. The workspace sends null
   for an emptied box, the save returned 200, and the old value was still there.
   It now takes `allow_clear`, which the PATCH route passes and create does not.
2. **A manually recorded seller reply never reached the conversation.**
   `apply_seller_reply` read the message, updated the profile and discarded the
   message. With no inbound channel connected — every deployment today — the
   new conversation thread was permanently empty. The manual path now writes the
   message to the platform's own `replies` table against the seller's Lead, with
   `source="manual"`, and the thread labels it **"recorded by a person"** so a
   transcription can never be mistaken for something that arrived on its own.
   The background path does not write, because the conversation engine already
   recorded the inbound message it is reacting to.

## 18. Two bugs worth knowing about

1. **`.stat-card` in `frontend/src/styles/shared.css` hardcodes a dark navy
   gradient** instead of reading `var(--bg-card)`. It is the only class in that
   file that does, and it renders wrong on every light-themed white label. The
   wholesale module no longer uses it. **It was not changed** — platform-wide
   surface, outside this module's scope, one line to fix when somebody owns it.
2. **The cross-tenant coverage guard keyed on path alone**, so a new METHOD on a
   covered path passed silently. It now keys on **(method, path)**. It went red
   immediately on `DELETE /wholesale/buyers/{id}` — the endpoint that exposed it
   — and on nothing else.

## 19. Phase 3 test state

```
FULL SUITE: 5,486 passed · 14 skipped · 1 failed (51m 38s)
  The one failure is test_zoom_integration.py::
  test_requires_video_not_overwritten_when_user_edited_row — confirmed
  pre-existing in Phase 2 and untouched by this module.

199 wholesale tests passed
  workflow 36 · analysis 33 · guards 28 · geo 22 · disposition 21
  cadence 19 · files 16 · matching 14 · flow 5 · cross-tenant 4

After the `replies` write, the neighbouring suites were re-run together:
  wholesale + lead + cadence + reply + sms — 759 passed, 4 skipped, 0 failed

frontend: npm run build ✓ · wholesaleNav.test.mjs 11 passed
visual:   5 screens × 7 deal-room tabs × 2 widths × 2 themes, zero errors
```

`test_the_phase_three_journey_lead_to_fee_collected` walks the brief's own
sequence in one test — LEAD → CONVERSATION → ANALYSIS → OFFER → CONTRACT →
DISPOSITION → CASH BUYER → TITLE → CLOSING → FEE COLLECTED — including the
part where a person deliberately picks the LOWER offer because that buyer has
verified funds, and ends by asserting all eight audit events are on the record.

`npm run build` does not catch an undefined identifier — `fmtMoney` was used on
the property list, compiled cleanly and threw in the browser. Every screen is
rendered and read before anything is called done.

## 20. Still open after Phase 3

1. City-to-county containment is still unresolved by design.
2. The buyer deal sheet carries no images: attaching them needs publicly
   reachable URLs, which the file rules forbid. It needs a signed-URL design and
   a decision about exposure, not a quick fix.
3. `with_next_action` clamps the list page to 50 rather than batching the
   per-deal lookup. A clean follow-up.
4. No paid provider is connected; cost caps default to 0.
5. `theme.js` resolves `localhost` to the light BookaBoost brand. Worth knowing
   before anybody reviews colour locally.

---

# PHASE 4 — OPERATOR EXPERIENCE + REAL FILE STORAGE

Phase 3 made the module correct. Phase 4 was about whether somebody can sit in
it all day. Nothing was redesigned, no backend logic was rewritten, and the
deal-state architecture is untouched: every change below is either a storage
configuration that had never been made, a payload field the UI needed and the
server already knew, or a rearrangement of things that were already on the
screen.

## 21. Media and documents actually work now

### 21.1 What was wrong

`wholesale_files.py` supports two backends — S3 and a local disk — and the
review environment had neither configured. Uploads were therefore refused with
an honest message, which is correct behaviour and completely useless for a
review: nobody could upload a photo, set a cover, or open a contract.

### 21.2 What was done

`.env` (gitignored, not in the repo) now carries:

```
MEDIA_STORAGE_BACKEND=local
WHOLESALE_LOCAL_MEDIA_ROOT=C:/Dev/advisorflow-web/.local_media
```

`.local_media/` is in `.gitignore`. No new environment variable names were
invented — both already existed in `wholesale_files.py`.

`capability()` was corrected rather than extended. It previously reported the
local backend as `durable: false`, which was wrong: a file on the server's own
disk survives a restart. It now distinguishes the two things that actually
matter and says where the bytes are:

```python
"durable":    b in (BACKEND_S3, BACKEND_LOCAL),
"replicated": b == BACKEND_S3,
"where":      "Amazon S3" | "this server's disk" | None,
```

The upload control reads `· stored on this server's disk, not replicated`
instead of the previous, incorrect `not durable`. It is one clause, secondary to
the button, and it never claims more than it is.

### 21.3 The bug this found

`document_json()` returned neither `file_id` nor anything about the stored file.
A document with a real uploaded PDF behind it rendered **identically** to a
filename-only reference: no View button, no size, no type, no upload date. It
was invisible in every deployment, not just this one. `document_json(d, stored)`
now carries a `stored_file` object, and the deal room batches the lookup for the
whole list in one query.

The documents workspace was rebuilt around that distinction. Every row says
which of three states it is in — a file is held, a name is held with no file, or
nothing is attached — and the actions follow: **View / Download / Replace** when
there is a file, **Attach file** when there is not, **Edit / Delete** always.
The signature select is tinted by status. There is no "send for signature"
button, and a disclosure says why: no e-signature provider is connected, and a
button that opened nothing would be a promise the product cannot keep.

### 21.4 How it was validated

`p4_media_check.py` drives the **running** review server over HTTP with a real
token for the real sandbox organization, against the real database, and checks
the bytes land on real disk. **38 checks, 38 passing**: capability reporting;
photo upload, list, authenticated serve, on-disk presence, cover change,
caption, delete; document upload, download, replace, delete, audit history; and
the refusal of a renamed executable. Everything it creates is marked `is_test`
and deleted again, so the review workspace is left exactly as it was found.

## 22. The Command Center answers the five questions

It had to answer, without a click: money in play, what needs attention, active
deals, **what is at risk or stalled**, and upcoming closings. Four of those were
already there. The fourth was not.

### 22.1 "At risk" is read off the record, never predicted

`operating_board` now computes a per-deal `risk` from two signals, both facts:

- **overdue** — the closing date has already passed and the deal is still open.
- **stalled** — nothing has been written to the deal's event log for
  `QUIET_DAYS` (7). The threshold is a stated number, the screen prints it, and
  a deal with no events at all falls back to its own age.

A deal with neither signal returns `None`. There is no score, no model, and no
reassuring green badge. The board returns `at_risk`, `at_risk_total` and
`quiet_days`; `headline.at_risk` feeds a stat tile that is the only red one on
the page. The empty state says what was checked rather than "nothing to show".

`tests/test_wholesale_board.py` pins this down — 8 tests covering the threshold
on both sides, the singular/plural day wording, overdue outranking silence, a
future closing date not being a risk by itself, the no-history fallback, and a
deal with no dates at all not being invented into a risk.

### 22.2 The pipeline is a route, not a wall

Nineteen equally-sized tiles told you that nineteen stages exist and nothing
about where the work is piled up. The stages are now grouped into the four
things that actually happen to a deal in order — finding the property, talking
to the seller, analysis and offer, contract to close — with closed and dead kept
apart so nineteen dead leads cannot set the scale of the live pipeline.

Two readings of the same numbers: a proportional strip across the top, where
segment width is each phase's share of the live count, and columns beneath it
where every stage keeps its own count, its own expected fee and its own link
into the property list. Each stage row's background is a bar sized against the
busiest stage; the number is printed beside it either way, because a bar is a
shape, not a second number.

Stage keys are org-configurable, so any key this file does not recognise appears
under **Other stages** rather than being dropped.

The attention cards now sit two to a row. The page gained a whole panel and got
**shorter**: 2,199 → 2,135 px.

## 23. The Deal Room header prioritises the economics

The header carried nine equally-weighted money rows in one narrow column, so the
spread had to be found rather than seen. The top line is now the six figures a
deal is judged on — **ARV, Max offer (MAO), Contract price, Buyer price,
Expected spread, Closing** — at display size, with **Fee collected** appearing
in green beside them once there is any.

Nothing was removed. Repairs, seller asking, our offer, the rest of the
calendar, the disposition counts, the seller and the title company are all still
on the header, below a rule, at the smaller weight. Seller and title are
secondary, as the brief asked, but still one glance away.

The spread is made prominent by size and position rather than colour: amber and
red mean *attention* and *problem* elsewhere on this screen, and the number the
business runs on should not read as a warning.

## 24. The seller workspace

Seven full-width panels stacked to 2,337 px, three of them mostly empty space to
the right of a text box. It is now two columns and **1,582 px** — the same
content, nothing removed.

- **Left.** The qualification and the answers it was scored from, in one panel
  (reading a score without its answers beside it meant scrolling past the whole
  thing). Then the composer. Then the conversation thread.
- **Right.** The follow-up sequence and the contact permissions — both settings
  *for* the conversation rather than parts of it.

**The composer was the real fix.** There were two boxes that looked identical
and did opposite things: one sends a message to the owner, one records something
the owner already said. They are now one control with an explicit choice —
**Send a message** / **Record what they said** — so the two can no longer be
confused. The send path is unchanged: it still posts to the existing gated
`POST /wholesale/deals/{id}/outreach`, which applies the suppression list, the
consent record and `test_records.is_outreach_eligible` before anything leaves.
Every qualification and compliance element is intact, including the
"not stated is not permission" note.

## 25. Comps, negotiation, buyers, assignment

**Comps.** Median and average ARV logic untouched. The table now shows
**distance** as its own scannable column — the field was already on the model
and in the payload, and it is the thing that decides whether a comp is a comp at
all. A comp with no distance on file says "not set" rather than reading as
nearby. Photo, address, distance, sold date, sale price, sq ft, $/sq ft with its
deviation from the median, beds/baths and inclusion status are all on the row,
and **Include/Exclude** (arithmetic) stays visually separate from **Delete**
(existence), which is red and confirms.

**Negotiation.** Already a chronological ledger carrying party, amount, MAO as
it stood at the time, status, timestamps and notes. It gained a coloured rail
per party — ink for our move, amber for theirs — so the shape of the back and
forth is visible before a number is read. The approval gates are untouched: no
UI change added a path around them.

**Buyer matching.** The engine is unchanged. The table now leads with what
decides the choice — match %, where they buy, whether they can take it, how fast
they close, track record — and the full criterion-by-criterion reasoning is
folded into a **View why** disclosure on each row rather than printed at all
times. The backend supplies `geography`, `max_price`, `buy_box_count`,
`typical_close_days`, `reliability_rating`, `proof_of_funds_on_file`,
`do_not_contact` and `activity` on each match, all batched.

**Assignment and closing.** The chain was correct and quiet. The three outcomes
now carry an accent rail, and **Fee actually collected** is drawn as the
terminus it is — its own rule above it and the largest figure in the panel,
green once money has moved and an em dash until it has:

```
The buyer pays        $156,000
− We pay the seller   $144,000
= Gross assignment fee $12,000
− Other costs             $800
= Expected net fee     $11,200
  Fee actually collected     —
```

## 26. Global UX

- **Fifteen long explanatory paragraphs** across nine files were converted from
  primary operator content to a one-line `Note` plus a collapsed `Why`. The full
  text is still there; it is one click away instead of always on. Implementation
  and environment explanations are now secondary by construction.
- Panel padding, page gaps, panel titles, table rows and notices were all
  tightened in Phase 4's density pass.
- A layout bug the render found: a `Why` disclosure carried no trailing space,
  so on the Assignment and Providers panels its summary overlapped the field
  label beneath it. Fixed, and a check now walks every `Why` on every screen
  asserting its next sibling starts below it.

## 27. What Phase 4 did NOT do

- No backend logic was rewritten. The only service change is the addition of
  `_deal_risk` / `QUIET_DAYS` and the last-event lookup that feeds it.
- No deal-state or stage architecture was touched.
- No approval gate was weakened, bypassed or moved.
- No outbound surface was added. The seller composer reuses the existing gated
  route.
- No data was invented and no missing configuration was hidden. Where a provider
  is absent — comps, e-signature, buyer email, buyer SMS — the screen says so and
  names the switch.
- Nothing was committed, pushed or deployed.

## 28. Phase 4 test state

```
FULL SUITE: 5,495 passed · 14 skipped · 1 failed (50m 04s)
  The one failure is test_zoom_integration.py::
  test_requires_video_not_overwritten_when_user_edited_row — the same
  pre-existing failure Phase 3 recorded, untouched by this module.

206 wholesale tests passed (11 files)
  workflow 36 · analysis 33 · guards 28 · geo 22 · disposition 21
  cadence 19 · files 16 · matching 14 · board 8 · flow 5 · cross-tenant 4

LIVE-SERVER VALIDATION (running review server, real DB, real disk)
  p4_media_check.py     38 checks, 0 failed   media + documents end to end
  p4_payload_probe.py   22 checks, 0 failed   every field the new screens read

frontend: npm run build ✓
visual:   5 screens × 8 deal-room tabs × 2 widths × 2 themes, zero errors
          interaction sweep: stage row → filtered list, at-risk row → deal,
          next action → tab, View why opens, Exclude ≠ Delete, delete confirms
          first, composer switch changes the action, stored document offers
          View/Download while a reference offers Attach
          overflow check: no clipped table at 1180 px or 1512 px
          collision check: every Why disclosure clears the element below it
```

### 28.1 The one test Phase 4 changed, and why

`test_local_storage_is_offered_but_never_called_durable` asserted
`cap["durable"] is False` for the local backend. That assertion was wrong, not
the code: a file written to the server's own disk survives a restart. The test
is now `test_local_storage_is_offered_and_says_exactly_what_it_is` and asserts
the more precise contract — durable **true**, replicated **false**, `where` set,
and the reason still naming `MEDIA_STORAGE_BACKEND=s3` so the fix stays
actionable. No assertion was deleted to make something pass; one inaccurate one
was replaced with three accurate ones.

## 29. Still open after Phase 4

Everything listed in section 20 is still open. Phase 4 adds:

1. **Field labels are not programmatically associated.** Most inputs use
   `<div class="ws-field"><label>Text</label><input/></div>` — no `for`, no
   wrapping — so a screen reader does not announce the label. Phase 3 fixed this
   on the controls it touched (`ws-stage-select`, the per-offer status select);
   the rest of the module has not been swept. It is a mechanical change across
   roughly sixty fields and was left out of Phase 4 rather than expanding scope.
2. **The comps table scrolls horizontally below about 1,250 px.** It has a real
   scrollbar and clips nothing, but nine columns plus a three-button action cell
   is at its limit. If a tenth column is ever wanted, something has to move into
   the row's sub-line.
3. **Local media storage is a review setting, not a production one.** `.env`
   sets `MEDIA_STORAGE_BACKEND=local`. Production must set `s3`; the capability
   endpoint, the upload control and the settings screen all say so.
4. **"Numbers at a glance" on the deal Overview now overlaps the header.** Since
   the header carries the six key figures at display size, that panel largely
   repeats them. It was left alone because it also carries provenance tags the
   header does not show — worth a decision rather than a quiet deletion.

---

# PHASE 5 — WHOLESALE PRODUCTION EXPERIENCE + PLATFORM CLEANUP

Phase 4 made the module liveable for the operator. Phase 5 is about the two
people the operator has to deal with — the investor and the seller — and about
the paper between them. It also carries a separate, deliberately isolated
platform workstream that touches nothing in this module.

Everything below follows the same rule: **verify the current implementation,
identify the gap, reuse the existing infrastructure, make the smallest correct
change, test it, document it.** Where the answer turned out to be "this already
works", that is recorded as a finding rather than rebuilt.

---

## 30. The publication boundary

### 30.1 The thing that had to be impossible

A deal room holds seller motivation, a qualification score, the MAO, the
negotiation history, every other buyer's offer, the internal margin and the
audit trail. An investor must see none of it. A seller must see none of the
buyer side of it.

Hiding elements in React is not a boundary. The boundary is a **whitelist
serializer**, `app/services/wholesale_publication.py`, which builds the external
payload key by key from the deal. It does not take the internal payload and
remove things: a field added to the deal room next year appears in the operator
payload and is *absent* from the buyer payload until somebody writes a line to
put it there. That is the difference between a boundary that decays and one that
does not.

```
FORBIDDEN_TO_BUYER   43 keys   motivation, qualification_*, mao, offers,
                               approvals, internal notes, other buyers, audit
FORBIDDEN_TO_SELLER  35 keys   assignment fee, buyer identities, buyer offers,
                               MAO, margin, qualification score, matching
```

`leaked_keys(payload, forbidden)` walks the built payload recursively and is
asserted against in the tests. When a deal is not published, the keys are
**absent** rather than null — a null still tells a reader that a field exists.

### 30.2 It was proved breakable, then proved caught

The boundary tests are only worth having if they can fail. `assignment_fee` was
injected into the buyer payload under the innocent key name `spread`, and the
value-level assertion (`assert '12000' not in body`) caught it. Backup restored,
22/22 green again. The tests assert on **values**, not key names, so renaming a
leak does not hide it.

### 30.3 Access is a token, never an id

`WholesaleShareLink` follows the existing `ProposalToken` pattern exactly —
`secrets.token_urlsafe(32)`, a row per recipient, revocable, optionally expiring,
with `WholesaleShareView` recording every open and every action. No sequential id
appears in any external URL.

`_resolve()` returns the **same 404 with the same sentence** for a revoked link,
an expired link, a wrong-audience link, an unknown token and an unpublished deal.
A different message for each would be an oracle.

The public router is mounted on its own prefix (`/wholesale-rooms`) with no
feature gate and no tenant dependency, so it cannot inherit — or be assumed to
inherit — the module's gates. The operator half sits on `/wholesale` with all
three.

### 30.4 What a buyer can actually do

Interested, make an offer, request a walkthrough, ask a question, pass. Each one
writes to the **existing `WholesaleBuyerOutreach` row** — the same row the
disposition desk already reads — rather than to a second "portal response" table
that would then need reconciling. A buyer action with no outreach row is a 409,
not a silently created record.

There are **no seller write routes at all**. A test asserts this by enumerating
the public router.

---

## 31. Media: internal vs published

`WholesaleFile` gained `category` (18 values) and `buyer_visible`, default
**False**. Uploading a photo does not publish it. The gallery shows a *Shared*
badge on the ones that are, the checkbox is per photo, and
`property.photo_published` / `_unpublished` are logged events.

Also added: multi-file upload (`POST /properties/{id}/photos/batch`, reporting
per file whether it was `uploaded` or `rejected` and why), reordering, captions,
cover selection and delete. One upload path, one serve path, one delete path —
unchanged from Phase 3.

---

## 32. Money that has actually arrived

**Expected fee is not revenue.** The two were structurally separable already;
Phase 5 made them separate facts:

- `POST /deals/{id}/close` no longer requires `wholesale_fee_collected`. Closing
  a deal is a closing, not a payment.
- `POST /deals/{id}/fee-collected` records the money: amount, method, reference,
  who recorded it, when, and a variance note when it differs from the expected
  figure.
- `payment_state(deal)` returns exactly one of `not_closed`, `payment_pending`,
  `fee_collected`. A closing date in the past moves nothing.
- Command Center **FEES COLLECTED** is fed only by recorded collections; the
  headline gained `awaiting_payment` so the gap is visible rather than blurred.

`closing_checklist(deal)` lists what a closing still needs, from the record.

---

## 33. The approval anomaly (A15)

**The finding: it is a real hole, not sandbox data.**

`request_approval` accepted any `kind`, at any time, for any `amount`, and never
checked whether the numbers being approved existed. That is how an assignment
approval for $12,000 was sitting on a deal with no selected buyer and no buyer
price: nothing had gone wrong, because nothing was checking.

The fix is narrow on purpose. `APPROVAL_BLOCKING_KINDS = ("assignment",)` — an
assignment approval with no buyer and no buyer price is refused with a 409 naming
what is missing, overridable with `acknowledge_missing`. An offer approval before
ARV is a genuine question somebody might want to ask, so it warns and proceeds.
`approval_readiness()` feeds the deal room so the buttons are state-aware rather
than three identical buttons that all look equally valid.

---

## 34. The contract chain (A6, A7, A8)

### 34.1 What this module does not do, and will not

It does not draft a contract. Not from a template, not from a clause library,
not from a model. A wholesale assignment is a binding real-property agreement
whose sufficiency turns on state law and the facts of the deal, and software
that emits one emits a liability with a confident font.

What it does instead:

**It keeps the customer's own forms.** `WholesaleContractTemplate` stores a file
the customer's attorney produced, with its provenance — what they call it, who
wrote it, which state it was written for (free text, deliberately not validated
into a state code, because a code would imply this module had checked the form is
good there), and their own note to whoever uses it next. The file goes through
the same media path as everything else and **is never parsed**. Templates are
archived, never deleted: a deal papered with one must still be able to say which.
The library starts **empty** — no form ships with the product.

**It hands over the facts.** `GET /deals/{id}/fill-sheet` returns every value the
deal already holds that a purchase or assignment document typically needs —
address, parcel, parties, price, earnest money, dates, title company — labelled
and grouped in transfer order. A blank stays blank and carries a note saying
where that fact actually comes from:

```
Legal description   not on file
                    Not held here. It comes from the title commitment or the deed.
```

A legal description guessed from a street address is how a wrong parcel reaches a
closing table. `test_the_fill_sheet_contains_no_contract_language` asserts the
whole payload against the vocabulary that only appears in a drafted agreement
(*whereas*, *hereby*, *the parties agree*, *assigns and transfers*, …).

### 34.2 The document lifecycle (A7)

Nine states, in `app/services/wholesale_esign.py`, each one a fact somebody or
something put there:

```
draft → ready_for_review → approved → sent → viewed → signed
                                        ↘ declined
  any non-terminal state → voided | superseded
```

`TRANSITIONS` declares the legal moves and `transition()` enforces them, refusing
with a sentence that names what *is* possible from here rather than "invalid
transition". Two rules worth stating:

- **Nothing advances a status on its own.** No scheduler, no inference from a
  date, no "it is probably signed by now". `viewed` is set only by a real fetch
  through a share link; `sent` only when a provider confirms it went; `signed`
  only from a provider callback or a person attaching the executed copy.
- **`signed` is refused when nothing is attached.** A document cannot be marked
  signed with no signed copy and no external reference behind it.

The pre-lifecycle vocabulary (`needed`, `uploaded`, `executed`, `void`) is
**mapped on read**, not migrated in place. A document row is evidence about a
legal document, and quietly rewriting its recorded state to fit a newer
vocabulary is a bad habit to start. `document_json` now carries `status_key`,
`status_label` and `allowed_next` so no client re-implements the mapping.

### 34.3 Signatures (A8)

Same registry shape as `wholesale_enrichment`: a base class, a manual provider
that is always available, and a commented worked example showing exactly where a
vendor is wired in — including the note that a vendor without a webhook would
leave documents at `sent` forever, which is a worse lie than not offering the
button.

The manual provider **mints nothing**: no envelope id, no certificate, no signer
identity, no `sent_at`. It returns `SEND_MANUAL` with a sentence explaining that
the document is signed however the customer already signs things and the executed
copy is uploaded here. `SignatureResult.left_the_building` is the only thing that
moves a document to `sent`, and it is False for the manual provider by
construction.

The documents screen asks `esign.capability()` and **the send button only exists
when something can actually send**. With no provider connected there is a
disclosure saying why, carrying the server's own reason rather than a sentence
hard-coded into the page — which was true of this deployment in Phase 4 and would
have become a lie the day a provider was connected.

The settings screen's esign provider list was an inline literal claiming one
hard-coded provider; it now comes from the registry.

### 34.4 These tests were proved able to fail

Two faults injected, the suite re-run each time, then restored:

```
manual provider returns SEND_SENT with an envelope id   → 2 failed, 13 passed
"suggested wording" contract prose on the fill sheet    → 1 failed, 14 passed
restored                                                → 15 passed
```

---

## 35. Screens

**Investor room** (`/investor/:token`) and **seller page** (`/my-property/:token`)
are separate public pages with their own self-contained stylesheet — they inherit
nothing from the operator theme, so a change to the internal UI cannot leak into
a page a stranger is reading. Both scroll cleanly at phone width.

**The publication desk** (Sharing tab) is where a person decides what is
published, per audience: the summary and condition they wrote, the asking price
they want shown, and three explicit switches for ARV, repairs and comps — all
off by default. Links are listed with copy and revoke, and the outside-activity
feed shows every open and every action with a timestamp.

**Property intake (A18).** Fifteen equal boxes before somebody could record an
address is why intake gets skipped. The form is now an obvious identification
block — street, unit, city, state, ZIP, parcel/APN, owner name — under the line
*"Give us enough to identify the property. The rest can be completed later."*,
with beds, baths, size, year, type, ownership, occupancy, county and market
folded behind one summary. **No field was removed.** `unit` was on the model and
accepted by the API but had no box, so it gained one.

**Empty states (A12).** An empty panel cost 52px of padding around one centred
sentence, which made "nothing here" the biggest thing on the page. It now reads
as a caption under the panel title. An empty Command Center went **1,511 → 1,297
px**; the populated one is unchanged at 2,135.

**The seller assistant (A14).** The AI panel was one unlabelled textarea. It now
says what the assistant actually is: it reads one inbound message and fills in
what the owner said, and it does not write, reply, send or decide anything. What
it looks for and when it hands back to a person are shown side by side; the only
control the backend honours is the extra instruction, behind an Advanced
disclosure.

**There is deliberately no tone or goal selector.** Nothing in this module writes
to an owner, so a tone setting would change nothing at all — which is precisely
the cosmetic setting the brief forbids. The panel says so instead of shipping
one.

**Money read-back (A17).** A formatted input fights every keystroke and loses
precision the moment somebody pastes. The box keeps the raw figure and a line
underneath reads it back — `70` → `70%`, `302951.72` → `$302,951.72`. The
figure is never rounded, never re-stored and never enters a calculation.

**Accessibility (A20).** A scripted audit across every wholesale screen and every
deal tab found 25 controls with no programmatic label — a label sitting *near* an
input is not a label. All of them now have `htmlFor`/`id`, a wrapping label, or
`aria-label` where the control is visually hidden. The audit now reports **every
control is labelled**, and it is a script that can be re-run.

---

## 36. Workstream B — the platform

Kept strictly separate. Nothing in this section touches the wholesale module.

### 36.1 Leads table (B1) — spacing only

`table-layout: auto` made `text-overflow: ellipsis` inert, and the measured table
was 1,600px inside a 1,480px panel. Now `table-layout: fixed` with pixel widths
for the known shapes and percentages for free text. Re-measured at **1,478 of
1,480px**, 52px rows, no horizontal scroll at 1512, 1280 or 1100.

**No lead data, qualification, filter, action or API was touched.**

### 36.2 Organization-specific catalog (B2)

Insurance products were appearing for every organization on the platform. The fix
is not a global delete: `OrgOffering` is a per-organization catalog, and
`ensure_seeded` seeds an organization **only from its own existing records** —
`keys_in_use` reads the keys actually present in that org's case files.
`LEGACY_LABELS` holds labels only, never a default catalog, so nothing is
inherited by an unrelated organization.

Deletion of an offering that historical records reference is **refused with a
409** telling the person to retire it instead. Existing insurance data is intact.

### 36.3 Lead capacity (B3, B4, B5)

**First finding: the capacity system is already good.** `lead_capacity.py` holds
over-capacity leads rather than dropping them, releases oldest first when
headroom appears, never touches `status`, and is released automatically by the
Stripe webhook on a plan change. None of that was rebuilt, and the limit was not
removed.

Two narrow gaps were closed.

**B4 — import capacity protection.** An import could previously be committed
without anyone knowing it would exceed the ceiling. `_capacity_preview` is
computed on the batch review and reports `{unlimited, limit, used, available,
will_create, blocked}`, so the warning appears **before** the commit. Nothing is
silently lost either way — over-capacity rows are held, not discarded.

**B5 — the counting rule, said out loud.** "2,500 of 2,500" is a figure a
customer can neither verify nor reduce without knowing what it counts. The rule
is already precise in `plan_limits.usage_for`; it was simply never shown to the
person it applies to. Under each usage bar the Billing screen now states it:

> Every lead in your workspace counts once. Prospects held over capacity do NOT
> count — they are kept aside until there is room, which is why the held figure
> is separate from this one.

> Anyone active who can sign in and work your records counts as one seat, whether
> this is their home workspace or they were given access to it. Deactivated
> people do not count.

**B3 — the options that exist.** At capacity the page previously said "Upgrade to
release them" and offered no way to do it. It now names the **cheapest configured
tier whose lead ceiling would hold what is in use plus what is held**, computed
from the same plan list the cards below are drawn from — so it can never name a
tier that is not really on offer — with a button that calls the change-plan path
this page already uses. When nothing self-serve is big enough it routes to the
quoted tier the catalogue already carries. It also says plainly that the ceiling
cannot be lifted from that screen, because a customer who assumes the obvious
fourth option exists will wait for it.

### 36.4 Custom plan (B6)

**Finding: the existing architecture is already correct and was not rewritten.**

The quoted tier is in the catalogue with `monthly_cents: None` and
`is_purchasable: false`. The card renders "Custom pricing", the button is a
brand-resolved contact link rather than a checkout the server would refuse, and
`require_purchasable` refuses the combination server-side anyway. No Stripe
operation is faked anywhere on that path. The only change made was the B3 routing
above, which sends an at-capacity customer *to* that tier when nothing listed
fits.

---

## 37. What Phase 5 did NOT do

- No deal-state or stage architecture was touched.
- No approval gate was weakened, bypassed or moved. One was **tightened**.
- No legal language was written, generated, assembled or suggested anywhere.
- No signature was simulated, and no envelope reference was minted.
- No billing operation was faked and no billing rewrite was performed.
- No lead data, qualification logic, filter, action or API was altered by the
  Leads spacing work.
- No insurance product was globally deleted.
- No capacity limit was removed.
- No cosmetic setting was created. Where the backend cannot honour a control, the
  screen says so instead of shipping the control.
- Nothing was committed, pushed or deployed.

---

## 38. Phase 5 test state

```
NEW TEST FILES
  test_wholesale_rooms.py        22   the publication boundary, value-level
  test_wholesale_contracts.py    15   templates, lifecycle, signatures
  test_import_capacity_preview.py 5   the pre-commit warning
  test_org_offerings.py           8   per-org catalog, no cross-org bleed

EXTENDED
  test_wholesale_cross_tenant.py      publication, fee-collected, photo batch
                                      and reorder, the five new id-bearing
                                      contract/lifecycle routes, and an
                                      explicit upload FIELD NAME so an attack
                                      cannot pass on validation instead of on
                                      the tenant check

FAULT INJECTION (each injected, run, then restored)
  assignment_fee leaked into the buyer payload as "spread"  → caught
  manual provider claims SEND_SENT with an envelope id      → 2 failed
  contract prose added to the fill sheet                    → 1 failed
  all restored                                              → green

frontend: npm run build clean (repo)
a11y:     scripted audit across 4 screens and 8 deal tabs — every control
          is labelled
```

### 38.1 Tests changed, and why

**`test_the_status_admits_when_the_deployment_cannot_send`** asserted the old
cadence wording. The customer-facing sentence no longer names an environment
variable (A13), so the assertion now checks **both** that `sending_note` says
"turned off" and does *not* contain the variable, **and** that
`sending_note_technical` does. That is a stronger assertion than the one it
replaced, not a weaker one: it pins down the separation rather than one string.

**`test_local_storage_is_offered_but_never_called_durable`** was corrected in
Phase 4 and is recorded there.

No test was deleted to make something pass.

---

## 39. Still open after Phase 5

Everything in sections 20 and 29 that was not addressed above, plus:

1. **`viewed` is only as good as the fetch.** A document is marked opened when it
   is fetched through a share link. A recipient who is sent the file another way
   and reads it there will never move the document, which is correct but worth
   knowing before anybody treats `viewed` as proof of receipt.
2. **The fill sheet is a transcription aid, not a merge.** Somebody still retypes
   into their own form. A merge would require reading inside the customer's file,
   which is a different and much larger decision than the one made here.
3. **No e-signature vendor is connected.** The registry, the lifecycle and the
   capability reporting are all in place; adding one is a class in
   `wholesale_esign.py` plus a webhook that moves the document to `signed` or
   `declined`. Until that webhook exists, do not add the provider.
4. **Local media storage is still a review setting.** Production must set
   `MEDIA_STORAGE_BACKEND=s3`.
5. **Accessibility was swept across the wholesale module only.** The rest of the
   platform has not been audited with the same script.

---

# PHASE 6 — EXTERNAL EXPERIENCES + OPERATOR UX

Phase 5 built the publication boundary and proved it holds. Phase 6 is what sits
on top of it: the page an investor actually reads, the page an owner actually
reads, and the operator screens that feed both.

The full review report is `handoff/WHOLESALE_PHASE6_REVIEW_REPORT.md`. This
section records what changed in the module and why.

## 40. The investor page was correct and unsellable

Rendered before anything was touched. Everything worked — the boundary held, the
buttons wrote real records — and it looked like an admin screen with fields
removed: no hero, the cover photo in a flat strip below the facts, the asking
price the same size as the county, no branding anywhere, and the five things an
investor can do 1,600px down the page.

That is a data dump that happens to be safe. It is not a page you send somebody
you want to buy a house from you.

### 40.1 What the page is now

The same data, published under the operator's own brand, arranged the way
somebody decides whether to buy a house:

```
masthead      the operator's name, logo and accent — never the vendor's
cover         one large photo, capped at 420px so the price stays above the fold
identity      address, then chips: type · occupancy · market · county
price         asking price at 36px; ARV, repairs and closing beside it at 19px
facts         beds, baths, sqft, lot, year, type, occupancy, county
narrative     summary, then condition and repairs
gallery       thumbnails → lightbox with next/previous, arrow keys, captions
comps         address, distance, sold, price, sqft, $/sqft, bd/ba
closing       dates and the title company
documents     only the ones with a file behind them
decision      five actions, each saying what it commits you to
```

`occupancy_status` and `market` were added to the address block because both
decide the deal and neither is private — a tenanted house is a different
purchase from a vacant one, and leaving it off meant every investor had to ask.

### 40.2 Branding, and why it is the operator's

`wholesale_publication.branding()` resolves, in order:

```
name    organization.brand_name → organization.name → None
logo    organization.brand_logo_url → the platform's logo → None
accent  organization.brand_color_primary → the platform's accent → None
```

No new column: all three already existed on `Organization`. A missing value
comes back as None and the page renders without it. It never substitutes a brand
that does exist for one that does not — the same rule `brand_config` states
about an unrecognised hostname.

An external page carrying the SOFTWARE VENDOR's name is worse than one carrying
none: it tells the investor who the wholesaler buys their tools from, which is
nobody's business and is not who is selling them a house.

### 40.3 The offer is now an offer

The old form collected an amount. An amount with no closing date and no idea how
it is funded is not something an operator can act on, so the form now collects
price, closing date, funding, proof-of-funds claim, contact details and notes.

**Three of those land on columns that already existed** — `target_close_date`
and `pof_status` have been on the outreach row since Phase 3. Only
`offer_financing` and the respondent block are new.

`POF_STATUSES` gained one member, `claimed`, sitting deliberately **before**
`received`: the buyer says proof of funds is with us and nobody has looked at a
document. Writing that into `requested` would lose the claim; writing it into
`received` would invent a document.

The respondent's name, email and phone are recorded **against the response**,
never over the buyer's CRM record. Whoever opened the link may not be the person
in the list — it may be an assistant, or the link may have been forwarded — and
a public page that rewrote somebody's contact list would corrupt it quietly. A
test asserts the CRM row is untouched.

## 41. The seller portal

It was already substantially right: the seller-safe display statuses worked, the
ladder was real, the dates and documents were correct. Phase 6 gave it a
masthead, the owner's own house at the top when a photo is published to them,
and the status stated once in the hero — *Property review · Step 3 of 5 ·
closing Sun, November 8* — rather than only being inferable from the ladder.

`WholesaleFile.seller_visible` is a **separate** flag from `buyer_visible`, and
`GET /wholesale-rooms/seller/{token}/photo/{file_id}` is a **separate route**
written out longhand rather than sharing a parameterised helper with the buyer
one. A single helper taking a column name is one wrong argument away from
serving an investor gallery through a seller link, and that is the last place in
the module where that mistake should be possible.

The surface is still read-only: there is no write route on it at all.

## 42. The contract sheet says what is ready

Three states, not two:

```
complete       a value is on file
needs_review   a value is on file and somebody should check it
missing        no value
```

The middle one earns its place. "Seller of record" is on file *and* has to be
confirmed against the deed before it goes into a legal document. Flattening it
into `complete` is how a wrong party name reaches a signature page; flattening
it into `missing` sends somebody looking for data they already have.

Counts are computed server-side and reported per field, per group and for the
sheet, so the operator screen and any other reader agree about what is ready.
`ready` means every field is on file and checked; it is **not** a statement that
the resulting document is legally sufficient, and nothing in this module makes
that claim about anything.

## 43. Payment status on the header

"Closed" and "paid" are different facts and the deal header was answering only
the first. `payment_state` now sits on the top line as a word rather than a
figure — setting "Closed — payment pending" in the same type as the money wrapped
it onto three lines and broke the row.

```
Expected · Closed — payment pending · Collected
```

## 44. The buyer CRM's track record

`_buyer_activity` already counted sheets sent, replies, offers, best offer and
selections. Phase 6 added opened, interested, passed, deals closed and average
close days.

Every one is a count of rows that exist. Two deserve their own note:

- **`opened` is not inferred.** It counts rows where `opened_at` is set, which
  happens when something observable happens — a share link fetched, a tracked
  email opened. With no such signal it stays 0. Back-filling it from "we sent it
  so they probably read it" would make an unobservable signal look like
  engagement.
- **`deals_closed` reads the deal rows**, so a buyer selected on a deal that
  then died is not credited with a close.

## 45. Settings that read as questions

The section headings are now the questions a wholesaler asks — *How do you
calculate offers? · Where do you buy? · What needs your approval? · What should
happen automatically? · What are you willing to spend?*

The single biggest change is splitting one list of ten checkboxes into two. It
was mixing "nothing happens here without me" with "do this for me while I am not
looking" — opposite intentions, and a person scanning for one had to read past
the other. **No switch was added, removed or renamed; only regrouped.**

Above the offer boxes, the formula in English with a worked example computed
from whatever is in the boxes right now, including unsaved edits:

> You pay up to **70%** of what a house will be worth fixed up, then take off
> repairs, **3%** of ARV in transaction costs, and your **$10,000** fee.
> On a $300,000 house needing no repairs, that is a maximum offer of
> **$191,000**.

## 46. The seller assistant's tone

Phase 5 deliberately shipped no tone control and said why: nothing in this module
writes to an owner, so a tone setting would steer nothing.

Phase 6 adds one, with its scope stated rather than implied. The assistant does
produce language — the one-sentence summary it writes back to the OPERATOR after
reading a reply — and the preference steers that and only that. The instruction
is appended *after* the extraction rules so it cannot displace one, and it is
never consulted on the deterministic opt-out path, which returns before a model
is involved at all.

The screen says so in as many words: it does not change any message an owner
receives, because nothing in this module sends one.

## 47. SS1, finished

The review screen now states, before anybody presses Commit:

```
Trinity Growth                      180 of 10,000 leads available
10,000  limit      9,820 in use     180 available
   750  rows       712 would create  31 already in your leads   7 cannot import
   180 can be imported now · 570 would be held
```

and then the options that exist, from `billing_catalog` — real configured tiers
only, with a tier sold by quote reported as such rather than given a number. An
empty list renders as "your administrator can arrange a ceiling that does",
never as an invented plan.

### 47.1 One sentence that was not quite true

The old panel ended "rows over the ceiling are simply not created". They are not
created — but they are also **not lost**. `import_commit_service` leaves them in
their reviewed state precisely so the batch can be committed again after an
upgrade, and says so in its own note. A screen implying the rows were gone would
send somebody to re-upload a file they still have staged. It now reads:

> they stay staged in this batch, exactly as they are, and commit on the next
> run once there is room. Nothing is deleted and nothing is rejected.

Verified in the commit service before the copy was written.

## 48. The journey test

`tests/test_wholesale_journey.py` walks one deal from a typed-in address to
recorded revenue in a single test — deliberately one test, because splitting it
would let each step pass against a fixture rather than against the state the
previous step actually left behind.

It found a real thing on its first run: the test went under contract without a
contract approval and was refused with a 409 naming exactly what was missing.
The gate was working; the test was wrong. The refusal is now asserted as part of
the journey.

Its last assertion is the one the whole module is built around:

```python
assert board_after["headline"]["fees_collected"] == (fees_before or 0) + 11800
```

Collected revenue moves by exactly what a person recorded, and not before.

## 49. Phase 6 test state

```
WHOLESALE SUITE   255 passed  (228 at the end of Phase 5)
  + 2  journey
  + 9  rooms — the Phase 6 security block
  + 4  import capacity preview

FAULT INJECTION (injected, run, restored)
  seller photo route reads buyer_visible  → 1 failed
  brand block carries a seller name       → 2 failed
  restored                                → 31 passed

RENDER  2 themes × 2 widths × 4 screens × 9 tabs, external pages at 390 too
        no page errors · no overflow · no unreachable controls · no tiny text
```

## 50. Still open after Phase 6

1. `viewed` reflects a fetch through a share link; a document sent another way
   never moves it. Do not read it as proof of receipt.
2. The fill sheet is transcription, not a merge. A merge means reading inside
   the customer's own file, which is a much larger decision.
3. `opened` stays 0 where the deployment cannot observe an open.
4. Local media storage is still a review setting; production needs S3.
5. The external pages were checked for contrast, tap targets and text size, but
   not with a screen reader.
6. No e-signature vendor. Adding one is a class plus a webhook — and without the
   webhook a document would sit at `sent` forever, which is worse than no button.

---

# PHASE 6.1 — THE EXPERIENCE, NOT THE MACHINERY

Phase 6 passed its functional and security review and the boundary held. What
came back from the human review was not a defect list, it was a verdict on the
canvas: on a 1550px monitor both external pages were a narrow centred column of
small stacked cards with a third of the screen empty on either side. Correct,
safe, and reading like an internal tool.

The full report is `handoff/WHOLESALE_PHASE6_1_POLISH_REPORT.md`. This section
records what changed in the module and why.

## 51. The pages were phone layouts that had been given a margin

Everything lived in one 1040px column of rounded rectangles. That is a
reasonable way to build a page you will only ever see on a phone, and it is
what both of these were. On a wide screen it produces the two complaints the
review made in the same breath — enormous dead space AND cramped content —
because they are the same problem seen from two sides.

So the pages are now built as a LAYOUT rather than a stack:

```
< 900px    one column · cover on top · sticky action bar at the bottom
≥ 900px    split hero: photograph | address, chips, price, primary action
≥ 1180px   two columns: the property | a sticky decision rail (372px)
≥ 1700px   content 1400px, rail 400px — and then it stops
```

### 51.1 Why it stops

A property description is prose and prose has a comfortable measure. Past about
1400px the choice is between a 100-character line and more grey, and more grey
is the better of the two. On a 3840px monitor the page centres with wide
margins by design. That is not the bug that was reported; the bug was 1040px of
content at *every* width, including 1550.

### 51.2 Type, scaled by role rather than uniformly

```
address        40px / 800    the thing you are looking at
asking price   50px / 800    the thing you are deciding about
ARV, repairs   22px / 700    context beside it
fact values    18px / 650
body prose     16px / 1.68
section labels 12px / 800 uppercase — deliberately quiet
```

"Make everything bigger" would have solved nothing. The Phase 6 page had the
asking price and the county at almost the same weight, which is the actual
defect.

## 52. The decision follows the reader

Above 1180px the five actions sit in a sticky rail beside the property and stay
on screen at every scroll position. Below that the same node falls back into the
flow and the phone gets the fixed bar.

**One node, moved by CSS — not two copies.** Two copies of an offer form is two
ways to send two different offers, and the bug that produces is one nobody finds
until a buyer complains that the price they submitted is not the price on the
board.

**Make an offer is now the primary, and it was not.** Phase 6 gave the primary
treatment to "I'm interested", so the strongest control on a page whose entire
purpose is producing offers committed the reader to nothing. PASS keeps its
place in the list — hiding it would be a dark pattern — drawn as the quietest
thing on the panel.

## 53. Two paragraphs no longer need two boxes

`About this property` and `Condition and repairs` are one panel with two
headings. Phase 6 gave each paragraph its own rounded rectangle, which is most
of why the page read as a stack of cards rather than as a document. The words
are the operator's own, rendered verbatim; nothing here rewrites or embellishes
them.

Comps went from 14px left-aligned to 15.5px with right-aligned tabular
numerics, because money and areas in a column are being compared down the
column. The provenance line stays. A missing sale date stays missing.

## 54. The seller portal says what the step means

The hero states the status once and now adds a sentence explaining it — *"The
property is being inspected and reviewed before closing."*

Those sentences describe the STEP, not the transaction. They cannot contradict
the deal because they do not refer to it, and a step the server adds later
renders with no description rather than with a guess.

The ladder moved under the hero at full width and became a horizontal path with
a connector line. "Where am I" is the question this page answers; it should not
be competing with an escrow file number for attention.

**Latest update** is now the largest body text on the page, in its own accented
panel, with an honest empty state. It had been a 15px paragraph in a box the
same size as the box holding the file number.

## 55. The ladder, finished

Phase 6 fixed half of this. Opening the page found the other half.

```
Phase 6   each step judged on its own evidence, so title — which opens the
          moment the file goes over — showed DONE above a Property review
          that was still running.
          FIX: a step is DONE only if every step before it is.

Phase 6.1 the mirror image. A deal whose recorded stage has run ahead of its
          paperwork showed  Offer NOT STARTED · Agreement IN PROGRESS  — an
          agreement being worked on for an offer that never happened.
          FIX: everything before the current step is behind you. Can only
          fire on a record with a gap in it, can only move a step forward,
          and can never move a finished step back or past the current one.
```

`tests/test_wholesale_seller_progress.py` is new and makes every requirement an
assertion rather than a promise: monotonic, exactly one current step, no gaps,
closing data alone cannot complete earlier steps, `Closed` needs real closing
state, an unknown stage does not guess forward, no internal stage name leaks,
and a written update to the owner cannot move the transaction.

Fault-injected both ways before being believed: removing the monotonic guard
fails 3, removing the one-boundary rule fails 1, restored passes 32.

## 56. What the investor sent, where the operator can see it

The Phase 6 gap, closed. The deal room collected six things and the board
rendered two — `offer_financing` and the respondent block were validated,
stored, returned by the endpoint and shown on no screen.

It is a sub-row under the buyer it came from, not four more columns on a table
that is already seven wide, because the whole point is that the person who
answered may not be the person in the CRM:

```
FROM THEIR DEAL ROOM LINK
Funding                              Hard money
Wants to close by                    2026-11-25
Answered                             9/24/2026, 6:28 AM
Answered by (not the listed contact) Priya Raman · priya@… · (214) 555-0188
Recorded against this response only — Lone Oak Capital's contact details
are unchanged.
```

### 56.1 And a select with no matching option

`POF_STATUSES` on the board never learned the `claimed` value Phase 6 added on
the server — the value the deal room writes when an investor ticks "already on
file with you". A row sitting on it rendered a `<select>` showing blank, so the
operator's next change looked like a correction of something they never set.
Added between `requested` and `received`, because that is where it sits in
reality: asked for, asserted, not seen. It reads *"Buyer says it is on file —
not seen"*.

## 57. Contrast, measured

`--wr-ink-3` was `#737a85` — 4.33:1 on white, under the 4.5 AA threshold, and
the colour of every label, eyebrow and caption on both pages. Almost all the
small text on an external page was failing by a hair. Now `#666d78`: 5.0:1 on
white and 4.8:1 on the background it sits on in the footer.

Two things the checker itself got wrong are recorded in the report, because
both would otherwise have been filed as page defects: a `color-mix()` resolves
to `color(srgb 0.048 …)` rather than `rgb(12 …)`, and a `display:none` parent
does not make its `inline-block` children visible.

## 58. Phase 6.1 test state

```
WHOLESALE SUITE   287 passed   (255 at the end of Phase 6)
  + 32  tests/test_wholesale_seller_progress.py

SECURITY          tests/test_wholesale_rooms.py   31 passed, unchanged
FRONTEND BUILD    358 modules, clean
FULL SUITE        5,591 passed · 14 skipped · 2 FAILED

The two failures are pre-existing, outside this module, and named in §10.1 of
the 6.1 report: one timestamp guard in the meeting-type backfill compares its
own write against `updated_at` and loses to Windows' millisecond clock
resolution. Phase 5 recorded the same two. Not fixed here — the brief rules
out refactoring unrelated platform systems, and the fix is a real decision
about a real scheduling feature.

WIDTHS   1550 · 1280 · 820 · 390, both pages
         no overflow · no clipping · no text under 11.5px
         no tap target under 44px on a phone · no unreachable control

A11Y     one h1 · heading order ok · every control labelled
         no contrast failure · every focusable shows a focus ring
         lightbox: open · ArrowRight · ArrowLeft · Escape, all by keyboard
```

## 59. Still open after Phase 6.1

1. `viewed` reflects a fetch through a share link; a document sent another way
   never moves it.
2. The fill sheet is transcription, not a merge.
3. `opened` stays 0 where the deployment cannot observe an open.
4. Local media storage is still a review setting; production needs S3.
5. **No screen-reader pass.** Contrast, targets, focus, labels and heading order
   are measured; behaviour under a screen reader is not.
6. No e-signature vendor.
7. **An investor cannot upload a proof-of-funds letter**, because nothing
   receives it on that route. The form records what they say; the operator
   attaches the document from their own side, where the upload endpoint is.
8. The external pages stop widening at 1400px — §51.1.
9. The contact card renders a role when the payload carries one; the seller
   room does not publish one yet.

---

# PHASE 7 — EVOSENSE ACQUISITION ENGINE

Full report: `handoff/WHOLESALE_PHASE7_EVOSENSE_REPORT.md` (architecture reconciliation §0 first).

## 60. What Phase 7 added

Autonomous opportunity discovery **before** a Wholesale deal exists: strategies, a
provider-neutral capability layer (SANDBOX / MANUAL / IMPORT today — no real vendor), one
canonical property per organization with identity review, signals with freshness, three
deterministic versioned scores with their WHY (Property Opportunity, Contact Confidence,
Seller Intent) plus Data Confidence, cost-aware enrichment through an atomic budget and a
cost ledger, kill switches, outreach eligibility on the platform's own DNC/suppression,
seller conversation intelligence (17 outcomes, facts with quotes), nurture, NEEDS YOU,
preliminary numbers through `calculate_offer`, and **human-only promotion into the existing
Wholesale deal** (`create_property` + `attach_seller(lead_id=…)`, history carried).

## 61. Where it lives

`app/models/evosense_models.py` (18 tables, all org-scoped) · `app/services/evosense/` ·
`app/routers/evosense_router.py` (`/wholesale/evosense`, same feature gate) ·
`frontend/src/pages/wholesale/evosense/` (Command Center, Discovery Inbox, Property
Intelligence, Strategies + Builder, Providers & Controls) · `scripts/evosense_hunt.py`
(scheduled entry) · `scripts/seed_evosense_review.py` (SANDBOX review org).

## 62. Rules a later phase must keep

1. A discovered property is not a `WholesaleProperty`; promotion creates one. One deal system.
2. A person becomes a `Lead` only when worked (outreach) — DNC, suppression, capacity inherited.
3. Suppression and DNC are read from and written to the platform tables only.
4. Money moves only through `budget.reserve()` (one conditional UPDATE); never read-then-write.
5. Scores are pure functions with a version string; change the formula → bump the version.
6. SANDBOX adapters are off per organization until an admin enables them, never serve a real
   property, and everything they produce is `is_test` and labelled.
7. New id-bearing EvoSense routes must be added to `tests/test_wholesale_cross_tenant.py`.

## 63. Phase 7 test state

See §16 of the Phase 7 report. Wholesale + EvoSense suite: 339 passed (287 + 52).

---

# PHASE 7.1 — EVOSENSE AUTONOMY CLOSEOUT

Full report: `handoff/WHOLESALE_PHASE7_1_AUTONOMY_CLOSEOUT.md`.

## 64. What closed

1. **Automatic hunting.** `JobName.EVOSENSE_HUNT` (`evosense_hunt_loop`) is a platform loop owned
   by the backend in `service_role`, recorded in `job_runs`, every 15 minutes:
   `evosense.scheduler.run_due` runs each ACTIVE strategy that is due (per-strategy cadence
   manual / daily / interval; daily by default), under an atomic per-strategy lock, through the
   same `hunt.run_strategy` as the Run hunt button. Failed hunts are recorded and retried in an hour.
2. **Inbound SMS into EvoSense.** The Twilio webhook body is now `sms_router.process_inbound_sms`
   (one path). After the platform persists the Reply, a reply from a seller in an EvoSense
   conversation (organization → Lead → engagement) is routed to EvoSense and read automatically;
   ambiguous senders go to ROUTING REVIEW; the platform AI pipeline does not auto-reply to
   EvoSense sellers; a repeated MessageSid creates nothing new; an AI failure keeps the message
   and marks AI REVIEW PENDING with automatic retry; pausing EvoSense never stops opt-outs.

## 65. Rules a later phase must keep

1. There is one hunt (`hunt.run_strategy`) and it takes the strategy lock. Do not add a second.
2. Inbound SMS has one path (`process_inbound_sms`). Persist first; EvoSense reads after commit.
3. Hard stops are deterministic and run even when EvoSense or AI is paused.
4. New id-bearing EvoSense routes go into `tests/test_wholesale_cross_tenant.py`.
5. Loops run only where `SERVICE_ROLE=backend`; the local server runs none unless that is set.

---

# PHASE 7.2 — PREMIUM PRODUCT EXPERIENCE + CANONICAL LOCAL REVIEW

Full report: `handoff/WHOLESALE_PHASE7_2_PRODUCT_EXPERIENCE.md`. Screenshots: `handoff/p7-2-shots/`.
Local only — not committed, not pushed, not deployed.

## 66. The local review, from now on

* **One organization:** `EvoSense Review (TEST)` (slug `evosense-review-test`), linked to the EvoSys Pro platform.
* **One login:** `evosense.review@example.test` / `EvoSense-Review-2026!`.
* **One database:** `C:\Dev\advisorflow-web\advisorflow.db`.
* **One launcher:** `START_EVOSYS_REVIEW.bat` (stop: `STOP_EVOSYS_REVIEW.bat`). It pins the local SQLite DB and
  `SERVICE_ROLE=local_review` (no background loops), runs `scripts/seed_evosys_review.py` (idempotent,
  non-destructive, no AI calls), starts the API and web app only if they are not running, and opens the login.
* Root cause of the empty review (for the record): the backend had been killed and nothing restarted it; the
  review data was split across three orgs; Deal Operations hid sandbox records by default.

## 67. What Phase 7.2 changed

1. **Design system** `frontend/src/pages/wholesale/ds/` (`evo-ds.css`, `evo-pages.css`, `ds.jsx`): semantic tokens,
   one score component, one status vocabulary, property imagery, drawer, tables that become cards, and a bridge
   that re-skins the older module screens. Every Wholesale/EvoSense page is wrapped in `EvoApp`.
2. **Navigation:** two worlds — ACQUISITION · EVOSENSE (Acquisition Command, Discovery Inbox, Strategies,
   Providers & Controls) and WHOLESALE OPERATIONS (Deal Operations, Properties, Cash Buyers, Contracts & Closing,
   Dispositions, Wholesale Settings). The old Wholesale "Command Center" is Deal Operations.
3. **Branding:** `/branding/org` returns the workspace `platform`; `theme.js shellTheme()` uses it only where the
   hostname is not a brand domain. localhost no longer renders an EvoSys workspace as BookaBoost.
4. **Environment truth:** `/demo/environment` returns `local_review` (SQLite process); the product bar shows one
   LOCAL REVIEW · SANDBOX DATA pill.
5. Read-only additions to the EvoSense command-center payload (`totals`, `recent_activity`, hero details).

## 68. Rules a later phase must keep

1. New Wholesale/EvoSense screens are built from `ds/` components and tokens — no one-off palettes. Colours are
   semantic tokens; status colours are never re-branded; filled primary controls use `--evo-primary-fill`.
2. A score is always shown with a way to its deterministic WHY; `null` is "not scored", never 0.
3. An estimate always carries its ESTIMATE / SYSTEM ESTIMATE label. No invented photos, metrics or timestamps.
4. A brand domain decides its own chrome. The workspace platform decides only on a non-brand host.
5. The canonical review seed stays idempotent and non-destructive; add scenarios by extending it, not by
   creating another review organization.

## 69. What Phase 7.3 changed (Wholesale premium visual rebuild)

The Phase 7.2 dark look was rejected in review. Phase 7.3 rebuilt the Wholesale / EvoSense experience as a
LIGHT premium operating system against the ONE approved mockup board. No business logic changed.

1. **Light design system.** `ds/evo-ds.css` rewritten: cool-white canvas, white cards, soft shadows, serif hero
   titles (Playfair Display), big coloured metric numbers, pastel status pills. The bridge re-points every older
   module token to light, so all nine deal-workspace tabs and every drawer form are light too.
2. **Contextual heroes** on every primary page (`Hero` in `ds.jsx`): licensed stock photography bundled in
   `ds/photos/` (Unsplash License, ids in `ds/photos/CREDITS.md`), a readable overlay, eyebrow, title, subtitle,
   quote, live meta chips, actions and an optional score ring. Drawn SVG scenes in `ds/scenes.jsx` are the fallback.
3. **Image truth.** A banner photo is a generic scene, never "this property". Property pages (EvoSense property,
   deal workspace) use a city-aerial banner unless the deal has its own uploaded photo, in which case that photo is
   used. Galleries/thumbnails show the real photo or a "No photo on file" placeholder.
4. **Focused Wholesale shell** (`components/WholesaleShell.jsx`, `wholesale-shell.css`, `Layout.jsx`): inside
   `/wholesale` (outside a configured vertical) the rail shows only the two worlds; every other platform screen
   (Overview, Leads, My Work, Replies, Activity, Availability, AI Hub, Your AI Team, My AI Workforce, Workforce
   Command, Proposals, Re-engagement, DNC List, Operations, Administration, Help) is folded under a collapsible
   **EvoSys Platform** section — same items, same visibility rules, nothing removed. The top bar carries one search
   (to the Discovery Inbox `?q=`), the LOCAL REVIEW pill and the signed-in person. The dark/light toggle is not
   offered inside Wholesale.
5. **Board compositions:** Acquisition Command (metric cards, Needs You, Top Opportunities image cards), Discovery
   Inbox (count tabs), Strategies (All/Active/Paused/Archived tabs, photo-headed cards), Providers & Controls
   (Service Providers / Controls & Compliance / Usage & Costs tabs, capability tiles with truth labels), Deal
   Operations (Pipeline / Needs attention / Closings / At risk tabs), Contracts & Closing (stage tabs), Properties,
   Cash Buyers (Verified-only filter), Wholesale Settings (horizontal tabs), deal workspace (property hero + key
   numbers), Seller Portal and Investor Deal Room (banner).
6. **Public contact fix:** `wholesale_publication.branding()` no longer falls back to the PLATFORM support phone
   (469-553-7417 — the software vendor's line, answered by another business). No wholesale public phone exists yet,
   so none is shown. Test: `tests/test_wholesale_p73_public_contact.py`.
7. **Acceptance script:** `scripts/review/p73look.py` — 30 screens × 4 widths incl. all 9 deal tabs and drawers;
   checks overflow, touch targets, required text, leftover dark surfaces and the platform phone.

## 70. Rules a later phase must keep

1. Wholesale stays light. New surfaces use `ds/` tokens; no page reintroduces a dark canvas (p73look fails on one).
2. Every primary page opens with `Hero`. Banner imagery is generic and licensed; add photos to `ds/photos/` with
   their source in `CREDITS.md`. Never put a stock house behind a specific address.
3. The focused shell must never delete a platform item — fold it under EvoSys Platform.
4. No public wholesale page shows the platform's phone. When a wholesale public contact setting exists, use it.

## 71. Phase 7.3 closeout: naming, public contact, brand-resolution gate

1. **Product naming hierarchy (commercial, not technical):** EvoSysPro (platform/brand) -> **EvoSys Wholesale**
   (the product) -> **EvoSense** (its acquisition/intelligence engine). The product name is brand-owned:
   `brand_config.PRODUCT_NAMES` / `product_name(slug, module)`, exposed on `GET /branding/org` as
   `platform.products.wholesale`. Brands without an entry get `None` and the UI shows the neutral "Wholesale".
   Customer-facing changes: the sidebar shows "EvoSys Wholesale · Powered by EvoSense" under the brand mark,
   and the browser tab reads "EvoSys Wholesale" on every /wholesale screen. Internal names (routes
   `/wholesale/evosense/*`, `app/services/evosense/*`, models, tables) are unchanged on purpose.
2. **Public Wholesale contact:** `wholesale_settings.public_contact_phone` / `public_contact_email`
   (Wholesale Settings > Public contact; `PATCH /wholesale/settings`, validated). Resolved ONLY from the
   organization's own settings by `wholesale_publication.public_contact()` into the room payload's
   `brand.support_phone/support_email` with `brand.contact_source = "wholesale_settings"`. **No fallback** to the
   platform support line/address or to any other organization; missing = none shown. The review seed sets the
   approved EvoSys Wholesale phone **469-553-7417** through this setting (only if unset). The Seller Portal masthead
   now shows the same configured contact as the Investor Deal Room (hidden at phone widths by the existing
   responsive rule).
3. **wholesale@evosyspro.live:** not configured anywhere in the repo; not verifiable from the Microsoft 365
   connector (signed in as support@evosyspro.live; no mail to that address; no People/alias scope). **WHOLESALE
   EMAIL ALIAS REQUIRES EXTERNAL CREATION.** The email field is ready; set it in Wholesale Settings once the alias
   exists. The platform support address is no longer used on wholesale public pages.
4. **Brand-resolution gate (permanent):** `theme.shellThemeSource()` reports where the shell brand came from
   (`host` | `workspace` | `default`); `Layout` stamps `data-brand-theme/-source/-platform`, `data-org-id`,
   `data-product` on the layout root. `scripts/review/brand_gate.py` (reusable, any module/tenant) checks org,
   platform, source, html theme, tab title, sidebar, website link, forbidden tenant names, and public-contact
   source; `p73look.py` runs it on every screen and writes `brand-gate-report.json`. Unit coverage:
   `tests/test_brand_resolution_gate.py`, `tests/frontend/brandResolution.test.mjs`,
   `tests/test_wholesale_p73_public_contact.py`. Also fixed: "Back to website" used the hostname brand
   (BookaBoost on localhost) and now uses the workspace brand; a late host-level `/branding` answer can no
   longer override the workspace theme on a non-brand host.

## 72. Rules a later phase must keep

1. A screen that renders the wrong tenant/platform brand FAILS acceptance. Run `brand_gate` in every browser
   acceptance run; `shell_source` must be `workspace` (or `host` on a brand domain), never `default`.
2. Never hardcode EvoSysPro (or any brand) for localhost; brand comes from the authenticated org's platform.
3. Public contact comes only from the organization's Wholesale settings. No platform or cross-tenant fallback.
4. EvoSense names the engine only. Product identity is the brand's product name (EvoSys Wholesale for EvoSysPro).
