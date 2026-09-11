# _dep.ps1 - run deploy.ps1 with the noisy PowerShell error chrome filtered out.
#
# It used to open with `Set-Location "C:\Dev\advisorflow-web"`, so running it
# from any other worktree deployed a DIFFERENT tree than the one you were
# standing in - the same hazard git_push.bat carried, and the more dangerous
# half of it, because this one pushes to production. It now resolves the repo
# from this script's own location, so it deploys the worktree it lives in.
Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONIOENCODING = "utf-8"
& .\deploy.ps1 2>&1 | Select-String -NotMatch "warning: in the working copy|CategoryInfo|FullyQualifiedErrorId|^\s*\+" | ForEach-Object { $_ }
Write-Host ("EXITCODE " + $LASTEXITCODE)
