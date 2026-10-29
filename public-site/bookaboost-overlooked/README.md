# BookaBoost · Overlooked Properties — Public Site Candidate

**Status:** review/staging candidate only. **NOT deployed.** Owner approval required to replace current `bookaboost.live`. Issue: #425.

## Direction
Nationwide premium property opportunity positioning. Signature “Overlooked Properties — Find What Others Miss.” Four areas: Residential, Wholesale, Land/Farms/Ranches, Commercial. Public experience free; professional access via `https://app.bookaboost.live`.

## Files & test
Static site: `index.html`, `styles.css`, `app.js`, `assets/favicon.svg`. Deploy by uploading all files/folders together into the **existing public website's document root only after confirming the hosting configuration and making a backup**. Do not upload into the app subdomain root, nor alter backend, auth or tenant configuration. `python -m http.server 8080` from this folder runs a local preview, then visit http://localhost:8080.

The site currently uses remote Unsplash photography (`images.unsplash.com`) with layered CSS color fallbacks. Verify asset licensing/availability and consider locally hosted optimized photographs before production. This build does not create or modify accounts, grant access, launch outbound communication, connect a payment processor, or expose data. No fabricated listing inventory, performance numbers, fake testimonials, or pricing claims.

## User-visible interactions
- Mobile menu opens/closes, closes on Escape and link click.
- Category links prepare a search-request mailto, no pretend live property API.
- The form validates category and location, then prepares an **email in the visitor's own email app** to `support@bookaboost.live` with selected criteria. No submission is sent by the site without their email action. Works only when a local mail handler is configured. If not, user can contact support address manually.
- Professional login links go to the existing app and remain entirely untouched.
- Opportunity Radar visibly labeled **in development/concept**. Not a live scored-property result.
- Subscription interest uses support mailbox. No pricing is invented.

## Production readiness gates
- [ ] Confirm the actual GoDaddy/static hosting document root and source backup.
- [ ] Owner visual signoff at 1440px, tablet, 390px.
- [ ] Test photo availability and licensing; ideally host compressed real-estate photos locally.
- [ ] Verify `support@bookaboost.live` is a monitored mailbox (the old site links to it; deliverability is not confirmed).
- [ ] Test navigation, menu, tab/keyboard focus, contact email links, form validation and mobile layout in a real browser.
- [ ] Verify legal Privacy Policy/Terms paths and real brand/business contact information before publication. Legal links intentionally withheld rather than pointing at the legacy funeral/SMS documents.
- [ ] Decide public lead capture integration. No live marketplace/MLS search or AI concierge is falsely claimed.
- [ ] Obtain separate owner GO for publishing to `bookaboost.live`; leave `app.bookaboost.live` as-is.