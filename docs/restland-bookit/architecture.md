# Architecture

One authoritative store: the EvoSys database. BookIT adds tables; it never copies a family,
appointment or follow-up.

| Concept | Source of truth | BookIT adds |
|---|---|---|
| Family | `leads` | paperwork status, family networks, ADD cases, service requests |
| Appointment | `booking_links` | close-ticket disposition, sales |
| Follow-up | `program_conversations` (row id `FU-<id>`) | — |
| Assignment / rotation | `handoff_sheet_rows.assigned_to` (existing round robin) | — |
| Team | `program_team_members` (synced from the sheet's Team tab) | access suspension, role |
| Team fields (Outcome, Sold?, notes, follow-up log, booking) | `handoff_sheet_rows.staff_json` | changes queued in `handoff_pending_edits` |

## One road for every team edit
A change made in BookIT is stored as a `handoff_pending_edits` row. The next sheet pass
(`restland_sheet.team_pass`, about every 2 minutes and right after a save) puts it through
exactly the road a sheet edit takes: written to the sheet cell, then applied to the app
(booking, attendance, notes). The sheet and BookIT therefore never disagree while both are in
use. If Google Sheets is unavailable, saves stay queued (shown as "Saving…") and go through when
it returns; a row no longer on the sheet keeps its change for 2 days, then it is recorded as
`row_gone` (never silently lost). **Known limitation:** while the sheet is the operational
source, an outage delays a team edit reaching the app; the post-cutover step is to apply pending
edits directly when no sheet is configured.

## Decisions
* New name, new path: EvoSys already has "EvoSys BookIt" (`/program/evosys-sales`, EvoSys's own
  sales team). Restland BookIT lives at `/program/restland-bookit` so the two never mix.
* The Program Access card is its own context list (`bookit_contexts`), not a product context —
  a product context hides its host workspace card.
* Money is integer cents computed on the server; one sale per idempotency key.
* No new scheduler: enrollment state lives on the contact; sending will reuse the existing
  campaign engine once templates are approved.
