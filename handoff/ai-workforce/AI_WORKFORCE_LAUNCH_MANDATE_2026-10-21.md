# P0 EXECUTION MANDATE — EVOSYS PRO AI WORKFORCE (2026-10-21)

## OWNER OUTCOME — NOT A RESEARCH TASK
Finish AI Workforce + AI Operations + AI Deployment as an actual, coherent, self-service product. Make the safe/test experience usable across the EvoSys Pro platform for authorized brands/tenants; prepare the fastest controlled production release without turning on live external messaging. Mike must be able to start a Lead Qualifier without technical assistance. This is BUILD -> TEST -> PROVE -> RELEASE PREPARATION, not another audit-only loop. Eighth-grade reading level and no developer involvement for normal eligible customers are mandatory acceptance criteria.

## SINGLE DEDICATED GITHUB LANE — ABSOLUTE ISOLATION
- Repository: MikeSimmonsAI/advisorflow-web
- Sole writable feature branch: feature/ai-workforce-launch
- Browser branch URL: https://github.com/MikeSimmonsAI/advisorflow-web/tree/feature/ai-workforce-launch
- Dedicated spec/path: handoff/ai-workforce/AI_WORKFORCE_LAUNCH_MANDATE_2026-10-21.md
- Execution/status tracker: https://github.com/MikeSimmonsAI/advisorflow-web/issues/5
- Shared-file coordination: https://github.com/MikeSimmonsAI/advisorflow-web/issues/239
- Relay inbox for running Claude: issue #1. Only an authorized new relay directive on issue #1 starts the GitHub Actions execution.
- GitHub Actions runner is Ubuntu, already checked out on the branch. DO NOT create the Windows C:\Dev\lanes\ai-workforce worktree there. That path is for a separate Windows Claude Desktop session only, if asked.
- Do NOT run git push in relay: runner pushes your COMMITTED checkpoints after execution. Never commit to main, rebase/force-push, merge PRs without authorization, or edit other lanes' branches/worktrees. Keep modifications on this branch.
- A non-essential git merge origin/main is NOT a blocker. Last run was stopped by denied merge permission. Work immediately on existing branch; coordinate conflict resolution when preparing PR. Main is only one CI commit ahead as of previous check; inspect current state before asserting freshness.
- Previous verified worker checkpoint: 37cd4190 added app/jobs/run_ai_workforce_worker.py and tests/test_ai_workforce_worker.py. Worker performs a T6 run pass, default off; no production scheduling or UI integration. 7 relevant tests passed in that run. NEVER call that a completed product.

## EXECUTION DISCIPLINE — ELIMINATE BOTTLENECKS
1. Start with real timestamp in America/Chicago using (preferred) python standard library datetime.now(ZoneInfo("America/Chicago")).isoformat() or the runner date command if permitted. State branch, HEAD, next 1-2 concrete deliverables on issue #5.
2. Within 10 minutes of an active execution slice, produce a working code change and run focused tests. No repeated inventory/rewrite of earlier audits. Read existing files and issue #5 first, then WRITE code.
3. Every 10-15 minutes of ACTUAL active work, post an issue #5 checkpoint with real current CT timestamp (YYYY-MM-DD HH:MM CDT/CST), elapsed, changed files, commit SHA, exact test commands/counts, blocked/unblocked items, and next action. No fake timestamps, invented percentages, or promises of unattended background work.
4. The GitHub relay Claude step has a 30-minute lease. Treat each run as one independently useful code slice: save a clean tested commit by ~minute 20 whenever worthwhile; send one terminal status on issue #1 using the relay contract. The Actions workflow will push any commits. Follow-on directives resume exactly from that commit. Do not falsely mark overall project done just because a time-boxed slice ended.
5. If sandbox denies git merge, git worktree, a compound command or a browser action: skip that operation, use permitted Read/Write/Edit/Python/pytest/single commands, and continue all unblocked code/test work. Block ONLY on a genuinely unavoidable dependency. Never stop after an audit. A tool permission limitation for an optional operation is not a customer blocker.
6. If a feature fails, identify root cause, patch, run its test, commit, continue. No generic refusal, circular questions, refactors unrelated to launch, new frameworks, or speculative redesigns.
7. Use dedicated feature branch and small reviewable PRs. Link commits/PRs in #5. Shared files (frontend root navigation/layout, app/main.py, render.yaml, migrations) require a concise collision note on #239 before modification; do not wait for unrelated branches to finish unless there is a demonstrated conflict.
8. Do not alter private customer data or copy personal data into screenshots, logs, commits, seeds, or chat.

