param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Rest)
Set-Location "C:\Dev\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
python scripts\run_gates.py @Rest 2>&1 | Select-Object -Last 40
Write-Host ("EXITCODE " + $LASTEXITCODE)
