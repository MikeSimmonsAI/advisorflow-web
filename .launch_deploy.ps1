Set-Location -LiteralPath $PSScriptRoot
Remove-Item -Force -ErrorAction SilentlyContinue "$PSScriptRoot\.deploy.log"
Start-Process -FilePath "powershell.exe" `
  -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File","$PSScriptRoot\.run_deploy.ps1") `
  -RedirectStandardOutput "$PSScriptRoot\.deploy.log" `
  -RedirectStandardError  "$PSScriptRoot\.deploy.err" `
  -WindowStyle Hidden
Write-Output "LAUNCHED"
