# EvoSense free data engine – handoff (Oct 10–11, 2026)

Master execution order: "EvoSys Pro / EvoSense – Free Data Engine, Wholesale Automation & Acquisition Improvements".
Budget: $0. Nothing paid was bought, switched on or called.

## PRs (all merged to main, deployed to Render)

| PR | What |
|---|---|
| #38 | Collin CAD records (data.texas.gov Socrata API) + durable copy of uploaded county files (TAD) |
| #39 | Houses-only option, 6 ready-made starter searches, Denton County records (county GIS) |
| #40 | Edit beds/baths/size/year on the acquisition page; automatic Tracerfy lookups for 65+ |
| #41 | RentCast value estimates behind a hard free budget, free county tax-value figure, PropStream CSV compatibility, this handoff |

## Sources – what is real and verified

| Source | Key | Access | Verified live | Notes |
|---|---|---|---|---|
| Dallas CAD certified export | `dcad` | Bulk zip, cached | HEALTHY | owner, situs, values, CDU, beds/baths |
| Tarrant Appraisal District | `tad` | File uploaded from Mike's PC (TAD refuses cloud IPs) | HEALTHY | **Now survives deploys** (see below) |
| Tarrant tax roll | `tarrant_tax_roll` | Byte-range zip | HEALTHY | delinquency |
| Linebarger tax-sale list | `lgbs_tax_sales` | Public JSON | HEALTHY | 9 DFW counties; Collin has 1 listing, Denton 0 |
| Dallas 311 code | `dallas_311_code` | Socrata | HEALTHY | |
| Fort Worth code | `fw_code_violations` | ArcGIS | HEALTHY | |
| **Collin CAD roll** | `collin_cad` | data.texas.gov dataset `5tkr-3759` (2026) via SoQL | HEALTHY, 502,479 rows | owner+mailing, situs, category, values (preliminary/certified label kept), deed date, year built, living area. **No beds/baths published.** Enabled for EVO Integrated Solutions. |
| **Denton County GIS** | `denton_gis` | gis.dentoncounty.gov `CAD/MapServer/0` ArcGIS query | HEALTHY, 384,308 rows | owner+mailing, situs, state code, certified value, homestead, year built, living area. **Deed YEAR only** (from instrument number). No beds/baths. Denton CAD's own website says not for bulk use – never touched. Enabled for EVO. |
| Census geocoder | `census_geocoder` | Public API | HEALTHY | |
| Tracerfy owner lookup | `tracerfy_contact` | Vendor API, paid per find | registered, **disabled** | same provider as the Get phones & emails button |
| RentCast AVM | (value_lookup) | Vendor API, free plan | **not connected** – `RENTCAST_API_KEY` not set | hard local budget 40/month (never > 45) |

### Real-data proof runs (local SQLite copy, real government rows, EvoSense's own ingest + scoring)
- Collin: 1,000 real rows -> 999 properties, 929 owners, 876 absentee, 180 out-of-state, 999 long ownership; re-run -> 0 created, 1,000 seen (no duplicates).
- Denton: 278 real rows (of 33,984 matching) -> 278 properties, 248 owners, 239 absentee, 34 out-of-state; re-run -> 0 duplicates.
- Scores from owner records alone top out ~33 (absentee 15 + long ownership 10 + out-of-state 8). They climb only when tax / code / condition / vacancy signals stack.
- Production will ingest Collin/Denton on the next scheduled hunt of Mike's "New Check Out" (all-Texas, daily; next ~2026-10-11 21:59 UTC). Claude cannot start hunts.

