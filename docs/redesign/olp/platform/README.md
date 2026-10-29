# OLP integrated platform design: development preview

Owner decisions 1–6 (Oct 29) are implemented for the **OLP product brand only**.
When the brand is switched back to BookaBoost (`VITE_BOOKABOOST_PRODUCT_BRAND=bookaboost`),
the build looks exactly as it did before. All screenshots here are from a throwaway local
database with synthetic records. No customer data appears.

## What changed (OLP mode only)
1. **Logos:** the refined "Powered by BookaBoost" endorsement, in light and dark versions. The original files are kept byte for byte in `../original/` (see `SHA256SUMS.txt`).
2. **Colours:** OLP gold replaces the blue primary accents on the Deal Room, Buyer Intelligence, Buy Boxes, Investors, Opportunity Radar and the Wholesale screens. Green, amber and red keep their meanings: success, warning and error.
3. **One header system:**
   - **Hero** (photo) for Radar, Acquisition, Buyers, Deal Room and Deal Operations.
   - **Compact** (no photo) for working screens.
   - **Detail** (faded photo) for records.
4. **A cohesive dark platform:** sidebar, top bar, canvas and gold actions. Every rule is scoped to `html[data-product-brand="olp"]`; nothing is set globally.
5. **One sidebar** with three groups: Opportunity Intelligence, Market Operations and Account & Settings.
   - It has the same items and the same permission flags, with no duplicates.
   - It keeps the same shape in Buyers and the Deal Room.
   - The product label reads "OLP · Overlooked Properties".
6. **Menu label:** the menu says "Opportunity Radar". The page title stays "Overlooked Properties".

## Files
| File | What it shows |
| --- | --- |
| `olp-24-screens-desktop.jpg`, `olp-24-screens-mobile.jpg` | Contact sheets of all 24 Wholesale screens at 1440 and 390 px |
| `olp-before-after-desktop.jpg`, `olp-before-after-mobile.jpg` | Each screen before (the #435 state) and after |
| `olp-empty-workspace-desktop.jpg` | A brand-new workspace with no data: empty states on all 24 screens |
| `flow-*.jpg` | Radar evidence drawer, buyer matches (read only) and the property research page |

## Local preview
1. Start the throwaway backend on 127.0.0.1:8010. It uses a SQLite file with a synthetic seed and refuses any other database.
2. Build the OLP version:
   ```
   VITE_API_BASE_URL=http://127.0.0.1:8010 npx vite build
   ```
3. Build the rollback version:
   ```
   VITE_API_BASE_URL=http://127.0.0.1:8010 VITE_BOOKABOOST_PRODUCT_BRAND=bookaboost npx vite build
   ```
4. Serve the build on localhost:5173 (OLP) or localhost:3000 (rollback). Sign in with the synthetic local accounts:
   - `bb-admin@example.com`: workspace with data.
   - `bb-new@example.com`: empty workspace.
   - `ops@example.com`: EvoSys operator, for the isolation check.
