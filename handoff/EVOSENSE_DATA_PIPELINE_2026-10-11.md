# EvoSense data pipeline: handoff (Oct 11, 2026, morning)

Follows `EVOSENSE_FREE_DATA_ENGINE_2026-10-10.md`. Budget: $0. Nothing paid was bought, switched on or called. Tracerfy: 0 paid requests.

## PRs today (merged, deployed, verified live)

| PR | What |
|---|---|
| #42 | Daily discovery rotates (reaches new accounts each day). Collin and Denton skip parcels with no street number. |
| #43 | **Dallas County tax roll (`dallas_trw`)** and **Fort Worth open code cases (`fw_open_cases`)** as free discovery sources. TAD lookup by exact situs address. |
| #44 | Ready-made searches read only the sources that fit them (`provider_preferences.discovery`). |
| #45 | Dallas tax-roll evidence date fix (a prior-year balance still owed counts as current, not "aging"). Raw evidence keeps the per-account sum so a reprocess can rebuild it. |

## New sources (live-verified from Render)

| Source | Key | Access | Pool |
|---|---|---|---|
| Dallas County Tax Office TRW file | `dallas_trw` | Weekly zip on dallascounty.org (published free; robots allow), HTTP byte ranges, about 60 MB read of 293 MB, about 33 s | 11,941 delinquent residential accounts of $250+ to the county unit (file of Oct 5). 1,865 over-65/disabled deferrals, 141 bankruptcies and 3,071 under $250 are skipped. |
| Fort Worth open code cases | `fw_open_cases` | City ArcGIS table (public) | 5,239 open cases: Substandard Building, Property Maintenance, High Grass/Weeds, Health Hazard |

Both are enabled for EVO Integrated Solutions LLC; `sources/verify` returned ok for each.

## Searches (strategies)

| Search | Status | Discovery focus | Next run (scheduler, 15-min tick) |
|---|---|---|---|
| New Check Out | active, unchanged | none (every source) | 2026-10-12 11:48 UTC (Mike's manual run at 11:43 UTC reset it) |
| Behind on Taxes (Mike's) | active, houses only | TAX | 2026-10-12 11:52 UTC |
| Tired Landlord | active, houses only | collin_cad, denton_gis, TAX | 2026-10-12 12:32 UTC (first ran 2026-10-11 12:27-12:32 UTC) |
| Behind on Taxes (2) | archived (a duplicate Claude created) | | |

## Live pipeline counts (2026-10-11 ~12:35 UTC)
- Workspace: 1,365 properties (Dallas 591, Tarrant 370, Denton 207, Collin 190, Parker 7). All visible in the inbox.
- Scores: 45-64: 52; under 45: 1,313; 65+: 0. Best: 61.
- Tired Landlord first run: observed 500 (100 per source), created 100 (Dallas tax roll), seen 400, scored 297, 45+: 2. DCAD lookup 105/106. Dallas 311 found 21. Houses-only exclusions 0.
- Behind on Taxes (11:48 UTC, before #44): created 200, all from Collin and Denton owner records, all low. This is the bug #44 fixed.

## Why nothing reaches 65+ yet
The free signals (tax delinquent 12, tax suit 8, absentee 15, out-of-state 8, long ownership 10, code violation 8, distressed 8, CDU poor 4) stack to the 50s-60s. The large items (VACANT 18, PRE_FORECLOSURE 16, equity up to 14) have no free automated property-level source in DFW today. Two options for Mike: (a) leave the scale and treat 55+ as the call list; (b) change workspace score weights (EvoSense > Controls page, score weights). Claude did not change weights.

## Pending for Mike
1. **Reprocess (optional):** fixes the date on the 100 Dallas tax-roll houses ingested before #45. Steps: EvoSense > Controls page, the re-derive section: dry run, review, then apply (apply asks you to type the run id).
2. **Denton County tax roll:** apps.dentoncounty.gov/website/taxfiles serves the weekly Tax Code 1.10 file to a real browser but refuses scripted downloads. Claude did not get around that. If Mike approves, it can be downloaded weekly by hand and uploaded like the TAD file.

## Data-gap list (verified, Oct 11)

| Need | Free automated source? | What was checked |
|---|---|---|
| Foreclosure notices (all 4 counties) | **No** | Clerk records are on `*.tx.publicsearch.us`; robots.txt disallows everything. Dallas County PDF postings stopped after Feb 24, 2026. |
| Probate | **No** | Dallas courts portal (Odyssey) uses reCAPTCHA. District clerk civil index is a paid subscription. |
| Deeds, mortgages, liens | **No** | Same publicsearch clerk systems. |
| Tax delinquency | **Yes** | Tarrant roll, Dallas TRW, Linebarger tax-sale lists. Denton: manual weekly only. Collin: none found. |
| Code / condition | **Partly** | Fort Worth open cases (live). Dallas 311 (complaints only; no case types for vacancy). DCAD CDU rating. |
| Vacancy (property level) | **No** | No public utility-shutoff or USPS data at property level. High grass/weeds is an indicator only. |
| Permits | Partly | Collin CAD permits (data.texas.gov 82ee-gbj5, current, 114,461 rows; mostly a sign of investment, not distress). Fort Worth permits current. Dallas open-data permits stale (2019-2020). |
| Rental registration (Dallas) | Possible | 311 "Single Family Rental Needs Registration" (2,327 in 12 months). Not built: needs an address-to-DCAD match. |
| Beds/baths | Partly | DCAD and TAD yes; Collin and Denton publish none. |

Lawful alternatives for the "No" rows: Texas Public Information Act requests to each county clerk for bulk foreclosure-notice / probate filing lists, or a person exporting from publicsearch and importing through Properties > Import list (foreclosure and probate list kinds already exist).
