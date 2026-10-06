# SCI — REGIONAL POOL NUMBER PLAN (proof of concept, updated 2026-10-06, run sci-regional-numbers-20261006-1528)

> **SUPERSEDES the one-number-per-campus plan below for the POC.** The POC needs **six** standard local numbers, one per area-code regional pool: **205, 334, 850, 251, 706, 318**. Not 30. The toll-free +1 844-917-2171 stays backup/overflow only, never the normal local sender. The 30-campus / 39-entity mapping is kept for internal identity, routing, cadence, reporting and audit. A shared number never collapses entities: outbound identity stays "Kerry Allan | <exact SCI Location>".
>
> | Area code | Pool id | Internal label (not customer-facing) | Entities |
> |---|---|---|---|
> | 205 | pool-205-birmingham | SCI Alabama Central / Birmingham-area pool | 17 |
> | 334 | pool-334-montgomery | SCI Montgomery / central Alabama pool | 6 |
> | 850 | pool-850-pensacola | SCI Pensacola / Florida Panhandle pool | 6 |
> | 251 | pool-251-mobile | SCI Mobile / southwest Alabama pool | 5 (incl. Pine Crest Cemetery West) |
> | 706 | pool-706-columbus | SCI Columbus GA pool | 2 |
> | 318 | pool-318-shreveport | SCI Shreveport LA pool | 2 |
>
> - **Pine Crest Cemetery West: verified** at 1599 Snow Rd S, Mobile, AL 36695: area code 251, its own physical campus. The address-unverified flag is removed.
> - **Oaklawn Central Care Center: still unresolved.** No area code, no location invented. It does not block the six pools; if it is verified inside a covered area code, no extra number is needed.
> - **Inbound SMS on a pool number:** the sender is matched by phone to a contact, and the reply attaches to that contact's own exact entity. An unknown sender goes to the regional review queue (`program_unmatched_replies`, no location), never guessed.
> - **Inbound voice on a pool number:** neutral greeting that names no location; voicemail from a known contact is handled under that contact's entity; from an unknown caller it goes to the regional review queue.
> - **Pool numbers in `phone_numbers`:** `workspace_id` NULL, `label` = `pool:<pool id>`. Code: `app/services/programs/regional_pools.py`. Tests: `tests/test_sci_regional_pools.py`. Worksheet: `handoff/SCI_TWILIO_SIX_NUMBER_WORKSHEET.md`.
>
> The campus table and sections below remain valid as the internal campus map. Wherever they say "campus number", "30 numbers" or "to buy", read: the campus's regional pool number, 6 numbers total, none purchased.

**Internal identity is per PHYSICAL CAMPUS (phone numbers are regional pools; see above).** Each named entity on a campus keeps its own identity: name, email alias, Outlook folder, reporting, campaign/source context and the "Kerry Allan | <entity>" sign-off. The entities on a campus share the phone number.

- **Exact count: 30 campuses for 39 named locations.**
  - 9 campuses hold two entities each: a funeral home with a cemetery or chapel.
  - 21 campuses hold one entity.
  - 1 of the 30 (Oaklawn Central Care Center) is unverified, because its address was not found. Pine Crest Cemetery West was verified on 2026-10-06.
- **Contacts: 535 / 535** clean contacts are bound to a campus through their location.
  - 25 of them belong to the 2 unverified campuses.
  - The 10 held source rows (9 contacts) are excluded and untouched.
  - This was checked against the full local import rehearsal. Real contacts are not loaded into staging.
- Grouping data: `scripts/sci_campuses.csv`. The grouping is by street address, from the locations' public pages: research, to be confirmed with SCI before any address goes in mail. The code is in `app/services/programs/campuses.py`.

## Routing rules

1. The campus number is stored in `phone_numbers`, with `workspace_id` set to one location on the campus.
2. **SMS to the campus number:** the sender is matched by phone across the workspace. The reply attaches to the contact's OWN location: a family of the funeral home who texts the shared number lands on the funeral home. No false "wrote to another location" flag is raised for entities on the same campus. Then:
   - cadence pause;
   - classification (intent + urgency);
   - alerts;
   - audit.