## EXACT BASELINE / TARGET ARCHITECTURE
Existing parts:
- T6 engine: app/models/workforce_models.py; app/services/workforce/{runtime,queue,activation,registry,tools,tool_impls,policy,handoff,outbound,simulator}.py; app/routers/{workforce_router,god_workforce_router}.py.
- T7 operations: app/jobs/run_ai_operations_worker.py and app/services/ai_operations/ plus operation flags and scheduled actions (inspect exact names). T7 due actions are NOT T6 work-item queue consumption.
- T8 offer/hire/deploy: app/models/ai_deployment_models.py; app/routers/{ai_deployment_router,god_ai_deployment_router}.py; app/services/ai_deployment/{catalog,commerce,configuration,readiness,lifecycle,activation,simulation}.py.
- T9 evidence/intelligence: app/models/workforce_intelligence_models.py; app/routers/workforce_intelligence_router.py; app/services/workforce_intelligence/{collect,readmodel,metrics,attention,reconciliation,command,proof}.py.
- Customer UI: frontend/src/pages/AIWorkforce.jsx, AIWorkforceEmployee.jsx, AIWorkforceCommand.jsx, AIEmployeeDetail.jsx, frontend/src/styles/aiWorkforce.css, customer routes/sidebar (locate via search); God controls under frontend/src/pages/god/.
- New starter T6 worker: app/jobs/run_ai_workforce_worker.py. WARNING: starter test asserts worker is NOT wired into render.yaml/main.py. When implementing a safe active test environment path, replace brittle dark-only assertions with accurate tests for default-OFF, gated test-only operation, scheduler lifecycle and explicit activation; don't preserve test assertions that obstruct the desired capability.
- Production audit of Oct 6 (NOT current live check): platform activation OFF; 0 offerings, terms, employees, deployments, work items, employee runs and intelligence rows; no Render AI operations worker. Re-check current environment before making live-status claims.

## RELEASE MILESTONES — EXECUTE IN ORDER, NO SKIPPING THE FIRST REAL SLICE

### M1: REAL T6 LEAD QUALIFIER, ISOLATED TEST TENANT — FIRST PRIORITY
A. Use test/local database and synthetic org, brand, account, synthetic lead. Ensure isolation from production and other tenants. Create idempotent fixture/test seed through supported domain/service/product APIs, not manually manipulating production DB tables.
B. Make Lead Qualifier a selectable/offered job for the synthetic eligible account. Configure catalog/brand offering, terms, eligibility/entitlement, hire and deployment; reuse existing T8 services and avoid duplicate records.
C. Implement contract-level tests for pick job -> save answers -> readiness -> hire/deploy -> controlled simulated activation -> enqueue lead via normal API/service -> actual T6 queue worker CLAIM -> runtime EXECUTION -> permitted simulated tool calls -> outcome/handoff -> durable work/run/audit/action records. No simulator-only green claims: demonstrate the actual consumer run_pass consumes a queued item.
D. Test valid worker claims, expiration/recovery, duplicate prevention, idempotency, bounded retries, error isolation by tenant, kill switch OFF gate and paused/retired behavior.
E. Ensure T7 scheduled actions are separately consumed by their designated gated worker and do not conflate T6 queue with T7. Document safe worker startup for test/stage and eventual production deployment. Do not provision paid infrastructure without approval.
F. Reconcile T9 from actually persisted synthetic activity. Assert useful non-zero metrics/results traceable to run IDs. Show legitimate zero, unknown and not-started states without fabricated evidence.
G. Provide reproducible ONE-command local synthetic acceptance run after prerequisites; list dependencies and exact command. Do not require manually editing the database to prove the slice.
M1 DONE ONLY IF one fake lead has traceable queued -> claimed -> executed -> completed/handoff, persisted logs and metrics, worker truly claimed it, and pause/resume/retire stop controls are tested. Output commit, tests and proof, not promises.

