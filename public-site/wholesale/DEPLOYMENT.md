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
5. **Connect investor registrations to EvoSys (recommended).** Set server environment `WHOLESALE_BUYER_INTAKE_URL` to `https://<platform API host>/site-intake/wholesale/<intake key>/buyer-inquiry`, using the SAME intake key the `/sell` page uses (Wholesale Settings → public intake key). Each registration then appears in EvoSys Wholesale → Buyers as a buyer with a buy box (source "website"), and the admins get an in-app notice. The private file is still written first and records whether the hand-off worked (`"evosys": {"sent": true}`), so nothing is lost if the platform is down. Nothing is texted or emailed to the investor.
6. Optionally set `WHOLESALE_NOTIFY_EMAIL` to an existing mailbox. Only a reference number is emailed, not personal data.
7. Create the mailbox or alias `wholesale@evosyspro.live` in the existing email provider and test inbound mail before advertising it as active.
8. Test seller inquiry, buyer registration, careers, demo link, HTTPS, mobile navigation, and email alias. Check a test registration reached EvoSys Wholesale → Buyers, then mark it as a test or delete it there.

## Important boundaries

- No platform/backend/database code has been modified.
- No Twilio automations or SMS marketing are triggered by this site. Buyer inquiry contact permission is not automated SMS consent.
- No property inventory or unverified financial returns are shown.
- The EvoSys Pro public website is currently versioned in the repository under `public-site/`, deployed manually to GoDaddy per `public-site/DEPLOYMENT.md`. The new site belongs in **`public-site/wholesale/`** and can deploy as a separate subdomain document root.
- Do **not** publish code with real secrets. Environment variables must be set in the host, not embedded in frontend files.
- Investor submissions reach EvoSys Pro through `WHOLESALE_BUYER_INTAKE_URL` (step 5). Without it, records are only stored privately on the web server.

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
- [ ] `WHOLESALE_BUYER_INTAKE_URL` set; a test registration shows up in EvoSys Wholesale → Buyers
