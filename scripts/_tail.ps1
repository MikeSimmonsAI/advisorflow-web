Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Remove-Item "scripts\_ck.ps1" -Force -ErrorAction SilentlyContinue
if (Test-Path ".deploy.log") { Get-Content ".deploy.log" -Tail 45 }
Write-Output "--- err ---"
if (Test-Path ".deploy.err") { Get-Content ".deploy.err" -Tail 12 }
