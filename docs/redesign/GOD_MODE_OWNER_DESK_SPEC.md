# God Mode — Owner Desk design language

Tracking issue: #411 · Branch: `redesign/god-mode-owner-desk-v1` · Status: **draft, awaiting owner visual review**

## Visual anchor
The approved mockup is saved as `screenshots/god-mode-owner-desk/approved-mockup.png` (received Oct 27 2026).
Matched: EvoSys Pro rail with the highlighted God Mode / Owner Desk card, rail shortcuts (Program Access,
Users & Permissions, Integrations, System Health, Audit Log — links to existing pages, no new routes),
Quick Links, Exit God Mode; six coloured summary cards; compact program tiles; six tool tiles with
"Open …" buttons; Needs Your Attention / Quick Actions / Recent Activity side by side; Platform Status strip.
Not copied on purpose: the search box and notification bell (no real platform-wide search or God
notification source exists yet), and the mockup's sample numbers — every number is real or says it isn't tracked.

## What it is
One look for every God Mode screen, taken from the approved Owner Desk direction:
light page, compact navy side rail, rounded white cards with soft shadows,
plain headings, few accent colours, no walls of charts.

## Building blocks (`frontend/src/pages/god/ui/GodKit.jsx` + `godKit.css`)
| Piece | Use |
|---|---|
| `Page` | Eyebrow, title, one-line explanation, actions on the right |
| `Section` | Heading + optional line + optional link above a group |
| `Strip` (`cols={5}` optional) | Row of summary numbers (`Kpi` cards) |
| `ToolCard` | Icon, name, what it is for, optional status pill, arrow |
| `ProgramCard` + `businessOf` | One customer: initials, business type accent, people/leads/messages, Open / Enter |
| `QuickActions` | Grid of the most common owner actions |
| `ActionQueue`, `HealthRow`, `Empty`, `ErrorState`, `Loading` | Attention list, status rows, clean empty/unavailable states |

Colours come only from God tokens (`godTokens.css`: `--gm-rail-*` for the navy rail,
`--gm-acc-*` for business accents). `tests/godTheme.test.mjs` enforces it.

## Screens
| Screen | Route | Change |
|---|---|---|
| Owner Desk | `/god` | Rebuilt: summary strip, Your programs, Platform tools, Needs your attention, Quick actions, Recent activity, Platform status, trends folded away |
| Organizations | `/god/organizations` | Page header + 5-number strip; table unchanged |
| AI Workforce | `/god/ai` | Emergency stop in header, status card, Status/Work strips, sections |
| Launch Board | `/god/launch-board` | Title aligned (already on GodKit) |
| Revenue & Billing | `/god/billing` | Page header with brand + search, card tiles ("not tracked" kept), Stripe/catalogue as plain cards |
| Settings & Security | `/god/settings` | Status card + tool cards grouped Security / Operations / Connection checks |
| Supporting tabs | various | Colour literals replaced by tokens so they inherit the look |

## Not changed
Routes, `GodRoute`, `godNav.js`, API calls, backend, permissions, org-context behaviour,
emergency stop, switchboard, billing controls, audit. Nothing was given to executives.
