# Wholesale buyer-selection state consistency — 2026-10-07

## Defect
`select-buyer` cleared `is_selected` on the other outreach rows but left their
`status == "selected"`, so a replaced buyer still read as chosen on the board and
in `responded_statuses` counters. `record_response` / `PATCH /outreach/{id}` could
also write `selected` directly (second "selected" buyer) or overwrite the current
selection's status (flag and status disagreeing).

## Fix (source-only, no migration, no new status)
Pure rules in `app/services/wholesale_selection.py`; router wiring in
`wholesale_buyers_router.py`.
- Replaced buyer returns to an EXISTING status: `offer_submitted` if they priced
  the deal, else `interested`. Audit: `buyer.selection_replaced` event per
  replacement with before/after and the replacing outreach id.
- Re-selecting the current buyer is an idempotent no-op (`already_selected: true`),
  no second event, `buyer_selected_at` untouched.
- Replaced rows are fetched tenant-scoped and `with_for_update()`.
- `selected` cannot be written through response/update (`select_via_select_buyer`).
- Delivery/reply statuses cannot overwrite the current selection
  (`selected_row_is_locked`). Offer writes keep `selected`.
- Selected buyer who passes/rejects releases the selection (flag + deal
  assigned buyer) with `buyer.selection_released`.
- Refusals are `"<label> [<code>]"`: buyer_opted_out, buyer_inactive,
  buyer_passed, select_via_select_buyer, selected_row_is_locked. DNC/inactive
  stay first; no provider is touched by any of these paths.

## Concurrent buyer-import race — design only
Two simultaneous imports of the same CSV can both pass the in-memory index check
and insert duplicates. A DB unique constraint on (organization_id, lower(email))
/ normalised phone is the real fix, but existing-data compatibility (duplicates
may already exist) and rollback cannot be proven without production access, so
NO migration was added and `app/migrate_add_import_tables.py` was not touched.
Source-only alternatives (advisory lock per org, serialisable retry) are
dialect-specific and unprovable here. GATED preflight needed before a migration:
1. read-only query for existing duplicate (org, normalised email) and
   (org, normalised phone) groups in the target DB;
2. a reviewed merge/cleanup plan for those groups (owner approval);
3. then a partial unique index created CONCURRENTLY, with `DROP INDEX` rollback.

## Proof level
- Executed here: `scripts/wholesale_selection_state_proof.py` (850 assertions,
  real pure module + source wiring).
- NOT executed here (needs provisioned runner): `tests/test_wholesale_selection_state.py`
  (py_compile only).
