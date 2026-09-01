Set-Location -LiteralPath $PSScriptRoot
Remove-Item -Force -ErrorAction SilentlyContinue "$PSScriptRoot\.deploy.log"
$script = Join-Path $PSScriptRoot '.run_deploy.ps1'
Start-Process -FilePath "powershell.exe" `
  -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$script`"") `
  -RedirectStandardOutput "$PSScriptRoot\.deploy.log" `
  -RedirectStandardError  "$PSScriptRoot\.deploy.err" `
  -WindowStyle Hidden
Write-Output "LAUNCHED"
