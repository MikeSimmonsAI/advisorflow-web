# UI-REDESIGN lane — permanent rules

These apply to every redesign session, by any Claude/ChatGPT session or person.

1. **Isolation.** Redesign work never overwrites SCI, Wholesale, Roofing, Atlantis, Insurance or any other concurrent development. Work in the redesign worktree on a `redesign/*` branch only.
2. **Preserve functionality.** Keep existing authentication, navigation, permissions, data access and working behaviour unless a change is explicitly authorized by Mike.
3. **Visual identity.** Each business keeps its own recognizable branding and visual language, inside shared EvoSys Pro standards (`frontend/src/design/`).
4. **Dedicated GitHub tracking.** Every redesign has its own issue, branch, commits, PR, test results and screenshots, labelled `lane:ui-redesign`.
5. **Production protection.** No major visual redesign is merged or deployed to production until required tests pass **and Mike has approved the final visual presentation.**
6. **Shared-file coordination.** Before modifying a shared component (e.g. `App.jsx`, `Layout.jsx`, `index.css`, auth helpers), inspect ownership and open PRs touching it. Announce concrete conflicts on the relay issue (#1). Report conflicts; don't wait indefinitely.
7. **No unrelated changes.** No changes to lead processing, SMS, Twilio, AI conversations, database migrations, billing, campaign automations or authorization logic as part of a visual redesign.
8. **Responsive.** Every redesigned screen works on desktop, tablet and mobile with no horizontal overflow.
9. **Reusable design system.** Prefer shared hero panels, cards, navigation, typography and tokens — scoped, never unnecessary global changes.
10. **Evidence before completion.** Each feature reports: tests run (and which failures are inherited from `main`), build result, screenshots, PR link, commit SHA, and deployment status only once deployment is real and verified.

**Access is never decided in a redesign.** Cards, menus and lists render only what the server already authorized (e.g. `/auth/my-contexts`). God Mode stays exclusive to the platform owner.

**Reporting.** Record real start time (America/Chicago) and starting SHA; check in about every 20 minutes and at milestones with timestamp, stage, done, blockers, branch/commit, next action. No invented or back-dated times.
