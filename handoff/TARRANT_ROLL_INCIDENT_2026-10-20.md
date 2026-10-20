# Tarrant tax-roll misread: targeted cleanup runbook (updated 2026-10-20 ~12:00 CT)

## Status
- **Parser fix: merged and live.** #245 was merged as 29b2d4b (code ed35988) at 00:03 CT on Oct 20. Since then
  production has read 339 tax-roll records with `tarrant_tax_roll/3`. Do not merge #245 again, and do not pause
  the source.
- **Cleanup of what the old reader stored: NOT done.** It needs this PR merged, then Mike's go-ahead for the
  production apply. Nothing in production has been cleaned up.

## What still needs cleaning (production, read-only, ~11:00 CT)
| | |
|---|---|
| Misread observations (`tarrant_tax_roll/2`, raw line ≥ 770 characters) | **1,017**, in 1 workspace, observed 2026-10-17 12:53 → 10-19 14:06 UTC |
| Properties | **1,017** (one observation each) |
| Active signals from those observations | **2,034** (none retracted yet) |
| Re-read since the fix was deployed | **0** (no misread record has been seen again) |
| `tarrant_tax_roll/3` observations on these properties | **0** |
| Contact points in the workspace | **0** |

## The tool (this PR): `app/services/evosense/roll_layout_fix.py`
**Scope.** The tool reads its targets from the evidence, not from a property list:

> provider `tarrant_tax_roll`, adapter `tarrant_tax_roll/2`, stored raw line ≥ 770 characters.

Everything else in the workspace is out of scope. After an apply those observations record `tarrant_tax_roll/3`,
so a second run finds nothing to do. The `roll_layout_fix/1` run record keeps the exact list of what was repaired.

| Step | Route (admin only) | Writes |
|---|---|---|
| Dry run | `POST /wholesale/evosense/roll-fix/dry-run` | nothing: runs inside a savepoint that is rolled back; refused (409) while a hunt is running |
| Apply | `POST /wholesale/evosense/roll-fix/apply` `{"confirm": "APPLY ROLL FIX <code>"}` | yes: row-locks the targets, keeps a full before-image, logs `roll_fix.applied`; refused while a hunt is running or if the code doesn't match the current data |
| Undo | `POST /wholesale/evosense/roll-fix/<run_id>/rollback` `{"confirm": "ROLLBACK <run_id>"}` | restores every before-value; refused if any of those rows changed after the apply |

The ordinary re-derive tool (`reprocess.py`, which processes the whole workspace) is **not** used, and it cannot
roll back this run.

## Evidence: a copy of production taken today, repaired for real, then rolled back
Every affected property, signal, score and observation was copied, plus every owner, ownership and person in the
workspace (read-only, ~11:00 CT). On that copy:
- the dry run was run (23 s locally; it wrote nothing — every row was compared);
- the real apply was run;
- every changed row was classified;
- the undo was run.

**Dry run / apply totals** (identical on 2026-10-20 00:17 and ~11:00 CT):
- 2,022 false signals retracted (1,005 TAX_DELINQUENT, 1,017 TAX_SUIT).
- 12 real delinquencies kept, with corrected wording.
- 116 signals from other sources on these properties preserved — **all 116 byte-identical after the apply**.
- Owners: 992 links moved to the identical owner already on file, 18 owners corrected, 1 shared owner left alone.
- Properties at 45+: 66 → 0 (65 from 45–54, 1 from 55–64).
- Plan code on the copy: **`APPLY ROLL FIX ab593b9808557955`**.

**Every row the apply changed, by reason. 0 rows changed outside the rules below.**

