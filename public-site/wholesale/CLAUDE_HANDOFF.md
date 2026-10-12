# Claude implementation order — EvoSys Pro Wholesale public site

**Project:** `MikeSimmonsAI/advisorflow-web`

**Site:** `wholesale.evosyspro.live`
**Email alias:** `wholesale@evosyspro.live`

A complete production-oriented microsite is supplied as `EvoSys_Pro_Wholesale_Website.zip` in the user's current ChatGPT conversation. The site contents are designed for `public-site/wholesale/` in the repository, with a separate PHP-capable GoDaddy subdomain document root.

## Your order

1. Inspect existing `public-site/DEPLOYMENT.md`, `public-site/sell/index.php`, and `public-site/careers/index.php`, and preserve their current live endpoints.
2. Unpack the wholesale site into `public-site/wholesale/` on a dedicated feature branch. DO NOT overwrite `public-site/index.html`, the existing `/sell/` form, `/careers/`, or any backend APIs. Do not modify ongoing backend work or production data.
3. Run HTML/CSS/JS/PHP syntax checks and desktop/mobile screenshot tests. The package has already passed initial validation and a direct test of the PHP buyer submission, but rerun in the target environment.
4. Publish the site through the existing GoDaddy hosting process to the subdomain document root. Confirm the DNS target, TLS, and safe hosting document root before modifying DNS. Do not touch main-domain records.
5. Set and validate `WHOLESALE_INTAKE_DIR` to a private directory outside web root; optional `WHOLESALE_NOTIFY_EMAIL`. Buyer profiles must be retained securely and not exposed by public URL.
6. Link `wholesale@evosyspro.live` to the existing mail environment as an alias, test inbound and reply-from behavior. Do not claim it's active before verified.
7. Keep homeowner CTA on `https://evosyspro.live/sell/`, careers application on `https://evosyspro.live/careers/`, software demo CTA on `https://evosyspro.live/request-demo/`. Do not build duplicates.
8. When backend team is ready, replace or bridge buyer-intake private file storage to the authorized wholesale CRM workspace via a server-to-server verified API. Keep property seller consents and investor contact permissions separate; do not add automated marketing SMS without proper opt-in.
9. Publish the two jobs in existing Careers Manager when the user has finalized compensation: `Wholesale Property Field Representative` and `Wholesale Acquisition Specialist`. Make the careers page point to those roles; currently it points to the existing careers index.
10. Return a factual go/no-go checklist: public site accessible, each page works, inquiry end-to-end, alias operational, actual CRM ingestion operational or not, all tests, blockers requiring owner's access. Never claim live deployment if DNS/host permissions unavailable.

**Operating style:** Work independently. Do not interrupt the owner for noncritical style choices. Keep deployment safe and do not overlap backend work. Final handoff should clearly distinguish done, pending, and needs-owner-action items.
