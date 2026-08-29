Set-Location -LiteralPath $PSScriptRoot
$msg = 'inbound SMS: resolve the receiving number against the ORGANIZATION''s shared sender as well as an advisor''s own. It checked users.twilio_phone_number only, so a tenant on a shared toll-free or 10DLC number - the normal configuration - had every inbound reply silently dropped on the unrecognized-number branch, STOP replies included, which is a compliance failure and not a missing feature. Advisor number first, then the org''s, then drop; the lead lookup stays scoped to the owning organization and the reply is attributed to the lead''s advisor rather than whoever owns the number.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
