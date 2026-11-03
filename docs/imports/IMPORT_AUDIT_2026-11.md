# Import system audit - November 2026 (Phase 1)

Scope: every way a file or list of people/businesses gets into EvoSys, traced from route to table on `main` at `3869ad2`.
Read-only findings with file:line references. Stage A fixes are in the PR that adds this file.

## The domains that must stay apart

| Domain | Owner | Model / table | How records get in today |
|---|---|---|---|
| Tenant contacts and leads | the customer's workspace | `org_contacts`, `leads` | **Guided importer** `/intake/*` (canonical); old paths below |
| EvoSys Sales prospects (we sell EvoSys to them) | the brand sales org | `opportunities` (`Opportunity`, `app/routers/sales_router.py`) | **One at a time only**: `POST /sales/opportunities` (`sales_router.py:1068`). No bulk import exists. |
| Customer Acquisition (e.g. Divine Shine sourcing offices) | that customer's org | `leads` + `acquisition_opportunities` (`app/services/acquisition/engine.py`) | One at a time: `POST /acquisition-ops/{org_id}/prospects` → `add_prospect` (`engine.py:219`); Places discovery (`discovery.py`). No bulk import exists. |
| SCI funeral programs | the SCI program org | SCI staging and family records (`app/services/programs/importer.py`) | `/program/intake/*` (same engine, SCI profile) then `/program/leads/import/batches/{id}/sci-preview` and `/load` (`program_router.py:2860,2904`) |

The 15 eligible Facebook cleaning businesses are already in **EvoSys Sales**. They are 15 `opportunities` rows in brand sales org `365a60c0…`, created Nov 3 03:56–04:00 UTC, stage `prospect`, industry "Cleaning Services". Each `source` reads `Facebook ad | FB-CLEAN-xxx | <place>` (IDs 004, 007, 010, 013, 017, 018, 020, 025, 026, 028, 032, 033, 035, 038, 042). `Opportunity` has no separate external-ID column, so the ID only exists inside `source`.

## Every entry point

