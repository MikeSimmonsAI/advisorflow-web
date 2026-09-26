# Universal Intake convergence — making `feature/universal-intake` the canonical intake layer

Written 2026-09-26 after the Wholesale Intake → Identity → Lead reconciliation
(`handoff/WHOLESALE_INTAKE_RECONCILIATION.md`). Purpose: Wholesale must NOT
grow a parallel contact/intake system. This is the remaining work, in order,
to put every Wholesale entry point onto Universal Intake once that branch lands.

## Where the two stand

**`origin/feature/universal-intake`** (a876548, 2026-09-24, Mike): 1 commit on
top of an older main; main is 15 commits ahead. Not merged. It adds:

* `org_contacts` (+ `org_contact_source_ids`, `import_batch_files`,
  `import_record_versions`, `intake_classifications`) and
  `leads.org_contact_id` / `leads.import_batch_id`;
* `app/services/intake/*`: normalize, matching (source id → email → phone
  with identity agreement → company+address → name+company; EXACT acts,
  POSSIBLE goes to review, never cross-org), classification (contact vs lead,
  `creates_lead`), eligibility (SMS/email readiness — never grants consent),
  commit (fill-blanks, `manually_edited_fields` protected), rollback (from
  record versions), worker runner;
* `/imports` UI (wizard + ledger); legacy `/leads/upload/*` kept.

**Wholesale today (main, 5012ee5+)**: `/sell` →
`wholesale_seller_intake.submit` → `wholesale_service.create_property` +
`attach_seller` (Lead + `wholesale_seller_profiles`). Its own person match
(phone, then email, org-scoped) and property match (normalized address).
EvoSense keeps `converged_contact_ref` (nullable, unused) on persons/contact
points, reserved for `org_contacts.id`.

## The rule for the converged design

| Concern | Owner after convergence |
|---|---|
| Who a PERSON is (dedupe, merge, source ids, contact record, field edits, rollback) | **Universal Intake** (`org_contacts`, `intake.matching`, `intake.commit`) |
| Whether a person is being WORKED (Lead) | Universal Intake classification (`creates_lead`) — Wholesale asks for a lead, never builds one |
| Consent / DNC / suppression evidence | Platform (`sms_consent_records`, suppression) — unchanged; intake never grants consent |
| Which PROPERTY this is (address/APN/unit identity) | Wholesale / EvoSense (`evosense.identity`) — Universal Intake has no parcel concept |
| The person's ROLE on a property (seller of record, additional contact, heir, agent) | Wholesale (`wholesale_seller_profiles`, EvoSense ownerships) |
| Deal lifecycle, re-engagement, qualification, notifications, AI context | Wholesale |

## Work items (in order)

1. **Merge `feature/universal-intake` into main.** Rebase onto main (15 commits
   behind), resolve `auto_migrate.py`, `models.py`, `registry.py`, `main.py`,
   `Leads.jsx`, `App.jsx`, `Layout.jsx`. Full suite + its own
   `test_universal_intake*.py`. Deploy with no behaviour change to Wholesale.
2. **A single-record intake entry point.** Universal Intake is batch-shaped
   (create_batch → mapping → analysis → commit). Web forms need a synchronous
   `intake.capture_one(db, org_id, record, source=..., classification=...)`
   that runs the SAME normalize → match → classify → commit path for one row
   and returns `(org_contact, lead_or_none, match_type)`. EXACT → reuse;
   POSSIBLE → create a separate contact flagged for review (never merged by a
   form); record an `import_record_versions` row so rollback works.
   `public_capture` (general site forms) should move onto the same function.
