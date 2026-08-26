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
if ($staged) {
    git commit -m "wip: auto-save before deploy [$ts]" | Out-Null
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
if ($staged2) { git commit -m $Message | Out-Null }
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
