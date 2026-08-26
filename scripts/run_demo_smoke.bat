@echo off
cd /d "%~dp0.."
set PYTHONIOENCODING=utf-8
python -X utf8 scripts\smoke_demo_mode.py 1> "%TEMP%\dm.txt" 2>&1
echo EXIT=%ERRORLEVEL%
