Set-Location -LiteralPath $PSScriptRoot
Remove-Item "$PSScriptRoot\.deploy.log" -ErrorAction SilentlyContinue
Remove-Item "$PSScriptRoot\.p.txt" -ErrorAction SilentlyContinue
$proc = Start-Process powershell.exe `
  -ArgumentList '-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File',"$PSScriptRoot\.run_deploy.ps1" `
  -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru
Write-Output ("DEPLOY_PID=" + $proc.Id)
