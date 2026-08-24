# deploy.ps1 -- AdvisorFlow one-command deploy
# Run this from the repo root on your machine (any branch is fine).
# Flow: staging changes -> main -> GitHub -> Render -> live
#
# Usage:  .\deploy.ps1
#         .\deploy.ps1 -Message "custom commit message"

param(
    [string]$Message = ""
)

$REPO = Split-Path -Parent $MyInvocation.MyCommand.Path
$RKEY = "rnd_OwUxCBblW8GJOx9Sb4XqEo0o9S8A"
$SVC  = "srv-d8rslocvikkc738v7ocg"
$ts   = Get-Date -Format "yyyy-MM-dd HH:mm"
if (-not $Message) { $Message = "deploy: [$ts]" }

Write-Host ""
Write-Host "=============================================="
Write-Host "  AdvisorFlow Deploy"
Write-Host "  $Message"
Write-Host "=============================================="
Write-Host ""

Set-Location $REPO

# ── Step 1: Save any uncommitted work on current branch ──────────────────────
Write-Host "[1/5] Saving current work..."
$branch = git rev-parse --abbrev-ref HEAD
git add -A
$staged = git diff --cached --name-only
if ($staged) {
    git commit -m "wip: auto-save before deploy [$ts]"
    Write-Host "  Committed uncommitted changes on $branch"
} else {
    Write-Host "  Nothing to commit on $branch"
}

# ── Step 2: Sync main with GitHub then merge current branch ──────────────────
Write-Host "[2/5] Syncing main branch..."
git fetch origin main
git checkout main
git reset --hard origin/main
if ($branch -ne "main") {
    Write-Host "  Merging $branch into main..."
    git merge $branch --no-edit
    if ($LASTEXITCODE -ne 0) {
        Write-Host "MERGE CONFLICT - fix conflicts then run deploy.ps1 again"
        exit 1
    }
}

# ── Step 3: Build ─────────────────────────────────────────────────────────────
Write-Host "[3/5] Building frontend..."
Set-Location "$REPO\frontend"
npm run build
if ($LASTEXITCODE -ne 0) {
    Write-Host "BUILD FAILED - fix errors then run deploy.ps1 again"
    Set-Location $REPO
    git checkout $branch
    exit 1
}
Write-Host "  Build OK"

# ── Step 4: Commit dist + push to GitHub ─────────────────────────────────────
Write-Host "[4/5] Pushing to GitHub..."
Set-Location $REPO
git add -f frontend/dist
git commit -m $Message
git push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "PUSH FAILED - check git credentials"
    git checkout $branch
    exit 1
}
Write-Host "  Pushed to GitHub main"

# ── Step 5: Trigger Render deploy ─────────────────────────────────────────────
Write-Host "[5/5] Triggering Render deploy..."
$h = @{ Authorization="Bearer $RKEY"; Accept="application/json" }
Invoke-RestMethod "https://api.render.com/v1/services/$SVC/deploys" `
    -Method Post -Headers $h -ContentType "application/json" -Body "{}" | Out-Null
Write-Host "  Render deploy triggered!"

# ── Done: return to working branch ────────────────────────────────────────────
git checkout $branch

Write-Host ""
Write-Host "=============================================="
Write-Host "  DEPLOYED. Live at app.evosyspro.live"
Write-Host "  (~2 min for Render to finish)"
Write-Host "=============================================="
Write-Host ""
