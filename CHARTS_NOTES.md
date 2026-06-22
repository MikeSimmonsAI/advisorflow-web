# Overview Charts Notes

This file documents the real Overview charts added after the visual polish pass.

The reference image for this task was treated as a **mood-board only**: glow, layout density, darker glass panels, and command-center styling. No fictional chart, fake gauge, health score, or placeholder metric was copied.

## 1. Reply Activity Over Time

Frontend location:
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

Backend endpoint:
- `GET /sms/replies/activity-by-day?days=14`

Data source:
- `Reply.received_at`
- joined to `Lead`
- scoped to `Lead.organization_id == current_user.organization_id`
- scoped to `Lead.assigned_to_id == current_user.id`

What it shows:
- One row per calendar day for the requested window.
- Count of inbound replies received on the logged-in advisor's own leads.
- Empty days are returned with `count: 0`, so the frontend does not invent missing data.

Why this is real:
- Every point in the line chart comes from persisted `Reply` rows.

## 2. Engagement Temperature Distribution

Frontend location:
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

Backend endpoint:
- `GET /leads/engagement-breakdown`

Data source:
- `Lead.engagement_temperature`
- scoped to the logged-in advisor's owned leads only.

Returned buckets:
- `hot`
- `warm`
- `cold`
- `unknown`

What it shows:
- A donut chart showing the advisor's current lead temperature mix.

Why this is real:
- It uses the existing `EngagementTemperature` enum stored on real `Lead` rows.

## 3. Cadence Health Gauge

Frontend location:
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

Backend endpoint:
- `GET /cadence/health-summary`

Data source:
- `CadenceState.status`
- `CadenceState.next_touch_due_at`
- joined to `Lead`
- scoped to the logged-in advisor's owned leads only.

Formula:

```text
health_score = healthy_active_count / active_count * 100
```

Definitions:
- `active_count`: number of `CadenceState` rows where `status == CadenceStatus.ACTIVE` for the current advisor's leads.
- `healthy_active_count`: number of active cadence rows where `next_touch_due_at` is set and is not overdue at request time.
- `overdue_active_count`: number of active cadence rows where `next_touch_due_at` is missing or earlier than request time.
- If `active_count == 0`, `health_score` returns `0` to avoid showing a fictional perfect score.

What it shows:
- A radial gauge of the percentage of active cadences that are not overdue.

Why this is real:
- The gauge is calculated from real `CadenceState` rows and due timestamps.

## 4. Status Funnel

Frontend location:
- `frontend/src/pages/Overview.jsx`
- `frontend/src/pages/Overview.css`

Backend endpoint:
- `GET /leads/status-funnel`

Data source:
- `Lead.status`
- scoped to the logged-in advisor's owned leads only.

Stages shown:
- `new`
- `sent`
- `replied`
- `hot`
- `booked`

What it shows:
- A simple horizontal funnel using real current lead counts by status.

Why this is real:
- Every bar is a count of persisted `Lead` rows.

## Tests Added

Test file:
- `tests/test_overview_charts_router.py`

Coverage:
- Reply activity counts exact day buckets and advisor/org isolation.
- Engagement breakdown counts exact HOT/WARM/COLD/UNKNOWN values and advisor isolation.
- Cadence health counts exact active/healthy/overdue values and verifies the 50% formula case.
- Status funnel counts exact stage values and excludes other advisor/DNC rows.

## What Was Intentionally Not Replicated From the Reference Image

Not copied:
- Generic fake reply velocity line not backed by data.
- Generic `92` health score with no explainable formula.
- Any chart/gauge showing metrics this app does not currently compute.
- Any invented revenue, performance, or AI-score widget.

Reason:
- AdvisorFlow should only show charts when the number comes from real database rows and has a clear explanation.
