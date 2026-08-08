@echo off
cd /d "C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web\frontend"
call npm run build
echo BUILD_EXIT_CODE=%ERRORLEVEL%
