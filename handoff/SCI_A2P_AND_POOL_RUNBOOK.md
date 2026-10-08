# SCI: six pool numbers, A2P readiness, inbound routing and callbacks

Written 2026-10-08 CT on `sci-program` (Desktop session). Staging only. **Nothing has been bought, registered, submitted or sent.**

## 0. Account structure (Mike, 2026-10-08 12:41 CT)

- **EvoSys Pro owns the Twilio infrastructure.**
  - The six SCI POC numbers are bought on the existing EvoSys Pro account.
  - They are assigned to the SCI POC workspace (`phone_numbers.organization_id` = the SCI org, label `pool:<id>`).
  - SCI is a proof of concept under EvoSys Pro, not an activated customer account.
- **How the POC is verified in code:** inbound webhooks signed by the EvoSys Pro (platform) account resolve through `platform_account()`, which reads `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` from the environment. The number row decides which organization receives the message, so SCI data stays in the SCI org.
- **Future paying customers** each get their own Twilio account or subaccount, stored on their organization (`org_twilio_account_sid` / encrypted token). Each has separate credentials, billing, registrations, numbers and program configuration.
  - The inbound guard resolves the signing account. Since 2026-10-08, a **customer-signed** text may only reach a number that customer's organization owns (`sms_router._check_tenant_owns_called_number`, matching the voice rule).
  - Before this change, a tenant holding its own valid credentials could sign a message naming another organization's number. That gap is closed and tested in `tests/test_sci_pool_staging.py`.
- **POC to production:**
  - POC numbers and A2P registrations are not assumed to transfer.
  - A converting customer gets a fresh account or subaccount with its own numbers and registrations. Any port or transfer is a deliberate, separate step.
  - The POC rows are retired from the SCI org (`is_active=False`, never deleted) only after the new numbers are live.

## 1. Purchase summary (pending Mike's final confirmation)

Mike approved the six area codes on 2026-10-08 12:23 CT and asked for a priced summary of exact numbers **before** anything is bought.

| Pool | Area code | Exact number | Monthly |
|---|---|---|---|
| pool-205-birmingham | 205 | _pending live Twilio search_ | $1.15 |
| pool-334-montgomery | 334 | _pending_ | $1.15 |
| pool-850-pensacola | 850 | _pending_ | $1.15 |
| pool-251-mobile | 251 | _pending_ | $1.15 |
| pool-706-columbus | 706 | _pending_ | $1.15 |
| pool-318-shreveport | 318 | _pending_ | $1.15 |
| **Total** | | | **$6.90 / month** |

**Prices** come from Twilio's US pricing page, read on 2026-10-08:
- Local long code: $1.15/month (bring-your-own: $0.50). No setup fee is listed for a local number.
- Usage, only when used: SMS $0.0083 per segment outbound and inbound, plus carrier pass-through of about $0.0025–$0.0045 per message.
- Voice minutes are billed per Twilio's voice pricing.

**Blocker for the exact numbers:** the Twilio console session in Chrome is signed out. Claude cannot enter Mike's password. Once Mike signs in, Claude searches each area code (Local, SMS + Voice capable) and fills in the table. Purchase happens only after Mike confirms the filled table.

**A2P costs** (Twilio support article, fees effective 2026-08-01; re-check before submitting). These apply only when real outbound SMS is pursued:
- Brand registration (one-time): $4.50 low-volume standard / sole proprietor, or $46 standard (includes secondary vetting). If the existing EvoSys brand is reused as the ISV, no new brand fee applies.
- Campaign vetting: $15 per campaign.
- Monthly campaign fee: Standard use cases $10/month; Low Volume Mixed $1.50/month.
- Assumption: AGENTS_FRANCHISES is billed as a standard use case ($10/month); confirm in the console at submission.

## 2. After purchase: wiring (Claude, staging only)

1. In Twilio, for each number:
   - Messaging webhook: `POST https://sci-staging-backend.onrender.com/sms/webhook/inbound`
   - Voice webhook: `POST https://sci-staging-backend.onrender.com/voice/inbound`
   - `GET /god/staging/sci/pool-numbers` prints both targets.
2. Record the numbers on staging (dry run first; the request is refused whole if any entry is wrong):
   ```
   POST /god/staging/sci/pool-numbers {"numbers": {"205": {"e164": "+1205…", "sid": "PN…"}, …}}            # dry run
   POST /god/staging/sci/pool-numbers {"numbers": {…}, "apply": true}                                       # write
   ```
   - Each row is written with `workspace_id` NULL, label `pool:<pool id>`, SMS + inbound voice + voicemail, and **no outbound voice**.
   - The request is refused if:
     - an area code does not match its number;
     - the number is a 555 fictional number or the 844 backup;
     - the number already belongs to another org or purpose;
     - `APP_ENV` is not `staging`.
   - Re-applying the same numbers is a no-op.
