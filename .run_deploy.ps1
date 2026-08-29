Set-Location -LiteralPath $PSScriptRoot
$msg = 'booking cleanup: delete the calendar artifact through the calendar that actually holds it (Graph ids were being sent to the Google client, which 404''d and reported ''already deleted'' while the event stayed on the advisor''s Outlook calendar)'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
