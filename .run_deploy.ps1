Set-Location -LiteralPath $PSScriptRoot
$msg = 'billing: resolve the support address from the brand config instead of hard-coding one. An EvoSys Pro customer opening Billing was told to email support@bookaboost.live about their own invoice - a company they have no relationship with. theme.js already resolves the brand from the hostname and every other screen uses it; this one never did.'
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
