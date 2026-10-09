# SCI Phase 3: live integration readiness

Prepared 2026-10-08 (night) on `sci-program`. Staging only.
- No production deploy.
- No live webhook change.
- No message sent.
- No number bought.
- Outbound SCI texting stays OFF.

## 1. Toll-free 844-917-2171: what Twilio actually says (read-only, console)

| Item | Value |
|---|---|
| Toll-Free Verification | **Approved on 2026-08-22** ("can be used for SMS/MMS") |
| Use category | **Account Notifications** |
| Opt-in type | Web Form: `https://evosyspro.live/sms-optin/` |
| Volume | about 10,000 messages a month |
| Registered sample | "Hi [FirstName], this is [Representative Name] from EvoSys Pro. Your appointment is confirmed for [Date] at [Time]. Reply STOP to opt out." |
| Business | EVO Integrated Solutions LLC |
| Voice webhook | `https://demo.twilio.com/welcome/voice/` (Twilio demo: callers hear Twilio's demo message today) |
| Messaging webhook | `https://advisorflow-backend.onrender.com/sms/webhook/inbound` (PRODUCTION backend) |
| Emergency address | not registered (console warning) |

**Scope.** The approval covers informational / account-notification messages.
- The platform registry now records the line as approved with scope `informational` only, so promotional content stays refused.
- **Judgement call for Mike:** first-touch follow-ups to Direct Mail leads are outreach, not account notifications. Carriers may read them as outside the approved use case. Families treated as promotional are set by `SCI_PROMOTIONAL_FAMILIES` (default: `cemetery_x_sell`).

## 2. Staging URLs for 844-917-2171 (prepared, NOT applied)

| Twilio field | Value | Method |
|---|---|---|
| Messaging: "A message comes in" | `https://sci-staging-backend.onrender.com/sms/webhook/inbound` | HTTP POST |
| Voice: "A call comes in" | `https://sci-staging-backend.onrender.com/voice/inbound` | HTTP POST |

Voicemail recording and transcription callbacks are generated inside the call's TwiML. Nothing else needs configuring.

**While the webhooks point at staging, every text and call to 844-917-2171 goes to staging and never reaches production.** That includes texts from anyone who already has the number. Keep the window short, and restore the current values afterwards:
- voice: `https://demo.twilio.com/welcome/voice/`
- messaging: `https://advisorflow-backend.onrender.com/sms/webhook/inbound`

## 3. Staging environment readiness (values never shown)

| Variable | Status | Needed for |
|---|---|---|
| `API_BASE_URL` | set | webhook URLs and callbacks |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` (EvoSys Pro account) | **NOT set** | validating Twilio signatures on staging (live test) |
| `SMS_PROGRAM_ORG_SCI` | not set | optional (SCI is resolved by name) |
| `SMS_CAMPAIGN_REGISTRY_JSON` | not set | optional (approval is now built in) |
| `SCI_SMS_SEND_ENABLED` | **off** | stays off |

The toll-free number row on staging:
- SCI organization;
- route `voicemail_only`;
- SMS + inbound voice + voicemail;
- outbound voice OFF.

Check any time with `GET /god/sms-consent/sci/telephony` (add `?live=true` once the credential is set, to read Twilio's webhooks and verification).

## 4. Live test procedure (designated test phones only)

**Preconditions:**
- A Mike-approved test window.
- One designated test phone (P1, Mike's own).
- Optionally a second test phone (P2) that matches no contact.

**Steps:**
1. Mike sets the staging Twilio credential (section 3) in Render. Wait for the redeploy.
2. Claude puts P1 on the seeded TEST contact: `POST /god/staging/sci/test-phone {"phone": "<P1>", "apply": true}`.
   - The contact sits at Eastern Gate Memorial Gardens.
   - The request is refused if P1 belongs to any other contact.
3. Claude confirms the setup: `GET /god/sms-consent/sci/telephony?live=true`.
4. Mike changes the two webhooks in section 2 to the staging values (the only Twilio change).
5. **Text test.**
   - From P1, text "Test reply" to 844-917-2171. Expect it on the test contact's conversation, at Eastern Gate Memorial Gardens.
   - From P2, text "Who is this?". Expect it in the toll-free review queue.
6. **Call test.**
   - From P1, call 844-917-2171. Expect no ring, the Eastern Gate Memorial Gardens greeting, then a beep. Leave a 5-second message.
   - From P2, call. Expect the neutral planning-line greeting.
7. Claude checks staging for each of these:
   - Reply attached to the test contact;
   - review item for P2;
   - two `InboundCallLog` rows marked `voicemail`;
   - a `Voicemail` with its recording saved to the test contact;
   - a callback task and an in-app notification.
8. **STOP test (optional).** From P2, text STOP. Expect P2 to be suppressed. Twilio sends its own STOP confirmation.
9. Mike restores the two webhooks to the values in section 2.
10. **Outbound (only with separate approval).** A single SCI text from 844-917-2171 to P1. It needs all of these:
    - `SCI_SMS_SEND_ENABLED=on` on staging, for that test only;
    - P1's consent recorded with evidence;
    - a manual send.

    Turn it back off immediately afterwards.

**Pass:** every step 5–8 result appears as described, Twilio's Messaging/Call logs show 0 outbound messages (other than Twilio's STOP reply), and the webhooks are restored.

## 5. Consent evidence: Direct Mail designation (no automatic attestation)

- The source file `SCI_Filtered_551_Leads.csv` lists **all 551 rows as Campaign Channel = Direct Mail**. Primary campaigns are Veteran Planning Guide, General Survey and similar mailers.
- The approved toll-free verification states that opt-in is the **EvoSys Pro web form** (`evosyspro.live/sms-optin/`).
- Nothing in the contact file is evidence of a web-form opt-in.
- **Change made.** Owner attestation now **requires the opt-in evidence**: `POST /god/sms-consent/sci/reconcile` refuses to `apply` without `evidence_phones`, the phone numbers taken from the actual opt-in records.
  - Only numbers that appear in those records are attested.
  - Every other number is reported `no_opt_in_evidence` and gets nothing.
  - STOP / DNC / suppression / prior opt-outs still always win.
- Dry run on the imported list: 533 contacts pass the protections. How many are actually covered depends on the opt-in export.
- **Mike to supply:** the export of `storage/sms_optins.csv` from the evosyspro.live host (or another opt-in record), so the match can run. Contacts with no matching record get no SCI texts until they opt in (the SCI opt-in page) or another basis is documented.

## 6. Exception report

**Invalid phone numbers (2)**: no texts and no caller match; email only.

| Lead ID | Contact | Location | Raw phone |
|---|---|---|---|
| 00QVv00000XDDT0 | Odies Donald | Alabama Heritage Cemetery | 1662458988 (9 digits after a leading 1; likely 662-458-988x) |
| 00QVv00000PQKBW | Edmund Brown | Oak Lawn Funeral Home | 1850207338 (likely 850-207-338x) |

Fix: get the correct number from SCI. Do not guess.

**Held records (10 records, 9 contacts)**: refused on every send path until reviewed in the platform.

| Queue | Lead ID | Contact | Reason |
|---|---|---|---|
| Data review | 00QVv00000PhINI | Christine Blackmon | name field says "NOT INTERESTED DISQUALIFIED" |
| Data review | 00QVv00000PQKBO | James McIntyre | "DISQUALIFIED" in name |
| Data review | 00QVv00000PQ55H | Harold Coulter Jr | "FULLY PREPLANNED" in name |
| Data review | 00QVv00000PNXBv | Leon Moring | "DISQUALIFIED" in name |
| Location review | 00QVv00000POCIT | Monique Davis | no location in the source |
| Location review | 00QVv00000Ceg1n | James Harper | no location in the source |
| Location conflict | 00QVv00000PYdqB + 00QVv00000Fjx9q | Dianna Trammell | same person and phone at Greenwood Serenity Memorial Gardens AND White Chapel-Greenwood Funeral Home |
| Duplicate review | 00QVv00000Duacz / 00QVv00000Dubgi | David Sr / Janet Gulley | shared email, different names (Striffler-Hamby Mortuary) |

Recommendation:
- Data-review contacts: do not contact (they read as declined or already planned).
- Location review: SCI to supply the location.
- Trammell: SCI to confirm which location (calls from that number get the neutral greeting until then).
- Duplicates: confirm the household.

**Oaklawn Central Care Center (9 contacts):**
- The location is unverified: no city, state or area code in `scripts/sci_campuses.csv`.
- **Change made.** Its contacts are now HELD from SCI texts (`LOCATION_UNVERIFIED`). Inbound replies and calls from them are still received.
- SCI to confirm the address. Then mark the location verified to release them.

## 7. What launch actually needs vs. later

**Required for the initial launch:**
- The text-and-voicemail loop on 844-917-2171 (built and verified on staging, 11/11).
- Opt-in evidence for the contacts to be texted (section 5).
- One person who receives voicemail and reply alerts. Today that falls back to SCI organization admins.
  - On production: create Kerry Allan's user and set her as the program's primary contact, or confirm admin recipients.
- A working planning-guide link. The platform serves `/planning-guide`, which works on deploy.
- The 844 webhooks pointed at the environment that runs SCI (production, after the production GO).

**Later enhancements (not blocking):**
- Per-location local phone numbers (`{location_phone}`).
- Booking links (`{booking_link}`).
- Custom voicemail greetings. The default already names the cemetery.
- Cemetery-specific planning guides. The shared guide is used until then.
- Per-location representative routing. The program contact or admins receive alerts until then.
- An emergency address on the 844 number (console warning; relevant only for outbound emergency calls, which this line never makes).

## 8. Launch checklist: only the actions needing Mike's authorization

1. Set `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` (EvoSys Pro account) on `sci-staging-backend`.
2. Approve a live test window; change the 844 webhooks to the staging URLs (section 2); restore them afterwards.
3. Provide the opt-in evidence export (section 5) and authorize attestation for matched numbers only.
4. Decide the exceptions (section 6), and whether SCI follow-ups fit "Account Notifications" (section 1).
5. Approve the outbound single test text to P1 (optional, section 4 step 10).
6. Production GO: deploy `sci-program`; create SCI in production; Kerry's account; point the 844 webhooks at production.
7. First-contact GO: turn on `SCI_SMS_SEND_ENABLED` and one campaign family, with a small batch.

## 9. LIVE TEST RESULT (2026-10-09, 10:55-11:17 CT): ALL PASS

The test used Mike's phone only (469-553-7417, the designated test phone on the SCI TEST contact at Eastern Gate Memorial Gardens). Staging was on build `7e56afe` with the EvoSys Pro Twilio credential set and `SCI_SMS_SEND_ENABLED=on`. Consent was recorded for the test phone only.

| Test | Result |
|---|---|
| Outbound SCI text from 844-917-2171 to the test phone (all gates applied) | PASS: delivered 10:55, signed "Kerry Allan, Eastern Gate Memorial Gardens", with the STOP line |
| Inbound reply | PASS: on the test contact's conversation, at Eastern Gate Memorial Gardens; staff alert created |
| Call | PASS: voicemail only, no ring; Mike confirmed the Eastern Gate Memorial Gardens greeting was correct |
| Voicemail | PASS: recording saved to the test contact; callback task created; in-app notification created |

**Webhooks.** Pointed at staging at 10:55. RESTORED at 11:17 and verified through the Twilio API:
- voice `https://demo.twilio.com/welcome/voice/` (POST);
- messaging `https://advisorflow-backend.onrender.com/sms/webhook/inbound` (POST).

**Still on.** `SCI_SMS_SEND_ENABLED=on` remains set on STAGING. Only the test phone has consent. Remove the variable in Render to switch staging outbound off.

## 10. LIVE TEST: 3 message types × 2 phones (2026-10-09, about 12:10-13:05 CT)

Setup:
- Staging only. 844-917-2171 webhooks were pointed at staging for the test.
- Kerry Allan is the representative for every cemetery.
- Each round moved the phone's TEST contact to a different cemetery and campaign, then sent that campaign's real approved text.

| Phone | Round | Campaign / cemetery | Text | Reply | Call → voicemail + task | Result |
|---|---|---|---|---|---|---|
| Mike 469-553-7417 | 1 | Veterans | delivered | "Yes please let scheduled something" | 17:16Z | PASS |
| Mike 469-553-7417 | 2 | Seminar | delivered | "Yes let's go" | 17:33Z | PASS |
| Mike 469-553-7417 | 3 | Web lead | delivered | "Right now 😂" | 17:36Z | PASS |
| Michael Schlueter 540-392-7776 | 1 | Veterans / Eastern Gate Memorial Gardens | delivered | "Got it vet" | 17:27Z | PASS |
| Michael Schlueter 540-392-7776 | 2 | Seminar / Striffler-Hamby Mortuary | delivered | "Test" (18:02Z) | not placed | reply PASS; call not run |
| Michael Schlueter 540-392-7776 | 3 | Web lead / Eastern Gate Memorial Funeral Home | not sent | none | none | not run |

Michael's Round 2 call and his Round 3 were not run: the participant stopped, and Mike closed the test. This is not counted as a failure. Every step that ran passed.

After the test:
- The 844 webhooks were restored through `POST /god/sms-consent/sci/telephony/restore-webhooks`. This god-only endpoint takes no input and can only set the two originals back:
  - SMS: advisorflow-backend `/sms/webhook/inbound`
  - Voice: `demo.twilio.com/welcome/voice/`
- The restore was verified with `GET /god/sms-consent/sci/telephony?live=true`.
- `SCI_SMS_SEND_ENABLED=on` is still set on staging. Mike can remove it in Render.
