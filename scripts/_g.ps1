param([string]$Script, [int]$Tail = 60)
Set-Location "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web"
$env:PYTHONIOENCODING = "utf-8"
python $Script 2>&1 | Select-Object -Last $Tail
Write-Host ("EXITCODE " + $LASTEXITCODE)
