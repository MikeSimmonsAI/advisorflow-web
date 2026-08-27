Set-Location $PSScriptRoot
& .\deploy.ps1 *>&1 | Tee-Object -FilePath deploy_run2.log | Out-Null
"DEPLOY SCRIPT FINISHED"
