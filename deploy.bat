@echo off
REM ============================================================
REM AdvisorFlow - Quick Deploy Script
REM Runs git add, commit, and push in one step.
REM Usage: just double-click this file, or run it from CMD.
REM ============================================================

echo.
echo ===================================
echo   AdvisorFlow Deploy
echo ===================================
echo.

REM Make sure we're actually inside a git repo before doing anything
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a git repository.
    echo Make sure deploy.bat is sitting inside your advisorflow-web folder.
    echo.
    pause
    exit /b 1
)

REM Informational only, never blocks anything - OneDrive actively syncs
REM and can briefly lock files deep inside .git\objects, which is the
REM most likely cause of any "Deletion of directory .git/objects/XX
REM failed" prompts. If that ever happens again, the fix is: pause
REM OneDrive syncing for a minute, then re-run this script.
echo %CD% | findstr /I "OneDrive" >nul
if not errorlevel 1 (
    echo NOTE: This repo is inside a OneDrive-synced folder.
    echo If Git ever gets stuck asking to delete .git\objects\ folders,
    echo pause OneDrive syncing for a minute and try again - it's a
    echo OneDrive file lock, not a real Git problem.
    echo.
)

echo Current changes:
echo -----------------------------------
git status --short > "%TEMP%\advisorflow_status.txt"
type "%TEMP%\advisorflow_status.txt"
echo -----------------------------------
echo.

REM If there's nothing to commit, stop here instead of doing a pointless push.
REM IMPORTANT: this checks git status --short directly (not just git diff),
REM since git diff alone misses untracked new files entirely - a real bug
REM caught before shipping this script. Tonight's actual work repeatedly
REM involved brand new files (e.g. Compliance.jsx) that only show up as
REM untracked, not as a tracked diff, until after "git add" runs.
for %%A in ("%TEMP%\advisorflow_status.txt") do set STATUSSIZE=%%~zA
del "%TEMP%\advisorflow_status.txt"
if "%STATUSSIZE%"=="0" (
    echo No changes detected. Nothing to deploy.
    echo.
    pause
    exit /b 0
)

set /p COMMITMSG="Describe what changed (or press Enter for a default message): "
if "%COMMITMSG%"=="" set COMMITMSG=Update AdvisorFlow

echo.
echo Adding all changed files...
git add .

echo Committing...
git commit -m "%COMMITMSG%"
if errorlevel 1 (
    echo.
    echo Nothing was committed - there may be nothing new to commit.
    pause
    exit /b 0
)

echo.
echo Checking for updates from GitHub first...
git fetch origin >nul 2>&1
git status -uno | findstr /C:"Your branch is behind" >nul
if not errorlevel 1 (
    echo.
    echo WARNING: Your local branch is behind origin/main.
    echo Someone or something else pushed changes you don't have yet.
    echo Run "git pull" first, then re-run this script.
    echo.
    pause
    exit /b 1
)

echo.
echo Pushing to GitHub...
git push

if errorlevel 1 (
    echo.
    echo ERROR: git push failed. Scroll up to see the error message above.
    echo Common causes: no internet connection, or you need to pull first.
    pause
    exit /b 1
)

echo.
echo ===================================
echo   Done! Pushed to GitHub.
echo   Render will redeploy automatically
echo   in 1-3 minutes.
echo ===================================
echo.
pause
