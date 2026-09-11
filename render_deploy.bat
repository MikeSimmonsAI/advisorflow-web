@echo off
REM ============================================================
REM render_deploy.bat - trigger a Render deploy by API, nothing else.
REM
REM It commits nothing and pushes nothing. Use deploy.bat to ship changes.
REM
REM CREDENTIAL: RENDER_API_KEY, from the environment. Never write a key into
REM this file - see docs/DEPLOYMENT.md.
REM
REM THE SERVICE IDS BELOW ARE NOT THE ADVISORFLOW ONES.
REM
REM AdvisorFlow's live services are:
REM     advisorflow-backend    srv-d8rsm2kvikkc738v8470
REM     advisorflow-frontend   srv-d8rslocvikkc738v7ocg
REM
REM The two ids in this script (srv-cus2...) belong to an older pair and have
REM been here since before the current blueprint. They are left untouched
REM rather than "corrected" on a guess: pointing a deploy trigger at a service
REM nobody verified is how you redeploy something you did not mean to. If you
REM want AdvisorFlow, use deploy.bat, or set the ids deliberately after
REM checking them in the Render dashboard.
REM ============================================================

if "%RENDER_API_KEY%"=="" (
    echo RENDER_API_KEY is not set, so nothing can be triggered.
    echo Set it once with:
    echo     setx RENDER_API_KEY "your-key-here"
    echo Then open a new terminal. See docs/DEPLOYMENT.md.
    exit /b 1
)

echo Triggering Render backend deploy...
powershell -Command "$key = $env:RENDER_API_KEY; $headers = @{'Authorization'=\"Bearer $key\"; 'Content-Type'='application/json'}; Invoke-RestMethod -Uri 'https://api.render.com/v1/services/srv-cus28q3tq21c73bkkklg/deploys' -Method POST -Headers $headers -Body '{}' | ConvertTo-Json"
echo.
echo Triggering Render frontend deploy...
powershell -Command "$key = $env:RENDER_API_KEY; $headers = @{'Authorization'=\"Bearer $key\"; 'Content-Type'='application/json'}; Invoke-RestMethod -Uri 'https://api.render.com/v1/services/srv-cus2b53tq21c73bkkkp0/deploys' -Method POST -Headers $headers -Body '{}' | ConvertTo-Json"
echo Done.
