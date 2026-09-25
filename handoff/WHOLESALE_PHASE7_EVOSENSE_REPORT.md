# PHASE 7 — EVOSENSE ACQUISITION ENGINE

**SET THE STRATEGY. LET EVOSENSE HUNT.**

Repo: `C:\Dev\advisorflow-web` (branch `main`, uncommitted — same working tree as Phase 6.1).
Nothing in this phase was committed, pushed, merged or deployed.

---

## 0. ARCHITECTURE RECONCILIATION

Written before any Phase 7 code, from a read of: `handoff/WHOLESALE_REAL_ESTATE_HANDOFF.md`,
`handoff/WHOLESALE_PHASE6_1_POLISH_REPORT.md`, `app/models/wholesale_models.py`,
`app/services/wholesale_{service,enrichment,ai,analysis,pipeline}.py`,
`app/routers/wholesale_router.py`, `Lead`, `SuppressionEntry`, `compliance_service`,
`cadence_service`, `test_records`, `plan_limits`, `master_contacts`, `ai_gateway`,
`service_role` / `job_models`, the entitlement registry, and — **read-only** —
`feature/universal-intake @ a876548` (not merged, not copied, not modified).

### 0.1 The decision the whole phase rests on

**Discovery is not the deal system, and it is not the contact database.**

* A discovered property lives in a new, tenant-scoped **EvoSense canonical property**
  (`evosense_properties`). It is *not* a `WholesaleProperty`, because every
  `WholesaleProperty` carries a `WholesaleDeal` and appears on every Wholesale
  screen. Evaluating 1,842 houses must not create 1,842 deals.
* When an opportunity becomes actionable it is **promoted** into the existing
  Phase 6 objects (`WholesaleProperty` + `WholesaleDeal` + `WholesaleSellerProfile`)
  through the existing `wholesale_service` functions. There is exactly one deal system.
* A person becomes a platform **`Lead` only when EvoSense decides to work them**
  (eligible contact + outreach). That is the Phase 1 rule ("the seller is a Lead")
  and it is what makes DNC, suppression, consent, cadence, message history and plan
  capacity apply without a second implementation. Owners EvoSense merely *knows
  about* live in the EvoSense owner/contact graph and cost no lead seat.

### 0.2 Concept-by-concept

| Phase 7 concept | Decision | Where / why |
|---|---|---|
| Seller as `Lead` (DNC, consent, `allow_*`, `is_test`, message history, master contact) | **REUSE NOW** | A worked owner becomes a Lead via the same construction `wholesale_service.attach_seller` uses, with `plan_limits` and `master_contacts.record_lead`. |
| `SuppressionEntry`, `compliance_service.is_phone_suppressed`, `Lead.status == "dnc"` | **REUSE NOW** | Eligibility calls them; an opt-out writes the platform's real suppression row, never an EvoSense-only flag. |
| Cadence engine (`cadence_service`, `CadenceState`) | **REUSE NOW** | An EvoSense **campaign** is an enrolment into the existing cadence engine. No second messaging platform. Sandbox (`is_test`) records are refused by that engine by design, so sandbox outreach is recorded as a *simulated* touch and labelled SANDBOX. |
| `wholesale_ai.extract_from_message` + `_deterministic_read` | **REUSE NOW / EXTEND NOW** | The reader (AI via `ai_gateway`, rules fallback, STOP never sent to a model) is reused unchanged. EvoSense adds, on top: the 17-value outcome taxonomy, fact extraction with message provenance and truth states, and Seller Intent. |
| `wholesale_analysis.calculate_offer` / MAO | **REUSE NOW** | Preliminary economics call it; every input is labelled KNOWN / SELLER STATED / PROVIDER / ESTIMATED / HUMAN / MISSING. |
| `WholesaleProperty`, `WholesaleDeal`, `WholesaleSellerProfile`, approvals, contracts, buyers, closing | **REUSE NOW (promotion target)** | Promotion calls `create_property` and `attach_seller(lead_id=…)`. Phase 6 approval gates are untouched and unreachable from EvoSense. |
| `EnrichmentResult` / `EnrichmentProvider` / `ManualProvider` | **REUSE NOW / EXTEND NOW** | The contact-enrichment capability returns the existing `EnrichmentResult` shape. EvoSense adds a capability registry (16 capabilities) around it rather than a second result shape. |
| `WholesaleSettings.enrichment_*_cap` (count caps) | **REUSE for the deal room; DO NOT DUPLICATE** | Deal-room enrichment after promotion keeps using them. Acquisition-side spend is money, not counts, so EvoSense budgets are cents with an atomic ledger (NEW). Converging the two is a documented P1 item, not a second cap on the same call. |
| `WholesaleEvent` (non-human actor audit) | **REUSE after promotion**; **NEW `evosense_events` before it** | `WholesaleEvent.property_id` references `wholesale_properties`, so it physically cannot point at a pre-promotion property. Same shape and actor vocabulary. User actions are *also* written to `audit_log_entries` via `log_action`, as Phase 1 does. |
| Entitlement | **REUSE NOW** | EvoSense is part of Wholesale: every route is behind `require_feature("wholesale_real_estate")`. No new feature key (that registry is shared platform code). |
| `ai_gateway` capability `wholesale_seller_qualify` | **REUSE NOW** | No new model, switch or cap invented. |
| Tenant scoping (`wholesale_service.read_org_id / write_org_id`, observer guard) | **REUSE NOW** | Same dependencies as `wholesale_router`. |
| Background jobs | **REUSE the pattern, NEW entry point** | A hunt is an idempotent function (`evosense.hunt.run_strategy`) with an `evosense_runs` record. Wiring a new in-process loop needs `JobName` + `service_role` + `main.py` edits in shared platform files, so P0 ships a `scripts/evosense_hunt.py` job entry (the `ROLE_JOB` path `service_role` already defines) plus "Run hunt now". See §Jobs. |
| Strategy / signals / canonical property / observations / identity resolution / owner-entity graph / three scores / cost ledger / atomic budget / handoff / nurture / attribution / feedback | **NEW PHASE 7 DOMAIN** | Nothing in the tree or the intake branch does any of this. |
| Property address normalization, APN normalization, property identity | **NEW PHASE 7 DOMAIN** | Property identity is not person identity; Universal Intake's matcher is person/company-oriented (email, phone, surname) and does not know APNs, units or parcels. |
| Phone / email normalization | **REUSE NOW** (`dedup_service.normalize_phone`, `compliance_service.usable_us_phone`); **ADAPT LATER** (intake `normalize.email`) | Same numbers must normalize identically everywhere suppression is checked. |
| `org_contacts` / `org_contact_source_ids` (intake branch) | **ADAPT LATER FROM UNIVERSAL INTAKE — DO NOT DUPLICATE** | EvoSense keeps *property-owner relationship* objects (owner, person, contact point) that the intake branch has no equivalent for. It does **not** build a general contact database. Every EvoSense person/contact point carries `converged_contact_ref` (nullable, unused in P0) reserved for the `org_contacts.id` it will map to. See §Universal Intake Convergence. |
| `ImportBatch` / `ImportStagedRow` / mapper / rollback (intake branch) | **ADAPT LATER — DO NOT DUPLICATE** | Phase 7 does not build a second import center. CSV property ingestion is a thin **IMPORT source connector** into the same ingest pipeline every provider uses (fixed columns, bounded size). Bulk mapped/staged/rollback-able property import converges onto intake. |
| Intake "Needs Enrichment" | **ADAPT LATER** | EvoSense has its own *economic* enrichment decision (whether to spend money); intake's flag is a *data completeness* status. They map, they don't merge (§Convergence). |
| Google Contacts bypass, legacy `/leads/upload/confirm` | **NOT TOUCHED** | Documented in §Convergence; not fixed in this phase. |

