Start-Sleep -Seconds 560
Set-Location "C:\Dev\advisorflow-web"
Get-Content ".deploy.log" -Tail 8
Write-Output "--- log ---"
git log --oneline -1 | ForEach-Object { $_.Substring(0, [Math]::Min(80, $_.Length)) }
Write-Output "--- status ---"
git status --porcelain
