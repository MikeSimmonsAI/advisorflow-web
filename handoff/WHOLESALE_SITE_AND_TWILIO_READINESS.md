# EvoSys Wholesale — public site, seller flow and Twilio campaign readiness

Last updated: 2026-09-27. This document covers the public website pass and the
material a Twilio campaign reviewer will check. **Nothing in Twilio was
touched.** No brand, campaign, Messaging Service, number, Advanced Opt-Out
setting or webhook was created or changed. No SMS was sent.

## 1. Brand relationship (as the public pages now state it)

| Layer | Name | Where it appears |
|---|---|---|
| Legal business | EVO Integrated Solutions LLC | Every footer, the SMS disclosure, Privacy §3, Terms §10, SMS Terms §8 |
| Platform | EvoSysPro | Homepage; /sell footer ("operated under the EvoSysPro platform") |
| Product | EvoSys Wholesale | Homepage product band, /sell header and footer, legal pages |
| Seller entry point | https://evosyspro.live/sell | Seller inquiry form and the **only** Wholesale SMS opt-in |

## 2. URLs

| Page | URL | Anchor a reviewer should open |
|---|---|---|
| Homepage | https://evosyspro.live/ | `#wholesale` (EvoSys Wholesale product band) |
| Seller inquiry + opt-in | https://evosyspro.live/sell | `#seller-form`, box `#f-sms_consent` |
| Privacy Policy | https://evosyspro.live/privacy.html | `#sms`, `#sms-wholesale` |
| Terms of Use | https://evosyspro.live/terms.html | `#sms-wholesale` (Section 10) |
| SMS Terms | https://evosyspro.live/sms-terms.html | `#wholesale` (Section 8) |
| EvoSys Pro SMS Opt-In (the other program) | https://evosyspro.live/sms-optin/ | Not the Wholesale program |

Public Wholesale contact: **469-553-7417**, the business callback number. It
comes from `WHOLESALE_PUBLIC_PHONE` in the web host's `private/config.php`. It
is **not** the SMS sender. The Wholesale SMS number is not assigned yet.

## 3. The seller flow

1. The seller lands on `/sell`, whether from the homepage "Sell a Property"
   button, a footer "EvoSys Wholesale" link, or directly.
2. They fill in the inquiry: property, situation, contact details and
   preferred contact method.
3. **Optional SMS Opt-In** (section 4 of the form). The heading reads
   "Optional SMS Opt-In", the subheading "Want text updates about your
   property inquiry?". It has three short plain-language facts and one
   checkbox whose label is the registered disclosure, verbatim.
4. "Send my property inquiry" submits. The note beside the button says
   submitting does not sign anyone up for texts.
5. The web host posts server-to-server to the platform intake
   (`/site-intake/wholesale/{key}/seller-inquiry`). The browser never sees
   that URL or the key.
6. The platform runs Universal Intake (dedupe, identity and lead linkage). If
   and only if `sms_consent` is true, it writes an `SmsConsentRecord` with the
   platform's own timestamp, the verbatim disclosure, the page URL, IP and
   user agent.

### Consent behaviour (unchanged, now covered by page-level tests)

* It is separate from the inquiry, optional, and **unchecked by default**.
  The only way it renders checked is echoing back the person's own tick after
  a validation error.
* It requires an affirmative act: only `sms_consent=yes` counts. An omitted
  field, an empty value, `on` or `true` all grant nothing.
* It is independent of the Terms. There is no Terms checkbox and no bundling.
  It is not required to submit.
* There is one consent path. There is no second checkbox, and nothing on the
  EvoSys Pro `/sms-optin/` page enrolls anyone in the Wholesale program.
* Choosing "Text message" as the contact method without ticking the box is
  refused with an explanation. Nothing is sent and nothing is inferred.

### Registered disclosure (verbatim — do not edit without re-registering)

> By checking this box, I agree to receive SMS text messages from EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro) regarding my property inquiry, including follow-up questions, appointment scheduling, and transaction updates. Message frequency varies. Message and data rates may apply. Reply STOP to unsubscribe, HELP for help. Consent is not a condition of any service. View our Privacy Policy and Terms.

Disclosure version `evo-wholesale-sell-2026-09-25`; form version `sell-v1`.

## 4. What changed in this pass

