@echo off
REM ============================================================
REM BookaBoost Deploy Script v2.1
REM Pushes to GitHub AND triggers Render backend deploy automatically
REM Usage: double-click or run from CMD
REM ============================================================

echo.
echo ===================================
echo   BookaBoost Deploy v2.1
echo ===================================
echo.

REM Make sure we're inside a git repo
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a git repository.
    echo Make sure deploy.bat is inside your advisorflow-web folder.
    echo.
    pause
    exit /b 1
)

echo Current changes:
echo -----------------------------------
git status --short > "%TEMP%\bookaboost_status.txt"
type "%TEMP%\bookaboost_status.txt"
echo -----------------------------------
echo.

for %%A in ("%TEMP%\bookaboost_status.txt") do set STATUSSIZE=%%~zA
del "%TEMP%\bookaboost_status.txt"
if "%STATUSSIZE%"=="0" (
    echo No changes detected. Nothing to deploy.
    echo.
    pause
    exit /b 0
)

set /p COMMITMSG="Describe what changed (or press Enter for default): "
if "%COMMITMSG%"=="" set COMMITMSG=Update BookaBoost

echo.
echo Adding all changed files...
git add .

echo Committing...
git commit -m "%COMMITMSG%"
if errorlevel 1 (
    echo.
    echo Nothing was committed.
    pause
    exit /b 0
)

echo.
echo Pushing to GitHub...
git push

if errorlevel 1 (
    echo.
    echo ERROR: git push failed. Check your internet connection.
    pause
    exit /b 1
)

echo.
echo ===================================
echo   Pushed to GitHub successfully.
echo   Triggering Render backend deploy...
echo ===================================
echo.

REM Trigger backend deploy via Render API
curl -s -X POST ^
  "https://api.render.com/v1/services/srv-d8rsm2kvikkc738v8470/deploys" ^
  -H "Authorization: Bearer rnd_OwUxCBblW8GJOx9Sb4XqEo0o9S8A" ^
  -H "Content-Type: application/json" ^
  -d "{\"clearCache\": false}" > "%TEMP%\render_response.txt"

type "%TEMP%\render_response.txt" | findstr /i "id" >nul
if errorlevel 1 (
    echo WARNING: Render deploy trigger may have failed.
    echo Check Render dashboard manually if needed.
) else (
    echo Backend deploy triggered on Render successfully.
)
del "%TEMP%\render_response.txt" >nul 2>&1

echo.
echo ===================================
echo   All done!
echo   GitHub: updated
echo   Render backend: deploying now
echo   Render frontend: auto-deploys
echo   Check status at dashboard.render.com
echo ===================================
echo.
pause
