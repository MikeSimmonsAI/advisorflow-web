@echo off
REM M2 live verification against the deployed backend.
REM   run_m2_live_verify.bat                      -> unauthenticated half only
REM   run_m2_live_verify.bat <email> <password>   -> full three-device scenario
setlocal
set BASE=https://advisorflow-backend.onrender.com
python "%~dp0m2_live_verify.py" %BASE% %1 %2
endlocal
