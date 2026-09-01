Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Remove-Item "scripts\_cleanup.ps1" -Force -ErrorAction SilentlyContinue
Write-Output "--- check-ignore on the new bundle ---"
git check-ignore -v "frontend/dist/assets/index-CNn61thC.js"
Write-Output "ignored_exit=$LASTEXITCODE (0 means IGNORED)"
Write-Output "--- what deploy.ps1 stages ---"
Select-String -Path "deploy.ps1" -Pattern "git add|git commit|git push" | ForEach-Object { $_.Line.Trim() }
