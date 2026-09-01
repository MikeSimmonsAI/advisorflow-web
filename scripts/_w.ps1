Start-Sleep -Seconds 560
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Get-Content ".deploy.log" -Tail 10
Write-Output "--- log ---"
git log --oneline -1 | ForEach-Object { $_.Substring(0, [Math]::Min(95, $_.Length)) }
Write-Output "--- status ---"
git status --porcelain
