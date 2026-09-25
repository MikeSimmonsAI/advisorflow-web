@echo off
rem Started by START_EVOSYS_REVIEW.bat. Close this window to stop the API.
title EvoSys Review - API
cd /d "%~dp0..\.."
"%~dp0..\..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > "%TEMP%\evosys-review-api.log" 2>&1
