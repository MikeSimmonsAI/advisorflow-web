# Universal Intake (Import Center)

One intake engine for every organization, vertical and source. Every connector
(CSV, Excel, HubSpot export, Google Contacts, Salesforce, GoHighLevel, API,
carrier feeds, scrapers, forms) hands the engine **headers + rows**; the engine
does all cleaning, matching and classification. No connector has its own.

```
SOURCE CONNECTOR -> create_batch -> save_mapping -> run_analysis -> (decision) -> run_commit
                    import_batches   mapping_json    import_staged_rows            org_contacts
                    import_batch_files                                             leads (opportunities only)
                                                                                   import_record_versions
```

## Three separate status concepts

| Concept | Where | Values |
|---|---|---|
| Import status | `import_staged_rows.intake_status` | ready, needs_review, duplicate, existing_match, blocked, needs_enrichment, invalid, failed, approved, imported, skipped |
| CRM classification | `record_class` + `classification` | contact, lead, customer, previous_customer, renewal, partner, vendor, employee, other / new_inquiry, cold_prospect, win_back, ... |
| Outreach status | `sms_status`, `email_status` | per channel; see below |

## Contact vs Lead

* Every committed row becomes an **OrgContact** (`org_contacts`). Nothing that
  reads `leads` sees these, so lead counts, pipelines, forecasts, SMS-ready and
  assignment queues are unaffected.
* A row ALSO becomes a **Lead** only when its classification has
  `creates_lead=True`, it has a usable phone or email, it is not blocked and it
  is not an unresolved review row. Leads are created with `tier=None` (they
  appear in Needs Review until tiered), `sms_consent=False`, and link back via
  `leads.org_contact_id` / `leads.import_batch_id`.
* Rows with no usable channel are **Needs Enrichment**: preserved as contacts
  (`lifecycle=needs_enrichment`), searchable and matchable, never leads.

## Outreach readiness

* **SMS ready** requires: valid number, known mobile line (a dedicated mobile
  column, or a line-type / verification value saying mobile), explicit SMS
  consent in the source, and no DNC / suppression / opt-out. Otherwise
  `pending_validation` (the honest default - the platform has no carrier lookup
  yet), or dnc / suppressed / opted_out / landline / invalid / no_phone.
* **Email ready** requires a well-formed, non-placeholder address with no
  bounce / unsubscribe / invalid / suppression signal. Role addresses (info@,
  sales@) and "risky/unknown/catch-all" verdicts are `review`.
* An import never grants TCPA consent and never un-bounces or un-suppresses an
  existing record (more-restrictive status always wins on update).

## Dedupe / matching (`app/services/intake/matching.py`)

Keys, strongest first: source system + record id (and every merged alternate
id, via `org_contact_source_ids`), email (exact unless names disagree), phone /
mobile (exact only when the identity agrees; different surnames on one number
= shared line, not a match), company + address, name + company. Only EXACT is
acted on without a person; POSSIBLE always goes to review and is never merged.
In-file exact duplicates default to **merge into the first occurrence** (fill
blanks + keep the duplicate's source id as an alternate id). Matching against
existing data loads only the organization's own records.

## Commit modes (decision screen)

* `stage_only` (default) - writes nothing; batch parked as STAGED.
* `ready_only` - ready + approved rows + exact existing-record updates
  (+ needs-enrichment contacts if chosen).
* `ready_and_review` - also undecided review rows: possible duplicates are kept
  separate and never become leads.

Non-stage modes require the organization name typed exactly. Updates obey the
batch's update policy (`fill_blanks` default, or `overwrite` for listed fields)
and never overwrite `org_contacts.manually_edited_fields`.

## Rollback (`app/services/intake/rollback.py`)

Reads `import_record_versions`. Created + untouched -> removed; created with
downstream activity (any table with an FK to `leads.id`), status/assignment/
tier/notes changes, or later imports -> contact archived / lead kept; updated
fields still holding the imported value -> restored to the prior value; changed
since -> kept and reported. `GET /rollback-plan` shows all of it before
`POST /rollback` (requires the batch ID typed).

## Tenant / God context

Every route calls `context.resolve()`: customer users import into their active
workspace; a God admin must have a customer selected (X-Org-Override) or gets
409. Batches record `acting_role` and `acted_as_platform_owner`; every audit
event (`audit_log_entries`, action `intake.*`) carries imported-by, role and
acting organization.

## Background work

Analysis and commit run in a worker thread with its own DB session and write
`stage`, `progress_pct`, `heartbeat_at` as they go. A batch whose heartbeat is
older than 10 minutes reports `interrupted` and can be re-run safely (analysis
replaces its staged rows; commit skips IMPORTED rows). SQLite / tests run
inline (`INTAKE_INLINE_JOBS=1|0` overrides).

## Schema (all additive)

New tables: `org_contacts`, `org_contact_source_ids`, `import_batch_files`,
`import_record_versions`, `intake_classifications` (created by `create_all`).
New nullable columns on `import_batches`, `import_staged_rows`, and
`leads.org_contact_id` / `leads.import_batch_id` (added by `auto_migrate`, no
defaults, no backfill). Existing leads are not reclassified.

## Legacy paths

`/import-batches` and `/leads/upload/*` remain for API compatibility. The
legacy commit refuses universal batches (409). The Leads screen's Import button
now opens `/imports/new`. Google Contacts still writes through the legacy
`import_leads_from_rows` path - moving it onto this engine is the next
connector task.
