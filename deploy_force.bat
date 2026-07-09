@echo off
git config gc.auto 0
git config --global gc.auto 0

echo.
echo ===================================
echo   BookaBoost Force Deploy
echo ===================================
echo.

set /p COMMITMSG="Describe what changed (or press Enter): "
if "%COMMITMSG%"=="" set COMMITMSG=force update %date% %time%

echo Adding all files...
git add -A

echo Committing...
git -c gc.auto=0 commit --allow-empty -m "%COMMITMSG%"

echo Pushing...
git -c gc.auto=0 push origin main

if errorlevel 1 (
    git -c gc.auto=0 push origin master
)

echo.
echo ===================================
echo   Done. Go to Render and click
echo   Manual Deploy on both services.
echo ===================================
echo.
pause
