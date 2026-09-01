Start-Sleep -Seconds 540
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Remove-Item "scripts\_go.ps1" -Force -ErrorAction SilentlyContinue
Get-Content ".deploy.log" -Tail 8
Write-Output "--- err tail ---"
Get-Content ".deploy.err" -Tail 5
Write-Output "--- log ---"
git log --oneline -3
Write-Output "--- status ---"
git status --porcelain
