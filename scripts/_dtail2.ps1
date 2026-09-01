Start-Sleep -Seconds 420
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Get-Content ".deploy.log" -Tail 12
Write-Output "--- log ---"
git log --oneline -1 | ForEach-Object { $_.Substring(0, [Math]::Min(90, $_.Length)) }
Write-Output "--- status ---"
git status --porcelain
