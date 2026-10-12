# EvoSys Pro Wholesale — deployment/handoff

**Intended public URL:** `https://wholesale.evosyspro.live/` (not currently activated by this package)

**Brand:** EvoSys Pro Wholesale, by EVO Integrated Solutions LLC.

## Contents

- Six responsive public pages: `index.html`, `sell.html`, `buyers.html`, `platform.html`, `careers.html`, `contact.html`.
- SVG illustration and favicon. Shared CSS and JavaScript under `assets/`.
- Working investor buy-box intake: `intake.php` stores submissions privately above the document root on a PHP 8+ host; optional private-reference email alert.
- Mobile navigation, accessible FAQ accordions, animated reveal, validated investor form, SEO title/description tags.
- Existing live workflows deliberately reused:
  - Seller inquiries: `https://evosyspro.live/sell/` (existing PHP intake and distinct optional SMS consent).
  - Careers: `https://evosyspro.live/careers/` (existing Careers Manager; publish both job records there).
  - Product demo: `https://evosyspro.live/request-demo/`.

## GoDaddy setup (independent of AdvisorFlow backend)

1. In the DNS/domain host for `evosyspro.live`, create the `wholesale` subdomain and point it to a PHP-capable web hosting document root such as `public_html/wholesale/`. DNS target type depends on the hosting setup (A or CNAME). Don't overwrite apex or `www` DNS.
2. Upload the site's **contents**, not the parent folder, to that subdomain document root. Keep `build.py` and this guide off the public web root if preferred.
3. Configure TLS certificate for `wholesale.evosyspro.live` and force HTTPS.
4. PHP 8+ is required for buyer registration. Ensure writable private storage **outside any publicly served document root**. Set server environment `WHOLESALE_INTAKE_DIR` to a private directory, e.g. `/home/ACCOUNT/wholesale-private/`. By default `intake.php` uses `dirname(__DIR__,2)/evosys-wholesale-private`. It rejects a path inside `DOCUMENT_ROOT`.
5. Optionally set `WHOLESALE_NOTIFY_EMAIL` to an existing mailbox. Only a reference number is emailed, not personal data. Inquiries can be viewed by a trusted operator from the private hosting directory until the backend integration is done. There is **no admin dashboard** for buyer intake in this package.
6. Create the mailbox or alias `wholesale@evosyspro.live` in the existing email provider and test inbound mail before advertising it as active.
7. Test seller inquiry, buyer registration, careers, demo link, HTTPS, mobile navigation, and email alias. The buyer registration is a stand-alone secure intake; a future backend adapter may replace private-file storage after Claude finishes that integration.

## Important boundaries

- No platform/backend/database code has been modified.
- No Twilio automations or SMS marketing are triggered by this site. Buyer inquiry contact permission is not automated SMS consent.
- No property inventory or unverified financial returns are shown.
- The EvoSys Pro public website is currently versioned in the repository under `public-site/`, deployed manually to GoDaddy per `public-site/DEPLOYMENT.md`. The new site belongs in **`public-site/wholesale/`** and can deploy as a separate subdomain document root.
- Do **not** publish code with real secrets. Environment variables must be set in the host, not embedded in frontend files.
- If you want investor submissions flowing directly into EvoSys Pro, have Claude wire the buyer intake payload (`intent=buyer`, full_name, company, email, phone, markets, strategy, budget, notes, contact_permission) to the existing organization-safe wholesale intake after the API contract is approved. Until then, records are only stored privately on the web server.

## Quick local preview

Static preview: `python -m http.server 8000` from this directory, then open `http://localhost:8000/`. Investor submissions need PHP.

PHP preview: `WHOLESALE_INTAKE_DIR=/tmp/evosys-wholesale-private php -S localhost:8000` from this directory, then open `http://localhost:8000/`. Use test data only and delete the test private directory afterward.

## Publishing checklist

- [ ] Subdomain DNS + hosting folder provisioned
- [ ] TLS/HTTPS working
- [ ] Site assets uploaded and all pages responsive
- [ ] Private intake storage configured and buyer form tested
- [ ] Existing seller intake tested from new page
- [ ] Both wholesale careers roles published in Careers Manager
- [ ] `wholesale@evosyspro.live` alias created and tested
- [ ] Production emails and applicant privacy checked
- [ ] Optional EvoSys Pro wholesale backend integration (future)
