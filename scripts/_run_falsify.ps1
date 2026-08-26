Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
python scripts\_falsify_g23.py 2>&1 | Select-Object -Last 70
Write-Host ("EXITCODE " + $LASTEXITCODE)
