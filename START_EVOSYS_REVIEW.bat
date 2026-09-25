@echo off
rem ======================================================================
rem  START_EVOSYS_REVIEW.bat  -  the ONE way to open the local EvoSys review
rem ======================================================================
rem  Double-click it. It is safe to run again while things are running.
rem
rem   * uses the project's own Python (.venv) and the local SQLite database
rem     advisorflow.db in this folder - never production
rem   * starts NO background loops (SERVICE_ROLE=local_review), no background
rem     AI, and no real SMS / email (sandbox data only)
rem   * runs the review seed, which is idempotent and non-destructive: it
rem     adds anything missing and changes nothing that exists
rem   * starts the API (port 8000) and the web app (port 5173) only if they
rem     are not already running, then opens the browser
rem
rem  Stop everything with STOP_EVOSYS_REVIEW.bat
rem ======================================================================
setlocal
title EvoSys Local Review
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [X] %PY% not found. Run setup-local.bat once, then try again.
  pause
  exit /b 1
)

rem ---- review-safe settings, inherited by everything started below ----
set "DATABASE_URL=sqlite:///./advisorflow.db"
set "SERVICE_ROLE=local_review"
set "APP_ENV="
set "AI_BACKGROUND_AUTOMATION_ENABLED=false"
set "VITE_API_BASE_URL=http://localhost:8000"
set "FRONTEND_URL=http://localhost:5173"
set "PYTHONIOENCODING=utf-8"

echo.
echo   EVOSYS  LOCAL REVIEW  -  sandbox data, local database, nothing leaves this PC
echo   -------------------------------------------------------------------------
echo   Database : %~dp0advisorflow.db
echo.

echo   [1/4] Checking the review data (safe to repeat) ...
"%PY%" scripts\seed_evosys_review.py > "%TEMP%\evosys-review-seed.log" 2>&1
if errorlevel 1 (
  echo         [!] The review seed reported a problem. Details: %TEMP%\evosys-review-seed.log
) else (
  echo         review data ready
)

echo   [2/4] API on port 8000 ...
call :listening 8000
if errorlevel 1 (
  start "EvoSys Review - API (close to stop)" /min "%~dp0scripts\review\run_api.bat"
  echo         starting
) else (
  echo         already running
)

echo   [3/4] Web app on port 5173 ...
call :listening 5173
if errorlevel 1 (
  start "EvoSys Review - Web (close to stop)" /min "%~dp0scripts\review\run_web.bat"
  echo         starting
) else (
  echo         already running
)

echo   [4/4] Waiting for both to answer ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0;$i -lt 90;$i++){ try { $a=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8000/ping).StatusCode; $w=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://localhost:5173/).StatusCode; if($a -eq 200 -and $w -eq 200){$ok=$true; break} } catch {} ; Start-Sleep -Seconds 1 }; if($ok){exit 0}else{exit 1}"
if errorlevel 1 (
  echo         [!] Not answering yet. Logs: %TEMP%\evosys-review-api.log and %TEMP%\evosys-review-web.log
) else (
  echo         ready
)

echo.
echo   Open    http://localhost:5173
echo   Login   evosense.review@example.test
echo   Pass    EvoSense-Review-2026!
echo.
echo   Stop    STOP_EVOSYS_REVIEW.bat   (or close the two minimized windows)
echo.
start "" "http://localhost:5173/login"
endlocal
exit /b 0

:listening
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %1 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
exit /b %errorlevel%
