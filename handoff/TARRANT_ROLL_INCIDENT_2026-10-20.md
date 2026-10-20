# Tarrant tax-roll misread — incident runbook (opened 2026-10-19 23:40 CT)

PR **#245** (`feature/wholesale-tarrant-roll-layout`, from main `aee2e14`). Separate from #242.
Nothing in production has been changed. Every step below that writes needs Mike's GO.

## What happened
- The Tarrant County tax roll published **2026-10-16** widened owner-name fields 30→50 characters; all later fields moved 40 characters right; lines 741→781.
- Our reader (adapter `tarrant_tax_roll/2`) kept the old offsets and had no layout check.
- From **2026-10-17 12:53 UTC**, every daily hunt that read the roll created properties from invented evidence: land value read as "prior-year taxes due", date fragments read as a "tax suit".
- Verified (production SELECTs): **1,017 properties, 2,034 active bad signals, 66 at 45+**. Outreach, lookups and spend were all off: nothing was sent or charged.

## The fix (#245)
- Per-line layout choice with validation; unreadable layouts stop the source instead of inventing data.
- No tax signal for a row that is not delinquent as of the roll's date.
- Adapter `tarrant_tax_roll/3`.

## Targeted repair (not the general re-derive)
`app/services/evosense/roll_layout_fix.py` — routes (admin):
- `POST /wholesale/evosense/roll-fix/dry-run` — writes nothing (savepoint, rolled back); returns scope, totals, per-property detail, `confirm_with`.
- `POST /wholesale/evosense/roll-fix/apply` `{"confirm": "APPLY ROLL FIX <code>"}`
- `POST /wholesale/evosense/roll-fix/{run_id}/rollback` `{"confirm": "ROLLBACK <run_id>"}`

Scope = tax-roll observations stored by `tarrant_tax_roll/2` with a raw line of 770+ characters.

### Dry run on a read-only copy of the affected production rows (00:17 CT)
| | |
|---|---|
| Scope | 1,017 observations / 1,017 properties |
| Retracted | 2,022 (1,005 TAX_DELINQUENT, 1,017 TAX_SUIT) |
| Kept, wording corrected | 12 (truly delinquent) |
| Preserved, other sources/records | 116 |
| Owners: re-linked to the existing identical owner / renamed / left alone | 992 / 18 / 1 (owner conflict settled on all but 16 properties) |
| At 45+ before → after | 66 → 0 (max after 39) |

## If #245 cannot be deployed before the next hunt
Next scheduled hunts (UTC): New Check Out 13:34, Behind on Taxes 13:50, Tired Landlord 14:12 (= 8:34–9:12 AM CT).
**Tarrant-roll-only pause** — no code needed, works on today's production:
- UI: EvoSense › Controls › Sources › *Tarrant County tax roll* → off, or
- API (admin): `PATCH /wholesale/evosense/providers` `{"key": "tarrant_tax_roll", "enabled": false}`
- Effect (tested in `test_tarrant_roll_only_pause_leaves_every_other_source_and_search_running`): only that source stops; every other source keeps running; no search is edited (New Check Out untouched).
- Undo: same call with `"enabled": true` (after #245 is live).

## Production order (each step: Mike's GO)
1. (optional, if before 8:30 AM CT and #245 not yet live) Tarrant-roll-only pause.
2. Merge #245 → Render deploys the backend → check `/health`, no new errors in logs.
3. `POST .../roll-fix/dry-run` on production → compare with the numbers above (new misread records from any hunt before deploy will add to the scope).
4. `POST .../roll-fix/apply` with the code from step 3 → spot-check 5 properties.
5. Re-enable the Tarrant roll source if it was paused; watch the next hunt's stats (`layout_2026-10` count, no `unreadable_line` spike).
Rollback after 4: step 4's `run_id` → rollback route. It restores every row exactly (verified on the copy) but
**refuses once anything has touched those rows since** (the next hunt re-seeing a record, a rescore, a person's
edit) — so decide on rollback before re-enabling hunts / the source.

Contact points on the owners involved: 0 in production (checked); a person with a phone/email or a
conversation is never removed by the repair (review finding, fixed and tested).
