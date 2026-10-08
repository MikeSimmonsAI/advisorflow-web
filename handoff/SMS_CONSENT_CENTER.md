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
4. **Twilio** (Mike):
   - register the new campaign;
   - buy the numbers;
   - add the registry entry with status PENDING.
5. **After Twilio shows Verified:**
   - set the entry to VERIFIED;
   - live-test reply, click and call with Mike's own phone;
   - then `SCI_SMS_SEND_ENABLED=on` only after a separate first-contact GO.
