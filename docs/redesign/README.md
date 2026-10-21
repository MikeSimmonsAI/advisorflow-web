# UI-REDESIGN lane

A permanent, protected workstream for redesigning EvoSys Pro screens **inside the existing AdvisorFlow app** — not a separate product, app or deployment.

| | |
|---|---|
| Lane | `UI-REDESIGN` |
| GitHub label | `lane:ui-redesign` |
| Branch pattern | `redesign/<screen>` (short-lived, cut from fresh `main`, one focused PR each) |
| Windows worktree | `C:\Dev\af-ui-redesign` (cloud sessions use their own separate worktree) |
| Rules | [RULES.md](RULES.md) — read before every redesign session |
| Coordination | [MIKE COMMAND RELAY — issue #1](https://github.com/MikeSimmonsAI/advisorflow-web/issues/1) for cross-lane conflicts; each redesign also has its own tracking issue |

## Redesign log

| Screen | Branch | Issue | Status |
|---|---|---|---|
| Program Access (`/workspaces`) | `redesign/program-access-v1` | #275 | Merged #277, live 2026-10-21 00:53 CDT. Spec: [PROGRAM_ACCESS_SPEC.md](PROGRAM_ACCESS_SPEC.md) |
| Sales Workspace (My Day) | `redesign/sales-workspace-v1` | — | PR #287, draft, awaiting Mike's visual approval |

Future examples (**not authorized until assigned**): `redesign/sci-dashboard`, `redesign/wholesale-dashboard`, `redesign/roofing-dashboard`, `redesign/atlantis-dashboard`, `redesign/insurance-dashboard`.

## Starting a new redesign

1. `git fetch origin main` and cut `redesign/<screen>` from it in the redesign worktree.
2. Open a tracking issue labelled `lane:ui-redesign`; list the files you expect to touch.
3. Check open PRs for those files (Rule 6). Post concrete conflicts on the relay issue.
4. Record the real start time (America/Chicago) and starting SHA on the issue.
5. Build on the shared design system below; keep the screen's data and access logic untouched.
6. Verify (tests, build, browser screenshots at desktop/tablet/mobile) and open the PR **as a draft** from the redesign template: `https://github.com/MikeSimmonsAI/advisorflow-web/compare/main...redesign/<screen>?template=redesign.md&labels=lane:ui-redesign`.
7. **Do not add `owner-approved`, mark ready, or merge until Mike writes "APPROVE REDESIGN PR #NUMBER FOR MERGE"** (see [RULES.md → Owner approval](RULES.md#owner-approval-enforced-since-2026-10-21)). The required check *Redesign owner approval* enforces it.

## Shared design system (`frontend/src/design/`)

| File | What it is |
|---|---|
| `evTokens.css` | Scoped tokens under `.ev-ds` (never `:root`): type, surfaces, radii, shadows, focus ring, container, buttons, eyebrow, pill. Per-business accents via `data-accent="sci|energy|roofing|wholesale|insurance|funeral|office|general"` → `--acc`, `--acc-strong` (AA with white text), `--acc-soft`, `--acc-ink`. |
| `WorkspaceCard.jsx` + `workspaceCard.css` | Branded business card: hero art, category, name, summary, info strip, one "Open workspace" button (hit area covers the card). Grid and list layouts. Presentation only. |
| `art/WorkspaceArt.jsx` | Local SVG hero scene per business, fixed 400×180 frame. No external images. |
| `art/PlatformArt.jsx` | "Many businesses, one secured platform" hero illustration. |

To adopt on another screen: wrap it in `className="ev-ds"`, import `evTokens.css`, set `data-accent` for the business. Don't add global CSS; don't force one color scheme or dark-only.
