Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
& .\deploy.ps1 2>&1 | Tee-Object -FilePath "$env:TEMP\af_deploy.log"
Write-Host ("EXITCODE " + $LASTEXITCODE)