### 0.3 What Phase 7 deliberately does not build

A generic CRM, a second leads table, a second messaging engine, a second import
center, a second deal system, a vendor-shaped schema, or any connector to a
provider we have no API, credentials or contractual access for.

---

## 1. What was built (P0)

EvoSense runs **before** a Wholesale deal exists:

```
strategy ─► hunt ─► source observations ─► ONE canonical property per org
        ─► signals (fresh / aging / stale) ─► PROPERTY OPPORTUNITY  (+ data confidence)
        ─► owner / entity / person / contact point ─► CONTACT CONFIDENCE
        ─► enrichment DECISION ─► atomic budget ─► provider (fallback) ─► cost ledger
        ─► outreach ELIGIBILITY (DNC, suppression, caps, attestation) ─► existing cadence engine
        ─► seller reply ─► 17 outcomes + facts with quotes ─► SELLER INTENT
        ─► NEEDS YOU ─► human PROMOTION into the existing Wholesale deal
```

Every step is deterministic, versioned where it scores, tenant-scoped, idempotent,
and recorded in `evosense_events`. No model decides a score, a budget, a
permission, DNC, compliance, money or a contract.

## 2. Data model — `app/models/evosense_models.py` (18 new tables)

All carry `organization_id NOT NULL`; every index leads with it. Created by the
existing `create_all` via one added import in `app/models/registry.py`. **No column
was added to any existing table** (so `auto_migrate.py` is untouched).

| Table | Purpose |
|---|---|
| `evosense_strategies` | What to hunt. Versioned on every edit; draft/active/paused/archived; never deleted. |
| `evosense_controls` | One row per org: 7 kill switches, org day/month budget, owner-touch cap (days). |
| `evosense_provider_configs` | Per-org provider enable/priority/cost overrides/health (no credentials). |
| `evosense_properties` | Canonical property per org: identity keys, facts with provenance + `fact_ranks`, conflicts, cached scores, bucket, next action, promotion link. |
| `evosense_observations` | Append-only "what one source said"; unique (org, provider, source_reference) = idempotency. |
| `evosense_identity_reviews` | AMBIGUOUS identity → a person decides (merge / new / dismiss). |
| `evosense_signals` | Evidence per source, with observed/effective/stale dates, confidence, provenance, cost; retracted not deleted. |
| `evosense_owners` / `evosense_ownerships` | Owner of record (individual/joint/LLC/corp/trust/estate) with honest `resolution`; ownership per source, conflicts as rows. |
| `evosense_persons` / `evosense_contact_points` | Owner → person (owner/co-owner/heir/executor/agent) → phone/email with source, validation, agreeing sources, status (active/wrong_party/suppressed/opted_out/invalid). `converged_contact_ref` reserved for Universal Intake. |
| `evosense_scores` | Every score ever computed, with version, inputs, factors; history never overwritten. |
| `evosense_budget_counters` | Spend per (org, scope, period), moved only by one conditional UPDATE. |
| `evosense_cost_ledger` | Every paid/attempted operation: reserved → charged / failed_refunded. |
| `evosense_enrichment_decisions` | Why money was or was not spent, for every property, every time. |
| `evosense_engagements` / `evosense_messages` | Working one owner: status, delivery mode (cadence / SANDBOX simulated), nurture date + retained reason. |
| `evosense_facts` | Seller-stated facts with quote, message id, extractor, truth state; superseded not deleted. |
| `evosense_handoffs` | NEEDS YOU queue. |
| `evosense_feedback` | GOOD_FIND / BAD_FIT / WRONG_OWNER / BAD_CONTACT / NOT_ACTUALLY_DISTRESSED / HIGH_PRIORITY / IGNORE, with a snapshot. Recorded, never auto-trained on. |
| `evosense_runs` / `evosense_events` | Hunt records (counts, status) and the pre-promotion audit trail (actor user/system/ai/automation). |

