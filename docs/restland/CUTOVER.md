# Restland cutover runbook (Issue #507)

The cutover is **additive only**. It adds rows (program, location, profile, review bucket, one inactive campaign family, one link per existing family) and changes **no** existing row. Nothing is switched on and nothing is sent.

Restland org id: `01fed629-9a95-412b-895a-c8b09f37a98c` (slug `restland`). Production database: `advisorflow-db` (`dpg-d8rslocvikkc738v7of0-a`).

## Order

1. **Approval.** The redesign PR must be approved with the exact phrase, then merged. Merging to main auto-deploys the backend (pre-deploy `python -m app.migrate`) and the frontend.
2. **Verify the deploy.** Both Render services must be `live` on the merge commit. `GET /health` returns the commit. SCI must still open (`/program/live` as an SCI member).
   - Until the cutover runs, Restland has no program record, so nothing changes for it.
   - The new code paths only act on an org that has a program.
3. **Preflight (dry run, writes nothing)**, in a Render Shell on `advisorflow-backend`:
   ```
   python scripts/single_location_program_cutover.py --org-id 01fed629-9a95-412b-895a-c8b09f37a98c
   ```
   Expect `"problems": []` and `baseline.leads` ≈ 10,576. Save the output.
4. **Backup.** Do both:
   - **Database-level:** confirm Render point-in-time recovery is available for `advisorflow-db` (paid plan) and note the exact timestamp just before step 5. If PITR isn't available on the plan, take a manual export from the Render dashboard (Recovery → Export) before going on.
   - **Scoped artifact:** the preflight output (step 3) plus the run summary the script stores (`program_import_runs.summary_json`). Together they list every id that was created.
5. **Apply:**
   ```
   python scripts/single_location_program_cutover.py --org-id 01fed629-9a95-412b-895a-c8b09f37a98c --apply \
       --hero-title "Restland" --contact-name "Mike Simmons"
   ```
   - One transaction. About 10.6k inserts in batches of 1,000. No locks on existing rows (insert-only).
   - Expected under a minute. No downtime. No job needs pausing:
     - the old cadence job keeps skipping (texts off);
     - the program loops find nothing to do (all families inactive, daily batch 0, AI setter off, alerts off).
   - Save the printed `run_id`.
6. **Reconcile:**
   ```
   python scripts/single_location_program_cutover.py --org-id 01fed629-9a95-412b-895a-c8b09f37a98c --reconcile
   ```
   Required:
   - `ok: true`
   - `linked_leads == leads`
   - `duplicate_links`, `orphan_records`, `cross_tenant_links`, `records_with_campaign` and `active_campaign_families` all `0`
7. **Smoke test (no sends).**
   - Sign in as wholesale@ (an org admin of Restland). Choose Your Business → Restland → `/program/live` reads "Restland Live / Restland Cemetery and Funeral Home".
   - Program Overview total equals the reconciled lead count.
   - Families search finds a known family.
   - Launch Readiness shows the Restland checklist.
   - Repeat for SCI: unchanged.
8. **State restoration.** Nothing was paused, so nothing needs restoring.

## Rollback

Triggers:

- reconciliation not `ok`;
- SCI regression;
- Restland screens failing for its staff;
- any send attempt observed for Restland.

```
python scripts/single_location_program_cutover.py --org-id 01fed629-9a95-412b-895a-c8b09f37a98c --rollback <run_id>
```

Rollback deletes exactly the rows that run created and nothing else.

It **refuses** if any linked record has since been worked:

- approved,
- held,
- given a campaign,
- had its journey started,

or if the cutover campaign was switched on. That's post-cutover activity, and it gets reconciled by a person, not discarded.

Leads, history, conversations, appointments and suppression were never touched, so there's nothing in them to restore.

If the code itself must be reverted, revert the merge commit. Without a program record Restland returns to the general platform exactly as before.

## What stays a launch dependency (not part of this cutover)

- Toll-free number for Restland: assign and verify it. Don't buy anything as part of the redesign.
- Inbound mailbox for email replies.
- Booking calendar user. Staff alert recipients.
- Which families go into which campaign, and the owner's GO. Then the owner turns on the workspace text switch.
