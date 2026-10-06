# SCI — 39 location numbers: plan and status (2026-10-06)

## Status in one line

**No numbers have been bought.** Under the existing Twilio registration, nothing can be provisioned for SCI *texting* without a new carrier registration. That registration needs an attestation from Mike/SCI. *Voice-only* use of local numbers needs no 10DLC registration, but it should wait until the map below is confirmed.

## What the Twilio account holds today (inspected read-only, 2026-10-06)

**A2P brands**

| Brand | Type | Status |
|---|---|---|
| EVO Integrated Solutions LLC (BN68b7…) | Low Volume Standard | Approved |
| Mike Simmon (BNa3dc…) | Sole proprietor | Approved |

**A2P campaigns:** one campaign, CM24a1…, approved. Use case Low Volume Mixed, under the EVO brand. Messaging service: "Low Volume Mixed A2P Messaging Service" (MG37…).

**Active numbers**

| Number | Type | Configuration |
|---|---|---|
| +1 844-917-2171 | Toll-free | SMS webhook goes to production `/sms/webhook/inbound`. **Voice webhook is still Twilio's demo URL (demo.twilio.com/welcome/voice)**, so a caller hears Twilio's sample message. **Keep this number** (backup / overflow). Mike should repoint its voice URL to `/voice/inbound` when he decides; production configuration was not changed tonight. |
| +1 469-224-1155 | Local (DFW) | Voice → `/voice/inbound`. Messaging → the Low Volume Mixed service. |
| +1 469-405-0255 | Local (Dallas) | Messaging → a second messaging service (created 2026-09-25). Voice not configured. |

## Why texting from 39 SCI numbers cannot be switched on under the existing registration

1. **The campaign describes EvoSys's own traffic.** These messages would come from SCI locations, signed "Kerry Allan | <location>". A 10DLC campaign has to describe the real sender and the real use case. Attaching 39 SCI-location numbers to EVO's Low Volume Mixed campaign would misdescribe the traffic.
2. **Throughput.** A Low Volume Standard brand is capped at low daily volume. That is fine for a 10-contact pilot, but it is not a 39-location program.
3. **The registration that fits** is a customer (secondary) brand for SCI, or for the SCI entity that operates these locations, registered through EvoSys as an ISV, with a campaign whose use case covers local-location customer care and marketing to people who asked for information. An **Agents/Franchises**-style campaign is the structure designed for many local numbers under one brand. Choosing between that and a standard Mixed/Marketing campaign depends on SCI's legal entity and consent language, and that statement must come from SCI/Mike.

**Needed from Mike/SCI:**
- the legal entity name, EIN and business address of the brand that will be registered;
- an authorized contact;
- the opt-in/consent description: how these 535 people asked to be contacted;
- sample messages (the message brain can supply these).

Claude will not submit any of this on anyone's behalf.

## The 39-location map

The area code is taken from the location's own public phone number. Addresses and phones come from each location's public page and are research only: **confirm with SCI before any of them is used**. The quality gate requires a verified postal address per location before automated email.

