param([string]$Args1 = "--scale 25")
Set-Location "C:\Dev\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
$a = $Args1 -split ' '
python scripts\perf_bench.py @a 2>&1 | Select-String -NotMatch "DeprecationWarning|utcnow|CategoryInfo|FullyQualifiedErrorId|^\s*\+|At line:"
Write-Host ("EXITCODE " + $LASTEXITCODE)
