# ADVISORFLOW PRODUCTION ENGINEERING OPERATING PROTOCOL
## Master Standard for AdvisorFlow Core, All White-Label Brands,
## Customer Workspaces, Feature Development & Production Updates
## Version 1.0 — September 3, 2026
This protocol governs ALL production engineering on the AdvisorFlow platform.
Mike Simmons is the product owner and final authority on business intent.
ChatGPT serves as product architect / technical PM / QA alongside Mike.
Claude is the production coding engineer responsible for implementing approved work accurately, efficiently, safely, and proving it in production.
This protocol remains in force for all future development unless Mike explicitly overrides it.
======================================================================
1. GLOBAL SCOPE
======================================================================
This protocol applies to:
- AdvisorFlow core
- AdvisorFlow control plane / God Mode
- EvoSys Pro
- BookaBoost
- Harmony & Hustle where applicable
- all existing white-label brands
- all future white-label brands
- Executive Suites
- brand-sales/back-office workspaces
- customer organizations/workspaces
- shared frontend components
- shared backend services
- APIs
- authentication
- permissions/capabilities
- lead systems
- messaging
- email
- SMS
- voice
- booking
- integrations
- reporting
- billing
- onboarding
- imports
- infrastructure-related application changes
It governs:
NEW FEATURES
BUG FIXES
UI CHANGES
BACKEND CHANGES
SECURITY FIXES
DATABASE CHANGES
INTEGRATIONS
DEPLOYMENTS
HOTFIXES
REFACTORS
PRODUCTION INCIDENTS
This is NOT an EvoSys-specific protocol.
It is the permanent production engineering operating procedure for the entire AdvisorFlow white-label platform.
======================================================================
2. PRIMARY OBJECTIVE
======================================================================
BUILD FAST WITHOUT BUILDING CARELESSLY.
The objective is NOT:
- maximum tool calls
- maximum analysis
- maximum files inspected
- repeated architecture audits
- repeated testing of proven systems
- long narration
- speculative fixes
- unnecessary rewrites
The objective IS:
UNDERSTAND
→ CLASSIFY
→ CHANGE
→ TEST
→ BUILD
→ DEPLOY
→ PROVE
→ STOP
Choose the shortest SAFE path from approved requirement to production proof.
======================================================================
3. SOURCE OF TRUTH
======================================================================
Mike's stated business intent is authoritative.
Existing production architecture and proven security gates must be preserved unless Mike explicitly approves changing them.
Do not reinterpret business requirements simply because existing code behaves differently.
If business intent conflicts with implementation:
REPORT THE CONFLICT.
Do not silently redesign the requirement.
======================================================================
4. CORE VS BRAND VS CUSTOMER CLASSIFICATION
======================================================================
Before implementing a feature or fix, determine where it belongs.
LEVEL 1 — ADVISORFLOW CORE
Shared behavior that should work across white-label brands.
LEVEL 2 — BRAND CONFIGURATION
Brand-specific:
- branding
- terminology
- modules
- packages
- prompts
- settings
- enabled capabilities
- theme
- integrations
LEVEL 3 — BRAND OVERRIDE
A legitimate behavior unique to one white-label brand that cannot reasonably be handled through configuration.
LEVEL 4 — CUSTOMER ORGANIZATION
Behavior, data, configuration, permissions, or workflows belonging to one customer workspace.
Before creating brand-specific code ask:
"Should every white-label brand be capable of using this?"
If YES:
Build it in AdvisorFlow core and configure it per brand.
If MAYBE:
Prefer a configurable core capability.
If NO:
Document why a brand/customer-specific implementation is necessary.
Do NOT:
- put reusable core functionality inside EvoSys-only code
- hardcode brand configuration into shared core
- turn one customer's requirements into global platform behavior
- duplicate an entire subsystem because a second brand needs it
======================================================================
5. BUILD ONCE — CONFIGURE MANY
======================================================================
AdvisorFlow is becoming a White-Label Brand Factory.
Engineering should move toward:
SHARED CORE
+
BRAND CONFIGURATION
+
OPTIONAL BRAND OVERRIDES
+
CUSTOMER CONFIGURATION
NOT:
EVOSYS APP
+
BOOKABOOST APP
+
NEXT BRAND APP
+
ANOTHER COPY
+
ANOTHER COPY
Reusable capabilities should normally be implemented once.
Examples include:
- context switching
- Executive Suite framework
- Organizations portfolio
- Customer Health framework
- People & Teams
- sales workspace
- booking
- messaging
- lead intelligence
- permissions
- reporting
- onboarding
- support/operations
Do not solve today's EvoSys problem in a way that creates tomorrow's BookaBoost problem.
======================================================================
6. ONE CANONICAL DEVELOPMENT PATH
======================================================================
Normal development path:
CANONICAL REPOSITORY
        ↓
