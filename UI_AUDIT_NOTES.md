# UI Audit Notes - Task 8

Scope: frontend-only mobile/responsive and CSS token pass. No backend files were changed for this task.

## New shared tokens added to `frontend/src/index.css`

Added these variables because the same values appeared across several files without an existing exact token:

- `--text-inverse: #061018` - used for text/icons on bright signal buttons and badges.
- `--text-danger-soft: #ffdce6` - used for readable text on red/danger surfaces.
- `--bg-field: rgba(7, 14, 31, 0.7)` - common input/select background.
- `--bg-field-soft: rgba(8, 17, 39, 0.6)` - softer input/chip background.
- `--bg-row: rgba(8, 16, 36, 0.52)` - mobile table/card row background.
- `--bg-row-hover: rgba(16, 32, 68, 0.72)` - elevated row hover background.
- `--border-danger: rgba(255, 77, 126, 0.34)` - common danger border tone.

## Hardcoded values replaced

### `frontend/src/index.css`
- Replaced the base page background gradient hardcoded hex stops `#02040b`, `#050e22`, and `#03060f` with existing background variables.

### `frontend/src/components/Layout.css`
- Replaced `rgba(47, 182, 255, 0.16)` sidebar glow with `--signal-blue-dim`.
- Replaced `#061018` avatar text color with `--text-inverse`.
- Replaced the mobile breakpoint behavior from a hidden sidebar to a hamburger-triggered overlay.

### `frontend/src/components/NotificationBell.css`
- Replaced `#0a0e14` badge text color with `--text-inverse`.

### `frontend/src/components/StatusBadge.css`
- Replaced `rgba(8, 17, 39, 0.6)` neutral dim background with `--bg-field-soft`.

### `frontend/src/styles/shared.css`
- Replaced bright button text `#061018` with `--text-inverse`.
- Replaced common secondary button background `rgba(8, 17, 39, 0.7)` with `--bg-field`.
- Replaced danger border `rgba(255, 77, 126, 0.32)` with existing danger palette where close enough.
- Added shared mobile rules for `.page-header`, `.panel-header`, `.stat-grid`, `.run-result`, and `.data-table` card-style rows.
- Added `.page-shell` styles for the new `PageShell` component.

### `frontend/src/pages/Admin.css`
- Replaced search input background `rgba(7, 14, 31, 0.7)` with `--bg-field`.
- Added mobile stacking for tabs, filters, metric cards, data panels, and funnel rows.

### `frontend/src/pages/AuditLog.css`
- Replaced green/red/blue repeated border values where close variables existed.
- Added mobile table-card behavior by allowing the audit table to drop its fixed min-width and stack rows.
- Added mobile pagination stacking.

### `frontend/src/pages/Campaigns.css`
- Replaced shared form/surface values with `--bg-field`, `--bg-row`, `--border-danger`, and existing signal dim variables where applicable.
- Added mobile stacking for the form grid, preview stats, action rows, and campaign table.

### `frontend/src/pages/Compliance.css`
- Replaced `#1478ff`, `#bd2452`, and `#ffdce6` with signal/text variables.
- Replaced danger border and field/surface backgrounds with shared tokens where applicable.
- Added mobile stacking for hero, stats, forms, and suppression table card rows.

### `frontend/src/pages/LeadCleanup.css`
- Replaced shared danger and row backgrounds with existing/new tokens where applicable.
- Added mobile stacking for duplicate groups, merge rows, lead rows, and action buttons.

### `frontend/src/pages/Leads.css`
- Replaced repeated input/select backgrounds with `--bg-field`.
- Replaced repeated chip/surface backgrounds with `--bg-field-soft` or `--bg-row` where applicable.
- Added mobile stacking for upload controls, filters, preview stats, bulk compose, and table wrappers.

### `frontend/src/pages/Login.css`
- Replaced `#0a0e14` button text color with `--text-inverse`.
- Added mobile card sizing/padding so login and forced-password screens fit narrow devices.

### `frontend/src/pages/Overview.css`
- Added mobile stacking for the dashboard grid, reply rows, and cadence rows.

### `frontend/src/pages/Replies.css`
- Replaced repeated blue-tint backgrounds with existing signal dim variables where applicable.
- Added mobile stacking for reply cards, filters, and the Task 3 triage action row.

### `frontend/src/pages/SystemHealth.css`
- Replaced common success/error border values with signal tokens where applicable.
- Added mobile stacking for the health header and status grid.

### `frontend/src/pages/WorkQueue.css`
- Replaced common row/card backgrounds with `--bg-row` / `--bg-row-hover` where applicable.
- Added mobile stacking for the work queue grid and header.

### `frontend/src/pages/LeadDetail.css`, `Settings.css`, `Templates.css`, `Users.css`
- Added max-width `768px` mobile stacking rules for their main grid/form/table patterns.

## New component

### `frontend/src/components/PageShell.jsx`

A reusable wrapper for the repeated page header pattern:

```jsx
<PageShell
  eyebrow="Command Center"
  title="Today's Work"
  subtitle="Priority actions for the active advisor."
  action={<button className="btn btn--primary">Refresh</button>}
>
  ...page content...
</PageShell>
```

Per the task, existing pages were not converted to use it yet.

## Mobile behavior notes

- The sidebar now collapses at `max-width: 768px` into a hamburger-triggered overlay.
- Multi-column grids added in Tasks 1-7 now stack to one column on phones.
- Shared `.data-table` rows become card-like stacked rows on mobile without needing page-specific table rewrites.
- For tables that do not yet have `data-label` attributes on `<td>` cells, the mobile card layout stacks cleanly but cannot show per-cell labels until those attributes are added in a later JSX pass.
