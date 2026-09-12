@echo off
REM Full three-device M2 scenario over real HTTP, against a throwaway local DB.
REM Nothing here touches production: the database is a file this script makes,
REM and the identity it seeds lives only in that file.
REM
REM NO CREDENTIAL IS WRITTEN DOWN. The JWT secret and the test account's
REM password are generated fresh on every run and exist only in this shell.
REM
REM PORT 8137 IS NOT ARBITRARY. This is a multi-worktree machine and another
REM thread already owns 8099; talking to somebody else's server reads exactly
REM like a wrong password. The check below refuses to proceed if anything is
REM already answering here, and the stop step kills the process it started by
REM PID rather than by window title.
setlocal
cd /d "%~dp0.."
set M2PORT=8137
set APP_ENV=development
set PY=C:\Dev\advisorflow-web\.venv\Scripts\python.exe

REM ABSOLUTE, not relative. A relative sqlite URL resolves against whatever
REM working directory the uvicorn child happens to inherit, and a seeder and a
REM server pointed at two different files look exactly like a wrong password.
set DBFILE=%CD%\_m2_local.db
set DATABASE_URL=sqlite:///%DBFILE:\=/%

for /f "usebackq delims=" %%L in (`"%PY%" scripts\m2_gen_secrets.py`) do %%L
if "%JWT_SECRET%"=="" goto :fail
if "%M2_VERIFY_PASSWORD%"=="" goto :fail

if exist _m2_local.db del /q _m2_local.db

echo --- database: %DATABASE_URL%
echo --- seeding throwaway database ---
"%PY%" scripts\m2_local_stage.py || goto :fail

echo --- confirming port %M2PORT% belongs to nobody else ---
"%PY%" scripts\m2_wait_for.py http://127.0.0.1:%M2PORT% --expect-down || goto :fail

echo --- starting backend on 127.0.0.1:%M2PORT% ---
start "m2-local-backend" /min "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %M2PORT% --log-level warning
"%PY%" scripts\m2_wait_for.py http://127.0.0.1:%M2PORT% 120 || goto :fail

echo --- three-device scenario ---
"%PY%" scripts\m2_live_verify.py http://127.0.0.1:%M2PORT% m2.verify@example.invalid "%M2_VERIFY_PASSWORD%"
set RC=%ERRORLEVEL%

echo --- stopping backend ---
"%PY%" scripts\m2_stop_local.py %M2PORT%
if exist _m2_local.db del /q _m2_local.db
exit /b %RC%

:fail
"%PY%" scripts\m2_stop_local.py %M2PORT%
exit /b 1
