# EvoSys Pro Universal SMS Consent Center

Branch `desktop/consent-center` (from `sci-program` dc25b03b). Written 2026-10-08 CT.

**Nothing in this document has been deployed to production.** No Twilio registration was submitted, no number was bought and no text was sent.

## What it is

| Page | Program | Ledger key (`sms_consent_records.program`) | Sending org | Status |
|---|---|---|---|---|
| `/sms-optin/` | general | `evosys_general_sms` | platform intake org (unchanged) | **open**: wording v2026-09, unchanged |
| `/sms-optin/?program=wholesale` | wholesale | `wholesale_seller_sms` (same as `/sell`) | `SMS_PROGRAM_ORG_WHOLESALE` | open once the env var is set |
| `/sms-optin/?program=sci` | sci | `sci_poc_sms` | `SMS_PROGRAM_ORG_SCI` (must be the org named "Service Corporation International") | **pending final review** |
| `/sms-optin/?program=<anything else>` | none | none | none | 404; nothing recorded |

**One page, separate consents.** Each program shows:
- its own sender;
- its own wording;
- an unchecked box;
- links to the Terms and Privacy pages.

**Wording lives in two places that must match:**
- `public-site/private/sms-programs.php`, shown on the page;
- `app/services/sms_programs.py` (`DISCLOSURES`), the platform's record.

`tests/test_sms_consent_center.py` fails if these differ by even one character.

**Each consent is kept twice:**
- A CSV on the web host, one per program. The general program keeps its original columns.
- A row in the platform ledger, sent through `SMS_OPTIN_WEBHOOK_URL` → `POST /site-intake/evosyspro/sms-optin`. The row records:
  - program and sender (through the program);
  - wording, verbatim, and its version;
  - the form id and form version;
  - the timestamp, taken from the platform's own clock;
  - the source page;
  - the visitor's IP address and browser;
  - the campaign it was collected for;
  - STOP status.

**No confirmation text is sent on any program.**

**STOP.** A STOP reply withdraws every program consent for that number in that org. This existing behaviour is now tested for the general program.

**Operator view (god only, read-only):**
- `GET /god/sms-consent/overview`: programs, campaign registry, and what each campaign may carry right now;
- `GET /god/sms-consent/records?program=general|wholesale|sci&phone=`: the ledger;
- `GET /god/sms-consent/sci-check?phone=&from_number=`: a dry run of the SCI gate.

## Per-campaign link and phone-number approval

`app/services/sms_campaigns.py` is the campaign registry.

- **Built in: CO3YNIF.** Messaging service `MG37d057536564f3228788425b0f83ec92`, number +14692241155, VERIFIED, links **NO**, phone numbers **NO**. This is preserved as is.
- **Adding the new campaign.** Use the `SMS_CAMPAIGN_REGISTRY_JSON` env var. No deploy is needed. Example, after Twilio assigns the IDs:
  ```json
  [{"key":"EVO-LINKS","campaign_id":"CM…","messaging_service_sid":"MG…","numbers":["+12058823908","…"],
    "status":"PENDING","has_embedded_links":true,"has_embedded_phone":true,"programs":["sci"]}]
  ```
  Change `status` to `VERIFIED` only after Twilio shows it Verified.
- **What a campaign may carry:**
  - An unregistered sender, or a campaign that is not VERIFIED, may carry **neither** links nor phone numbers.
  - The last check before every provider call (`send_sms`, `send_mms`, the Wholesale program send) refuses a body that carries something its campaign is not approved for. It never sends such a body.
- **Composing is unchanged.** Every SMS body still has links and phone numbers stripped (`sms_content_policy`). Letting the new campaign actually *include* links is a separate, deliberate change, made only after approval and with Mike's GO.

## SCI send gate (`sms_programs.sci_check`)

An SCI text goes out only when **all** of these hold. Each failing check is reported by its code.

