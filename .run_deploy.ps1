Set-Location -LiteralPath $PSScriptRoot
$msg = 'composer: act as the lead''s assigned advisor, not whoever opened the lead. The sender and the booking link were both read off the caller, so a link minted while the platform owner had a tenant''s lead open named the OWNER''s calendar - a family clicking it would have booked time with the platform instead of the funeral home.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