## 3. Services — `app/services/evosense/`

| Module | Responsibility |
|---|---|
| `common.py` | Vocabulary (16 capabilities, connector kinds, health, truth states, buckets, 17 outcomes, 9 decisions), controls, `log_event` (mirrors human actions to the platform audit log). |
| `strategy.py` | Validation (sentences for a person), lifecycle transitions + activation rules, the read-back summary sentence, geography match. |
| `providers.py` | Provider-neutral adapter base; SANDBOX adapters; MANUAL and IMPORT sources; per-org config; health (DEGRADED after 3 consecutive failures for 15 min, RATE_LIMITED, DISABLED, MISSING_CREDENTIALS computed from env); deterministic routing; status report with no credentials. |
| `sandbox_data.py` | The deterministic synthetic market (555-01xx phones, `.example` emails, "Sandbox" mailing streets). |
| `identity.py` | Street/APN normalization; EXACT / PROBABLE / AMBIGUOUS / NEW; per-org only. |
| `ingest.py` | One pipeline for every source; fact precedence human 100 > records 60 > feeds 40 > import 30; lower/equal-rank disagreement is a **conflict**, never an overwrite; owner parsing that never invents a beneficial owner; credential-shaped keys dropped from payloads. |
| `signals.py` | Catalog (18 types), freshness policy per type, derivation (absentee, out-of-state, high equity ≥50%, free & clear, long ownership ≥10y), stacking with evidence. |
| `scoring.py` | Property Opportunity, Data Confidence, Contact Confidence, Seller Intent (formulas in §7). |
| `evaluate.py` | Rescore + derive the ONE inbox bucket and next action from the records. |
| `enrichment.py` | Decision tree (§8), execution with reserve → call → charge/refund → fallback → phone validation. |
| `budget.py` | Atomic reservation, charge, refund to the original period. |
| `contacts.py` | Owner → person → contact point graph; bad status inherited org-wide ("a new strategy cannot resurrect it"). |
| `eligibility.py` | Server-side outreach checks (§9). |
| `outreach.py` | Lead creation (plan capacity + master contact), cadence enrolment or SANDBOX simulated message, follow-up with retained context. |
| `conversation.py` | Deterministic reader, 17 outcomes, facts with quotes, opt-out handling, nurture dates, handoff triggers, Seller Intent. |
| `handoff.py`, `economics.py`, `promotion.py`, `hunt.py`, `views.py`, `sandbox_seed.py` | As named. |

## 4. HTTP — `app/routers/evosense_router.py` (prefix `/wholesale/evosense`)

Gated by `require_feature("wholesale_real_estate")` on the router; reads use
`require_tenant_or_observer`, writes `require_tenant_user` + `require_not_observation`.
Budgets, provider switches and **resuming** a switch need a workspace admin; **pausing**
is open to every user. Id-bearing routes answer 404 across tenants.

`GET command-center · GET inbox · GET properties/{id} · POST properties (manual) · POST import (CSV) ·
POST properties/{id}/enrich|outreach|reply|nurture|promote|feedback|contacts|signals|rescore ·
POST properties/{id}/contacts/{cid}/wrong-party · POST handoffs/{id} · GET identity-reviews ·
POST identity-reviews/{id} · GET|POST strategies · POST strategies/preview · GET|PATCH strategies/{id} ·
POST strategies/{id}/clone|hunt|activate|pause|resume|archive · GET|PATCH providers · GET|PATCH controls · GET events`

Every id-bearing route was added to `tests/test_wholesale_cross_tenant.py`'s attack list
(its guard test fails on any unlisted `/wholesale/...{id}` route).

## 5. Jobs

`hunt.run_strategy()` is idempotent (observations are unique per source reference;
enrichment only runs for properties with no decision or a freed budget; no second
conversation), bounded (`max_properties`, default 500), locked per strategy for 30
minutes against concurrent runs, and writes an `evosense_runs` row with counts.
One failing provider never ends a hunt (the run is `partial`).

* **Scheduled:** `python scripts/evosense_hunt.py [--org <id>]` — every active strategy.
  Deliberately **not** registered in `main.py`'s background loops (shared platform
  registry; a job that spends money should be scheduled on purpose).
* **Manual:** "Run hunt: <strategy>" on the Command Center.
* **Nurture:** `conversation.resume_due()` runs inside every hunt.

