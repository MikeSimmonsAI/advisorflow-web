Set-Location $PSScriptRoot
& .\deploy.ps1 *>&1 | Tee-Object -FilePath deploy_run4.log | Out-Null
"DEPLOY SCRIPT FINISHED"
