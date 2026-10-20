# Atlantis Light & Power — overnight handoff (2026-10-20)

Lane C · tracking #237 · rules #239 and `C:\Dev\lanes\LANES.md`. This is the durable, resumable record of the overnight V4 run.

## 1. Production (not changed by this session)
- Production main is now `29b2d4b1`: another lane's emergency fix #245 (EvoSense tax roll), merged after Phase 1. Atlantis Phase 1 `aee2e142` (PR #241) is an ancestor of it, and #245 changes no Atlantis file. `/health` and `/version` return 200 for 29b2d4b (checked 00:57 CT).
- Live page `https://api.evosyspro.live/site/atlantis-light-and-power`: 200, md5 `e571d8131231b6c441044bc5ec9b2961`, identical to the Phase 1 publish. It was not republished.
- The live synthetic lead "Release Check" (`atlantis-release-check-20261019@example.com`, id `011692da-…`) is untouched. It predates test-record flagging and is NOT `is_test` (owner decides).
- PR #116 untouched (OPEN, `fb4b7399`). Chariot transactions OFF. No outreach, supplier action, DNS, WordPress, appointment or payment change.

## 2. Branch and PR
`feature/atlantis-phase2-ops` (from `aee2e142`) → **draft PR #246**, review only, awaiting Mike's GO. It merges cleanly onto current main (`git merge-tree` exit 0).

| Commit | What |
|---|---|
| 3ef90483 | Supplier hand-off: one open per supplier, outcomes (never enroll); reserved test addresses filed as `is_test` |
| 9dd07db8 | Qualification view: known / missing / next step / eligible suppliers; manual bill lines; one missing-details task |
| 4bb16dd9 | Move Concierge: complete only when all ticked; cancel clears move tasks; date change surfaces late tasks; re-sent form never unticks |
| a7bceaec | Commercial: advisor-led stages, forward-only tier, won only after supplier confirmation; lost/deferred with reason/history; multi-site |
| b023ac62 | Field acquisition: door outcomes, referral only with contact + permission, credited to rep, per-rep summary |
| 3ccf7619 | Review #1 fixes (scope, locking, move integrity, data repair) |
| a67cdb01 | Docs: external readiness, sales program, this handoff |
| f01743fd | Manager intake view `GET /energy-ops/intake-health` |
| b5042f9d | Staff screens: Field visits, New requests panel, hand-offs waiting for an outcome |
| 7cca2a86 | Review #2 fixes + 16-journey acceptance suite |

## 3. What was built
Backend endpoints, all under `/energy-ops` and tenant-scoped through `lead_scope`:
- `GET /suppliers`
- `POST /leads/{id}/handoff`, `GET /leads/{id}/handoffs`
- `POST /leads/{id}/handoffs/{hid}/outcome`
- `GET /supplier-handoffs`
- `GET|POST /leads/{id}/qualification`, `POST …/qualification/request-info`
- `GET /leads/{id}/commercial`, `POST …/commercial/stage`, `POST|PATCH …/commercial/locations`
- `POST /field/visits`, `GET /field/summary`
- `GET /intake-health` (managers)
- Behaviour changes on `PATCH /moves/{id}` and `POST /moves/{id}/checklist`

Screens. These need no new routes; `App.jsx`, `Layout.jsx` and `api/client.js` are untouched:
- **Tasks & Follow-Up:** the "Field visits" view (`?view=field`), plus a manager "New requests" panel.
- **Suppliers:** "Hand-offs waiting for an outcome", with an outcome form.

There are **no screens yet** for the per-lead qualification and commercial views. Those belong on the lead detail page, which is shared across lanes, so they need coordination.

No schema changes or migrations. Data lives in `Lead.custom_fields`, existing tables and audit entries.

## 4. Independent reviews
- **Review 1:** 1 high, several medium. All fixed in `3ccf7619` with tests.
  - High: a field rep could change or learn leads outside their scope.
  - Also fixed: apartment duplicates, case-sensitive email, move task scope and completion order, unlocked custom-field writes, `_lock` swallowing errors and dropping unflushed edits, meter id dropped on a repeated site, stale test flag, junk `supplier_handoffs`.
- **Review 2:** 0 high, 2 medium, 6 low; every review-1 fix confirmed. All fixed in `7cca2a86` except one:
  - Fixed medium: forwarded tasks counted as referrals; the field note was dropped.
  - Fixed low: a finished move's services could change; closed statuses counted as unassigned; cancelled tasks in the denominator; "null" owner; double fetch; sticky error; form not reset; ARIA.
  - **Not fixed (other lane):** `app/services/public_capture.py` `_parse_when` strips the timezone from a client `submitted_at` without converting to UTC, so a lead's `created_at` can be off by the visitor's UTC offset. This is shared intake code, so it's for Lane D. Effect on Atlantis: the manager panel's "waiting" ages for website requests may be off by a few hours.

## 5. Tests (from `C:\Dev\lanes\atlantis`)
```
python -m pytest -q -p no:cacheprovider -W ignore -rf tests/test_atlantis_*.py tests/test_commercial_atlantis.py tests/test_customer_sites.py tests/test_lead_capacity_matrix.py tests/test_plan_limits_coverage.py
```
- The browser tests need Chromium; `test_atlantis_ops_browser.py` also needs `npm run build` in `frontend/` first.
- Expected: everything passes except 2 guard tests (`test_lead_capacity_matrix`, `test_plan_limits_coverage`). Those fail only on SCI Lane A files (`services/programs/setter_eval.py`, `services/sci_staging_demo.py`), and the same failures exist on main.
- The frontend build passes.

## 6. Owner decisions (detail in `READINESS_EXTERNAL.md` and `SALES_PROGRAM.md`)
1. GO / NO-GO on PR #246.
2. Production default advisor (unset; admin fallback works).
3. Domain cutover for atlantislightandpower.com (GoDaddy DNS; no MX/SPF/DMARC).
4. Sender and inbox: the inbound mailbox feature is Microsoft 365 only, while atlantis-enterprises.com uses Google Workspace.
5. ComparePower referral URL (`COMPAREPOWER_AFFILIATE_URL` on the server).
6. Supplier wording and eligibility confirmation.
7. Compensation terms (structure documented, no rates).
8. Whether to flag the Release Check lead as a test record.

## 7. Rollout and rollback
- **Rollout after GO:**
  1. Merge PR #246 only.
  2. Wait for the Render deploy and check `/version`.
  3. Run the acceptance suite against the merged main locally.
  4. Open Suppliers and Tasks & Follow-Up as an admin.
  No republish is needed (no page change). No migration.
- **Rollback:** revert the merge commit (code only). The new `custom_fields` keys are ignored by older code.

## 8. Next steps for whoever resumes
1. On GO, do the rollout above.
2. Add the qualification and commercial panels to the lead detail page. This touches a shared page, so coordinate on #239 first.
3. Hand the `public_capture` timezone finding to Lane D.
4. Residential→commercial progression flags need a schema change. The path is documented in `SALES_PROGRAM.md`; coordinate with Lane D.
5. Website copy/edge-case improvements (dev only) were not started.
