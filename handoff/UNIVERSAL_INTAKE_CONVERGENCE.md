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
