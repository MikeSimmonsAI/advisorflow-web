<#
 SCI desktop verification entrypoint (Windows PowerShell). Local and synthetic only:
 in-memory SQLite, no live credentials, no provider calls, nothing is sent.
 Usage:  powershell -ExecutionPolicy Bypass -File scripts\sci_desktop_verify.ps1 [-SkipFrontend]
 Exit code: 0 = every step passed; otherwise the number of failed or blocked steps.
 Evidence: handoff\SCI_DESKTOP_EVIDENCE.md (commands, exit codes, redacted output tails).
 A step whose prerequisite failed is marked BLOCKED (counted as a failure), never passed.
 NOT YET EXECUTED on a desktop; only syntax-reviewed.
#>
param([switch]$SkipFrontend)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- Isolated, no-send environment (set BEFORE any python process imports the app) ---
# Every provider/credential/database variable that could point at a real service is
# removed, then local placeholders are set. Children inherit this process environment.
$scrub = @("TWILIO_ACCOUNT_SID","TWILIO_AUTH_TOKEN","TWILIO_PHONE_NUMBER","TWILIO_MESSAGING_SERVICE_SID",
  "SENDGRID_API_KEY","OPENAI_API_KEY","ANTHROPIC_API_KEY","STRIPE_SECRET_KEY","STRIPE_WEBHOOK_SECRET",
  "GOOGLE_CLIENT_ID","GOOGLE_CLIENT_SECRET","MS_CLIENT_ID","MS_CLIENT_SECRET","MS_TENANT_ID",
  "SMTP_HOST","SMTP_USER","SMTP_PASSWORD","REDIS_URL","SENTRY_DSN","ENCRYPTION_KEY")
foreach ($n in $scrub) { Remove-Item ("Env:" + $n) -ErrorAction SilentlyContinue }
$env:JWT_SECRET = "local-desktop-verify-secret-32-chars-min!!"
$env:DATABASE_URL = "sqlite:///:memory:"   # overrides any shared/production URL inherited from the shell
$env:PYTHONDONTWRITEBYTECODE = "1"

$venv = Join-Path $root ".venv-sci-verify"
$py = Join-Path $venv "Scripts\python.exe"
$ev = @("# SCI desktop verification evidence", "", "Run: $(Get-Date -Format s)",
  "Commit: $(git rev-parse HEAD)", "Branch: $(git rev-parse --abbrev-ref HEAD)",
  "DATABASE_URL: $($env:DATABASE_URL) (isolated)", "")
$script:fails = 0
$script:blocked = @{}   # step key -> reason

function Redact([string]$text) {
  # Never write credential-looking material to the evidence file.
  $t = $text -replace '(?i)(secret|token|password|api[_-]?key|authorization)(["'' :=]+)[^\s"'',]+', '$1$2[REDACTED]'
  $t = $t -replace '(?i)bearer\s+[A-Za-z0-9._\-]+', 'Bearer [REDACTED]'
  return $t -replace '(?i)(postgres(ql)?|mysql)://\S+', '[REDACTED-DB-URL]'
}

function Step([string]$key, [string]$name, [string[]]$needs, [scriptblock]$cmd) {
  Write-Host "== $key $name"
  foreach ($d in $needs) {
    if ($script:blocked.ContainsKey($d)) {
      $script:fails++; $script:blocked[$key] = "depends on $d"
      Write-Host "BLOCKED (needs step $d)" -ForegroundColor Yellow
      $script:ev += @("## $key $name", "result: BLOCKED (prerequisite step $d did not pass; not run, not passed)", "")
      return
    }
  }
  $global:LASTEXITCODE = $null
  $out = & $cmd 2>&1 | Out-String
  $code = $global:LASTEXITCODE
  if ($null -eq $code) { $code = if ($?) { 0 } else { 1 } }   # PowerShell-only failure with no native exit code
  if ($code -ne 0) {
    $script:fails++; $script:blocked[$key] = "failed ($code)"
    Write-Host "FAILED ($code)" -ForegroundColor Red
  } else { Write-Host "ok" -ForegroundColor Green }
  $tail = Redact((($out -split "`r?`n") | Select-Object -Last 25) -join "`n")
  $script:ev += @("## $key $name", "exit: $code", '```', $tail, '```', "")
}

# 0. prerequisites, stated explicitly
$sysPy = Get-Command python -ErrorAction SilentlyContinue
Step "0a" "python on PATH" @() { if (-not $sysPy) { Write-Output "python not found on PATH"; $global:LASTEXITCODE = 1 } else { & $sysPy.Source --version } }
Step "0b" "create isolated venv (.venv-sci-verify)" @("0a") { if (Test-Path $py) { Write-Output "venv exists" ; $global:LASTEXITCODE = 0 } else { & $sysPy.Source -m venv $venv } }
Step "1" "baseline stdlib harness" @("0b") { & $py -I scripts/sci_readiness_harness.py }
Step "2" "pip install -r requirements-dev.txt (venv only)" @("0b") { & $py -m pip install -q -r requirements-dev.txt }
Step "3" "synthetic flows (login, suppression isolation, booking cancel, inbound routing, booking confirm)" @("2") { & $py -m pytest tests/test_sci_desktop_synthetic_flows.py tests/test_sci_inbound_booking_flows.py tests/test_public_booking_api.py tests/test_public_booking_replay.py -q }
Step "4" "SCI suites" @("2") { & $py -m pytest tests/test_sci_campus_lock.py tests/test_sci_platinum.py tests/test_sci_readiness_console.py tests/test_sci_readiness_matrix.py tests/test_sci_regional_pools.py tests/test_sci_staging_bootstrap.py tests/test_sci_webhook_simulation.py -q }
Step "5" "intake / calendar / outcomes / compliance / workspace authority" @("2") { & $py -m pytest tests/test_site_intake.py tests/test_intake_capture.py tests/test_intake_identity_consent.py tests/test_universal_intake_api.py tests/test_calendar_router.py tests/test_calendar_scheduling_attack.py tests/test_outcomes_router.py tests/test_compliance_router.py tests/test_inbound_mailbox.py tests/test_reply_triage_actions.py tests/test_reply_classification_service.py tests/test_pipeline_appointments_cap.py tests/test_workspace_authority_and_features.py -q }
Step "6" "full backend suite" @("2") { & $py -m pytest -q }
if (-not $SkipFrontend) {
  $npm = Get-Command npm -ErrorAction SilentlyContinue
  Step "7" "frontend npm ci" @() { if (-not $npm) { Write-Output "npm not found on PATH"; $global:LASTEXITCODE = 1 } else { npm --prefix frontend ci } }
  Step "8" "frontend build" @("7") { npm --prefix frontend run build }
}
$ev += "Failed or blocked steps: $script:fails"
if ($script:blocked.Count) { $ev += ($script:blocked.GetEnumerator() | Sort-Object Name | ForEach-Object { "- step $($_.Name): $($_.Value)" }) }
$ev | Set-Content -Encoding UTF8 handoff\SCI_DESKTOP_EVIDENCE.md
Write-Host "Failed or blocked steps: $script:fails; evidence in handoff\SCI_DESKTOP_EVIDENCE.md"
exit $script:fails