3. **Unknown sender:** kept in the unmatched queue at the campus, never attached to anyone.
4. **Voice:** a campus greeting, voicemail with transcription, and reply handling as above. A missed call produces a suggested text that is never sent.
5. **Outbound identity is always per entity:** "Kerry Allan | Eastern Gate Memorial Funeral Home" or "Kerry Allan | Eastern Gate Memorial Gardens", from the campus number.
6. **Reporting:** `GET /program/campuses` (entities, contacts, number) and `GET /program/voice` (calls per location).

## Campus table

| # | Campus | Entities served | City, ST | Area code | Contacts | Number | Status |
|---|---|---|---|---|---|---|---|
| 1 | Alabama Heritage | Alabama Heritage Cemetery; Alabama Heritage Funeral Home | Montgomery, AL | 334 | 34 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 2 | Bayview | Bayview Fisher-Pou Chapel; Bayview Memorial Park | Pensacola, FL | 850 | 50 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 3 | Centuries Memorial | Centuries Memorial Funeral Home; Centuries Memorial Park | Shreveport, LA | 318 | 2 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 4 | Eastern Gate Memorial | Eastern Gate Memorial Funeral Home; Eastern Gate Memorial Gardens | Pensacola, FL | 850 | 23 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 5 | Elmwood | Elmwood Cemetery & Mausoleum; Johns-Ridout's Mortuary-Elmwood Chapel | Birmingham, AL | 205 | 18 | to buy | Confirm address with SCI, then buy |
| 6 | Greenwood | Greenwood Serenity Memorial Gardens; White Chapel-Greenwood Funeral Home | Montgomery, AL | 334 | 37 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 7 | Heritage Chapel Funeral Home | Heritage Chapel Funeral Home | Tuscaloosa, AL | 205 | 3 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 8 | Highland Memorial Gardens | Highland Memorial Gardens | Bessemer, AL | 205 | 7 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 9 | Johns-Ridout's Funeral Parlors | Johns-Ridout's Funeral Parlors | Birmingham, AL | 205 | 8 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 10 | Kilgore Green Funeral Home | Kilgore Green Funeral Home | Jasper, AL | 205 | 1 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 11 | Leak Memory Chapel | Leak Memory Chapel | Montgomery, AL | 334 | 16 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 12 | Memory Hill Gardens | Memory Hill Gardens | Tuscaloosa, AL | 205 | 1 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 13 | Memory Park Cemetery | Memory Park Cemetery | Milton, FL | 850 | 26 | to buy | Confirm address with SCI, then buy |
| 14 | Oak Lawn Funeral Home | Oak Lawn Funeral Home | Pensacola, FL | 850 | 24 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 15 | Oaklawn Central Care Center | Oaklawn Central Care Center | — | — | 9 | to buy | BLOCKED: address not found — confirm location |
| 16 | Oakwood Memorial Gardens | Oakwood Memorial Gardens | Gardendale, AL | 205 | 5 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 17 | Parkhill Cemetery | Parkhill Cemetery | Columbus, GA | 706 | 35 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 18 | Pine Crest | Pine Crest Cemetery; Pine Crest Funeral Home | Mobile, AL | 251 | 15 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 19 | Pine Crest Cemetery West | Pine Crest Cemetery West | Mobile, AL | 251 | 16 | pool 251 | Verified: 1599 Snow Rd S, Mobile, AL 36695 |
| 20 | Radney Funeral Home | Radney Funeral Home | Saraland, AL | 251 | 25 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 21 | Radney Funeral Home-Mobile | Radney Funeral Home-Mobile | Mobile, AL | 251 | 22 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 22 | Ridout's Gardendale Chapel | Ridout's Gardendale Chapel | Gardendale, AL | 205 | 11 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 23 | Ridout's Trussville Chapel | Ridout's Trussville Chapel | Birmingham, AL | 205 | 2 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 24 | Ridout's Valley Chapel | Ridout's Valley Chapel | Homewood, AL | 205 | 19 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 25 | Ridout's-Brown-Service Prattville Chapel | Ridout's-Brown-Service Prattville Chapel | Prattville, AL | 334 | 20 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 26 | Rockco Funeral Home | Rockco Funeral Home | Centreville, AL | 205 | 2 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 27 | Rockco Funeral Home (Montevallo) | Rockco Funeral Home (Montevallo) | Montevallo, AL | 205 | 2 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 28 | Southern Heritage | Southern Heritage Cemetery; Southern Heritage Funeral Home | Pelham, AL | 205 | 33 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |
| 29 | Striffler-Hamby Mortuary | Striffler-Hamby Mortuary | Columbus, GA | 706 | 52 | to buy | Confirm address with SCI, then buy |
| 30 | Sunset Brown-Service | Sunset Brown-Service Funeral Home; Sunset Brown-Service Memorial Park | Northport, AL | 205 | 17 | to buy | Ready to buy & configure (voice/voicemail now; SMS after registration) |