| # | Location | City, ST | Local area code | Status |
|---|---|---|---|---|
| 1 | Alabama Heritage Cemetery | Montgomery, AL | 334 | READY TO MAP - registration needed before SMS |
| 2 | Alabama Heritage Funeral Home | Montgomery, AL | 334 | READY TO MAP - registration needed before SMS |
| 3 | Bayview Fisher-Pou Chapel | Pensacola, FL | 850 | READY TO MAP - registration needed before SMS |
| 4 | Bayview Memorial Park | Pensacola, FL | 850 | READY TO MAP - registration needed before SMS |
| 5 | Centuries Memorial Funeral Home | Shreveport, LA | 318 | READY TO MAP - registration needed before SMS |
| 6 | Centuries Memorial Park | Shreveport, LA | 318 | READY TO MAP - registration needed before SMS |
| 7 | Eastern Gate Memorial Funeral Home | Pensacola, FL | 850 | READY TO MAP - registration needed before SMS |
| 8 | Eastern Gate Memorial Gardens | Pensacola, FL | 850 | READY TO MAP - registration needed before SMS |
| 9 | Elmwood Cemetery & Mausoleum | Birmingham, AL | 205 | READY TO MAP - registration needed before SMS |
| 10 | Greenwood Serenity Memorial Gardens | Montgomery, AL | 334 | READY TO MAP - registration needed before SMS |
| 11 | Heritage Chapel Funeral Home | Tuscaloosa, AL | 205 | READY TO MAP - registration needed before SMS |
| 12 | Highland Memorial Gardens | Bessemer, AL | 205 | READY TO MAP - registration needed before SMS |
| 13 | Johns-Ridout's Funeral Parlors | Birmingham, AL | 205 | READY TO MAP - registration needed before SMS |
| 14 | Johns-Ridout's Mortuary-Elmwood Chapel | Birmingham, AL | 205 | READY TO MAP - registration needed before SMS |
| 15 | Kilgore Green Funeral Home | Jasper, AL | 205 | READY TO MAP - registration needed before SMS |
| 16 | Leak Memory Chapel | Montgomery, AL | 334 | READY TO MAP - registration needed before SMS |
| 17 | Memory Hill Gardens | Tuscaloosa, AL | 205 | READY TO MAP - registration needed before SMS |
| 18 | Memory Park Cemetery | Milton, FL | 850 (alt 903) | CONFIRM with SCI (two places share this name) |
| 19 | Oak Lawn Funeral Home | Pensacola, FL | 850 | READY TO MAP - registration needed before SMS |
| 20 | Oaklawn Central Care Center | ? | confirm | BLOCKED - location not found; SCI to confirm address |
| 21 | Oakwood Memorial Gardens | Gardendale, AL | 205 | READY TO MAP - registration needed before SMS |
| 22 | Parkhill Cemetery | Columbus, GA | 706 | READY TO MAP - registration needed before SMS |
| 23 | Pine Crest Cemetery | Mobile, AL | 251 | READY TO MAP - registration needed before SMS |
| 24 | Pine Crest Cemetery West | ? | confirm | BLOCKED - location not found; SCI to confirm address |
| 25 | Pine Crest Funeral Home | Mobile, AL | 251 | READY TO MAP - registration needed before SMS |
| 26 | Radney Funeral Home | Saraland, AL | 251 | READY TO MAP - registration needed before SMS |
| 27 | Radney Funeral Home-Mobile | Mobile, AL | 251 | READY TO MAP - registration needed before SMS |
| 28 | Ridout's Gardendale Chapel | Gardendale, AL | 205 | READY TO MAP - registration needed before SMS |
| 29 | Ridout's Trussville Chapel | Birmingham, AL | 205 | READY TO MAP - registration needed before SMS |
| 30 | Ridout's Valley Chapel | Homewood, AL | 205 | READY TO MAP - registration needed before SMS |
| 31 | Ridout's-Brown-Service Prattville Chapel | Prattville, AL | 334 | READY TO MAP - registration needed before SMS |
| 32 | Rockco Funeral Home | Centreville, AL | 205 | READY TO MAP - registration needed before SMS |
| 33 | Rockco Funeral Home (Montevallo) | Montevallo, AL | 205 | READY TO MAP - registration needed before SMS |
| 34 | Southern Heritage Cemetery | Pelham, AL | 205 | READY TO MAP - registration needed before SMS |
| 35 | Southern Heritage Funeral Home | Pelham, AL | 205 | READY TO MAP - registration needed before SMS |
| 36 | Striffler-Hamby Mortuary | Columbus, GA | 706 (alt 706) | CONFIRM with SCI (two places share this name) |
| 37 | Sunset Brown-Service Funeral Home | Northport, AL | 205 | READY TO MAP - registration needed before SMS |
| 38 | Sunset Brown-Service Memorial Park | Northport, AL | 205 | READY TO MAP - registration needed before SMS |
| 39 | White Chapel-Greenwood Funeral Home | Montgomery, AL | 334 | READY TO MAP - registration needed before SMS |

**Summary:**

| Status | Count |
|---|---|
| Ready to map, waiting on registration | 35 |
| Confirm, because two places share the name (Memory Park Cemetery, Striffler-Hamby Mortuary) | 2 |
| Blocked, address not found (Oaklawn Central Care Center, Pine Crest Cemetery West) | 2 |

Where two locations share a campus and a public phone, SCI may prefer **one number per campus**. Examples: Eastern Gate Gardens and Funeral Home, or Alabama Heritage Cemetery and Funeral Home. That would cut 39 numbers to about 25 and keep routing exact, because the alias still names the location. Ask SCI.

## Provisioning steps, once the registration is approved

1. Buy one local number per row, in the row's area code. These are standard local numbers; keep the toll-free number.
2. Attach every number to the SCI campaign's messaging service.
3. In EvoSys, create one `phone_numbers` row per number:
   - `organization_id` = SCI;
   - `workspace_id` = that location;
   - `cap_sms`, `cap_voice_inbound` and `cap_voicemail` switched on;
   - route = ring members at their verified callback numbers (Kerry's number once she verifies it herself), then voicemail.
4. Point the number's voice URL to `/voice/inbound` and its SMS URL to `/sms/webhook/inbound`.

From then on, inbound texts and calls resolve to the location automatically. Inbound SMS routing by location number shipped in e268847, and voice/voicemail in 56cf3dd.

**Caller ID (CNAM):** set per number in Twilio once registered. Display depends on the carrier and is not guaranteed.