### M2: ONE UNDERSTANDABLE CUSTOMER EXPERIENCE
- ONE customer sidebar entry called AI Workforce leading to /ai-workforce. Do not show AI Operations/AI Deployment as separate required customer workflows. Preserve and properly wire three deep God/admin panels; no functionality regression.
- Only four normal-customer product tabs: SET UP | WORKING | NEEDS YOU | RESULTS. Consolidate older 'Your AI Team', 'My AI Workforce', 'Workforce Command'; redirect existing deep links without losing context. Do not hijack unrelated app routes.
- First screen: large Lead Qualifier, Appointment Setter, Follow-Up Specialist, Customer Care job cards, each showing in one sentence What it does, What it needs, Available/Unavailable and one button SET THIS UP. No dead buttons; only enable jobs confirmed supported; label unavailable jobs honestly.
- Guided wizard: Pick a job -> Tell us about your business -> Run test -> Start working -> Watch results. Max 5-7 short questions on any step. Back, save/resume, clear progress, sensible defaults; plain-language business details/hours/territory, lead source, allowed follow-up behavior, booking preferences and handoff contact. Never expose model name, prompts, API keys, T6/T7/T8/T9, activation_state, entitlement, 'read models' or internal codes to normal customers.
- RUN TEST must create safe synthetic work, execute actual gated worker and display evidence: what AI tried, what it did, what it refused and reason, and PASS or NEEDS ATTENTION. No fake data on production dashboards and no real SMS/email/voice.
- START WORKING checks platform/brand/org availability, offering/terms, valid configuration, employee state, worker health, allowed actions, channel readiness and other necessary prerequisites. In safe test mode start only the gated synthetic worker. For missing live-send approvals, display 'Live messages are turned off. Test mode is ready' instead of pretending live activity exists.
- WHY ISN'T IT WORKING? diagnostics must be calculated from real backend state. For each blocker, show What happened / Why / What to do next in plain English. When only admin can fix, show 'Ask your administrator', not a God Mode instruction; when customer can fix, provide a real action link/button.
- WORKING: Working now, Waiting, Needs You, Completed, Appointments, Handoffs; accessible employee details, last run time, recent actual actions/results, PAUSE/RESUME/STOP; no invisible background activity.
- RESULTS: actual completed runs, contacted/tested records, qualified/handoff counts, timing and conversion rates ONLY when denominator/data are real; show N/A with an explanation when not measurable; no fake zero or vanity graph.
- 8th-grade words, a single clear primary action per screen, error recovery, readable text, keyboard/accessibility/mobile/tablet; test first-time eligible non-God account onboarding. Never make God Mode a dependency for regular users after availability is provisioned.

### M3: MAKE BOTH JOBS WORK + CHECK ALL THREE ADMIN SCREENS
- Once M1 is green, repeat a full synthetic path for Appointment Setter with a fake calendar/slot/booking, correct tenant/availability and no real booking or external messages.
- Make AI Workforce, AI Operations and AI Deployment admin sections actually reflect their own real state, actionable diagnostics and correct backend contracts: definitions, offered jobs, provisioning, worker health, actions, runs, metrics, security/permissions and cancellation. Remove broken/dead controls; don't repaint pages with fake stats.
- Preserve tenant boundaries and existing job roles. Authorized admins can offer jobs to a brand/org; an entitled customer can set up and start safe mode from the single front door. Non-entitled tenants never see others' employee data. Brand-specific appearance must remain intact.
- Make functionality available in all authorized EvoSys Pro brand/workspace contexts through shared platform APIs + proper per-tenant entitlement, NOT by turning every feature on for every customer or mutating current real SCI, Wholesale, Atlantis, Roofing, BookaBoost, Restland records.
- No existing advisor workspaces, CRM inboxes, pipelines, customer email/phone workflows or other project branches may break.

