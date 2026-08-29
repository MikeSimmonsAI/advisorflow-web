# Local helper: run deploy.ps1 with the message held in this file rather than
# on a command line the tooling keeps re-quoting. Output goes to whatever the
# caller redirects; this script writes no log of its own, so the caller's
# redirect is the single owner of the file. Not part of the release.
Set-Location -LiteralPath $PSScriptRoot
$msg = 'Sweep: branded public routes (/book, /survey, /appointments/confirm); preview equals sent body; per-channel capability; visible Twilio sender; lead-page voice via orchestrator; cold-lead strategy; shared phone formatter; toast notices; Twilio 12300 TwiML; booking-confirmed idempotency and provider respect'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
