Set-Location -LiteralPath $PSScriptRoot
$msg = 'org settings: a placeholder is not a value. The phone example on the shared-SMS card was the PLATFORM''s own live Twilio number and the caller-id example was the platform''s brand, so a customer org admin opening an unconfigured org read both as their own configuration - the fields were empty and only the disabled Save button hinted otherwise. Neutral examples now, plus the Email Sender helper no longer offers the platform''s own address as the model for a customer''s domain.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
