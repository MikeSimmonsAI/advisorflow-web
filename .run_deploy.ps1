Set-Location -LiteralPath $PSScriptRoot
$msg = 'twilio: the platform owner is never a tenant''s sender. A god_admin inside a customer org via X-Org-Override carries that org''s id, so Twilio resolution read the OWNER''s personal credentials and reported the platform number as the customer''s sender - ready and green - and a Send would have texted a family from a number the funeral home does not own. Screen and send now skip it together and fall through to the organization''s own sender, or refuse.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
