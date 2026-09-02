# deploy.ps1 -- AdvisorFlow one-command deploy
# Run this from the repo root on your machine (any branch is fine).
# Flow: staging changes -> main -> GitHub -> Render -> live
#
# Usage:  .\deploy.ps1
#         .\deploy.ps1 -Message "custom commit message"
#         .\deploy.ps1 -SkipSmoke        (not recommended)
#
# ---------------------------------------------------------------------------
# FIXED 2026-08-25 -- DATA LOSS BUG
# The old step 2 ran `git reset --hard origin/main` unconditionally. When you
# were ALREADY on main (the normal case), that threw away the auto-save commit
# step 1 had just made, silently deploying stale code while reporting success.
# It cost a full session of backend work. The reset now only ever runs on a
# throwaway checkout of main, never on a branch holding your commits, and
# step 4 verifies the push actually contains your changes before claiming
# victory. Do not "simplify" this back.
# ---------------------------------------------------------------------------

param(
    [string]$Message = "",
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Continue"
$REPO = Split-Path -Parent $MyInvocation.MyCommand.Path

# Render API key: MUST come from the environment. There is no fallback.
#
# A key was previously hardcoded here and this repository is PUBLIC, so it was
# readable by anyone at raw.githubusercontent.com with no authentication. It is
# still present in git history -- removing it from HEAD does NOT revoke it.
# THE KEY IT REPLACED MUST BE ROTATED IN THE RENDER DASHBOARD.
#
# Set it once per machine (PowerShell, persists across sessions):
#   [Environment]::SetEnvironmentVariable("RENDER_API_KEY","<new key>","User")
# Then open a new terminal.
$RKEY = $env:RENDER_API_KEY
if (-not $RKEY) {
    Write-Host ""
    Write-Host "  RENDER_API_KEY is not set. Steps 1-5 can still run, but step 6"
    Write-Host "  cannot trigger the static frontend deploy."
    Write-Host "  Set it with:"
    Write-Host '    [Environment]::SetEnvironmentVariable("RENDER_API_KEY","<key>","User")'
    Write-Host ""
}
$FRONTEND_SVC = "srv-d8rslocvikkc738v7ocg"   # advisorflow-frontend (static site)
$BACKEND_SVC  = "srv-d8rsm2kvikkc738v8470"   # advisorflow-backend (auto-deploys on push)

$ts = Get-Date -Format "yyyy-MM-dd HH:mm"
if (-not $Message) { $Message = "deploy: [$ts]" }

Write-Host ""
Write-Host "=============================================="
Write-Host "  AdvisorFlow Deploy"
Write-Host "  $Message"
Write-Host "=============================================="
Write-Host ""

Set-Location $REPO

# -- Step 1: Save any uncommitted work on the current branch -------------------
Write-Host "[1/6] Saving current work..."
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
git add -A
$staged = git diff --cached --name-only
# Remembered so step 5 can give this commit the real message. The auto-save is
# a SAFETY NET, not a description of the work, and it was quietly becoming the
# permanent record: step 1 committed everything, so by step 5 there was nothing
# left to stage, the `git commit -m $Message` there was skipped, and the deploy
# shipped under "wip: auto-save before deploy" with the actual explanation
# discarded. Two production commits landed that way before it was noticed.
$WIP_COMMITTED = $false
if ($staged) {
    git commit -m "wip: auto-save before deploy [$ts]" | Out-Null
    $WIP_COMMITTED = $true
    Write-Host "  Committed uncommitted changes on $branch"
} else {
    Write-Host "  Nothing to commit on $branch"
}

# Remember exactly what we intend to ship, so step 5 can verify it landed.
$SHIP_SHA = (git rev-parse HEAD).Trim()
Write-Host "  Shipping commit: $SHIP_SHA"

# -- Step 2: Get main up to date WITHOUT destroying local commits --------------
Write-Host "[2/6] Syncing main branch..."
git fetch origin main
if ($branch -eq "main") {
    # Already on main. NEVER reset here - that is the bug that ate a session.
    # Fast-forward only: if origin has moved ahead, merge it in; if we cannot
    # fast-forward, stop and let a human decide rather than discarding work.
    git merge --ff-only origin/main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "  main has diverged from origin/main."
        Write-Host "  Your work is safe in commit $SHIP_SHA."
        Write-Host "  Reconcile manually (git log --oneline main origin/main), then re-run."
        exit 1
    }
    Write-Host "  main fast-forwarded (local commits preserved)"
} else {
    git checkout main | Out-Null
    git reset --hard origin/main | Out-Null   # safe: main holds no unpushed work here
    Write-Host "  Merging $branch into main..."
    git merge $branch --no-edit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "MERGE CONFLICT - fix conflicts then run deploy.ps1 again"
        exit 1
    }
}