| File | Change |
|---|---|
| `public-site/sell/index.php` | **Top navigation:** EvoSys Wholesale brand, EvoSysPro Home, How It Works, Situations We Help and Get Started. Below 1024 px these move into an accessible `<details>` Menu. **Consent block:** heading "Optional SMS Opt-In", subheading "Want text updates about your property inquiry?", three short facts, larger checkbox, gold top rule. **Footer:** adds SMS Terms and "EvoSysPro Home". The checkbox, its name and value, the verbatim label, the payload and the validation are **unchanged**. |
| `public-site/index.html` | Adds one product band: "EvoSys Wholesale — Property acquisition and wholesaling powered by EvoSysPro" with a "Sell a Property" button to /sell. The SMS card gains a one-line note that Wholesale seller texts use a separate opt-in on /sell. The header SMS Opt-In button is untouched. |
| `public-site/sms-optin/index.php` | Adds one side card: this page is for EvoSys Pro service messages, and sellers opt in on /sell. Consent text, form, storage and `source_url` are unchanged. |
| `public-site/privacy.html` | §1 now names the seller-inquiry data. §2 adds "review and respond to property inquiries". §3 Wholesale HELP line gains the phone and email. |
| `public-site/terms.html` | §10 HELP line now gives the phone and email instead of pointing to §11. It adds the mobile-information non-sharing sentence and a link to SMS Terms. |
| `public-site/sms-terms.html` | New §8 "EvoSys Wholesale Seller Messages" covering identity, opt-in, message types, frequency, rates, STOP, HELP and support, non-sharing, and links to Terms §10 and Privacy §3. The old §8 is renumbered to §9. |
| Footers (all static pages + `private/site.php`) | Add an "EvoSys Wholesale" link to /sell. |
| `tests/test_public_site_sell_page.py` | 18 tests: static checks plus a rendered `php -S` submission against a local stand-in intake. |

All URLs are unchanged. No backend file was changed.

## 5. Twilio campaign readiness — reviewer checklist

| Reviewer check | Status | Evidence |
|---|---|---|
| Opt-in is public (no login) and reachable from the brand's site | PASS | Homepage band and footers link to /sell |
| Business name in the CTA matches the brand | PASS | "EVO Integrated Solutions LLC (operating as EvoSys Wholesale / EvoSysPro)" |
| Checkbox is unchecked and not required | PASS | Source plus rendered test |
| Program and message purpose disclosed | PASS | "regarding my property inquiry, including follow-up questions, appointment scheduling, and transaction updates" |
| Frequency, rates, STOP, HELP at the point of opt-in | PASS | Disclosure plus facts list |
| "Consent is not a condition" | PASS | Disclosure plus facts |
| Privacy and Terms linked at the point of opt-in | PASS | Label links to Privacy §3 and Terms §10 |
| Privacy: opt-in data not shared with third parties | PASS | Privacy §3 (both general and Wholesale), Terms §10, SMS Terms §8 |
| Terms: program name, description, frequency, rates, STOP, HELP, support contact | PASS | Terms §10, SMS Terms §8 |
| Separate from the existing approved campaign's opt-in | PASS | /sms-optin/ is EvoSys Pro service messages; /sell is Wholesale; each page says so |
| Sender number and Messaging Service exist | **OWNER** | Not created (by instruction) |

### Values to paste at submission (proposed; nothing submitted)

* **Brand:** EVO Integrated Solutions LLC (existing, approved — do not modify).
* **Use case:** Low Volume Mixed.
* **Campaign description:** EVO Integrated Solutions LLC, operating as EvoSys Wholesale, sends text messages to property owners who submit a seller inquiry on https://evosyspro.live/sell and check the optional SMS consent box. Messages cover follow-up questions about the property inquiry, appointment scheduling and transaction status updates. No marketing to purchased or scraped lists. Phone numbers found in public records or data providers are never messaged without web-form consent.
* **Message flow / call to action:** Property owners opt in on https://evosyspro.live/sell by submitting the seller inquiry form and checking the optional, unchecked-by-default box labelled with the disclosure in §3 above. The box is not required to submit the form, and consent is not a condition of any service. Links to the Privacy Policy (https://evosyspro.live/privacy.html#sms-wholesale) and Terms (https://evosyspro.live/terms.html#sms-wholesale) sit in the checkbox label. After opt-in, a confirmation message is sent.
* **Opt-in confirmation (registered in code):** `EvoSys Wholesale: You're subscribed to messages about your property inquiry. Msg frequency varies. Msg & data rates may apply. Reply STOP to opt out, HELP for help.`
* **HELP reply:** `EvoSys Wholesale Support: For help call 469-553-7417 or email support@evosyspro.live. Reply STOP to cancel. Msg & data rates may apply.`
* **STOP reply:** `EvoSys Wholesale: You are unsubscribed and will receive no further messages. Reply START to resubscribe.`
* **Sample messages:**
  1. `EvoSys Wholesale: Hi {first_name}, thanks for your inquiry about {street}. What's a good time for a quick call this week? Reply STOP to opt out.`
  2. `EvoSys Wholesale: Confirming your walkthrough of {street} on {date} at {time}. Reply C to confirm or R to reschedule. Reply STOP to opt out.`
  3. `EvoSys Wholesale: Update on {street}: the purchase agreement is ready for your review. Reply HELP for help, STOP to opt out.`
