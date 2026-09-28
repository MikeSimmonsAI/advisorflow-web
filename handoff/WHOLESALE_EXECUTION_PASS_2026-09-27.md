# EvoSys / Wholesale execution pass — 2026-09-27

Scope: the "next execution pass" brief, P1–P8, done in priority order. The
seller site and SMS consent work (`78759ad`) is complete and was not reopened.

## Pre-flight findings

* **main** = `78759ad` at the start; the working tree was clean.
* **`feature/universal-intake` / `a876548` is already merged into main.** The
  branch has no commits that main lacks.
* **Other worktrees.** Nothing in them was touched.
  * Branches with commits not on main: `advisorflow-billing` (10) and
    `advisorflow-mobile-preview` (8).
  * Worktrees with local-only edits: `advisorflow-godpricing` (207 files),
    `af-godlight` (46), `advisorflow-web` (40), plus small ones.
* **Overlap map**

  | Area | Owner |
  |---|---|
  | Contacts / master person | Universal Intake (`org_contacts`, `intake.*`) |
  | Property identity | EvoSense (`evosense.identity`) |
  | Seller role | `wholesale_seller_profiles` |
  | Buyers | `wholesale_buyers` + buy boxes + outreach |
  | Audit | `wholesale_events` + the platform audit log |
  | Permissions | `capabilities.require_feature_capability` |

  There is no human task table: `ai_work_items` belongs to AI employees, and
  `/workqueue` is a view over leads.

## P1 — Property-Truth v3 (review point; nothing written)

* v3 (`derive/v3 + property_opportunity/v3`) was applied in production on
  2026-09-26 (run `901c790c`): 40 properties, 0 mismatches.
* **Verification dry run `97263bf4`, 2026-09-27.** Every figure below is live
  production compared with a fresh v3 re-derivation:

  | Measure | Result |
  |---|---|
  | Scores changed | 0 |
  | Threshold crossings | 0 |
  | Signals added / removed / changed | 0 / 0 / 0 |
  | Appraisal values changed | 0 |
  | ARV changed | 0 (all "Insufficient comparable sales") |
  | MAO changed | 0 (all "NOT CALCULATED — requires a verified ARV and repair assumptions") |
  | Owner-type or flag changes | 0 |
  | Market estimates presented as fact | 0 |
  | Observations re-derived from raw | 50 of 50 |

  Owner review flags as they stand: NAME_TRUNCATED 3, ET AL 1, ESTATE 1,
  LIFE_ESTATE 1, INSTITUTIONAL 1.
* **Provider-state gap: TAD is BLOCKED platform-wide.** TAD refused the
  platform's requests on 2026-09-26. As a result, the 20 Tarrant properties
  have no appraisal value and no TAD owner record (tax roll only). The
  registry reports this truthfully.
* **Data quality.**
  * 7 properties have absentee status UNKNOWN because the owner mails to a
    PO box. That is correct handling, not a guess.
  * Confidence: 18 medium, 21 low, 1 insufficient.
  * 39 of 40 properties are low-opportunity.

## P2 — Universal Intake closeout

* **Google Contacts** was the one live path that still wrote Leads directly.
  It is now a SOURCE of the canonical importer. `POST
  /intake/batches/google-contacts` and the legacy `/google-contacts/import`
  both stage one intake batch, which then goes through map → analyze → review
  → commit → rollback like any file.
  * All pages of contacts are read (up to 25,000).
  * Google's phone label is kept as source data and never treated as a
    carrier line type.
  * Import permission is required.
  * The Import Center has a "From Google Contacts" button.
* **Already sound, no change:**
  * Retry and recovery: the heartbeat marks an `interrupted` batch, and a
    re-run is safe.
  * Uploads up to 50 MB, run in the background.
  * Org isolation, audit, rollback.
  * Line type is shown as `unverified`, which is honest: the carrier lookup
    is evaluation-only.
* **Production check.** Since the 09-26 rollout fix, every /sell inquiry has
  been captured cleanly through intake. The two `intake_error` events on
  record are from the rollout itself.
* **Still open: convergence step 8.** This is the removal of the fail-safe
  Lead creation in `attach_seller`. Do it after a longer run of clean
  production captures.
* **Noted, not changed:** the legacy `/leads/upload/*` API still exists. The
  UI no longer reaches it.

## P3 — Cash buyers: who SAYS they buy vs who BUYS