3. **Credential (needs Mike's authorization; credential change):**
   - Inbound webhooks are signature-checked with the auth token of the Twilio account that owns the number: here, the EvoSys Pro account.
   - On `sci-staging-backend`, set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` to the EvoSys Pro account (platform account). Do **not** store it as the SCI org's own credential: SCI is not a customer account yet.
   - Mike enters these in Render; Claude never types or reads a secret.
   - Production is unaffected; this is the staging service only.
4. Proofs, all on staging with Mike's own phone as the only real handset:
   - known-contact text to each pool;
   - unknown-sender text → regional review queue;
   - STOP from an unknown number → suppressed;
   - call → neutral greeting, then voicemail + transcript;
   - missed call → follow-up draft (not sent).

## 3. Inbound routing (built and simulated today)

- **Pool number = no location.** A known sender is matched by phone inside the SCI org and is routed to **their own** entity. An unknown sender goes to `regional_review:<pool id>` with no location (`sms_router.process_inbound_sms`, `called_pool_id`).
- **STOP** from anyone, matched or not, is suppressed for the org before anything else.
- **Voice:**
  - The greeting names no location on a pool number.
  - A voicemail from an unknown caller goes to the regional review queue (`program_voice.on_voicemail_text`).
  - A missed call with no voicemail records one ACTIVE follow-up with a draft (`program_voice.on_missed_call`). Nothing is sent.
- **Proven on staging without a carrier:** `POST /god/staging/sci/simulate/pool_round_trip`. It runs the real handler with the pool id the webhook passes, creates no number row and sends nothing. Tests are in `tests/test_sci_pool_staging.py`.

## 4. Callback workflow and what it still needs

- A missed call or voicemail creates a follow-up with a suggested reply. Staff call back with the platform's click-to-call (`POST /calls/human`). This rings the staff member's **verified callback phone** first (`PUT /telephony/me/callback-phone` + verify), then bridges the family.
- **Open decisions (Mike / SCI):**
  - Whose phone rings for callbacks: Michael, location staff, or Kerry? Each person verifies their own number; none is invented.
  - Whether outbound callback calls show the pool number. That needs `cap_voice_outbound` turned on per pool number. It is off by default.
  - Who receives **location-level ("primary") alerts**. Staging reports "no phone/email configured for primary alerts". Only the management email (Michael) is delivered today.

## 5. A2P / carrier readiness (Mike decides and attests; Claude submits nothing)

What a registration needs, and where each piece stands:

| Field | Draft / status |
|---|---|
| Brand | Which brand appears in the texts? The copy says "Kerry Allan with {location}" (an SCI location). If EvoSys registers as the ISV, the campaign must describe SCI-location outreach run on the EvoSys platform. **Mike to confirm.** |
| Use case | AGENTS_FRANCHISES (multi-location business messaging on behalf of locations). The alternative is Low Volume Mixed under a correctly described brand. Do **not** attach these numbers to the existing EvoSys Low Volume Mixed campaign `CO3YNIF`; that would misdescribe the sender. |
| Campaign description | Draft: "Follow-up messages from a named representative of a specific funeral home / cemetery location to families who requested planning information from that location; replies are handled by staff; no marketing links." |
| Sample messages | Neutral Variant N (16/16 quality gate): `Hi {first}, this is Kerry Allan with {loc}. {Why}. Anything I can help with? Just reply here. - Kerry Allan, {loc} Reply STOP to opt out.` |
| Opt-in (how numbers were collected) | **UNRESOLVED and the deciding question.** The import recorded **0 contacts with SMS consent** (`SCI_OVERNIGHT_2026-10-05.md`). These contacts requested information from SCI locations, and the source does not show that they agreed to texts. A registration must describe a real opt-in; Claude will not invent one. Options: (a) SCI provides the documented opt-in language and the capture point; (b) email first, and text only contacts who give consent, for example by reply; (c) text only in response to a family's own inbound text or call. |
| Opt-out / help | STOP handling is built and global ("Can I stop by Friday?" is not an opt-out). The HELP reply text still needs wording: location name + "Reply STOP to opt out". |
| Message features | No links, no phone numbers in the body (matches the copy). Volume: ~535 contacts, at most a few touches each. Low volume. |

Voice and voicemail need **no** 10DLC. Pool numbers can carry inbound text and calls before any campaign is approved. **Outbound** SMS to anyone stays OFF until a matching campaign is approved and Mike gives a separate GO.

## 6. Still owner/external

- Twilio sign-in for the number search, and the final purchase confirmation.
- Twilio auth credential for staging.
- A2P brand and opt-in attestation.
- Callback phone owners and primary alert recipients.
- Kerry identity (D2).
- Oaklawn (C1).
- Footer postal addresses (C7).
- Michael's activation (B2).
- Production promotion (D4).
- First-contact GO (D5).
