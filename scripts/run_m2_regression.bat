@echo off
REM Full backend regression for the M2 ship candidate, truly detached.
REM See scripts/m2_launch_regression.py for why plain children and `start` both
REM died when the machine's bridge dropped.
setlocal
cd /d "%~dp0.."
C:\Dev\advisorflow-web\.venv\Scripts\python.exe scripts\m2_launch_regression.py %1
