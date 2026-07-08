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

REM Disable git garbage collection so it never asks interactive y/n questions
git config gc.auto 0

REM Make sure we're actually inside a git repo before doing anything
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo ERROR: This folder is not a git repository.
    echo Make sure deploy.bat is sitting inside your advisorflow-web folder.
    echo.
    pause
    exit /b 1
)

echo Current changes:
echo -----------------------------------
git status --short > "%TEMP%\advisorflow_status.txt"
type "%TEMP%\advisorflow_status.txt"
echo -----------------------------------
echo.

REM If there's nothing to commit, stop here instead of doing a pointless push.
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
git -c gc.auto=0 commit -m "%COMMITMSG%"
if errorlevel 1 (
    echo.
    echo Nothing was committed - there may be nothing new to commit.
    pause
    exit /b 0
)

echo.
echo Pushing to GitHub...
git -c gc.auto=0 push

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
echo   Render will auto-deploy in 1-3 min
echo   or trigger Manual Deploy on Render.
echo ===================================
echo.
pause
