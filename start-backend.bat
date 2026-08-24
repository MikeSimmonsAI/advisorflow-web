@echo off
echo AdvisorFlow Backend — localhost:8000
cd /d "%~dp0"
python -m uvicorn app.main:app --reload --port 8000
pause
