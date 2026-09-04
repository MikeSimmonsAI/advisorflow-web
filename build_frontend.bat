@echo off
cd /d "C:\Dev\advisorflow-web\frontend"
call npm run build
echo BUILD_EXIT_CODE=%ERRORLEVEL%
