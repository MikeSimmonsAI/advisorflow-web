# EvoSys Wholesale — Property Intelligence → Contactability → Seller Engagement → Qualified Opportunity

Build date: 2026-09-26 (night). Mike's brief: make EvoSys Wholesale able to take a
real property opportunity toward a qualified seller opportunity with as little
human work as reasonably possible. NEEDS YOU is reserved for the things only a
person can do.

**Principle held throughout:** reuse what exists. No second contact system,
conversation engine, workflow engine, AI framework or provider framework was
added. Everything new either reads the existing records or sits on the existing
authorities.

---

## 1. Architecture reused

| Concern | Existing authority (unchanged unless noted) |
|---|---|
| Who a person is | Universal Intake (`org_contacts`, `intake.matching`, `intake.commit`) |
| SMS permission | `wholesale_sms.check_eligibility`: program consent of record, opt-out, suppression, DNC, program switch, registered sender, quiet hours |
| Email refusal | `compliance_service` preflight rules; `org_contacts.email_status` (bounce / unsubscribe / suppressed) |
| Contact Confidence | `evosense/scoring.contact_confidence` (extended to v2, see §2) |
| Enrichment | `evosense/enrichment.decide/execute`, `providers.route`, `budget` (atomic reserve/charge/refund), `evosense_cost_ledger`, kill switches (extended with policy tiers, see §2) |
| Seller language | `evosense/conversation.classify` (deterministic, exact quotes) and `wholesale_ai.extract_from_message` |
| Seller Intent | `evosense/scoring.seller_intent`, now also fed by Wholesale seller facts |
| Nurture (EvoSense) | engagement `nurture` status, `conversation.set_nurture` / `resume_due` |
| EvoSense NEEDS YOU | `EvoSenseHandoff` + reason codes |
| Deal workflow | Wholesale pipeline, approval gates, deal analyzer (no ARV means no MAO) |
| Promotion | `evosense/promotion.promote`: human-only, one deal object |

## 2. Architecture added