# -- Step 3: Smoke tests (catch a broken app BEFORE it reaches production) -----
if ($SkipSmoke) {
    Write-Host "[3/6] Smoke tests SKIPPED (-SkipSmoke)"
} else {
    Write-Host "[3/6] Running smoke tests..."
    python scripts\smoke_import.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SMOKE IMPORT FAILED - not deploying."; exit 1 }
    python scripts\smoke_requests.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SMOKE REQUESTS FAILED - not deploying."; exit 1 }
    python scripts\smoke_sales_models.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SALES SCHEMA CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_tenancy.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "TENANCY REGRESSION FAILED - not deploying."; exit 1 }
    python scripts\smoke_sales_login.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SALES LOGIN CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_sales_workspace.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SALES WORKSPACE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_scheduling.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SCHEDULING CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_calendar_sync.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CALENDAR SYNC CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_sales_execution.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SALES EXECUTION CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_manager_workspace.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "MANAGER WORKSPACE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_retell_bridge.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "RETELL BRIDGE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_tenant_bridge.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "TENANT BRIDGE CHECKS FAILED - not deploying."; exit 1 }
    # What a family sees: which brand's address mail leaves under, and which
    # host their links point at. Guards the cross-brand leak that sent an
    # EvoSys customer's families mail from a BookaBoost address.
    python scripts\probe_public_identity.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PUBLIC IDENTITY CHECKS FAILED - not deploying."; exit 1 }
    # Which calendar a booking is actually written to. Guards the silent
    # Microsoft-because-it-is-first-in-a-tuple failure, and the fabricated
    # free day that a degraded provider used to produce.
    python scripts\probe_calendar_provider.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CALENDAR PROVIDER CHECKS FAILED - not deploying."; exit 1 }
    # One organization record, six channels, one identity - plus a source scan
    # that stops a new hand-built customer link being written tomorrow.
    python scripts\probe_restland_identity.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CROSS-CHANNEL IDENTITY CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_integration_migration.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "INTEGRATION MIGRATION CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_demo_firewall.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DEMO FIREWALL CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_demo_mode.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DEMO MODE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_demo_frontend.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DEMO FRONTEND CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_checkpoint6.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CHECKPOINT 6 CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_checkpoint6_frontend.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CHECKPOINT 6 FRONTEND CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_legacy_hardening.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "LEGACY HARDENING CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_staff_activation.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "STAFF ACTIVATION CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_sales_workspace_complete.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SALES WORKSPACE COMPLETION CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_sales_staff.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "SALES STAFF MANAGEMENT CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_platform_boundary.py 2>&1 | Select-String "LEAK|BROKE|checks passed|HOLDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PLATFORM BOUNDARY CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_org_settings_scoping.py 2>&1 | Select-String "LEAK|BROKE|checks passed|HOLDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CROSS-BRAND org_id SCOPING FAILED - not deploying."; exit 1 }
    python scripts\probe_brand_config.py 2>&1 | Select-String "FAIL|checks passed|HOLDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "BRAND CONFIG CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_workspaces.py 2>&1 | Select-String "FAIL|checks passed|HOLDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "WORKSPACES CONTEXT CHECKS FAILED - not deploying."; exit 1 }
    # GATE 27 - the two administrative delegation gates. Proves that holding an
    # admin role grants no infrastructure capability by itself, that a personal
    # grant is inert while the organization is not allowed to self-manage, that
    # advisors can never be granted one at all, and that God needs neither gate.
    python scripts\probe_delegation.py 2>&1 | Select-String "LEAK|BROKE|checks passed|HOLDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DELEGATION MODEL CHECKS FAILED - not deploying."; exit 1 }
    # GATE 28 - god reaches EVERY brand's sales workspace through god authority
    # plus the selected brand, holding no membership anywhere, while a normal
    # user still needs an active membership and a brand header grants nobody
    # anything.
    python scripts\probe_god_sales_workspace.py 2>&1 | Select-String "FAIL|checks passed|GOD REACHES" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "GOD SALES WORKSPACE CHECKS FAILED - not deploying."; exit 1 }
    # GATE 29 - P0 ADVISOR DATA ISOLATION. A plain advisor reaches no lead,
    # batch, count, conversation, activity or organization record outside their
    # own assignment - proved with two advisors in one org, a manager, a second
    # tenant, and real child records on the other advisor's leads so a passing
    # check cannot mean "the table was empty".
    python scripts\probe_advisor_isolation.py 2>&1 | Select-String "LEAK|BROKE|checks passed|HOLDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "ADVISOR ISOLATION CHECKS FAILED - not deploying."; exit 1 }
    # GATE 30 - PLATFORM / WORKSPACE ACCESS. Membership decides who may ENTER a
    # customer workspace; P0 still decides what they see once inside. Covers the
    # platform-only, workspace-only, dual-access and multi-workspace users, a
    # revoked membership losing both the button and the route, the legacy-column
    # backfill, and a clean customer activation end to end.
    python scripts\probe_workspace_context.py 2>&1 | Select-String "OPEN |BROKE|checks passed|MEMBERSHIP DECIDES" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "WORKSPACE ACCESS CHECKS FAILED - not deploying."; exit 1 }
    # GATE 31 - THE LEGACY COLUMN CANNOT MINT WORKSPACE ACCESS. users.organization_id
    # is the migration SOURCE for customer_org memberships, so it is held to seven
    # rules: only a real customer workspace, never inferred from a platform or
    # brand-sales relationship, never writes the column back, never duplicates,
    # never resurrects a revoked membership, idempotent, and it stops being able
    # to mint at all once the migration completes.
    python scripts\probe_workspace_backfill.py 2>&1 | Select-String "MINT |BROKE|checks passed|CANNOT MINT" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "LEGACY BACKFILL CHECKS FAILED - not deploying."; exit 1 }
    # GATE 32 - THE GOD-ONLY ACCESS DIAGNOSTIC. It reads one named person's
    # identity, memberships, workspace resolution and lead counts, which is as
    # sensitive as the database shell it replaces: god only, writes nothing,
    # leaks no credential, and names the actual cause rather than shrugging.
    python scripts\probe_access_diagnostic.py 2>&1 | Select-String "OPEN |BROKE|checks passed|GOD ONLY" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "ACCESS DIAGNOSTIC CHECKS FAILED - not deploying."; exit 1 }
    # GATE 33 - THE WORKSPACE ROUTE GUARD. An authorization guard must be able
    # to tell "the server refused you" from "the server did not answer": 401,
    # 404, 500, a timeout and a dropped connection are not refusals, and they
    # are not access either. Executes the real decision module over every
    # lifecycle, proves the suite by four reverts, and holds the two sibling
    # defects fixed - logout leaving one person's workspace context for the
    # next, and a failed dashboard request rendering as the number 0.
    python scripts\probe_workspace_route_guard.py 2>&1 | Select-String "REVERT|BROKE|checks passed|NO DENIAL" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "WORKSPACE ROUTE GUARD CHECKS FAILED - not deploying."; exit 1 }
    # GATE 34 - THE BROWSER CAN SEND WHAT THE CLIENT SENDS. A header added to
    # the API client and not to CORSMiddleware's allow_headers makes the
    # browser refuse to send the request at all: the preflight is answered 400
    # "Disallowed CORS headers", the server never sees the call, and the page
    # sees every request reject. This reads the headers OUT OF client.js and
    # sends real preflights for both production brand origins, so the next
    # header cannot be added in one place only.
    python scripts\probe_cors_preflight.py 2>&1 | Select-String "MISSING|BROKE|checks passed|PREFLIGHT SUCCEEDS" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CORS PREFLIGHT CHECKS FAILED - not deploying."; exit 1 }
    # GATE 35 - QUALIFICATION NARROWS AUTHORIZATION, NEVER WIDENS IT. The whole
    # risk of a qualification engine is that it becomes a second way to select
    # leads, and a second selector is a second place tenancy gets decided. This
    # asks an advisor to qualify a colleague's lead and another tenant's lead
    # through every entry point and requires the same refusal lead_scope gives,
    # then proves EXCLUDED and REVIEW_REQUIRED cannot reach the email queue at
    # any batch size - and that a qualified send still works. Six reverts.
    python scripts\probe_qualification.py 2>&1 | Select-String "OPEN |BROKE|REVERT|checks passed|NARROWS AUTHORIZATION" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "QUALIFICATION CHECKS FAILED - not deploying."; exit 1 }
    # GATE 36 - RECONCILIATION MAY RESTRICT, NEVER RELEASE. Comparing operational
    # records against a historical source is how compliance gets quietly undone:
    # a source row that says "Allow" overwrites a local opt-out, a column named
    # for a denial has cells that state permission, a shared household phone
    # merges two families, a timestamp column gets read as an action and counted
    # as engagement. Every one of those is asserted here, including a sweep of
    # all permission combinations requiring a denial on either side to win.
    # Six reverts.
    python scripts\probe_reconciliation.py 2>&1 | Select-String "FAIL|MISSED|checks,|reverts caught" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "RECONCILIATION CHECKS FAILED - not deploying."; exit 1 }
    # GATE 37 - AN IMPORT MAY RESTRICT, NEVER RELEASE. The platform imported
    # leads for its entire life with ONE permission column mapped - allow_calls -
    # and no mapping at all for email, bulk email or SMS, so every export's
    # opt-outs were discarded while the import reported success. This asserts a
    # denial in any of the four channels survives the import, that a later
    # permissive file cannot revive a person who opted out, that an unreadable
    # cell never becomes consent, that one tenant's denial cannot touch another
    # tenant, that "Last Activity Date" reaches the record instead of being
    # parked, and that the send gate actually reads the result. Nine reverts.
    python scripts\probe_import_compliance.py 2>&1 | Select-String "FAIL|MISSED|checks,|reverts caught|RESTRICT" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "IMPORT COMPLIANCE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_brand_owner_boundary.py 2>&1 | Select-String "REACHED|BROKEN|checks passed|WORKSPACE ONLY" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "BRAND OWNER BOUNDARY CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_platform_owner.py 2>&1 | Select-String "FAIL|checks passed|NEUTRAL" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PLATFORM OWNER CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_customer_provisioning.py 2>&1 | Select-String "FAIL|checks passed|END TO END" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CUSTOMER PROVISIONING CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_data_cleanup.py 2>&1 | Select-String "FAIL|checks passed|NOTHING ELSE" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DATA CLEANUP CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_cleanup_receipt.py 2>&1 | Select-String "FAIL|checks passed|OUTLIVES" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "CLEANUP RECEIPT CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_package_pricing.py 2>&1 | Select-String "FAIL|checks passed|EARNED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PACKAGE PRICING CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_demo_sites.py 2>&1 | Select-String "FAIL|checks passed|SANDBOXED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DEMO SITE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_tenant_isolation.py 2>&1 | Select-String "FAIL|checks passed|ISOLATED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "TENANT ISOLATION CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_owner_console.py 2>&1 | Select-String "FAIL|checks passed|HONEST" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "OWNER CONSOLE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\probe_delivery_receipts.py 2>&1 | Select-String "FAIL|checks passed|PERSISTED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "DELIVERY RECEIPT CHECKS FAILED - not deploying."; exit 1 }
    # Replays REAL HMAC-signed webhooks against the app and reads the rows back.
    # Production once returned 200 to an unsigned forgery; this is the gate that
    # keeps that from shipping again.
    python scripts\probe_twilio_webhook_auth.py 2>&1 | Select-String "FAIL|failure|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "TWILIO WEBHOOK AUTH CHECKS FAILED - not deploying."; exit 1 }
    # Retell voice: signed webhook replay, cross-org refusal, DNC sharing,
    # appointment correlation, and proof the old Twilio voice stack stays off.
    python scripts\probe_retell_voice.py 2>&1 | Select-String "FAIL|failure|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "RETELL VOICE CHECKS FAILED - not deploying."; exit 1 }
    python scripts\smoke_platform_frontend.py 2>&1 | Select-String "FAIL|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PLATFORM FRONTEND CHECKS FAILED - not deploying."; exit 1 }
    # A wrong build filter fails SILENTLY: the change just never reaches
    # production and nothing errors. Simulate every service against real commit
    # shapes instead of trusting the YAML by eye.
    python scripts\probe_render_build_filters.py 2>&1 | Select-String "FAIL|failure|PASSED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "RENDER BUILD FILTER CHECKS FAILED - not deploying."; exit 1 }
    # Proves requirements.txt alone still boots the app and every cron, with the
    # dev-only packages made genuinely unimportable.
    python scripts\probe_prod_deps_sufficient.py 2>&1 | Select-String "FAIL|failure|SUFFICIENT" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PRODUCTION DEPENDENCY CHECKS FAILED - not deploying."; exit 1 }
    # The completion sweep: the branded /book and /survey routes actually being
    # served, preview equalling the sent body, one missing field disabling only
    # its own channel, Twilio resolution visible before Send, the lead page
    # using the one proven voice path, and no infrastructure hostname or named
    # operator reaching a customer.
    python scripts\probe_sweep_public_and_compose.py 2>&1 | Select-String "FAIL|checks passed|SERVED" | ForEach-Object { "    $_" }
    if ($LASTEXITCODE -ne 0) { Write-Host "PUBLIC ROUTE / COMPOSE SWEEP CHECKS FAILED - not deploying."; exit 1 }
    Write-Host "  Smoke tests OK"
}

