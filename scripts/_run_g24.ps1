Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
python scripts\probe_brand_owner_boundary.py 2>&1 | Select-Object -Last 75
Write-Host ("EXITCODE " + $LASTEXITCODE)
