# VOLT → Google Sheets live lead mirrors (implementation)

Status: **not enabled in production** until private Drive access and a service-account credential are configured and the PR is tested/merged. The spreadsheets have been created separately, with styled dashboards, frozen filtered tables, and no personal records.

- VOLT Marketing Recruiting: https://docs.google.com/spreadsheets/d/1ePpqyCCa08YP9aDaD570n-ywsAC89eqw-xm8-rIRrfg/edit
- Consumer Leads: https://docs.google.com/spreadsheets/d/1d5O5HLgmcPKCiKJOVkBXhy0w_zocjkxO4HxLN3kNfp0/edit

## Operational contract

1. EvoSys PostgreSQL remains the authoritative data store. Each successful real lead creation/update or recruiting application submission queues an event **in the same DB transaction**. No Google API call can make an intake submission fail.
2. An existing single-owner VOLT background scheduler drains the durable queue (normally within the next two-minute pass). Retries with bounded backoff on Google 429, rate limits and transient failures. The record is reloaded from PostgreSQL so changes to status, assignment, source, etc. update the *same row*.
3. Separate workbooks, column A stable IDs; CSV export stays available from Google Sheets. No test lead/applicant is sent to either production workbook. No consent evidence text, SSN or health details are exported. Only fields in `SCHEMAS` are allowed.
4. Both workbooks are private; operators must separately review access. Never turn on public link sharing or grant a public Google API key access to spreadsheets containing phone/email details. Two Sheets share the single **explicitly approved** VOLT organization only; no other tenant data goes to them.
5. The recruiting workbook indexes submitted applications by application ID (one applicant may have multiple project applications). Consumer Leads indexes a deduplicated lead by lead ID.
6. Initial backfill and historical record export require a deliberate, audited admin action: do **not** backfill existing historic personal data automatically before checking permissions and consent.
7. Google Sheets is a reporting mirror, **not** a bidirectional editing API. Staff edits in Google Sheets do not change applicant/lead records, roles, consent or business status inside EvoSys.

## First-time connection checklist

- Review the two workbook URLs above, verify Drive ownership and restricted sharing (owner and specific operators only). Confirm any Google Workspace retention/PII policy.
- Create/use a dedicated Google Cloud service account with Google Sheets API permission. Share *only* each intended spreadsheet with the service account email as Editor. Do not use publicly shared spreadsheets or OAuth tokens in source code.
- In Render backend secrets, configure:
  - `VOLT_SHEETS_SERVICE_ACCOUNT_JSON` — entire service-account credential JSON as a secret
  - `VOLT_SHEETS_ORG_ID=c840313d-701b-46af-95d3-301c3644f0d8` — approved organization, confirm against database
  - `VOLT_RECRUITING_SHEETS_ID=1ePpqyCCa08YP9aDaD570n-ywsAC89eqw-xm8-rIRrfg`
  - `VOLT_CONSUMER_SHEETS_ID=1d5O5HLgmcPKCiKJOVkBXhy0w_zocjkxO4HxLN3kNfp0`
  - `VOLT_SHEETS_SYNC_ENABLED=off` by default; switch `on` only after CI, permission and synthetic tests
- Verify both Sheet tables exist with exact tab names `Applications` and `Lead Inbox` and headers from `sheets_export.SCHEMAS`.
- Run one isolated test and compare stable ID, source/assignment/status fields; repeat to confirm no duplicate; test a failing Google API response and recovery; test cross-tenant isolation and spreadsheet Formula injection.
- Add an admin-only status page showing last successful sync, pending count, last non-PII error type and pause/resume state; add monitored notification on repeated sync failures. This status UI is an important follow-up if not included in this PR.
- Confirm service-account credential access is permitted by Google Workspace policy. Do not log credential JSON or sheet content.

## Privacy/consent

Both files include personally identifiable information. Treat Google as a data processor, set permissions accordingly, and ensure privacy notice accurately discloses this transfer. Consent provenance stays in EvoSys; only its timestamp is mirrored. Existing public Consumer Leads intake remains OFF until its separate launch requirements are satisfied.

## Known scope boundaries

- This is a **one-way, automatically updating Google Sheets mirror**. It does not connect Meta/TikTok advertising accounts; those are tracked separately in issue #619.
- Google Sheets API hard limits and large datasets mean that a per-record column-A scan may need scaling/pagination. Pilot at current small VOLT volumes first; for large volumes add persisted row index/cached ID mapping and reconciliation.
- Recruiting workflow changes *after initial submit* must also enqueue a row in all relevant status change paths; confirm coverage during PR review. Lead create/repeat/assignment/status are already wired.
- Existing scheduler cadence is ~2 minutes, so this is near-real-time sync rather than a synchronous instantaneous write.
