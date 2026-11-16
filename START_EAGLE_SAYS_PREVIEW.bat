@echo off
rem ======================================================================
rem  START_EAGLE_SAYS_PREVIEW.bat - local preview of the EvoSys Pro mobile app
rem  (Eagle Says Mobile App Prototype, draft PR #628). Double-click it.
rem
rem   * Works from anywhere. If this folder is not the prototype branch, it
rem     makes a SEPARATE copy of the branch at C:\Dev\lanes\eagle-says-mobile
rem     (a git worktree). Your own checkout and its changes are not touched.
rem   * Uses its own local database (team_app_preview.db) with made-up people
rem     only - never production, no texts, no emails, no background jobs.
rem   * Uses ports 8010 (API) and 5174 (web) so it does not collide with
rem     START_EVOSYS_REVIEW.bat (8000 / 5173).
rem
rem  Stop it:  START_EAGLE_SAYS_PREVIEW.bat stop   (or close the two windows)
rem ======================================================================
setlocal EnableExtensions
title EvoSys Pro mobile app - local preview
set "BRANCH=feature/eagle-says-mobile-prototype"
set "LANE=C:\Dev\lanes\eagle-says-mobile"
set "API_PORT=8010"
set "WEB_PORT=5174"

if /i "%~1"=="stop" goto :stop

rem ---- 1. find the code ---------------------------------------------------
set "HERE=%~dp0"
if "%HERE:~-1%"=="\" set "HERE=%HERE:~0,-1%"
if exist "%HERE%\app\routers\team_app_router.py" (
  set "APPDIR=%HERE%"
  goto :have_code
)
set "MAIN="
if exist "%HERE%\.git" set "MAIN=%HERE%"
if not defined MAIN if exist "C:\Dev\advisorflow-web\.git" set "MAIN=C:\Dev\advisorflow-web"
if not defined MAIN (
  echo [X] Could not find the advisorflow-web folder.
  echo     Put this file inside your advisorflow-web folder and run it again.
  pause & exit /b 1
)
echo   [1/5] Getting the prototype branch from GitHub ...
git -C "%MAIN%" fetch origin %BRANCH% || (echo [X] git fetch failed - check internet / GitHub sign-in. & pause & exit /b 1)
if exist "%LANE%\.git" (
  git -C "%LANE%" checkout --detach origin/%BRANCH% >nul 2>&1 || git -C "%LANE%" checkout --detach FETCH_HEAD
) else (
  if not exist "C:\Dev\lanes" mkdir "C:\Dev\lanes"
  git -C "%MAIN%" worktree add --detach "%LANE%" origin/%BRANCH% || (echo [X] Could not create %LANE% & pause & exit /b 1)
)
set "APPDIR=%LANE%"
goto :have_code_main

:have_code
set "MAIN=%APPDIR%"
echo   [1/5] Using the code in this folder.

:have_code_main
rem ---- 2. Python ----------------------------------------------------------
set "PY=python"
if exist "%APPDIR%\.venv\Scripts\python.exe" set "PY=%APPDIR%\.venv\Scripts\python.exe"
if "%PY%"=="python" if exist "%MAIN%\.venv\Scripts\python.exe" set "PY=%MAIN%\.venv\Scripts\python.exe"
"%PY%" -c "import fastapi, sqlalchemy" >nul 2>&1
if errorlevel 1 (
  echo   [2/5] Installing Python packages ^(first time only^) ...
  "%PY%" -m pip install -r "%APPDIR%\requirements.txt" || (echo [X] pip install failed. & pause & exit /b 1)
) else (
  echo   [2/5] Python ready.
)

rem ---- 3. Web packages -----------------------------------------------------
if not exist "%APPDIR%\frontend\node_modules\vite" (
  echo   [3/5] Installing web packages ^(first time only, a few minutes^) ...
  pushd "%APPDIR%\frontend"
  call npm ci --include=dev || (popd & echo [X] npm ci failed. & pause & exit /b 1)
  popd
) else (
  echo   [3/5] Web packages ready.
)

rem ---- 4. preview data (made-up people only) ---------------------------------
cd /d "%APPDIR%"
set "DATABASE_URL=sqlite:///./team_app_preview.db"
set "JWT_SECRET=local-preview-secret-not-for-production-0123456789"
set "SERVICE_ROLE=local_review"
set "APP_ENV="
set "AI_BACKGROUND_AUTOMATION_ENABLED=false"
set "FRONTEND_URL=http://localhost:%WEB_PORT%"
set "VITE_API_BASE_URL=http://localhost:%API_PORT%"
set "PYTHONIOENCODING=utf-8"
echo   [4/5] Preparing preview people ...
"%PY%" scripts\seed_team_app_preview.py || (echo [X] Seeding failed. & pause & exit /b 1)

rem ---- 5. start API and web app --------------------------------------------
echo   [5/5] Starting the API (port %API_PORT%) and web app (port %WEB_PORT%) ...
call :listening %API_PORT%
if errorlevel 1 start "EvoSys app preview - API (close to stop)" /min cmd /c ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT% > "%TEMP%\eagle-says-api.log" 2>&1"
call :listening %WEB_PORT%
if errorlevel 1 start "EvoSys app preview - Web (close to stop)" /min cmd /c "cd /d "%APPDIR%\frontend" && npx vite --port %WEB_PORT% --strictPort --host localhost > "%TEMP%\eagle-says-web.log" 2>&1"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; for($i=0;$i -lt 120;$i++){ try { $a=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:%API_PORT%/health).StatusCode; $w=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://localhost:%WEB_PORT%/).StatusCode; if($a -eq 200 -and $w -eq 200){$ok=$true; break} } catch {} ; Start-Sleep -Seconds 1 }; if($ok){exit 0}else{exit 1}"
if errorlevel 1 (
  echo   [!] Not answering yet. Logs: %TEMP%\eagle-says-api.log and %TEMP%\eagle-says-web.log
  pause & exit /b 1
)

echo.
echo   READY  -^>  http://localhost:%WEB_PORT%/app
echo.
echo   Sign in with any of these (password for all: Preview123!)
echo     rep1@preview.test       sales rep  - Chat, Activity
echo     rep2@preview.test       sales rep  - use a second browser window to chat with rep1
echo     platform@preview.test   SCI + workspaces, announcements, chat rooms
echo     partner@preview.test    SCI + Roofing, Chat only until granted
echo     chatmgr@preview.test    manages chat rooms only
echo     outsider@preview.test   no Chat; Activity shows only its own workspace
echo.
echo   Phone-size view in Edge/Chrome: press F12, then Ctrl+Shift+M.
echo   Stop: run  START_EAGLE_SAYS_PREVIEW.bat stop
echo.
start "" "http://localhost:%WEB_PORT%/app/login"
endlocal
exit /b 0

:stop
echo Stopping the EvoSys app preview (ports %API_PORT% and %WEB_PORT%) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach($p in %API_PORT%,%WEB_PORT%){ $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; foreach($x in $c){ taskkill /T /F /PID $x.OwningProcess | Out-Null; Write-Host ('  port ' + $p + ' stopped') }; if(-not $c){ Write-Host ('  port ' + $p + ' - nothing running') } }"
endlocal
exit /b 0

:listening
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %1 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
exit /b %errorlevel%