* **Already existed:** buy boxes, match reasons, and a measured track record
  (sheets sent, replies, offers, times chosen, deals closed, average days to
  close). That record is kept apart from the stated past-deal count.
* **Added: `wholesale_matching.buyer_standing`.** It assigns one of six
  standings:

  | Standing | Meaning |
  |---|---|
  | proven | closed a deal here |
  | active | made an offer or was chosen |
  | responsive | replied |
  | unresponsive | 3+ sheets sent, no reply |
  | unproven | 1–2 sheets sent, no reply |
  | new | nothing sent yet |

  * Standing is measured on this organization only. A claimed past-deal
    count is reported but never counted.
  * It appears in the buyer list and the deal's match table, **beside** the
    buy-box fit score and never inside it. A test proves the score does not
    move.
* **Next:** link `wholesale_buyers` to `org_contacts` through intake (a
  "partner/buyer" capture, no Lead). The buyer would then share identity with
  the contact database.

## P4 — Distress sourcing (hooks, no scraper)

* A distress list is a **LIST KIND of the one EvoSense import**, not a new
  importer. The kinds are:
  * `tax_delinquent`, `tax_sale`, `tax_suit`
  * `pre_foreclosure`, `foreclosure_filing`
  * `code_enforcement`, `municipal_violation`
  * `lien`, `probate`, `driving_for_dollars`
* Each list flows through source → ingest → identity/dedupe → signals →
  scoring → eligibility → promotion into the Wholesale workflow.
* Each row carries its own evidence (`signal_date`, `case_number`,
  `case_status`, `amount`). It is stored with the signal, at confidence 60,
  and labelled "as stated by an imported list; not verified against the
  primary record".
* The list kind is part of the row identity. The same house on a tax list and
  a code list is one property carrying two pieces of evidence, and
  re-importing is idempotent.
* API:
  * `GET /wholesale/evosense/import/list-kinds`
  * `POST /wholesale/evosense/import` with `list_kind`, `list_source`
* **Connector approach for later sources.** A county or vendor feed that
  exists as a file or API becomes an `AcquisitionProvider`, the pattern
  already used by `dcad`, `tarrant_tax_roll` and the code sources. Everything
  else arrives as a list kind. No county-by-county code paths.
* **UI:** Providers & Controls → Distress Lists tab (upload, list kind, where
  the list came from, per-row results and rejects).

## P5 — Funding / capital partners (not a lending platform)

* **Partner record.** A partner is an `org_contacts` row captured through
  Universal Intake with the built-in `partner` classification (record class
  PARTNER, never a Lead). The lending facts sit in a 1:1 side table,
  `wholesale_funding_partners`. It holds:
  * products (DSCR, fix & flip, hard money, bridge, ground-up,
    land/development, private capital, transactional)
  * states and markets, property types
  * min/max loan, LTV, LTC, minimum credit, close time
  * contact person, referral relationship
  * verified (with who and when)
* **Submissions.** `wholesale_funding_submissions` records one deal sent to
  one partner: submitted → approved / declined / funded, with response time.
  A deal cannot be marked "funded" without an approval first.
* **Options for a deal.** Partners are ranked on their STATED criteria, with
  every reason shown. An unstated criterion counts neither for nor against; a
  stated mismatch excludes, with the reason.
* **Disclaimer.** Every response and screen says EvoSys does not lend,
  approve, price or guarantee financing.
* **UI:** a "Funding" tab on the deal page.
* **Next:** a deal-packet export for a submission, and a partners page.

## P6 — Automation-first / VA exception layer

* **New `wholesale_work_exceptions` table.** It is justified: the platform
  has no human-assignable task table. The 13 kinds from the brief are
  supported.
* **Outcomes:** Complete / Unable to Verify / Needs More Information /
  Escalate. Escalation goes back to the owner, and every outcome except
  Complete needs a note.
* **Least privilege.** The new `exception_queue_work` capability lets a
  person see and work ONLY the exceptions assigned to them, with just enough
  of the subject to do the job. Org admins qualify by role and can see,
  assign, sweep and take escalations. Everything is a wholesale event.
* **The sweep** is admin-only and idempotent. It raises:
  * properties with no owner → verify owner
  * buyers whose claimed proof of funds is unchecked → verify proof of funds
  * buyers with no buy box → verify buyer criteria
  * Wholesale sellers whose reply was classified CALLBACK and not reviewed →
    seller callback requested (priority 10)
  * EvoSense owners of record flagged estate, life estate, ET AL,
    institutional or truncated name → title / ownership anomaly
