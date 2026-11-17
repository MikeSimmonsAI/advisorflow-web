# My Deal Room — Portfolio Acquisitions

**Where:** Acquisition → **Portfolio Acquisitions** (`/wholesale/portfolios`). This adds one rail entry, placed after Discovery Inbox. It is not shown on BookaBoost.

## What it does
| View | Where | What the owner can do |
|---|---|---|
| A. Portfolio list | `/wholesale/portfolios` | Create **Seller Portfolio #001, #002 …** (name, selling entity, source, status, notes, an optional portfolio-level target). Each portfolio shows its progress and totals. Archive or restore. |
| B. Inventory | Portfolio → Inventory tab | Six cards: seller asking, stated rent, estimated value, repairs, our target offers, estimated spread. Each card says when its total is partial. Search, filters, sorts (spread, rent yield, repair burden, value confidence) with the rule written out. Select properties to package, assign for research, or remove. |
| C. Property evaluation | Click a property | The seller's listing (marked not verified, with price history), property facts, valuation, costs, the worked MAO, profit and discount. A rental view uses the seller's stated rent and says so. The owner can change ARV, repairs, target offer, as-is value or occupancy, with a source or reason required. Links to the full deal room. |
| D. Package builder | Packages tab | Any selection, or the whole portfolio. Each package has its own per-property offers. Pricing can be the sum of offers, a % off asking, a $ off asking, or a negotiated total. Copy and archive. Lines built on older figures are flagged and never silently changed. Packages are compared side by side. |
| E. Summary | Summary tab | Status breakdown, the assumptions in use (this workspace's Wholesale Settings), and prioritized short lists with their rules. |
| Import | "Import seller list" | CSV/XLSX, rows typed from a photo (the image must be named), or typed rows. Every import is **reviewed before commit**. It flags possible duplicates (within the list and against existing properties), land, missing or ambiguous addresses and bad numbers. Each row can be corrected or set to create, link or skip. Tenant names and phone columns are not read. Committed imports can be undone. |
| Exports | Buttons on each view | Inventory, one property, one package, and the comparison (CSV). Each is headed "Internal investment estimate… Not an appraisal". |

## Reuse — no second engine
- Every portfolio property is an ordinary `WholesaleProperty` with its `WholesaleDeal`. Existing records are linked, never copied.
- **ARV, confidence, comps:** the deal's own fields (arv_engine).
- **Repairs:** the deal's current estimate. Overrides are recorded in the repair history (`wholesale_repairs.record`).
- **MAO:** `wholesale_service.gated_summary` → `wholesale_analysis.calculate_offer`, with the MAO gate. This is the same figure the deal room shows.
- **Target offer:** `deal.proposed_offer`, the deal's own offer field.
- **Flip/rental scenarios:** `wholesale_underwriting.analyze`.
- **Events and audit:** `wholesale_service.log_event`. Every change keeps its before/after.
- **Access:** a new **Portfolio Acquisitions** module in the existing Team Access engine. Record visibility (assigned/team/workspace) applies to the properties inside a portfolio.

Only two figures are worked in the new code, and both are written out on screen:
- transaction costs = value × workspace % + flat. This is the same cost term the MAO formula subtracts.
- estimated profit = value − offer − repairs − transaction costs. It is shown only when all four are known.

## Database changes (additive, created by `create_all`)
| Table | Purpose |
|---|---|
| `wholesale_portfolios` | The portfolio; per-workspace number |
| `wholesale_portfolio_sources` | Each list brought in: preview rows, what a commit created (for undo) |
| `wholesale_portfolio_items` | Property ↔ portfolio, with the seller's row as listed (price, rent, type, row, source, confidence, review status, price history). Unique per (portfolio, property). |
| `wholesale_portfolio_packages` | A purchase scenario |
| `wholesale_portfolio_package_items` | Scenario offer and the snapshot it was built on. Unique per (package, item). |

Every table carries `organization_id` NOT NULL. No existing table or column changes.

## Security review
- Every route sits behind `require_tenant_user` (or observer for reads), the `wholesale_real_estate` feature, `require_not_observation` for writes, and the custom-access module/action map:
  - create, edit, assign and delete map to their actions;
  - every `export.csv` maps to **export**.
- Queries are always filtered by `organization_id`. Another workspace gets 404, and properties from another workspace cannot be linked.
- A restricted login sees only the properties it may see. Totals cover only those properties. Packages that contain a property it may not see are hidden, because their totals would reveal it.
- Investor (Deal Room) logins are not staff logins, so every portfolio route refuses them.
- Nothing sends a message, publishes, or calls a paid provider. Import does not create seller contacts.

## Tests
- `tests/test_portfolio_acquisitions.py` — 20 tests:
  - CRUD and the delete/archive safeguard;
  - import preview (duplicates, land, missing address, bad numbers, personal columns dropped), commit, link to an existing property, undo (untouched properties deleted, worked ones kept), XLSX, photo rows;
  - overrides need a source and match the deal room's MAO;
  - partial totals, price history;
  - package totals and every discount mode, any selection or the whole portfolio, scenario independence, copies, stale flags and refresh;
  - removal guards, comparison and exports;
  - restricted researcher, missing module, cross-tenant, investor token, nothing sent or published.
- `tests/test_portfolio_browser.py` — the built app in Chromium:
  - all five views, the evaluation drawer, scenario independence through the UI and the import preview;
  - phone (390 px) and tablet (820 px) widths with no horizontal scroll;
  - the menu entry on My Deal Room, and no entry on BookaBoost.
- `frontend/tests/wholesaleNav.test.mjs` — the pinned rail order now includes the new entry.

## Known limitations
- **The seller's two photographed sheets were not found.** I checked the Wholesale Drive folder and a Drive-wide image search; the only recent images are cleaning-business flyers. Nothing was imported from them, and no sheet figures appear anywhere.
  - Secure destination: upload the photos to Drive › Wholesale. Then, in the portfolio, choose **Import seller list → Rows read off a photo**, name the image, and type or review the rows.
  - Rows entered this way start at low confidence and stay marked "needs review" until checked against the photo.
- No OCR. Photo rows are transcribed by a person.
- No paid lookups are triggered from a portfolio. Valuation uses what is on each deal (comps, a labelled entry). "Research needed" is shown when nothing is on file.
- Packages are private analysis. Publishing to investors still goes through the existing deal-room workflow, one deal at a time.

## Rollback
1. Revert the merge commit. The routes, screens and module disappear.
2. The five new tables are left unused. To drop them:
   `DROP TABLE wholesale_portfolio_package_items, wholesale_portfolio_packages, wholesale_portfolio_items, wholesale_portfolio_sources, wholesale_portfolios;`
   Properties and deals created by imports are ordinary Wholesale records and stay.
3. Access profiles that granted the "portfolios" module keep that key. It is ignored once the module is gone.

## Production acceptance plan
Run with a **sandbox** portfolio only, then delete it:
1. My Deal Room opens.
2. Acquisition shows Portfolio Acquisitions.
3. Create a sandbox portfolio.
4. Import a 6-row synthetic CSV, review the preview and commit.
5. Evaluate a property.
6. Build packages: 2 properties, 3 properties, and all.
7. Check that the totals recalculate correctly.
8. Edit one package's offer and check the other package is unchanged.
9. Export each report.
10. A restricted login sees only its assigned properties.
11. BookaBoost has no menu entry and no access to the data.
12. The investor portal still signs in.
13. Undo the import, empty the portfolio and delete it.