## Tarrant file persistence (root cause fixed)
- Cause: uploads lived on Render's ephemeral disk; every deploy wiped them.
- Fix (#38): `providers.accept_upload` also stores the file in `evosense_source_files` (4 MB chunks, sha256). `sources/base.local_override` / `uploaded_info` call a restore hook when the disk copy is missing; restore checks chunk count + sha256, retries at most every 10 minutes after a miss.
- **Proven on production:** after the #39 deploy, `/sources/tad/upload` showed `restored_at 2026-10-11T03:54:24Z` with no re-send. The `tad_feed.ps1` re-send after deploys is no longer needed (the weekly refresh still is, to pick up TAD's new file).

## Features
- **Houses only** (`evosense_strategies.houses_only`): `scoring.land_only_reason` – land/commercial type, or documented $0 improvements with no living area / year built -> score 0 "excluded" (still visible). Missing improvement value is NOT treated as vacant.
- **Starters** (`strategy.STARTERS`, `GET/POST /wholesale/evosense/strategy-starters` body `{key}`): behind_on_taxes, tired_landlord, problem_property, long_time_owner, pre_foreclosure, probate. Drafts only; houses only; 9 DFW counties; $0 budget; no outreach. "New Check Out" verified unchanged by test.
- **Building facts editing** (`evosense_properties.user_facts`, `PATCH /properties/{id}/facts`): rank HUMAN, county value remembered, refresh never overwrites (ingest skips + updates remembered county value), null restores county value, event logged, promotion copies working values + provenance note.
- **Automatic Tracerfy** (`providers.TracerfyContactSource`, `enrichment.tracerfy_gate/record_wholesale_lookup`): 65+ only, Wholesale Settings caps (0 = off) checked per lookup, each lookup recorded as a WholesaleEnrichmentRequest (counts against caps), miss settles $0, DNC numbers dropped by `wholesale_enrichment.TracerfyProvider`. Also still bound by the strategy's EvoSense budget (all strategies $0 today) – so two locks.
- **Value estimates** (`value_lookup.py`, `sources/rentcast.py`, `evosense_api_usage`, `evosense_properties.valuation_detail`): atomic reserve BEFORE each request; cache 30 days; comps labelled "Listed comparable (asking price)" / "listing removed – sale price not confirmed" / "Rental comparable"; never written as comparable SALES, never an ARV input. Hunt values up to 5 properties ≥65 per run when connected. Free fallback: ZIP median tax value per sq ft × living area (≥5 houses), labelled as assessment-based.
- **PropStream CSV** (`propstream_csv.py`): loose header aliases, yes/status flags -> signals (VACANT, PRE_FORECLOSURE, PROBATE, ESTATE, TAX_DELINQUENT, LIEN, FREE_AND_CLEAR, TIRED_LANDLORD) labelled "as stated by a PropStream export". `POST /wholesale/evosense/import/preview` writes nothing and reports format, mapping, rejects, duplicates, signals. Wholesale Properties > Import list also understands PropStream headers. **Tested only with a synthetic fixture** – not verified against a real PropStream export.

## Provider Control Center (live, after #40)
Operational: OWNER_IDENTITY, PROPERTY_FACTS, TAX, CODE_VIOLATION, GEOCODING.
Manual only: PHONE, EMAIL, LINE_TYPE, FORECLOSURE, LISTING_HISTORY, VALUATION.
Not configured: PHONE_VALIDATION, EMAIL_VALIDATION, DEED, LIEN, MORTGAGE, SOLD_COMPS, IMAGERY.
(PHONE/EMAIL become operational when Tracerfy has credit, limits > 0, and `tracerfy_contact` is enabled; VALUATION when the RentCast key is added.)

## Live data (production, before the next hunt)
665 properties (Dallas 388, Tarrant 270, Parker 7); scored 45–64: 49, under 45: 616, 65+: 0.
Single-family typed 353; documented $0-building (land-only) 90; with appraisal value 640.
Signals: tax delinquent 405, tax suit 165, CDU poor 222, CDU undesirable 28, code complaint 11, code violation 7, absentee 254, long ownership 337, out-of-state 10.

## Blockers / limits (honest)
- **Data access:** no free bulk source found for foreclosure notices, probate filings, mortgages/equity, vacancy or sold prices in DFW. Pre-foreclosure / probate starters run on imported lists.
- **Redfin Data Center:** terms restrict MLS data to personal/non-commercial use and bar automated access; permission for ingestion into a commercial app could not be confirmed – **not ingested**.
- **Denton:** no deed date (year only); no beds/baths. **Collin:** no beds/baths.
- **Credentials (Mike):** Tracerfy credit (0), Wholesale Settings paid-lookup limits (0/0), `RENTCAST_API_KEY` (free sign-up), enabling `tracerfy_contact` and a strategy budget if he wants automatic paid lookups.
- **Not done on purpose:** VA workflow (Task 9, on hold), Harris/Bexar/Travis (Task 12, on request), Mike's "New Check Out" left unchanged (ask before switching it to houses only).

## Zero-dollar audit
New paid charges: $0. Paid external calls attempted: 0 (Tracerfy limits 0, RentCast key absent). Free API usage: data.texas.gov / Denton GIS / LGBS – public, no quota consumed that bills. Infrastructure: one new small table set in the existing Postgres (~50 MB for the TAD copy).

## Next actions
1. Mike: decide whether "New Check Out" should be houses only, or switch on the Tired Landlord / Behind on Taxes starters.
2. Mike (money): Tracerfy credit + limits; RentCast free key.
3. After the next scheduled hunt: check Collin/Denton counts in the Discovery Inbox and the source cards' record counts.
4. Optional: county records for Ellis/Kaufman/Rockwall/Parker/Johnson (same pattern), then Harris/Bexar/Travis on request.
