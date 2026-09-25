@echo off
rem  STOP_EVOSYS_REVIEW.bat - stops the local review API (8000) and web app (5173).
rem  Stops only what is LISTENING on those two ports. Touches no data.
echo Stopping the EvoSys local review ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach($p in 8000,5173){ $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; foreach($x in $c){ Write-Host ('  port ' + $p + ' -> stopping process ' + $x.OwningProcess); taskkill /T /F /PID $x.OwningProcess | Out-Null } ; if(-not $c){ Write-Host ('  port ' + $p + ' -> nothing running') } }"
echo Done. Your review data is untouched (advisorflow.db).
ping -n 4 127.0.0.1 >nul
