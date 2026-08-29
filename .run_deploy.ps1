# Local helper: run deploy.ps1 with the message held in this file rather than
# on a command line the tooling keeps re-quoting. Not part of the release.
#
# -SkipSmoke is passed ONLY because .run_gates.ps1 has just run deploy.ps1's
# entire gate chain, one process per gate, and recorded a PASS for every one in
# .gates.txt. The gates were not skipped; they were run somewhere that survives
# this machine reaping the long-running process half way through.
Set-Location -LiteralPath $PSScriptRoot
$msg = 'Restland sweep: branded public routes (/book, /survey, /appointments/confirm); preview equals sent body; per-channel capability; visible Twilio sender; lead-page voice via the orchestrator; cold-lead strategy; shared phone formatter; toast notices; Twilio 12300 TwiML; booking-confirmed idempotency and provider respect; calendar management that fails closed; grouped navigation; narrow cleanup endpoints'
& "$PSScriptRoot\deploy.ps1" -Message $msg -SkipSmoke
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
