# Data Depth: sold comps, ARV readiness, MAO gate, repairs, provider readiness

Built 2026-09-26. No provider has been purchased, activated or called. Every number the system shows comes from one of two places:

* evidence a person entered, labelled **MANUAL**
* a synthetic **SANDBOX** adapter, labelled **SYNTHETIC**, which only ever runs on test records

## The rules the software enforces

| Rule | Where |
|---|---|
| An ARV comes **only** from eligible closed sales. A tax/appraisal value, AVM or list price is shown beside it, labelled, and is never the ARV. | `app/services/arv_engine.py` (`other_valuations`, `is_arv: false`) |
| **INSUFFICIENT COMPARABLE SALES** is a valid, stored answer. An earlier comp-derived ARV that the evidence no longer supports is cleared. | `arv_engine.compute`, `wholesale_service.recalculate_analysis` |
| A comp counts only if it passes the workspace's versioned eligibility rules. Each exclusion carries a code and a sentence. | `app/services/comp_rules.py` (`comp_eligibility/v1`) |
| A typed comp is **MANUAL** forever. A person can verify it with an attestation, which makes it `human_verified`. It never becomes provider verified. | `POST /wholesale/comps/{id}/verify` |
| A manual comp without a source reference does not count. | rule `require_manual_reference` |
| ARV confidence is explained: every factor, with points. | `arv_engine._conf` |
| **No MAO** without an evidence-backed ARV at the workspace's minimum confidence **and** an accepted repair estimate. Repairs are never an assumed $0. | `app/services/mao_gate.py` |
| Repairs have a status (UNKNOWN / SELLER_REPORTED / MANUAL_ESTIMATE / INSPECTION_ESTIMATE / SYSTEM_ESTIMATE / VERIFIED) and an append-only history. | `app/services/wholesale_repairs.py`, table `wholesale_repair_estimates` |
| A person-entered ARV is never overwritten by comps. The comps' answer is shown beside it. | `recalculate_analysis` |
| A provider being configured is not "connected". The state is UNVERIFIED until a real call for that capability succeeds. Health is tracked per capability. | `evosense/capabilities.py`, `providers._cap_health` |
| A provider-returned number is a **candidate** with provenance (provider, reference, looked-up time, last seen, match evidence). A mobile number is never consent. | `evosense/contacts.py` (`trust_state`), `communication_eligibility.sms` |
| Enrichment checks value of information before price. It skips owners who are DO NOT CONTACT, already promoted, a firm no, or have too little evidence. | `evosense/enrichment.py` |
| Provider evaluation runs through the budget and ledger and never writes contact points. A real provider needs the owner's typed confirmation: "RUN PAID EVALUATION n". | `evosense/provider_eval.py` |

## Workspace settings (admin only, validated)

`PATCH /wholesale/settings`:

* `comp_rules`: distance, age, type, size / bed / bath / year tolerances, lot, credible price and $/sqft, excluded sale types, manual reference, minimum comps. Nothing is DFW-hardcoded.
* `mao_policy`:
  * `min_arv_confidence` (high / medium / low)
  * `allow_person_entered_arv`
  * `require_repairs`
  * `accepted_repair_statuses`

`GET /wholesale/settings` returns `comp_rules_effective` and `mao_policy_effective`.

## Endpoints added

**Wholesale:**

* `POST /wholesale/deals/{id}/comps`: adds evidence fields
* `PATCH /wholesale/comps/{id}`: records `excluded_by` and `exclusion_reason`
* `POST /wholesale/comps/{id}/verify`
* `POST /wholesale/deals/{id}/repairs`
* `GET /wholesale/deals/{id}/valuation`: the deal room includes `valuation`

**EvoSense:**

* `POST /wholesale/evosense/properties/{id}/comps`: a manual sold comp, which moves to the deal on promotion
* `GET /wholesale/evosense/capabilities`
* `POST`, `GET /wholesale/evosense/provider-evaluations[/{id}]`

## Cost of acquisition

`economics.acquisition_cost` reads the charged ledger only.

* **Buckets:** public_data, contact_enrichment, valuation_comps, other
* **Milestone costs:** cost to find, cost to contactability (`first_contactable_at`), cost to qualification (`qualified_at`)
* **Free lookups:** the number of free public lookups

A milestone that hasn't been reached shows no figure.

## Schema

Everything here is additive, applied by `auto_migrate` on deploy.

* **New tables:** `wholesale_repair_estimates`, `evosense_provider_evaluations`
* **`wholesale_comps`:** evidence and provenance columns added; `deal_id` nullable; new index `ix_wscomp_org_esprop`
* **`wholesale_deals`:** `repair_status`, `repair_low`/`repair_high`, `arv_version`, `arv_confidence`, `arv_detail`, `arv_calculated_at`, `mao_status`, `mao_detail`
* **`wholesale_settings`:** `comp_rules`, `mao_policy`
* **`wholesale_seller_profiles`:** `qualified_at`
* **`evosense_contact_points`:** `trust_state`, `provider_reference`, `looked_up_at`, `source_last_seen`, `match_evidence`, `raw_evidence`
* **`evosense_provider_configs`:** `capability_health`
* **`evosense_properties`:** `first_contactable_at`

## What needs a real provider (Mike's decision, see PROVIDER_SELECTION_PACKAGE.md)

* Provider-sourced sold comps. Until then, every comp is MANUAL.
* Real skip trace and line-type validation. Until then, contacts are SANDBOX on test records, or entered manually.
* A real provider evaluation. The harness is ready and needs the confirmation phrase.

## Tests

`tests/test_data_depth.py` (29 tests) covers:

* manual comps
* every eligibility exclusion
* tenant rules
* ARV success and insufficiency
* tax value never becoming ARV
* person-entered ARV kept
* confidence factors
* MAO gating on ARV, confidence and repairs
* repair history
* tenant isolation
* capability states
* the evaluation harness (synthetic, paid confirmation, tenant scope)
* candidate contacts are not consent
* cost buckets
* the EvoSense manual comps ARV
* value of information
