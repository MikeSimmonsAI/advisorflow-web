# Restland completion: identity, every family, earlier work (Issue #507)

Branch `redesign/restland-identity-and-history`, built from `main` @ `c6dda218`.

The earlier ChatGPT branch with the same name (`f3f9f91f`) was never pushed to GitHub. Its workspace isn't reachable from this environment either. Its requirements were therefore rebuilt here against current main; nothing was cherry-picked.

## Production baseline (read-only, 2026-11-04 22:35 CST, counts only)

These are different kinds of record. They are never added together.

| Record | Count | Notes |
|---|---|---|
| Lead records | 10,576 | 10,575 real, 1 test |
| Linked by the cutover (run `36a344ae`, 2026-11-04 09:12 CST) | 10,576 | 0 duplicates, orphans or cross-tenant links |
| Program state | AI setter off, approval mode on, daily batch 0, staff alerts off, 0 campaigns on, text switch not set | unchanged |
| Original inbound replies (`replies`) | 21, from 6 families | program responses: 0 |
| Sent texts (`messages`) / emails (`email_messages`) | 6 / 224 | |
| Legacy follow-up records (`pipeline_conversations`) | 40 | state records, not message threads |
| Booking links with a time | 6 booked (Aug 12–31, 5 families) · 2 cancelled | |
| Booking links "booked" with no time | 13 (4 belong to test records) | not appointments, not listed |
| Booking links never used | 1,912 pending · 5 expired | not appointments |
| Appointment case files | 11 | 5 tie to a booked booking · 1 to a cancelled booking (case `void`) · 2 with no booking · 3 point to a family record and booking that no longer exist |
| Notes / tasks | 0 / 0 | |
| `needs_tier_review` | 7,372 | see below |

## What changed

1. **Own colours.**
   - Forest green, warm ivory and muted brass (`RestlandTheme.css`), applied on both shells through `data-program-theme`.
   - The server decides the theme (`program_profile.THEME_BY_ORG`, or a location profile's `brand_settings.program_theme`). It's never decided by industry, slug or the browser, so SCI and any other funeral home stay as they are.
   - Every recolouring selector is scoped; a test enforces it. Semantic warning, error and success colours are unchanged.
2. **Every family findable.**
   - `GET /program/command/search` with no term browses all non-test families.
   - It's paged (`offset` / `limit`, at most 50), returns an accurate `total`, and uses a stable order: last name, first name, id.
   - Filters: all, replied (program or earlier replies), booked (has a booking time), needs review, never contacted, do not contact.
   - A single character still returns nothing, as before.
   - The screen resets to page 1 when the search or filter changes, ignores stale answers, and shows a failed load as an error.
   - Advisor seats can browse; other workspaces get 404.
3. **Earlier conversations.**
   - Original `replies` are read in place into the family thread, marked "Earlier reply" (`historical`).
   - They're deduplicated against `ProgramResponse.reply_id`.
   - Nothing is written and nothing is replayed through inbound handling: no alerts, no AI, no unread or hand-off counts (tested).
   - `GET /program/command/earlier-conversations` (tenant-scoped, paged) feeds an "Earlier conversations" list under the inbox.
   - Long threads say "Showing the latest 40 of N" and can load up to 500.
   - The legacy follow-up record appears as "Earlier follow-up (history)". It's a summary only and is never converted into an active program conversation.
4. **Appointments, calendar and case files.**
   - Live Appointments has Upcoming / Past / All and a real error state. Each item shows staff, status (cancelled or completed) and whether a case file exists.
   - The Calendar tab already had these filters.
   - Each family shows its full booking history, newest first.
   - `GET /program/command/case-files` is manager-only and read in place. It includes the 3 case files whose family no longer exists, flagged `family_missing` / `booking_missing`, never re-linked by guess.
   - Times are shown in America/Chicago from the stored wall time (`booking_time.wire`).
5. **Truthful status.**
   - Staff seats now get Restland's own name and colours. Before, they saw "SCI Live".
   - The family page says plainly when the program's AI setter is OFF, instead of "AI Handling — ON".
6. **Performance (analytics ledger).**
   - Each query narrows by an org-scoped subquery instead of 10,500+ bind parameters.
   - Rows are kept only for families in the same population as before. A test proves non-test, non-demo leads with a program record are the only ones counted.
   - Build locks are per workspace, so Restland never blocks SCI.
   - Cache freshness counts from the end of the build.

## Measured

- **Production, read-only EXPLAIN ANALYZE** of the new query shapes for Restland:
  - outreach attempts: **85 ms** (the earlier diagnostic measured that step at 2,498 ms);
  - families emailed: **82 ms**.
  - The full production build is measured again after deploy.
- **Local SQLite, 10,576 families, cold build:** main 855 ms, branch 631 ms.

## Remaining data and automation findings (not changed; decisions for the owner)

- **7,372 `needs_tier_review`.** All are tier `partial`, relationship `cold_lead`, with no status reason, last action or notes, except as noted:
  - 4,408 from `Restland_Dallas.csv` (imported 2026-08-12);
  - 2,599 from `google_contacts_restland_7_7_2026_under20mb_part_04.csv` (2026-07-10);
  - 365 from `All Active Leads (2012).xlsx` (2026-06-21, message track `upsell_existing_customer`, these do have status reasons).

  Phone or email presence doesn't establish the business relationship, so no deterministic correction exists. They're reviewable under Families → "Needs review". Nothing was relabelled.
- **Old cadence states.**
  - 96 lowercase `active` states are picked up by the old runner every hour. Texts are off for the workspace, so each logs one skip: about 2,300 rows a day.
  - 409 uppercase `ACTIVE` states are never selected; the runner compares case-sensitively.
  - After the program cutover, those 96 legacy states would still be refused even with texts on, because no campaign is assigned to their families.
  - An attempt to stop the repeated skip rows was reverted. An existing rule, with its own test, deliberately records every skip as evidence.
  - **Owner decision:** pause the 96 legacy states, which preserves their touch numbers. Do not "fix" the casing: that would make 409 dormant states start running.
- **Roofing remnants.**
  - 10,576 opportunities and 12,328 stage events were created together on 2026-10-21 at 05:41 UTC, when Roofing was switched on for Restland. Roofing is now off.
  - The Roofing API refuses Restland, and the program shell never reads these tables.
  - 5 opportunities point at Restland's real booked file reviews. They have no estimate or won amount, so they are mirrors, not Roofing business.
  - **Plan, owner-run, reversible:** export the rows, delete `roofing_stage_events` and `roofing_opportunities` for this org only, and keep the export. No family record or booking is touched.
- **Launch dependencies (unchanged, external):**
  - toll-free sender (the number on file is the rejected 10DLC ending 1155);
  - inbound mailbox;
  - booking calendar user;
  - staff alert recipients;
  - campaign membership and copy, the owner's GO, then the workspace text switch.

## Configuration / migration

None. No schema change and no data write. The theme for production comes from the org id map in `program_profile.py`.

## Rollback

Revert the merge commit. Nothing was written to data, so nothing needs restoring.