# -- Step 4: Build frontend ----------------------------------------------------
Write-Host "[4/6] Building frontend..."
Set-Location "$REPO\frontend"
& npm.cmd run build 2>&1 | Select-String "built in|error during build" | ForEach-Object { "    $_" }
if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD FAILED - fix errors then run deploy.ps1 again"
    Set-Location $REPO
    exit 1
}
Set-Location $REPO
Write-Host "  Build OK"

# -- Step 5: Commit dist + push, then VERIFY the push contains our work ---------
Write-Host "[5/6] Pushing to GitHub..."
git add -f frontend/dist
git add -A
$staged2 = git diff --cached --name-only
# THE MESSAGE GOES THROUGH A FILE, NOT THROUGH -m.
#
# PowerShell re-parses the arguments it hands to a native command, so a double
# quote INSIDE $Message splits it into several arguments. git then read the
# fragments as pathspecs and failed:
#
#   error: pathspec 'access' did not match any file(s) known to git
#
# Every deploy message here quotes something - an HTTP body, an error string, a
# status word - so this failed silently on every deploy that had anything worth
# saying, the commit never happened, and the work shipped under the step 1
# auto-save message instead. -F takes the bytes as they are.
$MSG_FILE = Join-Path $REPO ".deploy_commit_msg.txt"
# WriteAllText with an explicit UTF8Encoding($false) rather than Set-Content
# -Encoding UTF8: on Windows PowerShell 5.1 that switch writes a BOM, and the
# BOM ends up as the first character of the commit subject.
[System.IO.File]::WriteAllText($MSG_FILE, $Message,
    (New-Object System.Text.UTF8Encoding $false))
