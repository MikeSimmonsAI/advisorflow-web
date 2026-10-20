# Atlantis Light & Power — overnight handoff (2026-10-20)

Lane C · tracking issue #237 · rules #239 and `C:\Dev\lanes\LANES.md`.
This file is the durable record of the overnight V4 run. It is updated at the end of the run.

## 1. Production state (unchanged overnight)
- main / Render backend + frontend: `aee2e142` (PR #241, Phase 1). `/health` and `/version` report aee2e14.
- Live page: `https://api.evosyspro.live/site/atlantis-light-and-power`. Its stored md5 `e571d813…` matches main. It was **not** republished overnight.
- Synthetic live lead "Release Check" (`atlantis-release-check-20261019@example.com`, id `011692da-…`) is still in place. It was created before test-record flagging existed, so it is NOT flagged `is_test`. The owner decides whether to flag it (owner item).
- PR #116 is untouched (OPEN at `fb4b7399`). Chariot transactions OFF. No outreach, supplier action, DNS, WordPress, appointment or payment changes.

## 2. Branch
`feature/atlantis-phase2-ops`, cut from `aee2e142`. Not merged. A PR is opened for review only and needs a GO from Mike.

| Commit | What |
|---|---|
| 3ef90483 | Supplier hand-offs: one open hand-off per supplier (double-click safe), recorded outcomes, which never enroll. Reserved test addresses (example.com/.net/.org, .test/.example/.invalid/.localhost) are filed as `is_test` |
| 9dd07db8 | Advisor qualification view: known / missing / next step / eligible suppliers; manual bill lines (actual vs estimated); one "Get missing details" task |
| 4bb16dd9 | Move Concierge: completion needs every item ticked; cancel clears move tasks; a changed date surfaces late tasks; pending / next action / overdue; a re-sent form updates the open move and never unticks |
| a7bceaec | Commercial: advisor-led stages on the same lead (Rate Requests tier moves forward only, never to Contract Signed). Won only after supplier confirmation. Lost/deferred carry a reason and history. Several service locations stay under one business |
| b023ac62 | Field acquisition: door outcomes are recorded as what the rep said happened. Referrals need both a contact and permission, are credited to the rep, and land in the normal queue. Per-rep summary (managers see all reps, reps see themselves) |
| 3ccf7619 | Fixes from the independent review (see §4) |
| (docs) | `READINESS_EXTERNAL.md`, `SALES_PROGRAM.md`, this file |

## 3. New endpoints (all under `/energy-ops`, tenant-scoped through `lead_scope`)
- `GET /suppliers`
- `POST /leads/{id}/handoff`
- `GET /leads/{id}/handoffs`
- `POST /leads/{id}/handoffs/{hid}/outcome`
- `GET /supplier-handoffs?state=open|all`
- `GET|POST /leads/{id}/qualification`
- `POST /leads/{id}/qualification/request-info`
- `GET /leads/{id}/commercial`
- `POST /leads/{id}/commercial/stage`
- `POST|PATCH /leads/{id}/commercial/locations[/{loc}]`
- `POST /field/visits`
- `GET /field/summary?days=`
- `PATCH /moves/{id}` and `POST /moves/{id}/checklist` (behaviour changes only)

There are no schema changes and no migrations. Everything is stored in `Lead.custom_fields`, existing tables and audit entries. Shared hot spots (`auto_migrate`, `models/*`, `deps`, `main`, `App.jsx`, workflows, render yaml) were not touched.

## 4. Independent QA review — findings and outcomes
Fixed in `3ccf7619`, each with a test:
- **High.** A field rep could mark any workspace lead do-not-contact, or learn its id, by phone number. Matching now uses the rep's own lead scope. Matches the rep can't see are forwarded to the owner as a task, with no id returned and the lead left unchanged.
- Two residents at one address were treated as duplicates. Email matching was case-sensitive.
- Cancelling a move could cancel tasks on a lead the caller couldn't read, or one shared with another open move. Completion was checked before services added in the same change. A completed checklist could be unticked.
- Custom-fields writes were not locked (lost-update risk). `_lock` swallowed errors and, with autoflush off, would have discarded pending edits.
- A repeated commercial site dropped a newly supplied meter id.
- An auto-flagged test lead stayed hidden even after a real person used the same contact.
- A non-list `supplier_handoffs` broke the lead.

## 5. Tests and how to run them (from `C:\Dev\lanes\atlantis`)
```
python -m pytest -q -p no:cacheprovider tests/test_atlantis_field_ops.py tests/test_atlantis_move_concierge_ops.py tests/test_atlantis_commercial.py tests/test_atlantis_supplier_outcomes.py tests/test_atlantis_test_records.py tests/test_atlantis_qualification.py   # 57 passed
python -m pytest -q -p no:cacheprovider tests/test_atlantis_operating_flow.py tests/test_customer_sites.py tests/test_lead_capacity_matrix.py tests/test_plan_limits_coverage.py                                                                                   # 72 passed, 2 failed (SCI files only)
python -m pytest -q -p no:cacheprovider tests/test_atlantis_site_journeys.py tests/test_atlantis_pipeline_oct1.py tests/test_commercial_atlantis.py tests/test_atlantis_site_browser.py                                                                                # 44 passed
```
Known failures that are not Atlantis: `test_lead_capacity_matrix` and `test_plan_limits_coverage` name `services/programs/setter_eval.py` and `services/sci_staging_demo.py` (SCI Lane A). The same failures exist on main. The 54 full-suite and 9 `test_sci_decline_alternatives` failures noted on Phase 1 are also pre-existing on main.

## 6. External readiness and owner decisions
See `READINESS_EXTERNAL.md` (domain cutover, sender and inbox, calendar, supplier matrix, ComparePower link, default advisor) and `SALES_PROGRAM.md` (playbooks, residential→commercial path, training, compensation structure with **no** rates, revenue readiness, owner decision sheet). Nothing in either document was activated.

Owner-only items:
1. Production default advisor (currently unset; admin fallback verified).
2. Domain cutover for atlantislightandpower.com (GoDaddy DNS, no MX/SPF/DMARC).
3. Sender and inbox choice. The inbound mailbox feature is Microsoft 365 only; atlantis-enterprises.com runs Google Workspace.
4. ComparePower referral URL.
5. Supplier wording and eligibility confirmation.
6. Compensation terms.
7. Whether to flag the Release Check lead as a test record.
8. GO / NO-GO on merging the Phase 2 PR.

## 7. Rollback
Production rollback is not needed: nothing was deployed. If the Phase 2 PR is merged later and must be undone, revert the merge commit on main. There are no migrations, so the revert is code-only. Data written into `custom_fields` keys (`supplier_handoffs` entries with status/history, `commercial`, `bill`, `field_rep_id`, `contact_permission`) is ignored by the older code.

## 8. Next commands
- Review the PR: `gh pr view <n> --repo MikeSimmonsAI/advisorflow-web`
- After GO: merge only that PR, check Render `/version`, and run the journeys suite against main. No republish is needed: Phase 2 changes no page.
- Not started overnight: website copy/edge-case improvements (dev only), manager queue additions (unassigned inquiries, time to first action), and residential→commercial training flags (needs a schema change, so coordinate with Lane D).
