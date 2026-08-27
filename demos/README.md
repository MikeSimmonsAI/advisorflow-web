# Prospect demos

Two demos per prospect, both published on **our** domain, never on a third
party's. This folder holds the source of each one so the next prospect starts
from a working example rather than a blank file.

    demos/<prospect-slug>/website-concept.html      slot: "website"
    demos/<prospect-slug>/platform-walkthrough.html slot: "platform"

The full process — what to build, in what order, and why — is in the project doc
`PROSPECT_DEMO_SUITE.md`.

## Publishing

    POST /sales/opportunities/{id}/demo-site
      { "title": "...", "html": "<the file>", "slot": "platform" | "website" }
    -> https://app.evosyspro.live/demo/<token>

One live link per slot. Republishing a slot retires that slot's previous link and
leaves the other one alone. The token CHANGES on every republish, so update the
proposal and re-publish it so the portal blocks re-sync.

## Two rules these files must keep

**Handle your own in-page links.** These render inside
`<iframe srcdoc sandbox="allow-scripts">`, where a bare `href="#section"`
resolves against the HOST page's URL and reloads the app inside the frame.
Both files intercept `a[href^="#"]` and scroll themselves. Navigation driven by
`data-` attributes and buttons, as the platform walkthrough does, avoids the
problem entirely.

**Say what they are.** The website concept carries a design-concept bar; the
platform walkthrough carries a SAMPLE DATA banner stating that every name,
address, phone number and figure is invented. Contacts use the reserved
`555-01xx` block and `example.*` domains, the same convention Demo Mode enforces,
so a demo number can never reach a real person.

No integration status appears in a demo — no CONNECTED, SYNCED or HEALTHY. We do
not display what the backend cannot verify, and a demo is not an exception.

## Building the next one

Copy the closest folder, then replace the specifics — never the structure:

- **website-concept**: their market, their offer, the thing their current site
  gets wrong. For Building Equity Investments that was a buyer enquiry form on a
  business that needs seller leads.
- **platform-walkthrough**: their team, their lead flow, their sources. Keep the
  design tokens as they are — they mirror the real app in
  `frontend/src/index.css`, and a prettier fantasy is a promise we then have to
  keep.

Finish the reports screen on a number that names what they said they were
guessing at.
