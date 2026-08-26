@echo off
REM Wrapper so a deploy message containing parentheses survives being passed
REM through a shell. Edit MSG below, run this, then read %TEMP%\dep2.txt.
cd /d "%~dp0.."
set "MSG=feat/integrations: verify a credential by prefix, without handling the key"
powershell -NoProfile -ExecutionPolicy Bypass -File deploy.ps1 -Message "%MSG%" > "%TEMP%\dep2.txt" 2>&1
echo DEPLOY_EXIT=%ERRORLEVEL%
