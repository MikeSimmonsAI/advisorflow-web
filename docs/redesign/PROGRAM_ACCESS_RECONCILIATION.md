# Program Access: business inventory and access reconciliation

Read from production (read-only) on **2026-10-25 17:55 CDT**. Nothing in production was changed.
Branch `feat/program-access-expansion`. Related issues: #268 (operator grants), #275 (launcher design).

## How the launcher decides what to show (unchanged rule, now with previews)

- **Workspace cards** come from active `customer_org` memberships (`/auth/my-contexts → workspace_contexts`). The card shows the program the organization runs, or, for a general business, its **real name** with its **industry** as the label (for example COMMERCIAL CLEANING).
- A new client shows up by itself once its organization exists and the person holds a membership. No client list is written into the code.
- **Wholesale** comes from the named product seat plus its host workspace. The EVO organization *is* the Wholesale host, so it isn't shown a second time.
- **Back Office** comes from a sales membership, and **Executive Suite** from a `brand_executive` grant.
- **Previews (new).** For **sales managers of the brand** only: one card per *open, pre-won* deal that has no customer workspace and a *live* demo link. It opens the deal's existing demo address, which is `https://app.evosyspro.live/demo/<token>` in production. Previews are labelled "PREVIEW · NOT A LIVE WORKSPACE" and kept in their own section.
  - A preview is never a workspace, never a grant, and never provisions anything.
  - It disappears when the deal is won, lost or linked to a workspace, or when its link is revoked or expires.
  - It also disappears for a login that already has a workspace card with the same name.
- A sales prospect with no demo link gets **nothing**.

## Inventory

| Business | Organization (prod id) | Brand / platform | Implementation | Destination | Status | Card type |
|---|---|---|---|---|---|---|
| EVO Integrated Solutions LLC / EvoSys Pro | `ffb18ae7-…` (generic) | EvoSys Pro | Wholesale host + EvoSys's own org | `/wholesale/evosense` via the Wholesale card | Active | Shown as **Wholesale** (host, not duplicated) |
| Service Corporation International (SCI) | `0a884078-…` (funeral, program) | EvoSys Pro | SCI program | SCI program | Active | Workspace |
| Atlantis Light & Power | `856b3a49-…` (energy) | EvoSys Pro | Energy workspace; deal is in Onboarding | `/workspace/…` | Active | Workspace (no preview; it's becoming a customer) |
| EvoSys Pro Roofing | `edf0030f-…` (roofing) | EvoSys Pro | Roofing program | `/program/roofing` | Active | Workspace |
| Wholesale | product seat, hosted by EVO | EvoSys Pro | Wholesale / EvoSense | `/wholesale/evosense` | Active | Product card |
| Restland Cemetery and Funeral Home | `01fed629-…` (funeral) | EvoSys Pro | Live funeral workspace | `/workspace/…` | **Live customer** | Workspace |
| Insurance: Max Life Demo Agency (DEMO) | `548b4346-…` (insurance) | – | Insurance agency (demo) | `/workspace/…` → agency | Demo | Workspace "Insurance" |
| **Daniel's Cleaning = Commercial Cleaning Blueprint** (Daniel Almaguer) | `6fe0124d-…` (**cleaning**) | EvoSys Pro | Workspace exists with the client dashboard (prospects, VA activity, follow-up, walkthroughs, reports). Public site `/site/commercial-cleaning-blueprint` is published. Deal is **Demo Build** (demo delivered, 2 proposals). | `/workspace/6fe0124d-…` | Built, **not yet a customer; 0 members**; deal not linked to the org | **Preview** today. Workspace card for anyone given a seat (needs owner approval, see below). |
| **Countryside Land Partners** (Samuel St.Germain) | **none** | EvoSys Pro Sales deal | Platform demo + website concept (live links until 13–14 Dec 2026). Proposal sent. Stage Demo / Proposal. | Demo link | **Mockup only** | **Preview** |
| VP Wraps Vehicle Protection | none | EvoSys Pro Sales deal | Platform demo (live until 14 Dec) | Demo link | Mockup only | Preview |
| Building Equity Investments LLC | none | EvoSys Pro Sales deal | Platform demo + website (proposal sent) | Demo link | Mockup only | Preview |
| BookaBoost Wholesale | `3c4cb6a1-…` | **BookaBoost** platform (Lane E, #272) | Second Wholesale tenant (in progress) | – | 0 members | None (not an EvoSys client; BookaBoost lane) |
| WUPA | `d7ceef45-…` (slug `evosyspro`) | EvoSys Pro | Older demo org (demo advisors) | – | Legacy demo | Not proposed |
| Lakemont Family Services | `ds-e2057ac3-…` | EvoSys Pro | Demo Suite sample tenant | Demo Suite | Demo | Reached through Demo Suite, not the launcher |
| Fiber Cartel, We Up Again LLC, Rich 4 Real holdings | BookaBoost platform | BookaBoost | BookaBoost customers | – | Other company | Not EvoSys launcher |
| Harmony Hustle | Harmony Hustle platform | Harmony & Hustle | Separate company | – | Other company | Not EvoSys launcher |
| ZZ Launch Verify A/B, synthetic AI-ops proofs | test | – | Test records | – | Inactive / test | None |

Sales deals that are lost (Walmart, Test One, WUPA) or are demonstration-brand records (Sable Creek Wellness, Halverson Roofing, …) get **no** card.

## wholesale@evosyspro.live: exact reconciliation

Identity: one user, `aa954e8c-…`, "EvoSys Wholesale", role `org_admin` (not god), home organization EVO Integrated Solutions LLC.

| Access | Current (prod) | After this PR (no data change) | Proposed, needs owner approval |
|---|---|---|---|
| SCI | org_admin | Card ✓ | – |
| Atlantis Light & Power | org_admin | Card ✓ | – |
| EvoSys Pro Roofing | org_admin | Card ✓ | – |
| Wholesale (EVO + product seat) | org_admin + seat | Card ✓ | – |
| Restland | org_admin | Card ✓ | – |
| Max Life Demo Agency | org_admin | Card "Insurance" ✓ | – |
| Back Office (EvoSys Pro Sales) | sales_manager | Card ✓ | – |
| Commercial Cleaning Blueprint | **none** | **Preview** (its demo) | **org_admin**, the same pattern as the six above |
| Countryside, VP Wraps, Building Equity | none (no workspace exists) | **Preview** each | Nothing to grant until there's a won deal and a workspace |
| Executive Suite | none | – | Not proposed |
| BookaBoost Wholesale | none | – | Not proposed (BookaBoost lane) |
| God Mode | denied | denied | denied |

**The one prepared permission addition**: wholesale@ → `org_admin` in Commercial Cleaning Blueprint (`6fe0124d-…`). #268 authorizes only the six named programs, so this addition needs your approval.
- **Preferred path:** God Mode → Manage Access (audited).
- **Alternative:** `scripts/grant_wholesale_ccb_workspace.py`. It does a dry run by default, is idempotent, writes to the audit log, and has a `--revoke` rollback. It was rehearsed on a local database: grant, a second run that changed nothing, the card appearing, CCB-only data, and revoke.
- **Optional follow-up with the same approval:** link the CCB sales deal to its organization (`customer_organization_id`) so the deal and the workspace are tied. The launcher already avoids showing a preview twice by name.

**Other employees:** nobody gains access because a company is in the EvoSys portfolio.
- Previews require a sales-manager seat in that brand.
- Workspace cards require a membership.
- A salesperson, a customer user and another brand's manager were each verified to see nothing extra.
