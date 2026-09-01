Start-Sleep -Seconds 320
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Get-Content ".deploy.log" -Tail 8
Write-Output "--- log ---"
git log --oneline -3
Write-Output "--- HEAD subject length ---"
(git log -1 --pretty=%s).Length
Write-Output "--- status ---"
git status --porcelain
