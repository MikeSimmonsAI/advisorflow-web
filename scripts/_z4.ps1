Start-Sleep -Seconds 620
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Remove-Item "scripts\_go2.ps1" -Force -ErrorAction SilentlyContinue
Get-Content ".deploy.log" -Tail 6
Write-Output "--- HEAD subject (first 90) ---"
$s = git log -1 --pretty=%s
Write-Output $s.Substring(0, [Math]::Min(90, $s.Length))
Write-Output "--- status ---"
git status --porcelain