if ($staged2) {
    git commit -F $MSG_FILE | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "COMMIT FAILED - refusing to push a deploy with no record of what it is."
        exit 1
    }
} elseif ($WIP_COMMITTED) {
    # Nothing new to stage because step 1 already committed it all. Give that
    # auto-save commit its real message rather than shipping "wip". Amending is
    # safe here and needs no force-push: this commit was created moments ago in
    # step 1 and has not been pushed yet - the push is the next line.
    git commit --amend -F $MSG_FILE | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "REWORD FAILED - refusing to push a deploy with no record of what it is."
        exit 1
    }
    $SHIP_SHA = (git rev-parse HEAD).Trim()
    Write-Host "  Auto-save commit reworded with the deploy message"
}
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "PUSH FAILED - check git credentials. Your work is in $SHIP_SHA."
    exit 1
}

# The old script reported success after pushing a commit that contained none of
# the intended changes. Prove the shipped commit is actually an ancestor of what
# is now on origin/main before saying "deployed".
git fetch origin main 2>&1 | Out-Null
git merge-base --is-ancestor $SHIP_SHA origin/main
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "  !! ABORT: commit $SHIP_SHA is NOT on origin/main after push."
    Write-Host "  !! Your changes did NOT deploy. Recover with:  git cherry-pick $SHIP_SHA"
    exit 1
}
Write-Host "  Pushed and verified: $SHIP_SHA is on origin/main"

