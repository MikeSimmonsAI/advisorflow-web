# OLP: Overlooked Properties, powered by BookaBoost (brand exploration, not final)

OLP is being explored as the customer-facing product identity on top of the BookaBoost platform.
Nothing else changes: the BookaBoost platform, tenants, accounts, permissions and sign-in stay as they are,
and no new auth system, company or tenant model is created.

## Proposed brand assets (owner-supplied, awaiting final approval)
The files are in `frontend/public/brands/olp/` and are byte-identical to the delivered package (see `SHA256SUMS.txt`):

| File | Used for |
|---|---|
| `OLP_logo_primary_dark.svg` / `_light` | stacked logo: sign-in |
| `OLP_logo_horizontal_dark.svg` / `_light` | workspace header / sidebar (radar PR) |
| `OLP_icon.svg` | product tile (radar PR) |

The package's own notes are in `OLP_BRAND_ASSETS_README.md`. No trademark is claimed. No logo is recreated from
the AI mockups.

## Switch
`frontend/src/brand/productBrand.js` chooses the BookaBoost theme's product face. It defaults to **OLP**.
Build with `VITE_BOOKABOOST_PRODUCT_BRAND=bookaboost` to show the approved BookaBoost sign-in (#432) again.
That switch only changes presentation.

## Sign-in screens
See `login/`. These are real React renders at 1920, 1440, 820, 390, 375 and 320 px, plus these states:
- required fields;
- wrong password;
- loading;
- session expired.
