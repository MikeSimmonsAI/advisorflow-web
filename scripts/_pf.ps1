param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
python scripts\perf_bench.py @Rest 2>&1 | Select-String -Pattern "ROUTE|^---|^===|/sales/|/god/|/admin/|saved|regress|SAME|CHANGED|Error|Traceback|line [0-9]"
Write-Host ("EXITCODE " + $LASTEXITCODE)
