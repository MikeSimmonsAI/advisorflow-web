Set-Location -LiteralPath $PSScriptRoot
$msg = (Get-Content -LiteralPath "$PSScriptRoot\.deploy_msg.txt" -Raw).Trim()
& "$PSScriptRoot\deploy.ps1" -Message $msg
Write-Output ("DEPLOY_EXIT=" + $LASTEXITCODE)