* **Page:** `/wholesale/exceptions`, with a nav entry "Exceptions".
* **Next:**
  * more feeders: AI exceptions from the conversation engine, missing
    disposition data;
  * show a count of assigned exceptions on `/workqueue`.

## P7 — Mobile companion readiness (inspection only)

* **Blocker: file storage is not configured in production.**
  `/wholesale/files/capability` reports `backend: none` and uploads disabled.
  Photos and documents need `MEDIA_STORAGE_BACKEND=s3` plus credentials, and
  only Mike can provide those.
* **Push:** `device_push_tokens` exists, using Expo push. APNs/FCM direct is
  not wired.
* **Worktree:** `advisorflow-mobile-preview` holds 8 commits not on main.
* Deep links and permission-aware screens: not assessed in depth this pass.

## P8 — Platform cleanup (not started)

* Insurance and funeral assumptions still appear in several services:
  * `tier_config_service`, `industry_templates`, `ai_conversation_service`
  * `reply_classification_service`, `qualification`, `voice_service`
  * `pipeline_service`, and others
* `industry_templates` is the natural home for a vertical-aware catalog.
* The leads table density work was not started.

## Tests

* **New test files:**
  * `test_google_contacts_intake.py` (7)
  * `test_wholesale_buyer_standing.py` (13)
  * `test_evosense_distress_lists.py` (6)
  * `test_wholesale_funding.py` (7)
  * `test_wholesale_exceptions.py` (5)
* **Cross-tenant attack list** (`test_wholesale_cross_tenant.py`) now covers
  every new ID-bearing route.
* **Results** are recorded in the final report for this pass.

---

## Continuation (evening, 2026-09-27) — P3 / P5 / P6 finished

### P3 — buyers in the shared contact layer
- `wholesale_buyers.org_contact_id` (FK org_contacts, SET NULL) + index `ix_wsbuyer_org_contact` (auto_migrate).
- `app/services/wholesale_buyer_contacts.py`: every real buyer is captured through Universal Intake with the
  PARTNER classification (record class PARTNER, **no Lead**). Test buyers stay out. A buyer with no email/phone
  is skipped. Intake's matcher reuses an existing contact instead of duplicating. An intake failure never
  fails the buyer write (event `buyer.contact_link_error`; picked up by the next link run).
- Create / edit / import link automatically; identity edits write through to the contact as manual edits
  (protected from later imports). Buy boxes, standing, matching, outreach, POF, claims, history stay on the
  buyer row, untouched.
- Migration for existing buyers: `POST /wholesale/buyers/link-contacts?dry_run=true|false` (org admin only,
  idempotent, tenant-scoped). Buyers page shows "In contacts" and an admin "Link N to contacts" button.

### P5 — Funding Partners page + deal packet
- `/wholesale/funding` standalone page (nav: Funding Partners): list/add/edit/verify/deactivate. Criteria are
  labelled "Stated, unverified" until someone verifies. Track record is measured, not claimed.
- `GET /wholesale/funding/deals/{id}/packet?format=json|html[&partner_id][&submission_id]`
  (`wholesale_funding_packet.py`). Only real system data. ARV only if on file (with source/confidence);
  MAO only if the MAO gate says CALCULATED, else "NOT CALCULATED" + reasons; repairs with status; comps only
  those included, with origin; title fields; attachments listed if any, otherwise a note (storage not
  configured never blocks). No seller personal data, no assignment fee. Download only — logged as
  `funding.packet_generated` "(not sent)". Nothing is emailed or sent anywhere.

### P6 — automatic exceptions + My Work
- New sweep rules: `ai_exception` (seller reply the AI routed to a person — `needs_human`; EvoSense handoff
  open > 48h), `missing_disposition_data` (deals in disposition / buyer identified / assignment pending:
  one item per deal listing exactly what is missing, kept current).
- Anti-spam: open items never duplicated; a closed item is not reopened for 30 days; automatic pass skips
  sandbox records, caps 25 new per rule per pass, skips properties whose deal is closed/dead.
- Hourly loop `wholesale_exception_sweep_loop` (JobName.WHOLESALE_EXCEPTIONS, backend-owned, job ledger,
  off the event loop, per-org transaction). Writes only exception rows + audit events.
- `GET /wholesale/exceptions/summary` → My Work card: "assigned to me" (+ unassigned/escalated for admins).
  `/workqueue/today` shape unchanged (a test pins it).
