@echo off
REM ============================================================
REM  BookaBoost — install_files.bat v2.3.0
REM  Double-click to copy all build outputs to correct locations.
REM
REM  Main repo:   C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web
REM  Vercel app:  C:\Users\simmo\OneDrive\Desktop\advisorflow_complete
REM ============================================================

SET REPO=C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web
SET VERCEL=C:\Users\simmo\OneDrive\Desktop\advisorflow_complete

echo.
echo ============================================================
echo  BookaBoost File Installer v2.3.0
echo ============================================================
echo.

IF NOT EXIST "%REPO%" (
    echo ERROR: Repo folder not found: %REPO%
    pause & exit /b 1
)
IF NOT EXIST "%VERCEL%" (
    echo ERROR: Vercel app folder not found: %VERCEL%
    pause & exit /b 1
)

echo Copying main repo files...
echo.

echo   [1/8] LeadDetail.jsx  ^>  frontend/src/pages/
copy /Y "LeadDetail.jsx" "%REPO%\frontend\src\pages\LeadDetail.jsx"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [2/8] leads_router.py  ^>  app/routers/
copy /Y "leads_router.py" "%REPO%\app\routers\leads_router.py"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [3/8] calendar_router.py  ^>  app/routers/
copy /Y "calendar_router.py" "%REPO%\app\routers\calendar_router.py"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [4/8] email_router.py  ^>  app/routers/
copy /Y "email_router.py" "%REPO%\app\routers\email_router.py"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [5/8] email_poller_service.py  ^>  app/services/
copy /Y "email_poller_service.py" "%REPO%\app\services\email_poller_service.py"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [6/8] run_email_poller.py  ^>  app/jobs/
copy /Y "run_email_poller.py" "%REPO%\app\jobs\run_email_poller.py"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [7/8] render.yaml  ^>  repo root
copy /Y "render.yaml" "%REPO%\render.yaml"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [8/8] leads_router.py  ^>  app/routers/ (sms-optin endpoint)
REM Already copied above as step 2

echo.
echo Copying Vercel booking app files...
echo.

echo   [V1] optin.html  ^>  advisorflow_complete/
copy /Y "optin.html" "%VERCEL%\optin.html"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo   [V2] index.py  ^>  advisorflow_complete/api/
copy /Y "index.py" "%VERCEL%\api\index.py"
IF ERRORLEVEL 1 ( echo   FAILED && pause && exit /b 1 )

echo.
echo ============================================================
echo  All files installed successfully.
echo ============================================================
echo.
echo  AZURE CREDENTIALS (BookaBoost app - SIMMONSSTRONG tenant):
echo    Client ID:     0370359c-6156-49c6-9bbf-696a991ba868
echo    Client Secret: 9Xp8Q~OcyjZfmQPJGEcySSICdDjqiYoV4S~DLbhV
echo.
echo  Next steps:
echo    1. Run deploy.bat
echo    2. cd advisorflow_complete ^&^& vercel deploy --prod
echo    3. Twilio A2P: submit https://advisorflow-booking.vercel.app/optin
echo.
pause
