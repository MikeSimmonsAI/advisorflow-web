# EvoSys Wholesale - search, photos, truthful status, TAD file (Oct 10, 2026)

Branch: `wholesale/visibility-search` (from main 90485585). NOT deployed.

## What changed
1. **System Status tells the truth** (Acquisition Command): SMS / inbound replies say
   "Not set up" / "No texting number" until the Wholesale texting program can send;
   paid lookups say "Nothing connected" or "Budget $0" instead of "Allowed"; the
   strategy row reads "Next hunt · <strategy>".
2. **Discovery Inbox search**: "More filters" - street, owner, parcel, city, ZIP list,
   property type, tax value range, beds, baths, living area, year built, years owned,
   occupancy, missing data, and "must have ALL these signals". Main search box also
   matches owner name and parcel. Six new sorts. Rows show beds/baths/sq ft/year/years
   owned; county shown when the city is blank. Unknown values never match a range.
3. **Photos**: real Google Street View photo of the property's own address (and of
   each comp's address when no photo was uploaded), shown only when Google has imagery,
   labelled with source and date, cached per address. Needs a Google key with the
   "Street View Static API" enabled in `GOOGLE_STREET_VIEW_API_KEY` or
   `GOOGLE_MAPS_API_KEY` (falls back to `GOOGLE_PLACES_API_KEY`). Without it the page
   says why there is no photo; nothing is called.
4. **Public record panel** on each property: every county fact on file (parcel, type,
   beds, baths, area, year built + age, tax value / land / improvements / per sq ft,
   last deed, years owned, occupancy, owner + mailing) and free links (Google Maps,
   Street View, DCAD/TAD record page).
5. **TAD file** (Tarrant Appraisal District refuses our servers, serves the same free
   file to a person's PC):
   - Upload box: Providers & Controls -> Source Registry -> "Tarrant Appraisal District
     file" (platform admin only). Validates it is TAD's file, then lifts the block.
   - Nightly feed: `POST /source-feeds/tad` with header `X-Source-Token`; refused (503)
     unless the backend has `EVOSENSE_SOURCE_UPLOAD_TOKEN` set.
   - PC side: Windows task "EvoSys TAD nightly feed" (3:30 AM daily) runs
     `C:\Dev\overnight\tad_feed.ps1`; token in `C:\Dev\overnight\tad_feed.token`;
     log `C:\Dev\overnight\tad\tad_feed.log`. Sends only when TAD published a new file.
   - Proven with today's real file (50.6 MB): 210 San Angelo St -> owner, 912 sq ft,
     built 1949, deed 2009-11-30, tax value.
   - Caveat: the server disk is not durable; after a restart/deploy the copy is gone
     until the next nightly send (or a manual upload).

## Production data changed today (by request)
- Strategy "New Check Out": passing score 60 -> 45 (v2). 2 properties reach it now.

## Tests
- New: tests/test_evosense_search_photo.py (10), tests/test_evosense_source_upload.py (6).
- Wholesale/EvoSense/route/tenant/entitlement set: 1811 passed, 1 failed (attack-list
  guard for the new upload route) -> route made fixed `/sources/tad/upload`; re-run below.

## Mike's steps to turn it on
1. Merge `wholesale/visibility-search` into main.
2. Render -> advisorflow-backend -> Environment: add `EVOSENSE_SOURCE_UPLOAD_TOKEN` =
   the text in `C:\Dev\overnight\tad_feed.token`.
3. Optional photos: enable "Street View Static API" on the Google key; set
   `GOOGLE_MAPS_API_KEY`.
4. God Mode -> SCI & Wholesale Access: grant `wholesale@evosyspro.live` (turns the lock on).

## Known follow-ups
- Merge conflict expected with `release/wholesale-nightly` in EvoControls.jsx (both edit it).
- Tarrant city names: TAD gives a city only for owner-occupied rows; absentee-owned
  properties may still show "Tarrant County" instead of a city.
