# Restland → SCI operating model: parity matrix (Issue #507)

Reference: SCI as deployed on production, `main` @ `de3c17db` (backend and frontend both live on that commit on 2026-11-04, verified from Render deploys).
Baseline: production database, read-only queries, counts only. No family data is shown here.

## How it works

SCI isn't special in code. It's an `OutreachProgram` row on its organization plus `LocationProfile` rows (42 on production). Any organization with an active program record opens the same shell (`/program`, `/program/live`).

Restland gets:

- its own program record, with every sending control off;
- one location (the business itself) plus the standard Location Review bucket;
- one link (`ProgramSourceRecord`) per existing family.

Its leads, history, conversations, appointments, suppression and cadence states stay exactly where they are.

## Production baseline (2026-11-03 23:40 CST)

| Item | Restland | SCI |
|---|---|---|
| Families (leads) | 10,576 | 2,284 |
| Status breakdown | needs_tier_review 7,372 · new 1,583 · dnc 1,463 · sent 147 · booked 7 · replied 4 | — |
| Locations / profiles | 0 / 0 | 42 / 42 |
| Program record | none | active (AI setter auto, approval mode on, daily batch 40) |
| CRM contacts / contact registry | 9,069 / 9,063 | — |
| Cadence states | 409 `ACTIVE` + 96 `active` (inconsistent casing) | — |
| Cadence touch logs | 81,984 total; last 7 days: 15,840, all "texts are switched off for this workspace" | — |
| Campaigns / cadence templates | 1 / 3 (default "Funeral Home 9-Touch") | — |
| Appointment case files | 11 | — |
| Pipeline conversations | 40 | — |
| Suppression entries | 5 | — |
| Users (home org) | 7 (1 super_admin, 1 org_admin, 4 advisors active, 1 inactive) | — |
| Workspace memberships | 3 org_admin + 1 super_admin + 4 advisor active | — |
| Texting number on file | 10DLC, ending 1155 (campaign rejected/deleted Aug 2026) | toll-free (844) line |
| Workspace text switch | no row (off) | per SCI |
| Inbound mailboxes | 0 | connected |
| Org timezone | not set (code defaults to America/Chicago) | — |