# -- Step 6: Trigger Render ----------------------------------------------------
Write-Host "[6/6] Triggering Render deploys..."
$h = @{ Authorization = "Bearer $RKEY"; Accept = "application/json" }

# Static site: does NOT rebuild on push, it serves the committed dist.
try {
    Invoke-RestMethod "https://api.render.com/v1/services/$FRONTEND_SVC/deploys" `
        -Method Post -Headers $h -ContentType "application/json" -Body "{}" | Out-Null
    Write-Host "  Frontend (static) deploy triggered"
} catch {
    Write-Host "  WARNING: frontend trigger failed - $($_.Exception.Message)"
}

# Backend auto-deploys on push to main; report its status so a silent failure
# there is visible instead of assumed.
try {
    $d = Invoke-RestMethod "https://api.render.com/v1/services/$BACKEND_SVC/deploys?limit=1" -Headers $h
    Write-Host ("  Backend deploy: {0} ({1})" -f $d[0].deploy.status, $d[0].deploy.commit.message)
} catch {
    Write-Host "  WARNING: could not read backend deploy status - $($_.Exception.Message)"
}

# Return to the branch we started on.
if ($branch -ne "main") { git checkout $branch | Out-Null }

Write-Host ""
Write-Host "=============================================="
Write-Host "  DEPLOYED  ->  app.evosyspro.live / app.bookaboost.live"
Write-Host "  Shipped commit: $SHIP_SHA"
Write-Host "  Render takes ~2 min. Backend cold start adds ~15s."
Write-Host "=============================================="
Write-Host ""