### M4: VERIFIABLE RELEASE, NO FICTIONAL GO-LIVE
- Tests: focused T6/T7/T8/T9; integration/API; customer UI; authorization/tenant boundaries; pause/resume/retire; worker retries and crash recovery; channel no-send assertions; regression; frontend build. Report exact command, pass/fail/skips and baseline comparison. Fix introduced failures.
- Demonstrate first-time customer browser journey on real local/staging synthetic data. Evidence should include screenshot or accessible trace of choosing job, entering setup, test PASS, worker run/outcome, dashboard non-zero record and stop control. Never fabricate screenshot or claim browser testing if browser unavailable.
- Open reviewable PR(s) against main and link #5. Before release reconcile main movement and conflicts; do not overwrite other lanes' changes. Provide PR link, CI status, changed files, commit SHAs and release notes.
- Make an exact deployment plan for backend/frontend, migrations, worker scheduling/cron and health checks, default-off flags, feature visibility, tenant entitlement, billing/cost estimate, data safety, logs, rollback and kill switch. All production validation must be observable with exact version.
- OWNER AUTHORIZATION CLARITY: Owner requests the completed product to be made available platform-wide ASAP. This mandates release-readiness and rapid promotion of reviewed safe UI/code/test-mode features through the authorized deployment process. This is NOT blanket permission to run production AI on existing customer records, enable real SMS/email/voice, provision paid Render services, change carrier/A2P settings, auto-entitle every tenant, migrate/destructively alter live data, or merge directly to main in the guarded relay. For each actual approval gate, present ONE concise decision with exact risk/cost/switch and then proceed when approved. Do not repeatedly ask about ordinary local coding or testing.
- A production UI appearing or a green CI check is NOT sufficient. Report truly available only when the approved production version is deployed, the correct authorized account can use the feature and live smoke tests pass. Separate 'UI deployed', 'safe test mode ready', and 'real customer messaging enabled' in every report.

## TIMESTAMPED MILESTONE REPORTING (MANDATORY)
On #5 at start, every 10-15 real minutes of active work, on each commit/PR, and at run end, use:
[AIWF CT CHECKPOINT]
TIME: YYYY-MM-DD HH:MM CDT/CST (read system clock, America/Chicago)
ACTIVE MINUTES: measured, not guessed
BRANCH: feature/ai-workforce-launch
CURRENT COMMIT: full SHA or NONE YET
MILESTONE: M1/M2/M3/M4; PASS/PARTIAL/BLOCKED
DONE SINCE LAST CHECK: observable code + behavioral effect
TESTS: exact commands, exact passed/failed/skipped
PROOF: real API/worker/DB/UI trace, links to changes
NEXT 10-15 MINUTES: one specific action
BLOCKERS: exact external blockers ONLY (else NONE)
OTHER LANES/PRODUCTION: untouched / specific approved change

At terminal run on issue #1 follow the exact relay status contract for the matching relay_run_id. Include parent/milestone and next action. The overall objective stays OPEN until all acceptance gates pass; timed slice completion is not project completion.

## USER ACCEPTANCE — THE ONLY ACCEPTABLE END STATE
A first-time entitled non-God user can sign in -> select AI Workforce -> choose Lead Qualifier -> answer a few everyday questions -> Run test -> see genuine PASS/NEEDS ATTENTION and a traceable synthetic outcome -> click Start working in safe mode -> observe a worker process synthetic work -> read real results/handoffs -> Pause/Resume/Stop without any technical help. The three administrator panels display the same truthful system state, and an authorized release makes safe mode visible to appropriate tenants across platform navigation. Real customer outbound remains OFF until independently approved. No technical jargon, dead cards, inert workers, fake healthy dashboards, unreported blockers or unfinished handoffs.