3. **`/sell` onto it.** In `wholesale_seller_intake.submit`, replace
   `_existing_lead` + `attach_seller`'s Lead creation with `capture_one(...,
   classification="new_inquiry", creates_lead=True)`; keep the property match,
   `reopen_deal`, `set_primary`, notifications, consent recording exactly as
   they are. `attach_seller` takes the returned lead (`lead_id`) - the code path
   that already exists for "this owner is already in the system".
   Remove `_existing_lead` afterwards (one matcher, not two).
4. **Operator "attach owner" and Wholesale CSV import onto it.**
   `POST /wholesale/properties/{id}/seller` without `lead_id` → `capture_one`.
   The fixed-column property CSV becomes an intake target type ("properties"):
   the person half of each row goes through intake matching; the property half
   stays EvoSense/Wholesale ingest. Intake batch rollback must also retract the
   Wholesale/EvoSense observations carrying that batch id (signals retracted,
   never deleted).
5. **EvoSense owners.** Populate `converged_contact_ref` with `org_contacts.id`
   when an EvoSense person/contact point is promoted or contacted; map
   contact-point `(provider, source_reference)` to `org_contact_source_ids`.
   EvoSense keeps the owner-of-record relationship; it never becomes a second
   contact database.
6. **Seller edits write through.** `PATCH /wholesale/sellers/{id}` currently
   edits the Lead's name/phone/email. After convergence it edits the
   `org_contact` (marking `manually_edited_fields`, so a later import never
   overwrites an operator's correction) and the Lead mirrors it. The phone
   dedupe (409) and "consent does not travel to a new number" rules stay.
7. **Capacity semantics.** Today `/sell` REFUSES (503) when the plan's lead
   limit is full; general forms HOLD. Under intake the person is always kept
   as an `org_contact` and only the Lead is held - decide and document (Mike:
   product/billing decision).
8. **Retire parallel code.** After 3-6: delete `wholesale_seller_intake._existing_lead`
   and `_street_key`, the Lead-creation block in `attach_seller`, and any
   direct `Lead(...)` construction in Wholesale; add a test that fails if
   Wholesale constructs a `Lead` directly.

## Acceptance tests to write with the convergence

* `/sell` twice with the same email → one `org_contact`, one Lead, source ids
  recorded; with a different surname on the same phone → POSSIBLE, review,
  not merged.
* Operator edits a seller's name → later CSV import of the old spelling does
  not overwrite it (`manually_edited_fields`).
* Intake rollback of a batch that created a Wholesale seller → contact
  archived / lead kept if a deal exists; Wholesale deal untouched; EvoSense
  observations from that batch retracted.
* Tenant isolation: matching loads only the org's own contacts (both sides).
* Consent: nothing in intake sets `sms_consent`; a `/sell` consent still
  writes `sms_consent_records` with the server timestamp and verbatim text.

## Not in scope of convergence

Wholesale deal lifecycle, re-engagement, seller-aware AI, booking semantics
and notifications stay in Wholesale - they are about the property and the deal,
not about who the person is.


---

## Status update — 2026-09-26 (evening)

| Step | Status |
|---|---|
| 1. Merge `feature/universal-intake` into main | **DONE** (merge commit on `feat/universal-intake-converge`; only conflict was `app/models/registry.py`, both sides kept) |
| 2. Single-record intake entry point | **DONE** — `app/services/intake/capture.py` `capture_one()`: a one-row batch through the unchanged engine (create_batch → run_analysis → run_commit). |
| 3. `/sell` onto it | **DONE** — `wholesale_seller_intake.submit` calls `capture_one`; `_existing_lead` deleted. Wholesale keeps property identity, role on the property, deal lifecycle, notifications. |
| 4. Operator attach + Wholesale CSV | **DONE** — operator attach (`wholesale_service.add_owner`) and the property CSV (`attach_imported_owners` via `intake.capture.capture_many`), see below |
| 5. EvoSense `converged_contact_ref` | open |
| 6. Seller edits write through to `org_contacts` | **DONE** (`intake/contacts.apply_manual_edit`, see below) |
| 7. Capacity semantics | **DECIDED (Mike, 2026-09-26) and DONE** — see below |
| 8. Retire parallel code | partly (`_existing_lead` gone; `attach_seller`'s Lead creation remains for step 4) |

### Capacity decision (Mike, 2026-09-26) — platform-wide
Never discard a valid inbound inquiry because the workspace is at its active
Lead limit. The person/contact/property and provenance are preserved through
the intake layer; the Lead is created in the platform's existing HELD state
(`lead_capacity`: kept, not counted toward the plan, blocked from SMS / email /
voice / cadence / AI until capacity exists; released oldest-first by
`lead_capacity.release_available`, which starts nothing on release). The
workspace is notified that an inquiry is waiting because capacity is full.
Implemented generically: `intake.commit.activate_lead(hold_when_full=True)`
for EXTERNAL arrivals via `run_commit(..., hold_when_full=True)` /
`capture_one(external=True)`; bulk imports by a present user keep the
"contact kept, no lead" rule. Any other external connector should call
`capture_one` rather than creating a Lead.

### Bugs found in the intake branch while converging (fixed)
* Leads created by intake stored the phone as E.164 (`+12145550123`); every
  platform reader (inbound SMS sender lookup, suppression, dedupe) compares the
  platform format (`12145550123`). An intake-created person's reply would not
  have found their record. `activate_lead` / `_update_matched_lead` now write
  the platform format; the org contact keeps E.164.
* Matching against existing Leads by phone compared E.164 with the platform
  format and never matched; `lead_identity` and `load_existing` now normalize.
* `intake.audit.record` with no signed-in actor (a system capture) would have
  failed the flush on `audit_log_entries.actor_user_id NOT NULL`; system
  captures are recorded in the capturing module's event trail and
  `import_record_versions` instead.

### Production rollout, 2026-09-26 — incident and schema findings
* **62eff53 broke `/sell` in production** (visitors saw "couldn't send"). It was
  reverted within minutes (5e6be82), and `/sell` was verified working again.
  Outage window: about 14:30–14:50 CT. Any real seller who submitted in that
  window got an error and was not captured; ask them to resubmit if anyone
  reports it.
* **Re-landed as 7de601f with a fail-safe.** If `capture_one` raises for any
  reason, the submission rolls back the intake attempt, logs a
  `seller_inquiry.intake_error` event with a sanitized error, and falls back to
  the legacy lead match plus `attach_seller(external_arrival=True)`. That path
  still includes the capacity hold. A seller is never lost because intake
  failed.
* **The fail-safe exposed two schema drifts in production's `import_batches`:**
  1. Model columns missing (`created_by_id`, …), because the table predates the
     Lead Import Intelligence model. Fixed by fcc335a, which adds every
     `import_batches` / `import_staged_rows` model column to `COLUMNS_TO_ADD`.
  2. A legacy `kind` column left NOT NULL by the historical source-records model
     (f7a6b50). No current model writes it. Fixed by 9954a4e, which adds it to
     `NULLABILITY_TO_RELAX`. Both were reproduced on local Postgres using the
     legacy table shape.
  The same drift means **the legacy Import Center could not have created a
  batch on production either** until these fixes. That was pre-existing, not
  caused by convergence.
* **Keep the fail-safe** until several real submissions show `intake_batch_id`
  and no `intake_error` event. After that, reduce it to logging plus the
  capacity-held path. Don't delete it outright: a seller inquiry must never
  depend on the import pipeline being healthy.
* **Verified in production after 9954a4e:** a ZZTEST `/sell` submission
  (600 Smoketest Street, SI-CF666A59) recorded `intake_batch_id`,
  `org_contact_id` and `intake_match=new`, with `intake_error=null` and no
  `intake_error` event. The Lead was stored as `12145550166` (platform format)
  with source `wholesale_seller_inquiry`, and the one-row batch was `committed`.
  `/intake/batches` returns 200 for EVO. Every ZZTEST property (300, 400, 500
  and 600 Smoketest Street) is test-flagged.

### Steps 6 and 4a — 2026-09-26 (night)
* **Seller edits write through (step 6, ab9374d).** `PATCH /wholesale/sellers/{id}`
  sends name, email and phone to `intake.contacts.apply_manual_edit`. That
  function writes the org contact, adds each field the operator sent to
  `manually_edited_fields`, and audits the change as `intake.contact_edited`
  (target `org_contact`). The Lead then mirrors the change in its own format.
  Import blank-fill of a matched Lead (`commit._update_matched_lead`) now skips
  protected fields too, so a later import or `/sell` capture cannot refill a
  field the operator deliberately cleared. A Lead with no org contact (created
  before intake) is still edited directly, and no contact is invented for it.
* **Bug fixed in the same change:** an edited seller phone was being stored on
  the Lead as E.164, which the inbound SMS lookup can't match. It now stores the
  platform format. A production sweep of EVO's leads found one E.164 phone: a
  test-flagged ZZTEST seller I created earlier. No real seller was affected.
* **Operator "add owner" (step 4a).** `POST /wholesale/properties/{id}/seller`
  without `lead_id` now calls `wholesale_service.add_owner`, which works like
  this:
  - The plan-limit check runs first. At the limit the request is refused (402)
    and nothing is written.
  - `capture_one(external=False, explicit=True, user=...)` runs as the signed-in
    operator. The batch and audit record who did it, the list is "Wholesale
    owners", and the classification is `cold_prospect`.
  - An EXACT match reuses that contact and Lead. The same owner on a second
    property gives one Lead with two seller profiles.
  - A POSSIBLE match gets its own contact and Lead, marked for review.
  - A newly created Lead gets Wholesale's source category, `cold_lead`, the
    property's assignee and the property's test flag.
  - If intake errors, the owner is attached through the direct path and a
    `seller.intake_error` event is logged without personal data. If intake
    keeps a contact but makes no Lead, the direct path creates the Lead and
    links it to that contact.
* `capture_one` has two new parameters: `explicit` (a deliberate request gets a
  Lead even on a POSSIBLE match) and `user` (a real actor context).

### Step 4b — the Wholesale property CSV (2026-09-26, night)
* `POST /wholesale/properties/import` now handles each file in two halves:
  1. The PROPERTY half is written and committed first (Wholesale/EvoSense). It
     is kept whatever happens to the owners.
  2. The PERSON half is every row with an owner phone or email. All of them go
     to `wholesale_service.attach_imported_owners`, which calls
     `intake.capture.capture_many` to make ONE intake batch for the file.
* That gives one matcher across the whole file and the workspace. The same
  owner on three rows is one contact, one Lead and three seller profiles. An
  owner who is already in the workspace (for example from `/sell`) is reused.
* A POSSIBLE match gets its own Lead, marked for review. An existing
  do-not-contact person stays on their record, and every block still applies.
* Past the plan's lead allowance, the contact is kept with no Lead, and the row
  is reported in `capacity_blocked`. The property stays, in ENRICHMENT NEEDED.
* The response now includes `owners_attached` and `intake_batch_id`.
* **Rollback:** intake's rollback keeps any Lead that has downstream rows, and
  a seller profile counts as one. Rolling back the owner batch therefore never
  removes a seller's Lead and never touches a deal (this is tested). The CSV
  path writes no EvoSense observations, so there is nothing to retract for it.
  If EvoSense ingest ever starts carrying `intake_batch_id`, its retraction
  belongs in step 5.
* **Fail-safe:** if intake errors, every owner in the file goes through the
  direct path exactly as before. An `import.intake_error` event is logged
  without personal data.
* **Still open:** step 5 (EvoSense `converged_contact_ref`). Also step 8:
  `attach_seller` still builds a Lead directly, but only as the fail-safe path
  for all three entry points. Remove it once production has shown clean
  captures for a while.
