# LANE-F-SALES-REDESIGN — Worker contract and approved UI

Status issue: https://github.com/MikeSimmonsAI/advisorflow-web/issues/281
Coordination board: https://github.com/MikeSimmonsAI/advisorflow-web/issues/239
Repository: MikeSimmonsAI/advisorflow-web
GitHub branch: redesign/sales-workspace-v1
Exclusive PC worktree target: C:\Dev\lanes\sales-redesign
Primary route: /sales
Approved: 2026-10-21

## Design reference — authoritative
The six-panel image shared by Mike titled **Simplified Sales Workflow – My Day (New Design)** is the approved design. The assigned Claude worker must receive that image separately. DO NOT replace it with the alternative four-tab mockup.

Build and screenshot these six screens:
1. My Day
2. Follow Ups
3. Lead Details (simple action panel opened from list)
4. Book a Demo (short form)
5. Pipeline
6. Appointments

Visuals: narrow dark/navy sidebar; light content background; big, recognizable buttons for call, text, booking and follow-up; understated blue/teal/green colors; rounded, legible cards; low cognitive load. Use accessible states, responsive layouts, keyboard support, phone-width views. Screenshot numbers are illustrative only; production must use real data.

## Sales interaction design
- Rep logs in directly to personalized **My Day**.
- Overdue and today follow-ups prioritized; clear next action for every lead.
- From Follow Ups, open lead and call/text in one click using existing approved communication flows. No unsolicited production sends from testing.
- Record activity/notes and select outcome, next action/date without unnecessary navigation.
- Book a demo with contact/date/time/type/optional notes; invite and appear in appointments using existing scheduling/calendar services.
- Pipeline: New Leads -> Follow Up -> Demo Scheduled -> Won as simple presentation, with real underlying lifecycle states safely mapped; preserve full statuses/actions and history. No drag-drop-only essential operations.
- **After Won, hand off to the separate existing onboarding system.** No duplicate onboarding page.
- Rep UI hides manager clutter; manager mode remains accessible only to appropriately authorized users. Backend authorization, org isolation and God Mode remain intact.

## Technical rules
- Branch only; do not merge, release, or deploy. Mike approves production separately.
- Use separate clean Windows worktree **C:\Dev\lanes\sales-redesign**. Never code from someone else's working folder; never reset/stash/clean their work.
- First audit /sales, data services, current components, schedules, messaging, permissions and existing tests. Report audit before large rewrites.
- Install and VERIFY a local pre-push hook permitting only this lane branch and refusing main, sci-program and all other lanes. Record pass/refusal tests on #281. Branch presence alone is not protection.
- Explicitly coordinate on #239 before touching shared hot spots: frontend Layout.jsx, App.jsx, api/client.js, backend app/main.py, app/models/*, app/deps.py, app/auto_migrate.py, render*.yaml, .github/workflows/*.
- Protect SCI, Wholesale, Atlantis, Roofing, BookaBoost, Program Access and owner command center. Keep URL/route compatibility and existing database schema; request separate authorization for any unavoidable shared schema/route changes.
- Commit small slices; push only this branch; keep draft PR; do not consume or integrate unfinished lane commits.
- Run frontend/unit tests, access-control and org-isolation tests, integration tests for appointments and communication where possible, and browser E2E tests for complete sales flow. Report genuine test results and pre-existing failures clearly.
- Produce six browser screenshots and a working rep journey; no fixture-only fake production UI.

## Work status reporting
On issue #281, post `[LANE STATUS] LANE-F-SALES-REDESIGN` at start, every 15 minutes *while actively working*, on stage transitions and at stop.
Use actual local Central Time: YYYY-MM-DD HH:MM CT, branch, commit/PR, stage, done, tests, blocker, next action, ETA. NEVER guess a timestamp or call background reporting automatic.

Statuses follow #239. The lane is not ready for software edits until worktree AND push guard are created and verified on Mike's PC.

## Start checklist for Claude
1. Read issue #281, #239, and this file.
2. Confirm you can view Mike's **approved six-screen image**. Ask him to attach it if absent.
3. Inspect repo and live screen behavior safely; inventory existing components.
4. Create/verify local worktree and safe push guard; prove guard stops forbidden pushes.
5. Post start status and collision plan.
6. Build screens incrementally, test, screenshot, request review. NO merge/deploy.