## 6. Screens — `frontend/src/pages/wholesale/evosense/`

| Route | Screen |
|---|---|
| `/wholesale/evosense` | **Morning Command Center** — Needs you · What happened (24h, counted from events/decisions) · Spent (today, month, per contact found, per hand-off, per-strategy budget bars, SANDBOX note) · Best opportunities · What happens next · Inbox at a glance. Pause/Resume EvoSense, Run hunt per strategy. |
| `/wholesale/evosense/inbox` | **Discovery Inbox** — 16 buckets with counts, search, strategy/signal/sort filters, 3 score chips, signals, status, next action, blocked reason; server-paged 50; cards at ≤820px. |
| `/wholesale/evosense/property/:id` | **Property Intelligence** — the three score cards with every factor and version; conversation; seller-stated facts with quotes; preliminary numbers with truth labels; signals with evidence per source; owner (resolution), contacts with Contact Confidence factors, eligibility checklist; cost decisions + ledger; facts with provenance; conflicts; identity review; attribution; nurture; feedback; Promote to Wholesale. |
| `/wholesale/evosense/strategies`, `/strategies/new`, `/strategies/:id` | List (summary, version, metrics, lifecycle actions) and the **Strategy Builder**: 7 plain-language sections with a live server-written read-back and the activation problems before Save and activate. |
| `/wholesale/evosense/controls` | Kill switches, org budget, owner cap, capability classification, providers (kind, status, cost, health, enable). |

Styling: `evosense.css`, scoped `.es-page`, theme tokens only (dark default and every
light palette). Colour rule kept from Wholesale: amber = a queue with a person's name on
it, red = a genuine problem, purple = SANDBOX. 1440px reading width; one column at 1024px;
card lists at 820px; phone layout at 560px; 44px controls ≤1024px.
Nav: three items added to the existing Wholesale group (EvoSense, Discovery Inbox, Strategies).

## 7. Scores (deterministic, versioned, explained)

**Property Opportunity `property_opportunity/v1`** (0–100; `null` = INSUFFICIENT EVIDENCE)
* Outside the strategy's geography → 0. Any excluded signal present → 0.
* Current signal points: Vacant 18, Absentee 15, Pre-foreclosure 16, Tax delinquent 12, Probate 12,
  Estate 10, Out-of-state 8, Code violation 8, Distressed 8, Tired landlord 8, Free & clear 6, Lien 6,
  Expired/Failed listing 6, Price reduction 4, Operator flag 5; Long ownership 10 (≥15y) / 6 (≥10y).
  **Aging counts half, stale counts 0** (and is still shown).
* Equity (scored once, from the number): ≥60% 14, ≥45% 11, ≥35% 8, ≥20% 4; −15 below the strategy minimum.
* Strategy fit: value in range +7 / outside −20; wrong property type −25; preferred signals +2 each (max 6);
  owner geography not matched −10; owned fewer than the minimum years −10.
* Stacking: ≥3 distinct signals with ≥2 independent (non-derived) +7; exactly two +3.
* A required signal missing caps the score at 40.

**Data Confidence `data_confidence/v1`** — separate from opportunity (a 92 can be LOW data confidence):
value +20, equity +10/15, owner known +15, current signals +10 each (max 25), ≥2 independent sources +15,
stale −10, conflicts −20 → high ≥70 / medium ≥40 / low / insufficient.

**Contact Confidence `contact_confidence/v1`**: owner-name match +25 (co-owner +15, heir/executor +12,
registered agent 0), mailing address match +20, two+ sources agree +18, mobile validated +15 (other
validated +8, failed −40), record ≤1y +10 (>3y −20), prior successful response +6, wrong-party −30,
unresolved entity −12.

**Seller Intent `seller_intent/v1`** — only from SELLER-STATED facts and the outcome; never from
provider data or sentiment: willing to sell 25, asking price 18, quick close 15, condition 12,
appointment 12, callback 10, offer request 10, estate context 6, occupancy 5; interest without a
willingness statement +15; ≥2 replies +8; listed −30; price too high −5. DNC / wrong person /
not owner / sold / hard no → 0. NOT_NOW caps 25; call later / family / maybe cap 40.

Flagship (1418 Cedar Springs Rd): **PO 97** = 18 vacant + 15 absentee + 14 equity 61% + 12 tax + 10 owned 18y
+ 8 out-of-state + 7 value match + 7 multiple independent + 6 preferred. **CC 94** = 25 name + 20 mailing
+ 18 two sources + 15 mobile validated + 10 recent + 6 prior response (88 before the reply).
**SI 81** = 25 willing + 18 asking + 15 quick close + 12 condition + 6 estate + 5 occupancy.

## 8. Cost-aware enrichment and the atomic budget

Decision order (`enrichment.decide`, first match wins, every decision stored with reasons):
paused → RETRY_LATER · no owner → INSUFFICIENT_OPPORTUNITY · owner opted out / suppressed →
SUPPRESSED · good contact already, or owner looked up in the last 90 days (any property, any
strategy) → USE_EXISTING_DATA · no-match/failure in the last 7 days → RETRY_LATER · opportunity
below the strategy → INSUFFICIENT_OPPORTUNITY · no provider (sandbox never serves a real property)
→ RETRY_LATER (WAITING FOR DATA) · free source → USE_FREE_SOURCE · per-property cap → BUDGET_BLOCKED ·
above "ask me" amount → APPROVAL_REQUIRED · display budget check → BUDGET_BLOCKED · else PAID_LOOKUP_APPROVED.

