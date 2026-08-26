@echo off
REM ---------------------------------------------------------------------------
REM Start a LOCAL demo environment: APP_ENV=demo against a local SQLite file.
REM
REM The firewall installs at startup, so nothing this process runs can reach a
REM real provider. The database is a file in the repo (gitignored), never
REM Postgres, and app/services/environment.py refuses to boot if DATABASE_URL
REM ever looks like production.
REM
REM   scripts\demo_local.bat backend    -> uvicorn on 8098
REM   scripts\demo_local.bat seed       -> create the god operator + seed both
REM ---------------------------------------------------------------------------
cd /d "%~dp0.."

set "APP_ENV=demo"
set "DATABASE_URL=sqlite:///./demo_env.db"
set "JWT_SECRET=demo_local_only_not_a_real_secret_0123456789abcdef"
set "SECRET_KEY=demo_local_only_not_a_real_secret_0123456789abcdef"
set "ENCRYPTION_KEY=demo_local_only_not_a_real_secret_0123456789abcd"

if "%1"=="seed" goto seed
if "%1"=="backend" goto backend
echo Usage: demo_local.bat [backend^|seed]
exit /b 1

:backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8098
exit /b 0

:seed
python scripts\demo_operator.py
exit /b 0
