Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
& .\deploy.ps1 2>&1 | Select-String -NotMatch "warning: in the working copy|CategoryInfo|FullyQualifiedErrorId|^\s*\+" | ForEach-Object { $_ }
Write-Host ("EXITCODE " + $LASTEXITCODE)