Money moves only through `budget.reserve()`: one conditional
`UPDATE … SET spent_cents = spent_cents + :c WHERE id = :id AND spent_cents + :c <= :lim`
per applicable counter (org day, org month, strategy day, strategy month) inside one savepoint.
Zero rows → refused **before** the call. A failed/timed-out call is refunded to its own
period; a completed call (including a billable no-match) is charged. The fallback provider is
tried only if it also fits the budget; a no-match does not trigger a second paid opinion.

## 9. Outreach eligibility (`eligibility.check`, server-side, no override)

EvoSense running · SMS switched on · contact exists and is `active` · usable US number ·
**not on `suppression_entries`** · **no platform Lead with this number at DNC** · not a landline for SMS ·
passed validation · Contact Confidence ≥ strategy minimum · **owner not already in conversation
(any property, any strategy)** · owner not touched in the last N days (default 7) · not already a
Wholesale deal · **REAL cold SMS refused unless the strategy records the organization's
compliance confirmation**. A suppressed/DNC hit is written onto the contact point so every later
strategy sees it. Real owners are enrolled with `cadence_service.start_cadence`, which applies its
own gates on every touch; SANDBOX owners get a written, labelled, **simulated** message and nothing
leaves the building.

## 10. Seller conversation intelligence

Reader: deterministic rules decide the outcome; the platform AI reader (`wholesale_ai`, via
`ai_gateway`) is consulted only for a real (non-sandbox) message the rules cannot place, unless
AI replies are paused; it can never downgrade an opt-out. A STOP recognised by EvoSense's reader
**or** the platform's (stricter) reader wins over everything else in the message.

Actions: DNC → Lead `dnc` + `SuppressionEntry(REPLY_STOP)` + contact `opted_out` + cadence stopped ·
WRONG_PERSON → contact `wrong_party` (CC −30), engagement stopped, plus suppression + DNC if they
said stop · NOT_NOW / CALL_LATER / FAMILY / MAYBE → nurture until a date read from the words
("after the holidays" → Jan 6; "next year", seasons, "in N weeks/months", "next month/week",
"tomorrow"; otherwise the strategy default) with the reason kept, and the follow-up quotes it ·
SOLD / NOT_OWNER / LISTED / HARD_NO → stopped · INTERESTED / WANTS_OFFER / APPOINTMENT / ESTATE /
TENANT / PRICE_TOO_HIGH → responded; NEEDS YOU on intent ≥ threshold, a stated price, an offer or
visit request, a callback, estate, tenant issue, or an unreadable reply.
"around 150" is recorded as **$150,000 with the note that it was read as shorthand** — the quote
travels with the number.

## 11. Promotion and preliminary economics

Human only (signed-in user; no automation path). Idempotent. `create_property` →
`attach_seller(lead_id=<the Lead EvoSense worked>)` (no duplicate person, no second seat) → seller
profile fields from SELLER-STATED facts → deal to `seller_engaged` if the seller responded →
`WholesaleEvent "evosense.promoted"` carrying scores, spend, signals, seller facts with quotes and
the whole EvoSense timeline. The EvoSense record stays and links to the deal.

Preliminary numbers call `wholesale_analysis.calculate_offer` with the org's Wholesale Settings.
ARV = provider value (**PROVIDER REPORTED — not an ARV from comps**), repairs = per-sq-ft band from
the SELLER-STATED condition (**SYSTEM ESTIMATE**), asking = **SELLER STATED**. Flagship:
$305,000 × 70% − $41,250 repairs − $9,150 transaction − $10,000 fee = **$153,100 MAO**,
asking $150,000 → "inside by $3,100. Verify ARV and repairs before any offer." Internal only.

## 12. Human approval boundaries and security

EvoSense cannot send an offer, create/sign a contract or assignment, accept a buyer offer, move money
or mark funds collected — none of those functions is reachable from `app/services/evosense/`, and
promotion lands at `seller_engaged`, before every Phase 6 approval gate.
Investor Deal Room / Seller Portal: separate token routers that never import EvoSense; a test
promotes the flagship, publishes both rooms and asserts neither response contains signals, intent,
scores, contact data, seller identity (investor), MAO, costs or "inherited".
No credential is stored or returned (`/providers` is tested with a live-looking secret in the env).

## 13. Every external capability, classified

| Capability | Classification in this build |
|---|---|
| PROPERTY_SEARCH, PARCEL, ASSESSOR, OWNERSHIP, VALUATION | **SANDBOX** (sandbox property records) · **MANUAL** · **IMPORT** (CSV) |
| VACANCY | **SANDBOX** · MANUAL |
| TAX | **SANDBOX** · MANUAL |
| PROBATE, CODE_VIOLATION, FORECLOSURE | **SANDBOX** · MANUAL |
| CONTACT_ENRICHMENT | **SANDBOX** (primary $0.18 + fallback $0.25) · MANUAL |
| PHONE_VALIDATION | **SANDBOX** ($0.01) |
| LISTING | **MANUAL** only (a person can flag an expired / failed listing); no MLS connector |
| EMAIL_VALIDATION | **INTERFACE ONLY** (emails shown unverified) |
| ENTITY_RESOLUTION | **INTERFACE ONLY** (LLCs stay UNRESOLVED ENTITY) |
| COMPS | **INTERFACE ONLY** here; comps live in the existing Wholesale deal room |
| SMS delivery | **REAL / CONNECTED** only through the existing platform cadence engine for real owners (gated by the attestation); SANDBOX owners are **simulated** |
| AI reply reading | **REAL** via the existing `ai_gateway` capability when configured and switched on; rules otherwise |
| Email / voice outreach | **NOT BUILT** (switches exist and are enforced; there is nothing behind them) |
| **Any real property-data, public-record or skip-trace vendor** | **NOT BUILT — no credentials, no contract, no connector.** Adding one is one adapter class + one registry line (§3). |

