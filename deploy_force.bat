@echo off
REM ============================================================
REM deploy_force.bat - push an EMPTY commit to make Render redeploy.
REM
REM WHAT THIS IS FOR: forcing a rebuild when the code is already correct and
REM the deployed instance is not - a stuck build, a cache you want cleared, a
REM service that missed a webhook. It is a redeploy button, not a way to ship
REM changes.
REM
REM WHY IT NO LONGER STAGES ANYTHING
REM
REM It used to run `git add -A` and commit whatever it found, under a message
REM typed at the prompt. That made "force a redeploy" and "ship everything in
REM my working tree" the same keystroke - so a force-redeploy could carry
REM another thread's half-finished work, a scratch database, or a credential
REM into production, and nobody would look twice because the intent was
REM "just redeploy".
REM
REM Shipping changes is deploy.bat (or deploy.ps1). This one commits NOTHING
REM and refuses to run if the working tree is dirty, so the redeploy is
REM exactly the code that was already on main.
REM ============================================================

setlocal

git config gc.auto 0

echo.
echo ===================================
echo   AdvisorFlow Force Redeploy
echo ===================================
echo.

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: not inside a git repository.
    pause
    exit /b 1
)

REM A dirty tree means somebody has work in progress. Redeploying from here is
REM fine; silently including it is not - so say so and stop.
git diff --quiet
set DIRTY=%errorlevel%
git diff --cached --quiet
set STAGED=%errorlevel%
if not "%DIRTY%"=="0" goto :dirty
if not "%STAGED%"=="0" goto :dirty
goto :clean

:dirty
echo You have uncommitted changes:
echo -----------------------------------
git status --short
echo -----------------------------------
echo.
echo This script does not ship changes - it only forces a rebuild of what is
echo already on main. Commit or stash your work first, or use deploy.bat if
echo you meant to deploy it.
echo.
pause
exit /b 1

:clean
set /p REASON="Why are you forcing a redeploy? "
if "%REASON%"=="" set REASON=manual redeploy

echo.
echo Creating an empty commit...
git -c gc.auto=0 commit --allow-empty -m "redeploy: %REASON%"
if errorlevel 1 (
    echo ERROR: could not create the commit.
    pause
    exit /b 1
)

echo Pushing to main...
git -c gc.auto=0 push origin main
if errorlevel 1 (
    echo PUSH FAILED - nothing was deployed.
    pause
    exit /b 1
)

echo.
echo ===================================
echo   Pushed. Render auto-deploys both
echo   services from main. Watch it at
echo   dashboard.render.com
echo ===================================
echo.
pause
endlocal