| Code | Means |
|---|---|
| `SCI_SMS_DISABLED` | `SCI_SMS_SEND_ENABLED` is off (the default) |
| `SCI_COPY_NOT_FINAL` | the SCI wording is still pending review |
| `NO_SMS_CONSENT` | no SCI consent is on record for the number in the SCI org |
| `OPTED_OUT` | the number replied STOP, or was otherwise opted out |
| `CONSENT_WORDING_UNREGISTERED` | the consent was given under wording that is not the final registered version |
| `CAMPAIGN_NOT_APPROVED` | the sending number is not on a VERIFIED campaign that lists `sci` |

The suppression, DNC, hold and location-review gates still run first. There is no override.

## REVISED 2026-10-08 evening: SCI on the existing toll-free line

**The line.** SCI uses **+1 844-917-2171** for SMS and every inbound call. **No numbers are bought.** The six area-code pools stay defined in code but nothing requires them.

**Approval.** Toll-free approval is kept separate from 10DLC.
- The registry entry `TF-8449172171` (`kind: toll_free`) is approved by `verification_status` (TWILIO_APPROVED/VERIFIED), not by a campaign.
- It ships as **UNCONFIRMED** (fail closed).
- Setting it is one env entry, where `approved_scope` lists the scope the verification covered:
  ```json
  SMS_CAMPAIGN_REGISTRY_JSON=[{"key":"TF-8449172171","kind":"toll_free","numbers":["+18449172171"],
    "verification_status":"TWILIO_APPROVED","has_embedded_links":true,"has_embedded_phone":true,
    "programs":["sci"],"approved_scope":["informational"]}]
  ```

**Promotional content** needs `"promotional"` in `approved_scope`.
- Which campaign families count as promotional is set by `SCI_PROMOTIONAL_FAMILIES` (default `cemetery_x_sell`).
- Other families are treated as informational follow-up.

**Existing contacts.** The ~535 contacts get **owner-attested consent**, so they don't have to sign up again.
- Endpoint: `POST /god/sms-consent/sci/reconcile` with `{attested_by, evidence_reference, apply}`. It is a dry run unless `apply` is set.
- It skips contacts that are suppressed, marked DNC, have ever opted out of SCI, or have no valid mobile number. It never duplicates a record.
- A STOP afterwards still ends consent.

**Sender.** SCI texts go out only from the toll-free line (`SCI_TOLL_FREE_NUMBER`, default the 844 number), on the EvoSys Pro platform account. They never use an advisor's or a local number.

**Replies.**
- A reply to the toll-free line from a known contact goes to that contact's own conversation and cemetery.
- A reply from an unknown sender goes to the review queue `regional_review:pool-tollfree-844`.

**Calls.**
- Every call goes **straight to voicemail**: no ringing, no forwarding, no AI. This is enforced even if the number's route says otherwise.
- The caller is matched by phone number:
  - one cemetery: that cemetery's greeting (`LocationProfile.brand_settings.voicemail_greeting`, or a default that names it);
  - unknown, or ambiguous (contacts at different cemeteries): a neutral greeting, and the voicemail is saved to no contact (review queue).
- The voicemail is saved to the contact, and the assigned representative gets a call-back task plus an in-app notification.