## 14. Universal Intake convergence plan (§64)

| Topic | Plan |
|---|---|
| `org_contacts` | The platform contact record. EvoSense persons/contact points map to it through `converged_contact_ref` (present, nullable, unused). EvoSense keeps only the property-owner *relationship* (who owns what, in what role, per source). |
| `org_contact_source_ids` | An EvoSense contact point's (provider, source_reference) becomes a source id on the converged contact. |
| `ImportBatch` / `ImportStagedRow` | Bulk property lists move onto intake's staging/mapping/rollback; EvoSense's CSV source becomes one intake target type ("properties"). Until then it is a bounded, idempotent, fixed-column connector — not an import center. |
| Normalization | Phone: already the platform normalizers. Email: adopt intake `normalize.email`. Address/APN: EvoSense's normalizer is property identity and should be offered back to intake for address columns. |
| Dedupe | Person dedupe = intake's matcher. Property identity stays EvoSense's (APN/unit/ZIP). Never merged across orgs on either side. |
| Classification vs strategy | Intake classifies *what a row is* (contact / lead / company). A strategy decides *whether a property is worth pursuing*. An intake row can feed EvoSense as an observation; a strategy never re-classifies a contact. |
| Needs Enrichment | Intake: data is incomplete. EvoSense: money may be spent. Mapping: intake "needs enrichment" on a property-owner row → EvoSense decision queue; EvoSense never marks intake rows. |
| Contact vs Lead vs Opportunity | Contact = known person (org_contacts). Lead = person being worked (created only on outreach, as today). Opportunity = EvoSense property + engagement; becomes a Wholesale deal only on promotion. |
| Rollback | Intake batch rollback must also retract EvoSense observations from that batch (observation `run_id` / `source_reference` carry the batch id). Signals are retracted, not deleted. |
| Audit | Intake audit + `evosense_events` + `audit_log_entries` (human actions mirrored) — one timeline view per contact in P1. |
| DNC | Single authority already: `suppression_entries` + `Lead.status`. Intake must write opt-outs there; EvoSense only reads/writes there. |
| Capacity | Leads cost seats only when worked (outreach / promotion), through `plan_limits`, as today. |
| Google Contacts bypass | Not touched. It must route through intake before EvoSense can trust contacts sourced from it. |
| Legacy `/leads/upload/confirm` | Not touched. Keep until intake replaces it; EvoSense does not read from it. |

`feature/universal-intake` was read-only: not merged, not copied, not modified.

## 15. The required journeys — what the review data shows

All in organization **EvoSense Review (TEST)**, all SANDBOX, all produced by the real engine
(`scripts/seed_evosense_review.py` runs two hunts and reads three simulated replies; nothing is
written by hand that the engine would not write).

