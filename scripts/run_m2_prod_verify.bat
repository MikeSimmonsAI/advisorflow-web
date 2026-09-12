@echo off
REM M2 PRODUCTION acceptance run, against the live backend, using the
REM clearly-labelled QA sales identity provisioned through God Ops.
REM
REM   run_m2_prod_verify.bat <token_file>   first run: spend the setup link
REM   run_m2_prod_verify.bat                later runs: password from env
REM
REM The QA password is generated here and never written to a file. Re-running
REM without a token file requires M2_VERIFY_PASSWORD already set in the shell.
setlocal
cd /d "%~dp0.."
set BASE=https://advisorflow-backend.onrender.com
set QAUSER=m2.qa.verify@evosyspro.demo.invalid
set PY=C:\Dev\advisorflow-web\.venv\Scripts\python.exe

if not "%~1"=="" (
  for /f "usebackq delims=" %%L in (`"%PY%" scripts\m2_gen_secrets.py`) do %%L
  echo --- spending the one-time setup link ---
  "%PY%" scripts\m2_prod_activate.py %BASE% "%~1" || goto :fail
)
if "%M2_VERIFY_PASSWORD%"=="" goto :fail

echo --- three-device production scenario ---
"%PY%" scripts\m2_live_verify.py %BASE% %QAUSER% "%M2_VERIFY_PASSWORD%"
exit /b %ERRORLEVEL%

:fail
echo M2 production verification could not start.
exit /b 1
