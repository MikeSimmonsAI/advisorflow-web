# EvoSys Wholesale: Phase 7.3 Closeout

**Scope:** product naming, public contact configuration, and the brand-resolution acceptance gate.

- Branch: `feature/wholesale-evosense-p7`, on top of the approved Phase 7.3 commit `8f9bcfd`, which was **not** rewritten.
- `main` was not touched and nothing was deployed.
- The Phase 7.3 visual design is unchanged. The only visible additions are:
  - the product line under the brand mark;
  - the browser tab title;
  - a "Public contact" tab in Wholesale Settings;
  - the configured public contact in the Seller Portal masthead, using the same pattern as the Investor Deal Room.

## Naming hierarchy

EVOSYSPRO (platform/brand) -> **EVOSYS WHOLESALE** (product) -> **EVOSENSE** (acquisition/intelligence engine).

| Where | Before | After |
|---|---|---|
| Sidebar, under the brand mark (Wholesale screens) | logo only | "EvoSys Wholesale" / "Powered by EvoSense" |
| Browser tab (Wholesale screens) | "EvoSys Pro" | "EvoSys Wholesale" |
| Wholesale Settings | 10 tabs | + "Public contact" |

Every existing "EvoSense" label (Acquisition Command, Strategies, Property Intelligence, the engine status copy) already names the engine and is correct. None calls EvoSense the platform or the whole product.

Internal names are unchanged: `/wholesale/evosense/*`, `app/services/evosense/*`, the models, tables and APIs.

The product name is **brand-owned**. It lives in `brand_config.PRODUCT_NAMES` and is served on `GET /branding/org` as `platform.products.wholesale`. A brand without a name shows the neutral "Wholesale" and never borrows EvoSysPro's.

## Localhost brand resolution

1. On a brand domain, the host decides the brand.
2. On a non-brand host (localhost), the **authenticated organization's platform** decides it (`/branding/org -> platform`).
3. Only when neither says anything is the historical default used. That case is reported as `source = default`, a **FALLBACK**, and the gate fails it.

Nothing is hardcoded for localhost.

The shell stamps these attributes on the layout root:

- `data-brand-theme`, `data-brand-source`, `data-brand-platform`
- `data-org-id`
- `data-product`

Two fixes along the way:

- **"Back to website".** It used the hostname brand, which linked to BookaBoost's site on localhost. It now uses the workspace brand.
- **Late host answer.** A host-level `/branding` answer arriving after the workspace theme can no longer override it on a non-brand host.

## Public Wholesale contact

- **Setting.** `wholesale_settings.public_contact_phone` and `public_contact_email`, edited in Wholesale Settings -> Public contact and saved through `PATCH /wholesale/settings`. Both values are validated, and a blank value clears the field.
- **Resolution.** `wholesale_publication.public_contact()` reads **only this organization's** row and never creates one. It feeds `brand.support_phone` and `support_email` in both room payloads, with `contact_source = "wholesale_settings"`.
- **Fallback:** none.
  - Missing configuration means no public contact is shown.
  - Nothing falls back to the platform's support line or address.
  - Nothing is ever taken from another organization.
- **EvoSysPro configuration.** The review seed sets the approved phone **469-553-7417** through this setting, and only if the field is empty.
- **Mobile.** The masthead contact stays hidden at phone widths, as the approved responsive rule already does.

## Email alias: wholesale@evosyspro.live

**WHOLESALE EMAIL ALIAS REQUIRES EXTERNAL CREATION.**

- The address is not configured anywhere in the repo or `.env`.
- The Microsoft 365 connector is signed in as `support@evosyspro.live`. No mail has been addressed to wholesale@, and the connector lacks the scope needed to list aliases.
- The application is ready for it: set it under Wholesale Settings -> Public contact once the alias exists.
- The platform support address is not substituted.

## Permanent brand gate

These pieces make up the gate:

- `scripts/review/brand_gate.py`: reusable across any module or tenant.
- `p73look.py`: runs the gate on every screen and writes `brand-gate-report.json`.
- `tests/test_brand_resolution_gate.py`: the backend half.
- `tests/frontend/brandResolution.test.mjs`: the shell's resolution logic.
- `tests/test_wholesale_p73_public_contact.py`: the public-contact rules, tenant isolation and missing-config behaviour.

## Results

| Check | Result |
|---|---|
| Windows acceptance walk | 124 screen-widths |
| Windows brand gate | organization EvoSense Review (TEST) `acc04387-77b2-4e93-9835-5a559ebd87f2`; platform `evosyspro`; shell source `workspace` on every screen; no fallback; 0 brand failures |
| Windows tab title | "EvoSys Wholesale" |
| Windows public contact | `469-553-7417` from `wholesale_settings`; no email |
| BookaBoost / Restland / cemetery / funeral text | none on any screen |
| Stop/start | twice (0 listeners after stop, ready after start, migration applied, seed set the phone) |
| Windows targeted tests (47 files) | **1,004 passed, 0 failed** |
| Cloud targeted tests | 535 passed |
| Cloud full regression | 5,652 passed, 16 skipped |
| Frontend tests | 7/7 files pass (brandResolution 8/8, wholesaleNav 11/11); godTheme has 2 failures that were already there before this work |
| axe (settings, command, both rooms) | 0 violations |

Failures in the cloud full regression:

- `test_zoom_integration` (1 failure) was already failing before this work.
- `test_startup_memory` gave 5 errors, caused by the environment (a timeout under load). Re-run alone, it passed 10/10.

The walk flagged one data item: 4119 Bonnie View Rd was missing from Contracts & Closing. That deal was moved from `title_closing` to `new_property` in the UI by the review user (event `deal.stage_changed`, actor `user`) during human review. It is data, not code, and was left as the reviewer set it.