| Journey | Property | What happened | Test |
|---|---|---|---|
| Positive | 1418 Cedar Springs Rd | Property records + vacancy feed ("1418 CEDAR SPRINGS ROAD", ZIP+4) + tax roll ("1418 Cedar Springs Rd.", "Dallas County", no ZIP) → **one** canonical property. Signals Vacant, Absentee, Out-of-state, High equity 61%, Tax delinquent, Long ownership 18y. PO 97. Skip trace **$0.18** + validation $0.01; +1 214-555-0142 mobile, valid, 2 sources agree, mailing matches. Simulated opener. Seller reply → INTERESTED; 6 facts with quotes; SI 81 ≥ 70 → NEEDS YOU; preliminary MAO $153,100 vs $150,000 asking. Promote → Wholesale deal at `seller_engaged`, same Lead, history carried. | `test_flagship_*` (2) + API promote test |
| Wrong person | 4915 Live Oak St | "Wrong person. Stop texting me." → WRONG_PERSON; contact `wrong_party`; CC 70 → 40; suppression entry + Lead DNC; engagement stopped; a second strategy hunting the same house cannot start outreach; exactly one outbound message ever; no paid re-lookup. | `test_wrong_person_*` |
| Not now | 7302 Ferguson Rd | "Not interested right now. Maybe after the holidays." → NOT_NOW; nurture until **Jan 6, 2027**; SI 0 (capped); no suppression; when due, back to the queue and the follow-up quotes their words. | `test_not_now_*` |
| Budget $1.00/day ×6×$0.20 | — | Six threads, one barrier: **exactly five** reservations, ledger **$1.00**, counter 100¢ (SQLite with whole-transaction retry, and PostgreSQL 16 at READ COMMITTED). Plus a pinned stale-read interleaving and refund-to-same-day. | `test_evosense_budget_concurrency.py` |
| Budget blocked | 3102 Avenue J, 2710 Stadium Dr | "Tarrant Probate — $0.20/day": first estate $0.18 + $0.01; the other two BUDGET_BLOCKED, $0 spent on them, "$0.01 left today, lookup costs $0.18". | `test_tiny_budget_*` |
| Dedupe | 1418 Cedar Springs; 5530 Bonnie View Rd | Manual "1418 Cedar Springs Road" and CSV "1418 CEDAR SPRINGS RD, 75201-2702" merge; the CSV's different owner and $999,000 value become **conflicts**, not overwrites; re-import is "seen". Tax roll "5530 Bonnie View Rd Unit B" (no APN) → **AMBIGUOUS → identity review**, never merged. | `test_manual_and_csv_*` |
| Multi-property owner | Raymond T. Castillo (6 Tarrant houses) | One owner row, **one** $0.18 lookup reused for all six, one conversation, the others blocked "OWNER ALREADY IN CONVERSATION", one outbound message. | `test_six_properties_*` |
| Tenant isolation | Org A / Org B | Same synthetic market hunted by both: disjoint rows, B sees none of A's conversation, 404 on every A id; every id-bearing EvoSense route in the cross-tenant attack list. | `test_two_organizations_share_nothing` + `test_wholesale_cross_tenant.py` |
| Provider failure | 1805 Nolte Dr | Primary skip trace times out → reservation **refunded**; fallback ($0.25) answers no-match; WAITING FOR DATA; no contact invented; 3 consecutive failures → DEGRADED and routed around; with no budget for the fallback it is "not attempted" and nothing is charged. | `test_provider_failure_*` (2) |
| Also in the data | 2611 Glenfield Ave (PO 100, no contact → waiting), 3330 Hatcher St (number already on the suppression list → SUPPRESSED, never messaged), 2124 S Ervay St (Oak Cliff Holdings LLC → UNRESOLVED ENTITY, registered-agent landline, CC 6), 6120 Wedgwood Dr (Castillo conversation), 14 properties below threshold. | |

## 16. Tests

```
PHASE 7 SUITE (tests/test_evosense_*.py)            52 passed  (53 with PostgreSQL enabled)
  journeys 35 · api/security 14 · budget concurrency 3 (+1 PostgreSQL)
CROSS-TENANT (tests/test_wholesale_cross_tenant.py)  4 passed, attack list now covers 21 EvoSense routes
WHOLESALE SUITE (-k "wholesale or evosense")         339 passed  (287 Phase 6.1 + 52) — on Windows (C:\Dev\advisorflow-web)
                                                     and in the cloud copy, both on the final code
FULL REGRESSION                                      5,643 passed · 15 skipped · 1 failed (pre-existing) — §16.1
FRONTEND node tests                                  12 passed · 2 failed — the same 2 fail on the untouched
                                                     tree (God Mode theme tokens, another workstream)
FRONTEND BUILD                                       clean, cloud and Windows (365 modules)
REAL BROWSER (Windows, Chromium via Playwright)      7 pages × 4 widths = 28 checks: 0 page errors,
                                                     0 horizontal overflow, 0 clipped tables,
                                                     0 controls under 44px at ≤1024px
```

### 16.1 Full regression

Run in the cloud copy of the same tree (Linux, `pytest -n 4`, 29 min):
**5,643 passed · 15 skipped · 1 failed.**

* 5,643 = the Phase 6.1 baseline 5,591 + the 52 new EvoSense tests. No existing test changed state.
* 15 skipped = the Phase 6.1 14 + the PostgreSQL budget test (skipped without `EVOSENSE_TEST_PG_URL`;
  it was run separately against PostgreSQL 16 and passed).
* The 1 failure, `test_zoom_integration.py::test_requires_video_not_overwritten_when_user_edited_row`,
  fails identically on the untouched snapshot of the tree — it is one of the two pre-existing
  failures named in the Phase 6.1 report. The other one (a Windows clock-resolution guard) passes on Linux.
* A serial full run on Windows would take several hours (no `pytest-xdist` installed there, and I did
  not install packages into your environment); on Windows the Wholesale + EvoSense suite (339) and the
  EvoSense + cross-tenant files (56) were run on the final code.
* Found by the Windows run and fixed: two EvoSense rows written in the same millisecond got the
  same timestamp on Windows, so "latest decision" was ambiguous. EvoSense timestamps are now
  strictly increasing within a process (`evosense_models._now`).

## 17. Review environment (the real repo, verified from Windows)

```
backend    http://localhost:8000    python -m uvicorn app.main:app --port 8000 --host 127.0.0.1   (C:\Dev\advisorflow-web)
frontend   http://localhost:5173    npm run dev -- --port 5173 --strictPort                        (C:\Dev\advisorflow-web\frontend)
database   C:\Dev\advisorflow-web\advisorflow.db   (backed up first: advisorflow.db.pre-p7-evosense.bak)
frontend/.env   VITE_API_BASE_URL=http://localhost:8000   (already present)
```

Login **evosense.review@example.test / EvoSense-Review-2026!** (org admin of
**EvoSense Review (TEST)**, a new organization; no other organization was touched).
Re-seeding is safe: `python scripts\seed_evosense_review.py` reuses the org, user and strategies,
and hunts are idempotent.