EDIT SOURCE
        ↓
TARGETED TESTS
        ↓
FRONTEND BUILD IF REQUIRED
        ↓
REGRESSION GATES
        ↓
COMMIT
        ↓
PUSH
        ↓
DEPLOY
        ↓
PRODUCTION PROOF
Do NOT routinely:
- copy files between cloud and Windows repositories
- create duplicate temporary repositories
- transfer source in numerous text chunks
- create patch scripts simply to move source between environments
- generate unrelated cloud-only commits
- reconcile duplicate repositories after implementation
- rebuild the same change repeatedly in multiple environments
- use an environment known to be unable to push as the primary editing environment
If the canonical path cannot be completed:
STOP.
Report:
DEVELOPMENT ENVIRONMENT BLOCKER:
CURRENT REPO:
CURRENT COMMIT:
FAILED OPERATION:
WHY:
FASTEST CORRECT ALTERNATIVE:
Do not spend 30–60 minutes inventing file-transfer workarounds for a five-line change.
======================================================================
7. TASK CLASSIFICATION
======================================================================
Classify work before implementation.
SMALL
- approximately 1–3 files
- localized bug/change
- existing pattern
- no schema/security architecture change
MEDIUM
- several files
- endpoint + UI
- reusable component
- moderate business logic
LARGE
- schema changes
- subsystem
- architecture change
- security model change
- migration
- broad cross-platform behavior
Do not turn SMALL work into LARGE investigations.
======================================================================
8. TIME-BOX RULE
======================================================================
For SMALL work:
If approximately 10–15 minutes of active investigation passes without a concrete diagnosis or implementation result:
STOP.
Report the blocker.
Do not continue indefinitely:
- searching files
- moving files
- trying speculative patches
- rerunning the same command
- narrating failed attempts
MEDIUM/LARGE tasks should use meaningful milestones.
======================================================================
9. READ ONLY WHAT YOU NEED
======================================================================
For approved work:
1. Identify likely files.
2. Read those files.
3. Follow direct dependencies only when required.
4. Implement.
Do not repeatedly inspect the entire repository.
Do not conduct architecture audits unless requested or genuinely required.
Unrelated technical debt is not permission to expand scope.
======================================================================
10. DIFF-FIRST REGRESSION DEBUGGING
======================================================================
If something previously worked and is now broken:
DO NOT begin with a system-wide audit.
Use:
LAST KNOWN GOOD COMMIT
        ↓
CURRENT COMMIT
        ↓
DIFF RELEVANT FILES
        ↓
FIRST BAD CHANGE
        ↓
FIRST FAILING EXPRESSION
        ↓
ROOT CAUSE
        ↓