Contacts total: 535.

Parkhill Cemetery (4161 Macon Rd) and Striffler-Hamby Mortuary (4071 Macon Rd) are adjacent but at different addresses, so they are kept as 2 campuses. If SCI confirms they are one campus, the count becomes 29.

## Campus lock (relay run sci-readiness-campus-20261006-1512)

Recomputed from `scripts/sci_campuses.csv` and `scripts/sci_location_aliases.csv`; locked by `tests/test_sci_campus_lock.py`.

- **30 physical campuses / 39 entities** (9 shared pairs, 21 single). 30 campuses remain for internal routing only. The POC needs 6 local numbers (regional pools). None purchased.
- Every entity has its own alias; no two entities share one.
- Contact counts above sum to **535**, matching the documented clean population. The 551-row source file (`SCI_Filtered_551_Leads.csv`) is not in the repo, so per-contact mapping was not re-run here. The held 10 rows / 9 contacts are excluded by `on_hold` in `campuses.plan`.
- **Provisional / unverified (no address on file, nothing invented):**
  - `Oaklawn Central Care Center` (9 contacts): kept as its own campus. The only link to Oak Lawn Funeral Home (Pensacola) is a similar public name. If SCI confirms the link, the count becomes 29 (28 together with a Parkhill/Striffler-Hamby merge).
  - `Pine Crest Cemetery West` (16 contacts): now verified (Mobile, AL, 251), still a separate campus from Pine Crest, Mobile.
- Parkhill Cemetery (4161 Macon Rd) and Striffler-Hamby Mortuary (4071 Macon Rd) stay separate campuses.

## Carrier registration

- **Target 10DLC classification:** use case **AGENTS_FRANCHISES**, Twilio's multi-office, one-local-number-per-office use case. It applies only once a registered Brand legitimately represents the multi-location sender. This is the TARGET, not a claim that it is approved today.
- **Today the account holds:**
  - Brand "EVO Integrated Solutions LLC": Low Volume Standard, approved.
  - One campaign: Low Volume Mixed, approved. It covers EvoSys's own traffic.
  - Numbers: toll-free 844-917-2171 (kept as backup/overflow), plus 469-224-1155 and 469-405-0255.

## What can be done TODAY, with no SCI paperwork

| Item | Status |
|---|---|
| Campus grouping, 535-contact binding, routing, voice, voicemail, transcription, missed-call follow-up, reporting | **Done** in code. Proven in staging with a fictional campus number. |
| Buy the 30 campus numbers (local, by the area codes above) and configure voice + voicemail | **Ready.** Voice does not need 10DLC. Needs Mike's explicit purchase authorization: roughly 30 numbers at Twilio's local-number monthly rate. |
| Email proof of concept | **Not blocked.** The live loop already passed. |

## What must wait for valid carrier registration

- **SCI-branded outbound SMS from the campus numbers.** Production SMS stays OFF until a campaign that matches this traffic is approved. The campus numbers are not attached to EVO's Low Volume Mixed campaign, because that would misdescribe the sender.

## Fastest legitimate path to real-contact SMS

Mike decides and attests; Claude submits nothing.

1. Register the SCI program traffic as its own campaign under EvoSys as the ISV, with use case AGENTS_FRANCHISES. It needs only:
   - the brand that will appear in the messages;
   - the opt-in description (how these contacts asked for information);
   - sample messages, which the message brain supplies.
   - No subsidiary or location-level entity structure is needed for a proof-of-concept campaign whose brand is the business actually sending.
2. **In the meantime:** toll-free verification of 844-917-2171 for this exact use case is usually the quickest single-number route. Its verification status could not be read from the console in this session.