| New piece | What it is |
|---|---|
| `app/services/communication_eligibility.py` | One deterministic verdict per (person, channel) for SMS, email and a person's phone call. Every check is in one of four categories: **permission / block / operational / timing**. States: ELIGIBLE, BLOCKED, NO_PERMISSION, NOT_OPERATIONAL. Quiet hours are timing only, never ineligibility. |
| `app/services/contactability.py` | UNKNOWN / ENRICHMENT_NEEDED / CONTACT_DATA_FOUND / CONTACTABLE_SMS / CONTACTABLE_EMAIL / CONTACTABLE_OTHER / DO_NOT_CONTACT / REVIEW_REQUIRED. Derived with reasons and per-channel verdicts. Cached on EvoSense properties on every rescore; computed on demand for Wholesale sellers. |
| Contact Confidence v2 | +30 "verified by a person" (`POST /wholesale/evosense/properties/{id}/contacts/{cp}/verify`, reversible); −15 when ownership evidence conflicts. Everything else is unchanged from v1. |
| Enrichment policy tiers | `EvoSenseStrategy.enrichment_policy`: `free_only`, `standard` (the default and prior behaviour), `aggressive` (pays down to 15 points below the strategy's opportunity minimum, still inside every budget), `manual_approval`. Admin-only to change. The execute() fallback obeys the policy: a free decision never falls back to a paid provider, and an approved lookup never falls back to a costlier one. |
| Enrichment economics | Each decision records `policy`, `opportunity_score`, `confidence_before` and `confidence_after`. `economics.acquisition_cost(prop)` totals the ledger for a property. It appears on the EvoSense property page, the promotion event and seller intelligence. |
| `WholesaleSellerFact` (table `wholesale_seller_facts`) | One row per thing a seller told us. Each row keeps the words it came from (`quote`), the message it came from (`message_ref`: `reply:<id>`, `form:<submission>`, `evosense:<msg>`, `manual`), how it was read (`rules`/`ai`/`form`/`person`) and a truth state. An AI reading is **seller_stated**, never verified; only a person verifies. Newer facts supersede older ones and nothing is deleted. |
| `app/services/wholesale_seller_intel.py` | Facts from replies, inquiry forms, operator edits and EvoSense promotion. Seller Intent via EvoSense's scorer. Qualification outcome: QUALIFIED / NEEDS_MORE_INFORMATION / NURTURE / NOT_INTERESTED / DISQUALIFIED / HUMAN_REVIEW, with `missing` and `known_unknowns`. Wholesale nurture. |
| `app/services/wholesale_command.py` | The seller lifecycle **view** (derived, never stored), the NEEDS YOU feed and the Morning Command Center. |
| Endpoints | `GET /wholesale/command-center`, `GET /wholesale/needs-you`, `GET /wholesale/sellers/{id}/intelligence`, `POST /wholesale/sellers/{id}/facts/{fid}/verify`, `POST /wholesale/sellers/{id}/nurture`. Settings gain `qualification_criteria`, `nurture_default_days` and `cold_seller_email_confirmed` (all admin-only). |
| UI | A "This morning" panel on Wholesale Deal Operations (NEEDS YOU with why and evidence, plus actionable counts). A Seller intelligence panel on the deal page (lifecycle, qualification, intent, contactability per channel, facts with quotes, a Verify button, end nurture). Contactability on the EvoSense property page. |

## 3. Schema changes (additive only; auto-migrate `COLUMNS_TO_ADD`, new table via create_all)

* `evosense_contact_points`: `verified_at`, `verified_by_id`, `verification_note`
* `evosense_properties`: `contactability`, `contactability_detail`, `contactability_at`
* `evosense_strategies`: `enrichment_policy` (default `'standard'`)
* `evosense_enrichment_decisions`: `policy`, `opportunity_score`, `confidence_before`, `confidence_after`
* `wholesale_settings`: `cold_seller_email_confirmed` (default false), `qualification_criteria`, `nurture_default_days`
* `wholesale_seller_profiles`: `seller_intent`, `seller_intent_detail`, `qualification_status`, `qualification_detail`, `nurture_until`, `nurture_reason`
* new table `wholesale_seller_facts`

Verified on a local Postgres by dropping columns and re-running migrate.

## 4. State-machine changes

**No new stored status system.** The seller lifecycle (DISCOVERED → OWNER_IDENTIFIED → CONTACTABILITY_PENDING → CONTACTABLE → OUTREACH_ELIGIBLE → ENGAGED → QUALIFYING → QUALIFIED → OPPORTUNITY, or NURTURE / NOT_INTERESTED / DO_NOT_CONTACT / UNREACHABLE / REVIEW_REQUIRED) is computed from the existing states, which are:

* Lead status
* EvoSense property status and engagement status
* Wholesale deal stage
* the seller's qualification outcome
* contactability

Each answer carries `derived_from`, so nothing is hidden.

Two behaviour changes in the existing Wholesale flow:
1. **Auto-qualify is gated on the outcome.** A seller reply moves a deal to `qualified` only when the qualification outcome is QUALIFIED, not on the score band alone. A high score with the timeline still unknown is a question to ask, not a qualified seller.
2. **Not now is nurture.** A "not now" or "maybe later" reply sets the seller's nurture date. The deal is not killed. A seller in nurture who writes in again leaves nurture automatically.

**Compliance fix found during the build.** A Wholesale seller reply meaning STOP used to mark the Lead DNC only; the "real suppression" in the code was a no-op import. It now also writes the platform suppression entry and withdraws seller-program consent. Either reader recognising STOP is enough.

## 5. Provider / enrichment framework status

* **BUILT and LIVE:**
  * policy tiers
  * decision economics
  * acquisition cost
  * the existing budget, ledger, cache, duplicate-call prevention, failure/refund, fallback and kill-switch machinery
* **TESTED WITH SYNTHETIC DATA:** everything above, against the sandbox skip-trace providers (simulated prices; no money moves).
* **REQUIRES PROVIDER:** real skip-trace / contact enrichment, phone line-type validation and email validation. Only sandbox, manual and CSV sources are connected, and the interface-only entries (rentcast, regrid, attom) are not purchased. Until one is, "Awaiting contact data" is honest: nothing is invented.

## 6. Communication eligibility behaviour

* **SMS:** eligible only with consent of record on this number in this workspace, no block, program ON and a registered sender. A skip-traced mobile number is **never** consent.
* **Email:**
  * Permission basis is one of:
    * **SELLER_INITIATED:** they inquired.
    * **EXPLICIT_PERMISSION:** `allow_email` is true.
    * **WORKSPACE_CONFIRMED:** an admin recorded that emailing owners the workspace found is lawful for it. This is off by default.
  * Blocks: DNC, remove-all, test record, capacity hold, `allow_email` false, bad email, bounce/unsubscribe/suppressed, and a DNC record anywhere in the workspace with the same address.
  * **Always NOT OPERATIONAL today:** there is no seller-email sending path, and the answer says so.
* **Phone call by a person:** permitted when the seller contacted us or agreed to calls. Calling an owner who never contacted the workspace is **not** assumed to be allowed (it raises Do-Not-Call-registry questions).

## 7. Qualification behaviour

The default criteria are: selling interest and timeline required, timeline no longer than 6 months, Seller Intent at least 30. Each workspace can change them (admin).

* Unknown required fields give NEEDS_MORE_INFORMATION and are named. They are not a rejection.
* A long timeline, "not now" or "call me later" gives NURTURE.
* "No" or "listed with an agent" gives NOT_INTERESTED.
* STOP, wrong person, not the owner or already sold gives DISQUALIFIED.
* An estate, family or title complication, or a reader that could not understand the message, gives HUMAN_REVIEW.

## 8. NEEDS YOU behaviour

Each item has `why` ("WHY YOU ARE SEEING THIS"), `evidence` (the seller's own words, the scores) and a `link`. Priority order:

1. Seller requests an offer
2. Seller wants to talk
3. Appointment requested
4. Approval pending (offer / contract / assignment)
5. Seller proposed a price
6. Asked for a call back
7. Inquiries waiting on capacity
8. Ownership needs confirming
9. Human review
10. EvoSense handoffs (their own priority)
11. Identity reviews
12. Paid lookups outside policy

An item goes away when a person verifies the fact, decides the approval, or the seller's state changes. Routine machine work never appears. Neither do opted-out sellers or test records (unless asked for).

## 9. Command Center changes

The "This morning" panel on `/wholesale` shows:

* NEEDS YOU
* New qualified sellers (7 days)
* Seller replies (48 h)
* Appointments
* Awaiting contact data (EvoSense + deals)
* Nurture due / upcoming
* Provider and budget problems
* Spend (today / month)
* Pipeline moves

Every count links to its list.

## 10. Separation of claims

| | |
|---|---|
| **BUILT** | Everything in §2 |
| **LIVE** | Contactability, communication eligibility and policy tiers (6bcae93); the rest ships with the seller-intelligence commit once the full suite passes |
| **TESTED WITH SYNTHETIC DATA** | All 16 scenarios in the brief: `test_contactability`, `test_seller_intel`, `test_command_center`, `test_enrichment_policy`, `test_promotion_handoff`, `test_acquisition_scenarios` (with a guard that fails if any SMS or email reaches a provider), plus the existing EvoSense journeys |
| **REQUIRES PROVIDER** | Real contact enrichment, phone and email validation, a seller email sending path, voice automation |
| **REQUIRES MIKE** | (1) Turning on the seller SMS program when the campaign is approved. (2) Whether EVO may email owners it found (`cold_seller_email_confirmed`), which is a legal/compliance decision. (3) Choosing and paying for a skip-trace provider. (4) Enrichment policy and budgets per strategy (defaults are safe: standard, with the existing budgets). |

## 11. Next highest-leverage build

Connect ONE real contact-enrichment provider behind the existing adapter interface, with `manual_approval` as the starting policy. Everything downstream (confidence, contactability, economics, NEEDS YOU) already consumes its output. The second priority is a seller email sending path through `outbound_email_gate` (a new `send_source` plus a history row). That would make "Contactable — Email" reachable for sellers who inquired.
