# EvoSys e-signature - built-in signing (2026-10-10)

Branch `wholesale/evosys-esign`. Replaces "pay DocuSeal per document" as the default:
signing is now part of the platform, $0 per contract. DocuSeal stays available as an
opt-in (`WHOLESALE_ESIGN_DEFAULT=docuseal` with its key, or an org's esign_provider).

## The flow
1. Deal -> Contracts -> Ready-made contracts -> Send for e-signature.
   `POST /wholesale/deals/{id}/contracts/{kind}/send` creates the WholesaleDocument and an
   **envelope** (`esign_envelopes`) holding the exact document HTML and its SHA-256.
2. Signer 1 gets an email: "Please sign: ..." with a private link
   `{WHOLESALE_ESIGN_PUBLIC_URL or FRONTEND_URL or https://app.evosyspro.live}/sign/<token>`.
   Only the SHA-256 of the token is stored; resend issues a new token (old link dies).
3. The signer's page (`frontend/src/pages/portal/SignDocument.jsx`, phone-first):
   read the document -> "Email me a code" (6 digits, 10 min, 5 tries, 5 codes, 45 s apart)
   -> consent box (ESIGN 7001(c) disclosure) -> typed name or drawn signature -> Sign.
4. Next signer is emailed automatically (signing order: seller then you; buyer then you).
5. After the last signature the envelope is **sealed**: `esign_pdf.render_signed_pdf` builds
   the contract + signatures + Certificate of Completion (signers, verification, consent,
   IPs, browsers, full audit trail, document fingerprint); the PDF's SHA-256 is stored;
   everyone (signers + sender) gets "Completed: ..." with the PDF attached; the deal's
   document becomes Signed and deal.contract_status / assignment_status = signed.
6. Decline: the signer can decline with a reason; sender is emailed; document Declined.
7. Sender controls (Documents list, `SigningProgress`): Waiting on X / Resend link /
   Withdraw / Details (per-signer status, fingerprint) / Download signed PDF.
8. Links expire after 30 days (`EXPIRE_DAYS`).

## Files
* `app/models/esign_models.py` - esign_envelopes, esign_signers, esign_events (registered in
  app/models/registry.py; tables are created by create_all at deploy - no migration file).
* `app/services/evosys_esign.py` - the engine (send, view, code, verify, sign, decline,
  finalize, remind, void, summary).
* `app/services/esign_pdf.py` - ReportLab PDF + certificate. **New dependency:
  `reportlab==4.2.5`** (pure-Python; pulls pillow + chardet) in requirements.txt.
* `app/routers/esign_public_router.py` - `/esign/sign/{token}` (+ /code /verify /sign /decline
  /signed.pdf). No account; the token is the authorization.
* `app/routers/wholesale_contracts_router.py` - built-in branch in contract send; sender routes
  `GET /wholesale/documents/{id}/esign`, `POST .../esign/remind`, `POST .../esign/void`,
  `GET /wholesale/documents/{id}/signed.pdf`.
* `app/services/wholesale_esign.py` - `EvoSysSignatureProvider` (key `evosys`, always on);
  `active_provider` now defaults to it.
* Frontend: `/sign/:token` route in App.jsx; `SigningProgress` in wsDocuments.jsx.

## Email
Sent through `email_service.send_email_via_provider` with the organization (brand-resolved
Resend key / from address), message types `esign_link`, `esign_code`, `esign_completed`,
`esign_declined`, `esign_voided` (all default to "sensitive": no audit BCC). If Resend is not
configured the send route still records the envelope and says the email did not go out;
"Resend link" retries.

## Verified
* `tests/test_evosys_esign.py` (8): full journey through the real routes, link hashing, code
  required, consent required, turn order, drawn + typed signatures, sealing + fingerprint
  matches the emailed attachment, sender download, deal signed, resend kills old link, void,
  decline, expiry, code rate limit, bad PNG refused.
* Cross-tenant attack list extended (esign status / remind / void / signed.pdf).
* Real browser run on the PC (built-in browser, phone size 375x812): seller drew a signature
  on the pad, buyer typed, completion emails carried the PDF, the downloaded PDF's SHA-256
  equalled the emailed one (038d2ae2...).

## Owner steps
1. Merge the PR (Render installs reportlab and creates the three tables on deploy).
2. Make sure `FRONTEND_URL` (or `WHOLESALE_ESIGN_PUBLIC_URL`) on advisorflow-backend is the
   address people should open, e.g. https://app.evosyspro.live - the signing link uses it.
3. Send a test contract to your own two email addresses and sign both.
4. Have a Texas real estate attorney review the starter contracts and confirm e-signature use.
