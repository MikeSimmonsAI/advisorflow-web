Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
Remove-Item "scripts\_z2.ps1","scripts\_z3.ps1" -Force -ErrorAction SilentlyContinue
& ".\.launch_deploy.ps1"
