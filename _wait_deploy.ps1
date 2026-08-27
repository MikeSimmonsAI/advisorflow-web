$p = Join-Path $PSScriptRoot 'deploy_run.log'
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 8
  $raw = Get-Content $p -Raw -ErrorAction SilentlyContinue
  if ($raw -match 'DEPLOY COMPLETE|not deploying|error during build|\[6/6\]') { break }
}
Get-Content $p -Tail 45
