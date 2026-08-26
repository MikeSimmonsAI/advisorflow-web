@echo off
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python -X utf8 scripts\smoke_tenant_bridge.py 1> "%TEMP%\tenant_smoke.txt" 2> "%TEMP%\tenant_smoke_err.txt"
echo EXIT=%ERRORLEVEL%
