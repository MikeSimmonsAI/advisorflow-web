<#
 SCI desktop verification entrypoint (Windows PowerShell). Local and synthetic only:
 in-memory SQLite, no live credentials, no provider calls, nothing is sent.
 Usage:  powershell -ExecutionPolicy Bypass -File scripts\sci_desktop_verify.ps1 [-SkipFrontend]
 Exit code: 0 = all steps passed; otherwise the number of failed steps.
 Evidence: handoff\SCI_DESKTOP_EVIDENCE.md (commands, exit codes, output tails).
#>
param([switch]$SkipFrontend)
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
# Safe no-send configuration. Placeholder values for local tests only.
$env:JWT_SECRET = "local-desktop-verify-secret-32-chars-min!!"
$env:DATABASE_URL = "sqlite:///:memory:"
foreach ($n in @("TWILIO_ACCOUNT_SID","TWILIO_AUTH_TOKEN","SENDGRID_API_KEY","OPENAI_API_KEY","ANTHROPIC_API_KEY")) {
  Remove-Item ("Env:" + $n) -ErrorAction SilentlyContinue
}
$ev = @("# SCI desktop verification evidence", "", "Run: $(Get-Date -Format s)", "Commit: $(git rev-parse HEAD)", "Branch: $(git rev-parse --abbrev-ref HEAD)", "")
$fails = 0
function Step($name, [scriptblock]$cmd) {
  Write-Host "== $name"
  $out = & $cmd 2>&1 | Out-String
  $code = $LASTEXITCODE
  if ($code -ne 0) { $script:fails++; Write-Host "FAILED ($code)" -ForegroundColor Red } else { Write-Host "ok" -ForegroundColor Green }
  $tail = ($out -split "`n" | Select-Object -Last 15) -join "`n"
  $script:ev += @("## $name", "exit: $code", '```', $tail, '```', "")
}
Step "1 baseline stdlib harness" { python -I scripts/sci_readiness_harness.py }
Step "2 pip install -r requirements-dev.txt" { python -m pip install -q -r requirements-dev.txt }
Step "3 synthetic flows (login, workspace isolation, suppression)" { python -m pytest tests/test_sci_desktop_synthetic_flows.py -q }
Step "4 SCI suites" { python -m pytest tests/test_sci_campus_lock.py tests/test_sci_platinum.py tests/test_sci_readiness_console.py tests/test_sci_readiness_matrix.py tests/test_sci_regional_pools.py tests/test_sci_staging_bootstrap.py tests/test_sci_webhook_simulation.py -q }
Step "5 intake / inbound email / reply / appointments / workspace authority" { python -m pytest tests/test_universal_intake_api.py tests/test_inbound_mailbox.py tests/test_reply_triage_actions.py tests/test_reply_classification_service.py tests/test_pipeline_appointments_cap.py tests/test_workspace_authority_and_features.py -q }
Step "6 full backend suite" { python -m pytest -q }
if (-not $SkipFrontend) {
  Step "7 frontend npm ci" { npm --prefix frontend ci }
  Step "8 frontend build" { npm --prefix frontend run build }
}
$ev += "Failed steps: $fails"
$ev | Set-Content -Encoding UTF8 handoff\SCI_DESKTOP_EVIDENCE.md
Write-Host "Failed steps: $fails; evidence in handoff\SCI_DESKTOP_EVIDENCE.md"
exit $fails