| # | Route | Writes | Org decided by | Reachable from the UI? | Notes |
|---|---|---|---|---|---|
| 1 | `/intake/*` (`intake_router.py`) | `org_contacts`; `leads` only for lead-creating classifications | `intake.context.resolve`: active workspace, or X-Org-Override for God; bound to the batch at creation and re-checked on every call (`context.py:53-81`, `intake_router.py:102-110`) | **Yes**, Import Center `/imports/new` | **Canonical.** Stage-only by default. Commit needs the org name typed. Per-row savepoints, atomic commit claim, resume and retry, rollback plan. No outreach on commit. |
| 2 | `/program/intake/*` + `/program/leads/import/...` | SCI staging and family records | Same resolver **plus** must equal the SCI program org (`sci_intake_router.py:53-58`) | Yes, SCI only | SCI profile (`fields.py:247-300`). CRM commit refuses SCI batches (`intake_router.py:645-650`). |
| 3 | `/import-batches/*` (`import_batch_router.py`) | `leads` | `user.organization_id` (home org, or God override); never the active workspace | URL only (`App.jsx:1521-1522`, no nav link) | **Old staging path.** Before Stage A: tier `pre_need` for every org (`import_commit_service.py:183`), email-only rows routed to SMS, `new_inquiry` track missing, row edits/ready/archive/delete worked on guided batches. |
| 4 | `/leads/upload/preview`, `/leads/upload/confirm` (`leads_import_router.py`) | `leads` (confirm stages then commits via #3) | `current_user.organization_id`, while GET/DELETE `/leads/import-batches` use the active workspace (`:357-411`), so the same user can upload to one org and list or delete in another | **No.** The panel in `Leads.jsx` never opens (`showImport` is never set true, `:139,1005`); `/leads?import=1` redirects to `/imports/new` (`:428`) | Still used: GET `/leads/import-batches` as a filter source (`Leads.jsx:346`, `CampaignBuilder.jsx:202`, `LeadsWorkspace.jsx:223`). Stage D. |
| 5 | `/google-contacts/import` | a guided `/intake` batch (no leads) | intake resolver | No (inside the dead panel) | Moved onto the guided engine already, but was mounted **without** the `imports` gate `/intake` has. **Fixed in Stage A.** |
| 6 | `/scraper/import` (`lead_scraper_router.py:439`) | `leads` directly, no staging | request body org id or X-Org-Override (God only) | God "Classic Scraper" (`App.jsx:1740`) | Bypasses the guided importer. Assigns leads to the God user, drops the address into notes, dedupes by phone only. Stage D. `/scraper/stage` already routes through the guided engine (`lead_routing.py:457-470`). |
| 7 | `POST /crm/inbound/{org_id}` (`crm_router.py:428`) | `leads` | org id in the URL plus inbound credential | API only | No staging. Tier taken from the payload. Stage D review. |
| - | Wholesale buyer/seller lists, EvoSense lists, roofing | their own domain tables | their own routers | yes | Separate domain importers, out of scope; not tenant leads. |

Dead code (no live callers): `import_service.import_leads_from_excel(dry_run=False)`, the only code that auto-starts cadences (`import_service.py:967-981`); `import_leads_from_rows` (`:1042`); `google_contacts_service.pull_google_contacts`; `import_staging_service._parse_google_contacts_rows`; repo-root `leads_router.py`. Only tests and scripts reference them. They are removed in Stage D after a last consumer check.

## Funeral behaviour: intended vs accidental

* **Intended (keep):** the SCI profile and SCI load (`fields.py:247-300`, `programs/importer.py`). Funeral tiers chosen by `industry_templates.org_lead_tiers(org)` only for funeral orgs at promote time (`intake_router.py:1209`).
* **Accidental (Stage A fixed):** old commit defaulted every lead to `pre_need` with track `pre_need_lock_price`, whatever the org's industry.
* **Accidental (Stage B):** `fields.py:199-207` `_VERTICAL_HINTS` (interment, plot, lot, section, plan…) applies to every org. The hints are low-confidence only, but "lot", "section" and "plan" mis-tag columns in other industries. The old `import_service` NSMG/Restland address filters and Pre-Need/At-Need routing run in dead code only.

## Already works / needs repair / missing

| | Item | Evidence |
|---|---|---|
| ✅ works | Guided importer binds a batch to one server-resolved org and re-checks on every step; God needs a selected customer | `context.py:53-81`, `intake_router.py:102-110`, `test_universal_intake_api.py` |
| ✅ works | Source system + source record ID kept and matched (incl. alternate IDs) | `matching.py:74-124,268-277`, `commit.py:256` |
| ✅ works | Phone to E.164 without area-code guessing; unknown state kept and flagged, never defaulted | `normalize.py:98-191` |
| ✅ works | Import never grants SMS consent; more-restrictive status wins on update; no cadence/send on commit or promote | `commit.py:327,459`, `promote.py:17-23` |
| ✅ works | Stage-only default, typed org-name confirmation, atomic commit claim, per-row savepoints, retry/resume, rollback with plan | `intake_router.py:692-738`, `commit.py:508-706`, `rollback.py` |
| ✅ works | SCI kept out of the CRM and the CRM out of SCI | `intake_router.py:645-650`, `engine.py:67-69`, `program_router.py:2815-2823` |
| 🔧 repaired (A) | Old commit invented `pre_need`; routed email-only to SMS; missing `new_inquiry` track | `import_commit_service.py` |
| 🔧 repaired (A) | Old batch routes could edit/advance/archive/delete guided and SCI batches | `import_batch_router.py` `_refuse_guided` |
| 🔧 repaired (A) | `/google-contacts/import` reached the importer without the `imports` gate | `main.py` |
| 🔧 repaired (A) | Acquisition hand entry stored state "TX" when none was given | `acquisition/engine.py` |
| 🔧 repaired (A) | Import error report cells could run as spreadsheet formulas | `app/utils/csv_safe.py`, `wholesale_buyer_network.error_report_csv` |
| 🔧 needs repair (B) | Destination comes from whichever customer/workspace was last stored in the browser (`client.js:858-890`); only the typed name confirms it | needs an explicit destination step shown throughout |
| 🔧 needs repair (B) | A number in a column *named* "mobile" is treated as a known mobile line | `eligibility.py:38-51`. It is still never SMS-ready without explicit consent, but the label should read "listed as mobile by the source", not verified |
| 🔧 needs repair (B) | Funeral words in generic column hints | `fields.py:199-207` |
| ➕ missing (B) | An explicit destination on every batch, with adapters for **EvoSys Sales** and **customer Acquisition** (today only tenant contacts/leads and SCI) | grep: no Opportunity / add_prospect in `app/services/intake` |
| ➕ missing (B) | Industry/workflow profile beyond `sci` | `fields.py` has only `SCI_PROFILE` |
| ➕ missing (B) | Geographic eligibility (allowed/excluded states and countries) | no implementation in `app/` |
| ➕ missing (B) | Sales `Opportunity` source-record ID, needed to match the 15 FB-CLEAN rows without parsing text | `Opportunity` columns |
| ➕ missing (C) | Downloadable review/error reports for guided imports; saved mapping per source; request idempotency key | JSON only today |
| ➕ missing (D) | Retire #3, #4, #6, #7 or route them through the guided engine; delete dead code; update docs | above |

**A rewrite is not needed.** The guided importer already does the parsing, normalising, matching, staging, commit, rollback and audit correctly for tenant records. What it lacks is an explicit destination with commit adapters for Sales and Acquisition, profiles, and geography. Those are added to it (Stage B), not built beside it.

## Stage plan

* **A (this PR):** the confirmed defects above. No schema change.
* **B:** a `destination` and `profile` on every guided batch, validated server-side against what the actor may write to. Commit adapters: tenant contacts/leads (existing), EvoSys Sales (`Opportunity`), customer Acquisition (`add_prospect` rules), SCI (existing). Profiles: general, SCI, cleaning-services prospect. Geographic rules (contiguous US minus California for the cleaning list). Additive nullable `opportunities.source_record_id`, filled for the 15 existing rows from their `source` text so they match and are never duplicated.
* **C:** guided UI steps (destination → upload → map → review counts → commit selected → results), shown destination throughout, downloadable spreadsheet-safe review and error reports, intro drafts kept as drafts.
* **D:** legacy retirement (410 with migration path, or routed through the engine), dead-code removal, docs.