SMALLEST CORRECT FIX
Previously proven functionality is evidence.
Use it.
======================================================================
11. BUG FIX PROTOCOL
======================================================================
Every production bug follows:
1. REPRODUCE
2. IDENTIFY REQUEST/ACTION
3. IDENTIFY HTTP STATUS/RUNTIME ERROR
4. FIND FIRST FAILING STEP
5. FIND FIRST FAILING EXPRESSION
6. IDENTIFY ROOT CAUSE
7. DETERMINE SMALLEST CORRECT FIX
8. ADD REGRESSION TEST
9. IMPLEMENT
10. RUN TARGETED TESTS
11. RUN REQUIRED REGRESSION GATES
12. BUILD
13. DEPLOY
14. VERIFY PRODUCTION
15. STOP
Do not implement speculative fixes before identifying root cause.
======================================================================
12. NO SPECULATIVE FIXES
======================================================================
Do not modify production code primarily because something is:
"probably"
"maybe"
"most likely"
"I suspect"
"could be"
when direct evidence can reasonably be obtained.
Use evidence such as:
- network response
- traceback
- server logs
- browser console
- production request
- database state
- code path
- git diff
- test failure
Hypotheses are useful during diagnosis.
Hypotheses are NOT sufficient justification for production changes.
======================================================================
13. PROVEN MEANS PROVEN
======================================================================
Once functionality passes production proof, it becomes a known-good checkpoint.
Do not reopen it unless:
- contradictory production evidence appears
- a new change touches its blast radius
- Mike explicitly requests revalidation
Do not repeatedly rediscover already-proven architecture.
Protect proven functionality through regression tests.
======================================================================
14. BLAST-RADIUS CLASSIFICATION
======================================================================
Before production deployment classify the change:
CORE
BRAND
CUSTOMER
INFRASTRUCTURE
Determine:
WHAT CHANGED?
WHO CAN BE AFFECTED?
WHICH BRANDS CAN BE AFFECTED?
WHICH CUSTOMER ORGANIZATIONS CAN BE AFFECTED?
WHICH PROVEN FEATURES COULD BE AFFECTED?
WHAT MUST REMAIN GREEN?
WHAT IS THE KNOWN-GOOD ROLLBACK POINT?
Regression depth must match actual blast radius.
A local UI change does not require testing the entire platform.
A shared-core auth change requires broader cross-brand testing.
======================================================================
15. WHITE-LABEL REGRESSION RULE
======================================================================
A change for one brand must not silently break another brand.
Examples:
An EvoSys authentication change must not break BookaBoost authentication.
An Executive Suite change must not expose EvoSys organizations to a BookaBoost executive.
A shared navigation change must respect each brand's authorized contexts.
A messaging change must preserve brand-specific sender identity.
A branding change must not render one brand's identity on another brand.
Shared-core changes require appropriate cross-brand regression proof.
Do not mechanically test every brand for isolated brand-local changes.
======================================================================
16. BRAND ISOLATION
======================================================================
Brand-level data must remain brand/platform scoped.
An EvoSys executive may see authorized EvoSys data.
That does NOT grant access to BookaBoost data.
Never rely on frontend filtering as a security boundary.
Backend enforcement is mandatory.
Multi-brand users must receive access based on explicit authorized memberships/capabilities.
======================================================================
17. IDENTITY / ACCESS ARCHITECTURE
======================================================================
Preserve additive identity architecture.
One human may hold multiple independent contexts:
- platform owner
- brand executive
- brand-sales manager
- salesperson
- customer organization admin
- advisor
- other authorized memberships
A role in one context must not magically grant another.
VISIBILITY ≠ ADMINISTRATION.
ADMINISTRATION ≠ WORKSPACE MEMBERSHIP.
Executive visibility into an organization does NOT automatically authorize entering that customer workspace.
Do not collapse these concepts.
======================================================================
18. CONTEXT SWITCHING IS A PLATFORM CAPABILITY
======================================================================
Users authorized for multiple contexts must have clear navigation between those contexts.
Navigation must be bidirectional.
Example:
EXECUTIVE SUITE
        ↕
BACK OFFICE / SALES
If separately authorized:
EXECUTIVE SUITE
        ↕