**Templates.** Outbound templates gain `{location_phone}` (facility_phone), `{booking_link}` (appointment_link) and `{planning_guide_link}` (brand_settings.planning_guide_link, default https://evosyspro.live/planning-guide). They reach the family only when the toll-free line is verified and the message falls inside its scope; otherwise they are stripped.

**Staging setup.** `POST /god/staging/sci/toll-free {"apply": true}` records the 844 number on the SCI org with route voicemail_only and no outbound voice. It is idempotent and works on staging only.

## Going live, in order (each step needs Mike's approval)

1. **Approve the SCI wording.** Set `'status' => 'final'` in `sms-programs.php` and `copy_status="final"` in `sms_programs.py` (same commit). Re-run the tests.
2. **Upload `public-site/`** to the host, in particular:
   - `sms-optin/index.php`;
   - `private/sms-programs.php`;
   - `planning-guide/index.php`;
   - `.htaccess` (it serves `/planning-guide` without a redirect);
   - `privacy.html`, `sms-terms.html`, `sitemap.xml`.

   Then confirm `https://evosyspro.live/planning-guide` returns 200 with no 301.
3. **Backend env** (Render):
   - `SMS_PROGRAM_ORG_SCI` and `SMS_PROGRAM_ORG_WHOLESALE` set to the org ids.
   - Confirm `SMS_OPTIN_WEBHOOK_URL` on the host points at `/site-intake/evosyspro/sms-optin`.
4. **Twilio (Mike), toll-free (supersedes buying numbers):**
   - confirm 844-917-2171's Toll-Free Verification status and scope;
   - point its Messaging webhook to `…/sms/webhook/inbound` and its Voice webhook to `…/voice/inbound`;
   - set the registry entry above.
5. **Staging:**
   - `POST /god/staging/sci/toll-free {"apply":true}`;
   - `POST /god/sms-consent/sci/reconcile` (dry run, then apply, with the attestation);
   - set `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` (Mike enters them);
   - live-test reply, call and voicemail with Mike's own phone.
6. **Then:**
   - set `SCI_SMS_SEND_ENABLED=on` only after a separate first-contact GO;
   - production only after Mike's GO.

## Phase 2 verification (2026-10-08 night, staging fb0382e)

**Twilio, read-only (console, EVOSYS Pro account):**
- +1 844-917-2171 voice webhook: `https://demo.twilio.com/welcome/voice/`. That is Twilio's default demo, so callers today hear Twilio's demo message, not SCI voicemail.
- +1 844-917-2171 messaging webhook: `https://advisorflow-backend.onrender.com/sms/webhook/inbound` (the PRODUCTION backend).
- I could not read the Toll-Free Verification status: the console's verification page would not render. It remains UNCONFIRMED in the registry, so the gate fails closed.
- +1 469-224-1155 (CO3YNIF): unchanged.

**Staging environment:**
- `API_BASE_URL` is set.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `SMS_PROGRAM_ORG_SCI` (resolved by name instead) and `SMS_CAMPAIGN_REGISTRY_JSON` are not set.
- `SCI_SMS_SEND_ENABLED` is off.

**End to end on staging** (`POST /god/staging/sci/simulate/toll_free_round_trip`, real handlers, no carrier): **11/11 passed, 0 sent.**
- SMS from a known contact lands on that contact's conversation, at its own cemetery.
- SMS from an unknown number goes to review.
- STOP suppresses the number.
- A call from a known caller goes to voicemail only, with a greeting that names the caller's cemetery.
- A call from an unknown caller goes to voicemail only, with a neutral greeting.
- The voicemail is saved to the contact, a callback task is assigned to the contact's rep, and the rep is notified.

**Contact list** (the real `SCI_Filtered_551_Leads.csv`, imported through the real `program_setup` pipeline into a THROWAWAY local database, which was deleted afterwards):
- 551 rows → 535 contacts promoted, 10 held records (review queues), 39 locations, 0 contacts without a resolved cemetery.
- Owner-attested reconciliation dry run: **533 eligible**, 2 skipped (invalid phone numbers `1662458988`, `1850207338`).
- 7 numbers appear on more than one row; 1 number spans two cemeteries (ambiguous: neutral greeting, review).
- Every row's `Campaign Channel` is `Direct Mail`.

**Locations** (`GET /god/sms-consent/sci/locations`):
- 39 locations: 38 on the toll-free line, plus Oaklawn held (no verified area code).
- 0 have a local phone, a booking link, a custom greeting or a cemetery-specific planning guide. That data was never supplied, and none is invented.
- No program primary contact (Kerry) user exists. Voicemail alerts now fall back to the organization's admins.

**Planning guide:**
- The platform now serves `/planning-guide` itself (staging: `https://sci-staging-backend.onrender.com/planning-guide`, which returns 200).
- SCI links default to the platform page. Set `SCI_PLANNING_GUIDE_URL=https://evosyspro.live/planning-guide` once that page is uploaded; it is 404 live today.
