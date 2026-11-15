# VOLT → Google Sheets live lead mirrors

Status: **built, shipped switched off.** Sync stays off (`VOLT_SHEETS_SYNC_ENABLED` unset or `off`) until a Google service account exists, has access to both sheets, and the change has passed security review. Nothing in this document is a claim that the integration is connected. It is connected only when the EvoSys status panel shows "Connected · waiting for the first record" or "Syncing" and a real record has been written.

- VOLT Marketing — Recruiting Leads: https://docs.google.com/spreadsheets/d/1ePpqyCCa08YP9aDaD570n-ywsAC89eqw-xm8-rIRrfg/edit (tabs: `Dashboard`, `Applications`)
- Consumer Leads — Health Coverage: https://docs.google.com/spreadsheets/d/1d5O5HLgmcPKCiKJOVkBXhy0w_zocjkxO4HxLN3kNfp0/edit (tabs: `Dashboard`, `Lead Inbox`)

Both files are private: owner only, plus D'Angelo once the owner shares them with him. Never enable link sharing, and never share them with recruiting staff or agents.

## How it works

1. **PostgreSQL is the source of truth.**
   - A SQLAlchemy `after_flush` listener (`sheets_outbox.register_listeners`) watches `VoltLead`, `VoltApplication` and `VoltRep` changes.
   - It writes one outbox row per record into `volt_lead_sheet_outbox`, inside the same database transaction. It uses an upsert keyed by (org, business, record_id) and bumps `version` each time.
   - There are no Google calls in any request path, so a Google outage can never fail an intake or a status change.
   - Every write path is covered automatically, including all recruiting transitions after submission and rep phone or email changes. No call sites have to remember to enqueue.
2. **Worker:** `_volt_sheets_loop` runs in `app/main.py`.
   - It is leased through `job_run_service` as `volt_sheets_loop`, so only one instance runs.
   - It polls every 15 seconds while there is work and checks in hourly when idle.
   - Each pass:
     - takes a Postgres advisory lock;
     - claims up to N rows, storing `claim_token` and `claimed_version`;
     - commits;
     - makes the Google call with no database locks held;
     - finishes the row only if the claim token still matches.
3. **Writes**
   - The worker reloads each record from PostgreSQL and builds the row from `sheets_export.SCHEMAS`.
   - It reads column A once per batch: existing IDs get updated in place with `batchUpdate`, and new IDs are appended. One row per stable ID; the first occurrence wins.
   - Values are written RAW, so text is never treated as a formula. Text starting with `= + - @`, a tab or a CR gets a leading `'`.
   - Dates are written as Central-time serial numbers, so the sheet formats and sorts them correctly.
4. **Failure handling** (every Google call times out after 30 s)
   - If the record changed during the write, the row stays pending and is written again.
   - If a worker crashes, its claim expires after 10 minutes and the row is picked up again.
   - Rate limits, Google outages and network errors retry with exponential backoff (15 s doubling, capped at 1 h, at most 12 attempts).
   - Setup problems (no credential, 401, 403, 404, wrong header row) do not use up attempts. They retry every 15 minutes and show on the status panel.
   - Data errors wait for a manual retry.
   - Only the error category is stored or logged; record content and credentials never are.
5. **Reconciliation**
   - Every 30 minutes the worker re-queues records changed in the last 2 days.
   - An operator can also trigger a recent re-check.
   - Only the owner can run "Copy all existing records", which is a full backfill and is audited.
6. **Exclusions:** test leads and test applicants, drafts, deleted records, and any organization other than `VOLT_SHEETS_ORG_ID`. No consent text, SSN, health or medical data is ever sent; only the consent timestamp is.
7. **Alerting:** if the sync has not succeeded for 30 minutes while work is waiting, the VOLT notify list gets one alert email, repeated at most every 6 hours.

## EvoSys controls

- Status panel: Consumer Leads → Settings, and VOLT Ops → Overview. It is visible to the owner and the named operator only. It shows:
  - connection health
  - a link to the sheet
  - last success and last attempt
  - pending, retrying and failed counts
  - last error category
  - paused or running
  - last verification
  - the service-account email (never the key)
- Buttons: Check connection, Retry failed, Re-check recent changes, Pause/Resume, and (owner only) Copy all existing records. Every action is audited (entity `google_sheets`).
- CSV export: Consumer Leads inbox, Download CSV.
  - Agents only get their own leads.
  - Filters: status, source, campaign, agent, and a From/To date range in Central time.
  - The file is UTF-8 with a BOM, cells are formula-safe, and every download is audited (`lead_csv_export`).
  - Recruiting keeps its existing Export CSV.

## Spreadsheet design

- **Data tabs** (`Lead Inbox`, `Applications`)
  - The worker refuses to write unless row 1 matches `SCHEMAS` exactly, and reports `sheet_layout` instead.
  - The header row and first column are frozen, date columns are formatted as Central date-times, and status cells are colour-coded.
  - Consumer follow-ups that are overdue show in red.
- **Dashboard tabs** are formulas only, over open-ended ranges, so they keep working as rows are added:
  - KPI cards, tables by status, source and campaign (consumer) or project and project × status (recruiting), a 12-week Monday-based trend, follow-ups due (consumer) or newest applications (recruiting), and 2 charts.
  - Counts use the status labels the sync writes: Consumer — New, Contacted, Qualified, Appointment Set, Enrolled, Not Interested. Recruiting — Submitted, Needs update, Approved, Started, Withdrawn / closed.
- Do not edit the data tabs by hand. The sheet is a one-way reporting copy, and edits there never change EvoSys.

## Owner setup (required before turning on)

1. In Google Cloud (any project you own), enable the **Google Sheets API**.
2. Create a service account, for example `volt-sheets-sync`, with **no project roles**. Create a JSON key and keep it only in Render.
3. Share **each** spreadsheet with the service-account email as **Editor**, and turn off "Notify people". Share nothing else with it.
4. On the Render backend (`srv-d8rsm2kvikkc738v8470`), set:
   - `VOLT_SHEETS_SERVICE_ACCOUNT_JSON` = the whole key JSON (secret)
   - `VOLT_SHEETS_ORG_ID=c840313d-701b-46af-95d3-301c3644f0d8`
   - `VOLT_RECRUITING_SHEETS_ID=1ePpqyCCa08YP9aDaD570n-ywsAC89eqw-xm8-rIRrfg`
   - `VOLT_CONSUMER_SHEETS_ID=1d5O5HLgmcPKCiKJOVkBXhy0w_zocjkxO4HxLN3kNfp0`
   - `VOLT_SHEETS_SYNC_ENABLED=off`
5. After deploy, open the status panel and use **Check connection** for both businesses. It should show "Connected · waiting for the first record". If not, the panel names the problem: missing credential, no permission, sheet not found, or header mismatch.
6. After security review, set `VOLT_SHEETS_SYNC_ENABLED=on`. New and updated real records then appear within about 15–30 seconds. Use **Copy all existing records** (owner only) for history, once you are satisfied with access and consent.
7. To stop at any time, use **Pause** on the panel or set the flag back to `off`. The queue is kept and resumes where it left off.

## Privacy

- Both files contain names, phone numbers and emails. Google acts as a processor, so the privacy notice must disclose this transfer before the sync is turned on.
- Consent evidence stays in EvoSys.
- Public Consumer Leads intake remains off, under its own launch checklist.
- Ad-platform connections (Meta/TikTok) are out of scope; see #619.