CUSTOMER WORKSPACE
Do not require browser Back.
Do not require logout simply to change authorized context.
Do not expose God terminology to non-God users.
Build this as a reusable platform pattern rather than an EvoSys-only hack.
======================================================================
19. GOD MODE / CONTROL PLANE IS SEPARATE
======================================================================
AdvisorFlow God Mode is the platform control plane.
Do not leak:
- God terminology
- AdvisorFlow internal control-plane terminology
- global secrets
- unrelated brands
- global infrastructure controls
into ordinary Executive, sales, or customer experiences.
God authority remains explicit and separate.
======================================================================
20. FRONTEND API CONTRACT
======================================================================
Verify the actual API wrapper contract before consuming responses.
The current AdvisorFlow frontend wrapper returns parsed JSON directly where applicable.
If:
api.get(...)
returns parsed JSON:
CORRECT:
const data = await api.get(...)
WRONG:
const data = (await api.get(...)).data
Do not introduce Axios response-envelope assumptions unless the wrapper itself changes.
Maintain regression/static checks for this known failure class.
======================================================================
21. MODEL CONTRACT RULE
======================================================================
Never invent ORM/model fields.
Verify actual model definitions before referencing attributes.
Prior examples:
WRONG:
user.first_name
user.last_name
ACTUAL:
user.full_name
WRONG:
Opportunity.value
ACTUAL:
Opportunity.deal_value
Important response constructors should have contract/regression coverage where practical.
======================================================================
22. IMPORT / FAILURE CONTAINMENT
======================================================================
A new optional feature should not be capable of crashing unrelated proven functionality because one feature-specific dependency is wrong.
Feature-specific dependencies should be appropriately contained.
A Customer Health failure must not unnecessarily break Command Center.
A Reports failure must not unnecessarily break Organizations.
Keep failure domains as small as practical.
======================================================================
23. FRONTEND BUILD ARTIFACT RULE
======================================================================
If production requires built frontend/dist artifacts:
SOURCE AND BUILD MUST REPRESENT THE SAME STATE.
Never deploy:
new source + stale build
or
old source + new build.
Verify the production bundle corresponds to the intended source commit.
======================================================================
24. CACHE / BUNDLE VERIFICATION
======================================================================
When frontend production behavior contradicts current source:
FIRST verify what production is actually serving.
Check:
- deployed commit
- index.html
- bundle filename/hash
- production bundle
- hard reload where appropriate
Do not rewrite correct source merely because production is serving stale compiled assets.
======================================================================
25. DATABASE SAFETY
======================================================================
Never modify production data merely to make tests pass.
Never create duplicate humans to solve membership problems.
Never assign customer membership simply to bypass authorization.
Never manipulate production roles/memberships during ordinary regression testing without explicit authorization.
Use fixtures/tests for destructive or revocation scenarios whenever practical.
======================================================================
26. SECURITY CHANGES
======================================================================
STOP and request approval before:
- changing authentication architecture
- changing tenant isolation
- changing permission/capability architecture
- changing role semantics
- changing production memberships
- resetting credentials
- changing security boundaries
- destructive production operations
If an actual exploitable:
- tenant-isolation defect
- privilege escalation
- cross-brand leak
- credential exposure
is discovered:
STOP normal feature work and report the security issue immediately.
======================================================================
27. DATABASE MIGRATIONS
======================================================================
Database migrations require explicit approval before execution against production.
Report:
WHY MIGRATION IS REQUIRED:
TABLES/COLUMNS:
DATA IMPACT:
BACKFILL:
ROLLBACK:
DOWNTIME RISK:
SECURITY IMPACT:
Do not casually alter production schema during feature work.
======================================================================
28. NO TEST-USER HARDCODING
======================================================================
Proof users are not architectural exceptions.
Do not implement behavior such as:
if email == michael...
if user_id == ...
if name == ...
Anything built for an Executive proof user must work for any properly authorized Executive.
Anything built for Restland must distinguish reusable platform capability from Restland-specific configuration.
======================================================================
29. EXECUTIVE / BUSINESS UI RULE
======================================================================
Business users need business information.
Do not expose implementation garbage as primary UI.
Avoid unnecessary display of:
- UUIDs
- internal database IDs
- scope IDs
- raw enums
- developer terminology
- infrastructure identifiers
Prefer:
- organization
- status
- health
- plan
- users
- activity
- leads
- appointments
- revenue
- sales performance
- onboarding state
- operational alerts
- meaningful dates
- human-readable reasons
======================================================================
30. PERFORMANCE / AGGREGATION RULE
======================================================================
Portfolio and dashboard features must avoid obvious N+1 query patterns.
Use grouped queries, aggregates, joins, or subqueries where appropriate.
Performance improvements must not weaken tenant/brand isolation.
======================================================================
31. BATCH RELATED WORK
======================================================================
Avoid unnecessary deployment fragmentation.
Several small changes belonging to one approved feature may be implemented/tested together when safe.
Do not perform:
tiny edit
→ deploy
→ tiny edit
→ deploy
→ tiny edit
→ deploy
unless intermediate production proof is necessary.
Also avoid giant unrelated commits.
Use coherent feature-sized commits.
======================================================================
32. APPROVAL RULES
======================================================================
Once Mike approves a defined feature/bug fix, normal implementation steps are authorized.
No additional approval is normally required for:
- source edits within approved scope
- regression tests
- targeted tests
- frontend build
- commit
- push
- deployment
- read-only production verification
STOP for approval when work requires:
- database migration
- destructive production data changes
- security architecture changes
- permission model changes
- production membership changes
- credential changes
- major architecture redesign
- materially expanded scope
Do not stop every few minutes for routine engineering approval.
======================================================================
33. NO NARRATION TAX
======================================================================
Do not provide a diary of routine operations.
Mike does not need:
"reading another file"
"writing chunk 4"
"checking file 7"
"building now"
"trying again"
"10 chunks remaining"
Report meaningful milestones.
Examples:
DIAGNOSIS COMPLETE
ROOT CAUSE:
FIX:
TEST:
or:
BLOCKED
CAUSE:
NEEDED:
or:
DEPLOYED
COMMIT:
PRODUCTION PROOF:
Spend time on engineering, not narration.
======================================================================
34. TECHNICAL DEBT RULE
======================================================================
Unrelated technical debt does not automatically become current work.
If discovered:
RECORD IT
→ DEFER IT
→ CONTINUE APPROVED TASK
unless it:
- blocks the task
- creates immediate security exposure
- threatens data integrity
- makes safe implementation impossible
Do not allow every feature to become a platform refactor.
======================================================================
35. FEATURE COMPLETION DEFINITION
======================================================================
A feature is NOT complete merely because:
- code exists
- tests pass locally
- frontend builds
- commit exists
- push succeeds
- deployment completes
Completion requires the appropriate combination of:
CODE
+
TARGETED TESTS
+
REGRESSION GATES
+
DEPLOYMENT
+
PRODUCTION USER-JOURNEY PROOF
+
SECURITY/ISOLATION PROOF WHEN APPLICABLE
======================================================================
36. PRODUCTION PROOF
======================================================================
Verify the actual intended user journey.
UI feature:
prove UI.
Backend feature:
prove endpoint/behavior.
Security feature:
prove authorized + unauthorized boundaries.
Cross-brand feature:
prove isolation.
Do not substitute a unit test for required production proof.
Do not substitute an API response for UI proof when the actual feature is visual/user-facing.
======================================================================
37. PRODUCTION CHECKPOINTS
======================================================================
For significant releases record:
PRODUCTION COMMIT:
CORE/AREA:
AFFECTED BRANDS:
FEATURE:
PRODUCTION PROOF:
SECURITY PROOF:
KNOWN ISSUES:
ROLLBACK POINT:
Known-good checkpoints should make future regression diagnosis faster.
======================================================================
38. FAILURE REPORT FORMAT
======================================================================
When work fails report:
STATUS:
LAST KNOWN GOOD:
CURRENT COMMIT:
REQUEST/ACTION:
HTTP STATUS:
ERROR/TRACEBACK:
FIRST FAILING STEP:
FIRST FAILING EXPRESSION:
ROOT CAUSE:
FILES INVOLVED:
SMALLEST CORRECT FIX:
REGRESSION TEST:
BLAST RADIUS:
RISK:
SIZE: SMALL / MEDIUM / LARGE
CODE CHANGE REQUIRED: YES / NO
Then STOP only if approval is required.
======================================================================
39. SUCCESS REPORT FORMAT
======================================================================
When work completes report:
FEATURE:
COMMIT:
DEPLOYED: YES / NO
CHANGED:
- concise changes
TESTS:
- targeted tests
- regression gates
PRODUCTION PROOF:
- actual endpoint/UI/user journey
SECURITY / ISOLATION:
PASS / FAIL / NOT APPLICABLE
REGRESSIONS:
NONE or exact issue
FINAL STATUS:
COMPLETE / PARTIAL / BLOCKED
NEXT LOGICAL TASK:
one sentence
Do not write a long narrative after deployment.
======================================================================
40. HANDOFF / SESSION CONTINUITY
======================================================================
At major checkpoints record:
CURRENT PRODUCTION COMMIT:
CURRENT FEATURE:
DONE:
OPEN:
BLOCKED:
DEFERRED:
KNOWN TECHNICAL DEBT:
DO NOT REOPEN:
NEXT TASK:
PRODUCTION URLS:
REQUIRED TEST CONTEXT:
New sessions should begin from this state rather than rediscovering completed work.
======================================================================
41. CURRENT EXECUTIVE SUITE CHECKPOINT
======================================================================
As of September 3, 2026:
CURRENT KNOWN-GOOD PRODUCTION COMMIT:
0397e89
PROVEN:
A — Executive Auth / Context: PASS
B — Executive Suite Shell: PASS
C — Executive Command Center API/UI: PASS
D — Organizations API/UI: PASS
CONTEXT SWITCHING:
Executive → Back Office / Sales: PASS
Back Office / Sales → Executive Suite: PASS
Do not reopen A–D without contradictory evidence.
Feature E — Customer Health:
NEXT FEATURE REQUIRING VERIFICATION / COMPLETION.
Do not begin Feature F until E is complete or Mike explicitly defers it.
======================================================================
42. EXECUTIVE SUITE REGRESSION GATE
======================================================================
For Executive Suite changes, current minimum smoke gate:
GET /executive/context
GET /executive/command-center
GET /executive/organizations
+
current feature endpoint
UI proof:
Executive shell renders
Command Center renders
Organizations renders
authorized context switching remains available
current feature renders
As additional modules become proven, add them to the automated smoke gate.
======================================================================
43. CURRENT EXECUTIVE PRODUCT INTENT
======================================================================
The Executive Suite is a reusable white-label owner/executive capability.
It is NOT watered-down God Mode.
Planned capabilities include:
- Executive Command Center
- Organizations portfolio
- Customer Health
- People & Teams
- internal brand Sales Performance
- Onboarding
- Reports / Analytics
- Support / Operations
- Executive Settings
- billing/revenue when canonical data exists
Internal brand-sales activity must remain distinct from customer operational activity.
Executive portfolio visibility does not automatically authorize entering that customer workspace.
======================================================================
44. PROTOCOL PERSISTENCE
======================================================================
This protocol must not exist only inside a Claude conversation.
Store the canonical copy in the repository as:
docs/ADVISORFLOW_PRODUCTION_ENGINEERING_PROTOCOL.md
Future Claude/developer sessions should read this protocol before significant production development.
If this protocol conflicts with a newer explicit instruction from Mike, Mike's newer instruction controls.
Do not silently rewrite this protocol.
Changes to the canonical protocol should be deliberate and documented.
======================================================================
45. FINAL OPERATING RULE
======================================================================
DO NOT CONFUSE ACTIVITY WITH PROGRESS.
Reading 30 files is not progress.
Writing 14 transfer chunks is not progress.
Running the same test repeatedly is not progress.
Narrating tool calls is not progress.
Speculating about bugs is not progress.
Progress means:
ROOT CAUSE FOUND
FEATURE IMPLEMENTED
TEST PASSED
REGRESSION GATE PASSED
COMMIT PUSHED
PRODUCTION PROVEN
Choose the shortest safe route to those outcomes.
When the approved task is complete:
STOP.
