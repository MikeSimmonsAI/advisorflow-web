Set-Location -LiteralPath $PSScriptRoot
$msg = 'booking links: the email sender and the resend button minted their link for whoever was sending, not for the lead''s advisor - the same defect already fixed in the composer. A link created while the platform owner had a tenant''s lead open pointed the family at the OWNER''s calendar. All three paths now share one acting_advisor helper, and a gate holds them together.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