Browser evidence: `handoff/p7-shots/` — 28 full-page screenshots (7 pages × 1550/1280/820/390)
taken on Windows by `handoff/p7-shots/p7look.py` against the running local servers, and
`browser-check.json` with the per-page measurements. The "Complete your profile" card that
floats over the lower right is the platform's own onboarding checklist for a new user, not EvoSense.

## 18. Files

**New:** `app/models/evosense_models.py` · `app/services/evosense/` (22 modules) ·
`app/routers/evosense_router.py` · `frontend/src/pages/wholesale/evosense/` (6 JSX + 1 CSS) ·
`scripts/evosense_hunt.py` · `scripts/seed_evosense_review.py` · `tests/test_evosense_journeys.py` ·
`tests/test_evosense_api.py` · `tests/test_evosense_budget_concurrency.py` · `handoff/p7-shots/`.

**Shared files, minimal additive edits (3-way checked against the snapshot before writing — no
other workstream had changed them; line endings preserved):**
* `app/models/registry.py` — one import in each of its two blocks (LF).
* `app/main.py` — one router import + one `include_router` (CRLF).
* `frontend/src/App.jsx` — 5 imports + 6 routes (CRLF).
* `frontend/src/components/Layout.jsx` — 3 nav items in the existing Wholesale group (CRLF).
* `tests/test_wholesale_cross_tenant.py` — EvoSense ids in the fixture + `evosense_attacks()`.
* `handoff/WHOLESALE_REAL_ESTATE_HANDOFF.md` — Phase 7 section appended.

No conflicts with another thread's work were found. Nothing committed, pushed, merged or deployed.
`feature/universal-intake` untouched.

## 19. Known gaps (honest)

1. **No real data vendor.** Every discovery and contact source is SANDBOX, MANUAL or IMPORT.
2. **Real cold SMS is not demonstrated** — sandbox owners are simulated by design, and real
   owners need the compliance attestation + the platform cadence engine's own gates. Inbound
   real SMS replies reach EvoSense only when typed in ("Record reply"); wiring the platform's
   inbound SMS webhook to `conversation.receive` for EvoSense-worked Leads is P1.
3. **Scheduling** is a script entry (`scripts/evosense_hunt.py`), not a registered in-process loop.
4. Contact Confidence for emails is weak (no email validation connector).
5. The CSV import is fixed-column (see `hunt.CSV_COLUMNS`) — mapping/staging/rollback converge onto Universal Intake.
6. `occupancy_preferences` on a strategy is stored and shown but not yet scored (owner geography is).
7. Preliminary ARV is the provider value, not comps — labelled so everywhere.
8. Feedback is recorded, never learned from (by design in P0).
9. The AI reader is consulted only for real, unplaceable replies; the sandbox journeys are read
   by rules only, so the AI path is exercised by the existing Phase 6 tests, not by a sandbox journey.
10. No screen-reader pass (same as Phase 6.1).
11. Pre-existing, unrelated: 2 frontend God-Mode token tests and the 2 known full-suite failures.

## 20. P1 (not built, on purpose)

Production property / public-record / skip-trace connectors · MLS/listing, email validation,
entity resolution · heat maps and geography views · learning from feedback and outcome
attribution to tune scores (with versioning already in place) · inbound SMS webhook → EvoSense ·
in-process scheduler registration · converging EvoSense acquisition spend with the deal-room
enrichment count caps · Universal Intake convergence items in §14 · voice/email campaigns.

## 21. Exact review order

1. Start both servers (§17). Sign in at `http://localhost:5173/login` as the review user.
2. **Command Center** `http://localhost:5173/wholesale/evosense` — SANDBOX banner; Needs you:
   1418 Cedar Springs Rd with three reasons and Seller intent 81; What happened; Spent ($1.76 today,
   SANDBOX note, the probate strategy's bar full); Best opportunities; What happens next.
3. Click the flagship → **Property Intelligence**. Read the three score cards top to bottom and
   check each factor against §7. Conversation: the simulated opener and her reply. What the seller
   said: six facts, each quoting her; "around 150" read as $150,000 with that note. Preliminary
   numbers: $153,100 MAO, every line labelled. Signals with their sources. Contacts: why 94. Cost:
   $0.18 + $0.01. Where this came from: three sources, one property.
4. Press **Promote to Wholesale**, confirm, then **Open Wholesale deal** — Seller Engaged, asking
   $150,000, the promotion event in the deal history.
5. **Discovery Inbox** — walk the buckets: Budget blocked (2, Tarrant probate), Waiting for data
   (Glenfield: no contact exists; Nolte: provider failed, refunded, fallback found nothing),
   Suppressed (Hatcher), Closed out (Live Oak — open it: wrong party, CC 40, one message),
   Nurture (Ferguson — Jan 6), Contact found (S Ervay LLC: UNRESOLVED ENTITY; three Castillo
   houses: "owner already in conversation"), Needs review (Bonnie View — merge or keep separate).
6. **Strategies** — read both summaries; open **New strategy**, fill a few sections, watch the
   read-back change; try activating with no geography (refused, with the reason).
7. **Providers & Controls** — every capability's honest label; pause "Paid data", press
   "Run hunt" on the Command Center and see nothing bought; resume.
8. Narrow the window to a phone width on the Inbox and a property — cards, no sideways scroll.
9. Run `python -m pytest tests/test_evosense_journeys.py tests/test_evosense_api.py tests/test_evosense_budget_concurrency.py tests/test_wholesale_cross_tenant.py -q`.