| Table | Rows | What changed |
|---|---|---|
| signals | 2,034 | the misread observation's tax signals: 2,022 retracted, 12 rewritten |
| signals | 746 changed + 25 new | derived signals on the same properties (absentee, out-of-state, long ownership) recomputed from the corrected owner; 14 of them retracted |
| observations | 1,017 | stored record and adapter version only. **The raw county line is unchanged.** |
| ownerships (misread links) | 990 / 2 | `owner_id` moved to the existing identical owner / set not current where a same-source twin already existed |
| ownerships (other sources' links) | 994 | `in_conflict` true → false only (the false owner conflict is settled). **No owner changed on any other source's link.** |
| owners | 18 | name and mailing fields of owners created only by a misread record |
| persons | 4 replaced | people derived from those 18 owners' names; a person with a lead, phone, email or conversation is never removed |
| properties | 1,017 | score, status, next action, contactability, conflicts and signal count only. **No address, parcel or building field changed.** |
| scores | 2,029 | new score rows; previous rows marked not current |
| contact points | 0 | none |

Not touched by the apply:
- every row belonging to any other property;
- any other workspace (covered by `test_apply_leaves_every_out_of_scope_row_byte_identical_and_other_workspaces_alone`).

**Undo on the copy:** every row in all 8 tables was restored exactly; 0 rows differed afterwards.

## Production procedure (each write step needs Mike's go-ahead)
0. **Merge this PR and let Render deploy it.** Check `/health`, and that no new errors appear in the backend logs.
   - The other steps are separate and come later.
1. **Pick a quiet window.** Hunts run 13:34–14:30 UTC (8:34–9:30 AM CT); both routes refuse while one is running.
2. **Run the dry run** (an admin in the EVO workspace): `POST /wholesale/evosense/roll-fix/dry-run`.
   - It writes nothing. Save the JSON response.
3. **Compare with this document.**
   - `scope`: 1,017 / 1,017.
   - `totals` match the table above.
   - `confirm_with` = `APPLY ROLL FIX ab593b9808557955`, if no data has changed since ~11:00 CT.
   - A different code means the data moved. Read `properties[*]` for the difference before going further.
4. **Mike's go-ahead, then apply:** `POST /wholesale/evosense/roll-fix/apply` with the exact `confirm_with` text.
   - Save the returned `run_id`.
5. **Verify** with the read-only SQL below, and spot-check 5 properties in EvoSense.
6. **Decide on undo now, before anything else touches these rows.**
   - Undo works only while the repaired rows are untouched. The next hunt that re-reads one of these properties
     ends the undo window.

### Verification SQL (read-only)
The repaired observations are the Oct 17–19 tax-roll records. The corrected reader's own records start
Oct 20 13:56 UTC, so the date window below separates the two.
```sql
-- R = the repaired observations
with r as (select id, property_id from evosense_observations
            where provider_key='tarrant_tax_roll' and length(raw_payload)>=770
              and observed_at between '2026-10-17 12:53' and '2026-10-19 14:07')
select
 (select count(*) from r) as repaired_obs,                                                   -- expect 1017
 (select count(*) from evosense_observations where provider_key='tarrant_tax_roll'
   and adapter_version='tarrant_tax_roll/2' and length(raw_payload)>=770) as still_in_scope, -- expect 0
 (select count(*) from evosense_signals s join r on s.observation_id=r.id where s.active) as kept,        -- expect 12
 (select count(*) from evosense_signals s join r on s.observation_id=r.id where not s.active) as retracted, -- expect 2022
 (select count(*) from evosense_properties p where p.id in (select property_id from r)
   and p.opportunity_score>=45) as at_45_plus,                                                -- expect 0
 (select count(*) from evosense_signals s where s.active and s.source<>'evosense_derived'
   and s.property_id in (select property_id from r)
   and (s.observation_id is null or s.observation_id not in (select id from r))) as other_evidence; -- expect 116 (+ anything a later hunt added)
select id, status, property_count, applied_at from evosense_reprocess_runs where rule_version='roll_layout_fix/1';
```
The same query run on production **before** the cleanup (~12:00 CT today) returned: repaired_obs 1017,
still_in_scope 1017, kept 2034, retracted 0, at_45_plus 66, other_evidence 116.

## After the repair
- About 147 open work items sit on these properties. Most were raised from the false signals; review them
  (or let the sweep's normal rules close them).