* **Opt-out keywords:** STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT. **Help keywords:** HELP, INFO.
* **Embedded links / phone numbers in messages:** No embedded links; a phone number only in HELP.
* **Privacy URL:** https://evosyspro.live/privacy.html. **Terms URL:** https://evosyspro.live/terms.html.

## 6. Test data safety

Live verification used one clearly marked test inquiry per path. The
submissions are named `ZZTEST …` at `… Test Fixture Ln`, use 555-01xx numbers
and have no email. The Wholesale SMS program stays **OFF**: no Messaging
Service is configured and the gate refuses every send. No email, SMS or
cadence goes out. Test records are marked, not deleted, so audit history
stays whole.

## 7. Rollback

* **Web host:** before each write, the exact host copy was saved byte-for-byte
  inside the web-denied `private/` folder as
  `private/bak-20260927__<path with / replaced by __>`, for example
  `private/bak-20260927__sell__index.php`. The folder returns 403 publicly;
  this was checked. To roll back, copy a backup's contents over its file in
  cPanel File Manager, or re-upload `public-site/` from git at `33acf17`.
  `private/bak-20260927__test__site.php` is a copy of `site.php` used to test
  byte-exact saving, and is safe to delete.
* **cPanel note:** cPanel's save API rewrites the `<meta charset>` position
  when saving these pages. On `index.html` it drops the `/`; on
  `sell/index.php` it puts it on the `<head>` line. The host copies have
  always differed from git in this way only, and deployment verification
  allows for exactly that and nothing else.
* **Repository:** `git revert <this commit>`. The backend is unaffected, since
  no backend file changed, so the Render deploy is a no-op for behaviour.

## 8. Live verification (2026-09-27)

* **Upload.** All 12 site files were written through the cPanel API. Each was
  hash-checked before writing, to confirm the host held the expected prior
  version, and again after writing, to confirm it now equals the git version.
* **/sell, desktop 1366 px.** The navigation shows EvoSysPro Home, How It
  Works, Situations We Help and Get Started. Anchors `#how`, `#situations` and
  `#seller-form` resolve. No horizontal overflow.
* **/sell, mobile 375 px.** The Menu opens, all four items fit, and choosing
  one scrolls to the section and closes the menu. No overflow. The heading
  "OPTIONAL SMS OPT-IN" is visible and the box is unchecked (`checked`,
  `defaultChecked` and `required` are all false). The contact shown is
  469-553-7417.
* **Footer links.** Privacy (`#sms`), Terms (`#sms-wholesale`), SMS Terms
  (`#wholesale`) and EvoSysPro Home all return 200. The `private/` backups
  return 403.
* **Submission without SMS.** Reference SI-32F35658 ("ZZTEST Live NoSMS
  0927", 214-555-0141). The inquiry was accepted, the thank-you page says no
  texts, and the platform holds **0** consent records for the number.
* **Submission with SMS**, box ticked by a real click. Reference SI-98F31798
  ("ZZTEST Live SMS 0927", 214-555-0143). One `opted_in` consent record was
  written: source `https://evosyspro.live/sell`, version
  `evo-wholesale-sell-2026-09-25`, verbatim disclosure. The confirmation text
  was **refused by the gate** with `PROGRAM_DISABLED,
  MESSAGING_SERVICE_NOT_CONFIGURED`. Eligibility reads `eligible: false`.
  Program status: enabled false, can_send false, no Messaging Service, no
  campaign, no sender.
* **Test records.** Both test leads are named ZZTEST and their notes say "Not
  a real seller. Do not contact." They are left in place for audit history.
* **Other pages** (homepage, Privacy, Terms, SMS Terms, SMS Opt-In) at
  375 px. All render with no overflow, and the required SMS language is
  present on each.
* **Tracerfy plan** `078a8709-…`: still `planned`, no spend.

## 9. Remaining before Twilio submission (owner / external only)

1. Create the Wholesale Messaging Service and buy or assign the Wholesale
   number. Both need your Twilio console access and are paid.
2. Submit the Low Volume Mixed campaign under the existing brand, using §5.
   The submission is external and paid.
3. After approval, put the MG SID and number into the Wholesale settings, and
   configure Advanced Opt-Out with the §5 HELP/STOP text. Only then consider
   turning the program on.
4. Optional: create `wholesale@evosyspro.live` and set `WHOLESALE_PUBLIC_EMAIL`.
   Until then, support@evosyspro.live is the published address.
