Start-Sleep -Seconds 240
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Get-Content ".deploy.log" -Tail 6
Write-Output "--- HEAD subject (first 100) ---"
$s = git log -1 --pretty=%s
Write-Output $s.Substring(0, [Math]::Min(100, $s.Length))
Write-Output "--- status ---"
git status --porcelain
