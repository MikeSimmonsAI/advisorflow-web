Start-Sleep -Seconds 570
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Get-Content ".deploy.log" -Tail 8
Write-Output "--- log ---"
git log --oneline -1 | ForEach-Object { $_.Substring(0, [Math]::Min(80, $_.Length)) }
Write-Output "--- status ---"
git status --porcelain
