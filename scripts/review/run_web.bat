@echo off
rem Started by START_EVOSYS_REVIEW.bat. Close this window to stop the web app.
title EvoSys Review - Web
cd /d "%~dp0..\..\frontend"
call npm run dev -- --port 5173 --strictPort > "%TEMP%\evosys-review-web.log" 2>&1
