# Overlooked Properties (Opportunity Radar) — nationwide data plan

Issue #427 · PR #428. This file is a plan. Nothing in it is switched on.

## What Phase 1 (this PR) does

- The screen at `/wholesale/evosense/overlooked` reads **only the signed-in workspace's own records** (`EvoSenseProperty` + `EvoSenseSignal`). Nothing on the screen is invented, and no coverage is claimed for places with no records.
- The state list always shows all 50 states + DC. Each state shows how many records the workspace actually has there. The county list is built from real records only.
- **Buyer matches are read-only.** They reuse `wholesale_matching.match_deal_to_buyers` against the workspace's existing Buyer Intelligence buy boxes. A buyer counts only when all of these hold:
  - the buy box clears the match bar (`GOOD_MATCH`);
  - location is confirmed;
  - property type is confirmed.
  
  A missing fact is never counted. Nothing is sent and nothing is written.
- **Watchlist / Save is on hold.** There is no new table.

## How data reaches the radar (already in the codebase)

Every source goes through one path: `app/services/evosense/ingest.py::ingest()`.

1. **Precedence.** Facts are ranked as human correction (100) > assessor / parcel / ownership records (60) > other feeds (40) > file import (30). A higher-ranked source replaces a lower one. A conflicting value from an equal or lower source is recorded as a conflict for a person to review; it is never silently overwritten.
2. **Signals.** Signals (vacancy, tax delinquency, pre-foreclosure, absentee owner, long ownership, expired listing…) are stored with their source, date and confidence. Freshness rules live in `signals.py`. The radar shows these exactly as stored.
3. **Connectors.** Today's connectors are county public-record sources (`PublicRecordSource` in `providers.py`, currently Texas counties), the CSV import (`propstream_csv.py`), and manual entry.

Because the radar reads only the canonical tables, a new connector in any state shows up on the screen with **no screen changes**.

## Phases 2–5 (each needs owner approval before it starts)

| Phase | What | Cost / risk | Approval needed for |
|---|---|---|---|
| 2 | **Bring-your-own data.** Let each BookaBoost workspace import its own lists (CSV / PropStream-style export) through the existing import path, with a preview of what will be added before it is saved. | No paid service. Writes only to the importing workspace. | Turning import on for BookaBoost workspaces. |
| 3 | **Free public-record connectors by state.** Add `PublicRecordSource` adapters for county assessor / tax / foreclosure portals, starting with the states customers actually work. Each adapter declares its counties, so coverage is reported truthfully. | No paid service. Must respect each site's terms and rate limits; scheduled pulls. | Which states / counties first; turning on scheduled pulls. |
| 4 | **Licensed national feed (optional).** One nationwide parcel + ownership + distress provider plugged in as an `AcquisitionProvider`, with per-workspace budget caps (`budget.py`). | **Paid.** Licence terms decide whether data may be shown to more than one workspace. | Vendor, contract and monthly budget. |
| 5 | **Watchlist and alerts.** The held Save feature (new table, per-user, workspace-scoped), plus in-app alerts when a saved property gains a new signal. No outbound messages unless separately approved. | New table + migration. | Schema change; any notification channel. |

## Rules that hold in every phase

- Workspace isolation: every query is filtered by the caller's organization. A forged workspace header is ignored. Another tenant's property id returns 404. These are covered by `tests/test_overlooked_radar.py` and the cross-tenant attack list.
- Real data only: no demo or synthetic rows in production. Unknown facts stay "unknown".
- Read before write: the radar screen itself never writes.
- No outreach from this screen.