Existing data-quality issues (they predate this work and aren't migration defects):

- 7,372 families are stuck at `needs_tier_review`.
- Cadence state status has mixed casing (`active` vs `ACTIVE`). The 96 lowercase rows are retried every ~15 minutes and each logs a skip. That's about 2,300 rows/day of log growth.
- 10,576 `roofing_opportunities` / 12,328 `roofing_stage_events` exist for Restland, left over from the Roofing capability incident on 2026-10-21.

## Matrix

Disposition key: **Reuse** = the SCI component as is · **Adapt** = changed so it fits one location / Restland's identity · **Preserve** = Restland's existing data or behaviour kept untouched · **Retire** = not part of Restland · **Gate** = shown only to SCI.

| Item / workflow | Current SCI | Current Restland | Restland target | Disposition | Implementation | Verification |
|---|---|---|---|---|---|---|
| Entry / launcher card | SCI card → `/program` | "Cemetery & Funeral Services" card → general platform | Funeral card (own name) → `/program/live` | Adapt | `workspace_access.program_is_sci`, `programAccessModel.programOf` | `restlandSingleLocation.test.mjs`; click test opened `/program/live` as Restland |
| Shell menu, title bar, footer, tab titles | "SCI Live", SCI POC footer | general platform sidebar | "Restland Live / Restland Cemetery and Funeral Home", single-location footer | Adapt | `program_profile.profile`, `useProgramProfile`, `SciNav.navFor` | screenshots `restland_live_*`, `sci_live_*` |
| Location filter (top bar) | All locations (N) | n/a | fixed chip with the one location | Adapt | `ProgramCenter.jsx` | screenshot `restland_overview_desktop` |
| Location leaderboard / location performance | shown | n/a | hidden | Retire (for Restland) | `sci/Dashboard.jsx`, `command/Performance.jsx` | screenshots |
| Per-screen location pickers (Leads, Send, Assets, Campaign preview, analytics) | shown | n/a | hidden for one location | Adapt | `homes.length > 1` guards | static test |
| Locations tool | directory of 42 | n/a | "Location": one profile (identity, phone, booking link, branding) | Adapt | `toolsFor`, `Locations.jsx` | screenshot `restland_locations_desktop` |
| Review / Location Review | shown | n/a | kept (bucket exists for unmatched records) | Reuse | — | — |
| Dashboard / Command Center | KPIs, pipeline, actions | general Overview | same panels, "Families here" KPI | Adapt | `sci/Dashboard.jsx` | screenshot |
| Families search / record / notes / files | Program family file | general Lead Detail | program family file over the same Lead rows | Reuse | source records link existing leads | preview; tests |
| Conversations / inbox / reply | program conversations | `pipeline_conversations` (40) | program inbox; old conversation rows preserved | Reuse + Preserve | — | — |
| Inbound reply handling | program only (`responses.on_inbound`) | generic pipeline/concierge auto-reply | program only, never answered twice | Adapt | `sms_programs.is_program_org` in `sms_router`, `pipeline_service`, `ai_conversation_service` | `test_inbound_reply_is_answered_only_by_the_program` |
| AI appointment setter | auto | n/a | OFF | Reuse (off) | cutover sets `ai_setter_mode='off'` | test |
| Campaigns / journeys | SCI families, Start/Pause/Resume | 1 campaign + "Funeral Home 9-Touch" cadence | 1 inactive family linked to Restland's own 9-Touch template; no record assigned yet | Adapt + Preserve | `single_location_cutover` | reconciliation `records_with_campaign=0`, `active_campaign_families=0` |
| Choose & Send / daily batch | approval mode, batch 40 | n/a | approval mode ON, batch 0 | Reuse (off) | cutover | test |
| Central Alabama panels, 9:05 SMS retest | SCI campaign-specific | n/a | hidden | Gate | `profile.sci_only_panels` | static test, screenshots |
| Text sending gate | SCI consent ledger + toll-free-only | org 10DLC sender (dead), workspace switch off | held on every path (manual too) until the owner turns the workspace text switch on | Adapt (safety) | `sms_programs.program_text_refusal` in `sms_service._final_send_guards` | `test_texts_held_on_every_path…` |
| Automated sends | campaign family must be on | cadence job skipping | refused: no campaign assigned + approval mode + texts off | Reuse | `identity.send_refusal` | test |
| Email sending | program email runner (env-gated) | email service | program runner; families inactive so nothing runs | Reuse (off) | — | reconciliation |
| Delivery states / bounces / suppression | program + Twilio callbacks | existing suppression (5) | unchanged; suppression always wins | Preserve | — | existing suites |
| Opt-ins & opt-outs | consent ledger | DNC leads 1,463 | shown from the same data | Reuse | — | preview shows DNC as opted out |
| Appointments / calendar | program appointments + booking user | case files (11), calendar router | program screens; booking calendar not chosen yet (blocker) | Reuse + Preserve | — | launch checklist B1 |
| Launch Readiness | SCI fixed gates (844, Kerry, Oaklawn) | n/a | Restland's own live checklist | Adapt | `readiness_check.program_gates` | `test_launch_checklist_is_restlands_own`, screenshot |
| Settings: text switch, alerts, sender identity | per program | org settings | per program; text switch owner-only | Reuse | — | — |
| Staff alerts | Mike / Michael / Kerry | n/a | OFF until recipients are configured | Reuse (off) | cutover | checklist A1 |
| Users / roles / access | memberships; server-scoped | 7 users, memberships | unchanged; program endpoints manager-only, scoped server-side | Preserve | — | `test_other_workspace_cannot_read_restland_program` |
| Tenant isolation | org-scoped by active workspace | — | same | Reuse | — | test + cross-tenant reconciliation |
| Old cadence job (`run_cadence_job`, `_cadence_loop`) | SCI excluded unless enabled | 96 states retried, all skipped | left running, still sends nothing (texts off, and the send refusal now also applies) | Preserve (documented) | — | cutover runbook |
| Roofing remnants on Restland | — | 10,576 roofing rows | untouched (separate cleanup) | Preserve | — | — |
| SCI-only jobs (stuck visits, practice run, journey repair, appointment copy) | SCI by name | — | don't run for Restland | Retire (for Restland) | already SCI-scoped | — |
| Toll-free sender | approved (844) | none | blocker: needs a toll-free number assigned and verified | External | — | checklist S1 |
| Email reply mailbox | connected | none | blocker | External | — | checklist M1 |

## Gaps that still need a person

1. **Restland toll-free number.** Not purchased, not assigned or verified. Until then texts stay off.
2. **Mailbox for email replies.** Not connected.
3. **Booking calendar user and staff alert recipients.** Not chosen.
4. **Campaign copy and which families go into it.** Restland's own 9-Touch wording is linked, but no family is assigned and nothing is on. Mike decides.
5. **Production cutover.** The script runs only after the draft PR is approved and deployed (see `CUTOVER.md`).
