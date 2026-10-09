# SCI POC: Twilio six-number provisioning worksheet

Run: sci-regional-numbers-20261006-1528. Target: controlled test run Thursday 2026-10-08. **No number has been bought. Nothing in Twilio was changed.** Buying numbers needs Mike's explicit authorization.

Fill the "Chosen number" column after buying. Webhook targets use the staging host placeholder `<STAGING_HOST>`; confirm the exact paths against the deployed routes before saving them in Twilio.

| # | Area code | Chosen number (placeholder) | Friendly label (internal) | `phone_numbers.label` | Regional pool id | Voice webhook target | SMS webhook target | Backup behavior | Readiness |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 205 | `+1205XXXXXXX` | SCI Alabama Central / Birmingham-area pool | `pool:pool-205-birmingham` | pool-205-birmingham | `https://<STAGING_HOST>/voice/inbound` | `https://<STAGING_HOST>/sms/webhook/inbound` | Overflow to +1 844-917-2171 only | NOT BOUGHT |
| 2 | 334 | `+1334XXXXXXX` | SCI Montgomery / central Alabama pool | `pool:pool-334-montgomery` | pool-334-montgomery | same | same | same | NOT BOUGHT |
| 3 | 850 | `+1850XXXXXXX` | SCI Pensacola / Florida Panhandle pool | `pool:pool-850-pensacola` | pool-850-pensacola | same | same | same | NOT BOUGHT |
| 4 | 251 | `+1251XXXXXXX` | SCI Mobile / southwest Alabama pool | `pool:pool-251-mobile` | pool-251-mobile | same | same | same | NOT BOUGHT |
| 5 | 706 | `+1706XXXXXXX` | SCI Columbus GA pool | `pool:pool-706-columbus` | pool-706-columbus | same | same | same | NOT BOUGHT |
| 6 | 318 | `+1318XXXXXXX` | SCI Shreveport LA pool | `pool:pool-318-shreveport` | pool-318-shreveport | same | same | same | NOT BOUGHT |

Backup: toll-free +1 844-917-2171 is overflow only. It is never the normal local sender.

## Checklist, after the six numbers are acquired (Mike buys; Claude can then configure staging)

1. [ ] Buy one standard local number in each of 205, 334, 850, 251, 706, 318. (Mike authorizes the spend.)
2. [ ] Record each E.164 in the "Chosen number" column.
3. [ ] In Twilio, set each number's voice URL and SMS URL to the targets above. Staging only.
4. [ ] Add six `phone_numbers` rows in staging: `workspace_id` = NULL, `label` = `pool:<pool id>`, `cap_sms` and `cap_voice_inbound` true, `cap_voicemail` true. A pool number must never get a `workspace_id`; that would make it name one location.
5. [ ] Send a staging-only test text from a seeded fictional contact to each number. Confirm it attaches to that contact's exact entity.
6. [ ] Text each number from an unseeded phone. Confirm it lands in the regional review queue with no location.
7. [ ] Call each number. Confirm the neutral greeting names no funeral home or cemetery.
8. [ ] Carrier/A2P registration is Mike's decision and attestation. Outbound SMS to real customers stays OFF until a matching campaign is approved. Voice and voicemail do not need 10DLC.
9. [ ] Mark each row READY here once steps 3 to 7 pass.

## Not covered by these six numbers
- Oaklawn Central Care Center (9 contacts): location unresolved. No area code assigned. If it is verified inside 205, 334, 850, 251, 706 or 318, it needs no extra number.
